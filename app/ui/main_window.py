import os
import xml.etree.ElementTree as ET
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton, QFileDialog,
    QLabel, QInputDialog, QMessageBox, QMenu
)
from app.styles import apply_global_styles
from app.ui.workspace_tab import WorkspaceTab
from app.ui.commit_notification_dialog import CommitNotificationDialog
from app.git_watcher import get_global_watcher

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.title = 'GitLab 快捷工具'
        self.left = 100
        self.top = 100
        self.width = 800
        self.height = 700
        self.config = self.load_config()
        self.git_watcher = get_global_watcher()
        # 设置主窗口引用，用于通知按钮点击时打开对话框
        self.git_watcher.set_main_window(self)
        self.initUI()
        # 启动定时器检查待处理的创建 MR 请求
        self._start_pending_mr_checker()

    def load_config(self):
        try:
            tree = ET.parse('config.xml')
            root = tree.getroot()
            return root
        except (FileNotFoundError, ET.ParseError):
            root = ET.Element('config')
            ET.SubElement(root, 'gitlab')
            ET.SubElement(root, 'workspaces')
            tree = ET.ElementTree(root)
            tree.write('config.xml', encoding='UTF-8', xml_declaration=True)
            return root

    def save_config(self):
        if self.config is not None:
            workspaces_node = self.config.find('workspaces')
            if workspaces_node is None:
                workspaces_node = ET.SubElement(self.config, 'workspaces')
            for ws in workspaces_node.findall('workspace'):
                workspaces_node.remove(ws)
            for i in range(self.workspace_tabs.count()):
                tab_widget = self.workspace_tabs.widget(i)
                if isinstance(tab_widget, WorkspaceTab):
                    ws_node = ET.SubElement(workspaces_node, 'workspace', {
                        'name': self.workspace_tabs.tabText(i),
                        'path': tab_widget.path
                    })
                    for j in range(tab_widget.target_branch_list.count()):
                        branch_name = tab_widget.target_branch_list.item(j).text()
                        ET.SubElement(ws_node, 'target_branch').text = branch_name
            tree = ET.ElementTree(self.config)
            tree.write('config.xml', encoding='UTF-8', xml_declaration=True)

    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        main_layout = QVBoxLayout()

        workspace_buttons_layout = QHBoxLayout()
        self.add_workspace_button = QPushButton('添加工作目录')
        self.notification_button = QPushButton('新提交通知')
        workspace_buttons_layout.addWidget(self.add_workspace_button)
        workspace_buttons_layout.addWidget(self.notification_button)
        main_layout.addLayout(workspace_buttons_layout)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.tabCloseRequested.connect(self.remove_workspace_tab)
        self.workspace_tabs.currentChanged.connect(self.on_workspace_tab_changed)
        self.welcome_tab = QWidget()
        welcome_layout = QVBoxLayout()
        welcome_label = QLabel('请选择一个工作区标签页以开始')
        welcome_label.setAlignment(Qt.AlignCenter)
        welcome_label.setObjectName('welcomeLabel')  # 设置对象名称
        welcome_layout.addWidget(welcome_label)
        self.welcome_tab.setLayout(welcome_layout)
        self.workspace_tabs.addTab(self.welcome_tab, '')
        welcome_index = self.workspace_tabs.indexOf(self.welcome_tab)
        if welcome_index != -1:
            self.workspace_tabs.tabBar().setTabVisible(welcome_index, False)
        self.workspace_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.workspace_tabs.customContextMenuRequested.connect(self.show_workspace_context_menu)
        main_layout.addWidget(self.workspace_tabs)

        self.setLayout(main_layout)

        self.add_workspace_button.clicked.connect(self.add_workspace)
        self.notification_button.clicked.connect(self.show_commit_notifications)

        self.load_workspaces()
        self.apply_styles()

    def load_workspaces(self):
        if self.config is not None:
            workspaces_node = self.config.find('workspaces')
            if workspaces_node is not None:
                removed_workspaces = []
                for ws in list(workspaces_node.findall('workspace')):
                    name = ws.get('name')
                    path = ws.get('path')
                    if name and path and os.path.isdir(path):
                        self.add_workspace_tab(name, path, ws, make_current=False)
                    else:
                        removed_workspaces.append(name or path or '未命名工作区')
                        workspaces_node.remove(ws)
                if removed_workspaces:
                    self.save_config()
                    QMessageBox.warning(self, '移除无效的工作区',
                                        '以下工作区的路径无效，已被自动移除：\n\n' + '\n'.join(removed_workspaces))

    def add_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if path:
            name, ok = QInputDialog.getText(self, '工作区名称', '为这个工作区输入一个名称:', text=path.split('/')[-1])
            if ok and name:
                self.add_workspace_tab(name, path, None)
                self.save_config()

    def add_workspace_tab(self, name, path, workspace_config, make_current=True):
        # 标准化路径为绝对路径
        path = os.path.abspath(path)
        tab = WorkspaceTab(path, self.config, workspace_config, name)
        self.workspace_tabs.addTab(tab, name)
        if make_current:
            self.workspace_tabs.setCurrentWidget(tab)

        # 启动 Git 监听，传递 workspace name
        self.git_watcher.add_repository(path, name)

    def remove_workspace_tab(self, index):
        if index < 0:
            return
        tab_name = self.workspace_tabs.tabText(index)
        reply = QMessageBox.question(self, '确认移除',
                                     f"您确定要移除工作区 '{tab_name}'吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            tab_widget = self.workspace_tabs.widget(index)
            if isinstance(tab_widget, WorkspaceTab):
                # 停止 Git 监听
                self.git_watcher.remove_repository(tab_widget.path)

            self.workspace_tabs.removeTab(index)
            self.save_config()

    def closeEvent(self, event):
        self.save_config()
        # 停止定时器
        if hasattr(self, '_pending_mr_timer'):
            self._pending_mr_timer.stop()
        # 停止所有 Git 监听
        self.git_watcher.stop_all()
        event.accept()

    def on_workspace_tab_changed(self, index):
        w = self.workspace_tabs.widget(index)
        if isinstance(w, WorkspaceTab):
            w.reload_new_branch_history()
            w.ensure_initialized()
            for i in range(self.workspace_tabs.count()):
                if self.workspace_tabs.widget(i) is self.welcome_tab:
                    self.workspace_tabs.removeTab(i)
                    break

    def show_workspace_context_menu(self, position):
        tab_index = self.workspace_tabs.tabBar().tabAt(position)
        if tab_index != -1:
            context_menu = QMenu(self)
            rename_action = context_menu.addAction('重命名')
            rename_action.triggered.connect(lambda: self.rename_workspace_tab(tab_index))
            context_menu.exec_(self.workspace_tabs.mapToGlobal(position))

    def rename_workspace_tab(self, index):
        current_name = self.workspace_tabs.tabText(index)
        tab_widget = self.workspace_tabs.widget(index)
        if isinstance(tab_widget, WorkspaceTab):
            tab_path = tab_widget.path
        else:
            tab_path = "Unknown Path"
        new_name, ok = QInputDialog.getText(self, '重命名工作区',
                                            '输入新的工作区名称:',
                                            text=current_name)
        if ok and new_name:
            self.workspace_tabs.setTabText(index, new_name)
            if self.config is not None:
                workspaces_node = self.config.find('workspaces')
                if workspaces_node is not None:
                    for ws in workspaces_node.findall('workspace'):
                        if ws.get('path') == tab_path:
                            ws.set('name', new_name)
                            break
            self.save_config()

    def apply_styles(self):
        apply_global_styles()

    def _start_pending_mr_checker(self):
        """启动定时器检查待处理的创建 MR 请求"""
        self._pending_mr_timer = QTimer(self)
        self._pending_mr_timer.timeout.connect(self._check_pending_mr_requests)
        self._pending_mr_timer.start(500)  # 每 500ms 检查一次

    def _check_pending_mr_requests(self):
        """检查并处理待处理的创建 MR 请求"""
        if not self.git_watcher.pending_create_mr_requests:
            return

        # 取出所有待处理的请求
        requests = self.git_watcher.pending_create_mr_requests[:]
        self.git_watcher.pending_create_mr_requests.clear()

        for request in requests:
            try:
                from app.ui.create_mr_dialog import CreateMRDialog
                dialog = CreateMRDialog(
                    repo_path=request.repo_path,
                    workspace_name=request.workspace_name,
                    config=self.config,
                    source_branch=request.branch,
                    parent=self
                )
                dialog.exec_()
            except Exception as e:
                QMessageBox.warning(self, '错误', f'打开创建 MR 对话框失败: {e}')

    def show_commit_notifications(self):
        """显示提交通知对话框"""
        # 直接传递 watcher 的 commits 列表的引用，而不是副本
        dialog = CommitNotificationDialog(self.git_watcher.commits, self)
        result = dialog.exec_()

        # 如果用户在对话框中清空了记录，需要更新 watcher
        if not self.git_watcher.commits:
            self.git_watcher.clear_commits()

