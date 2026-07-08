import os
import sys
import glob
import copy
import subprocess
import xml.etree.ElementTree as ET
from PyQt5.QtCore import Qt, QTimer, QEvent, QStringListModel
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton, QFileDialog,
    QLabel, QInputDialog, QLineEdit, QCompleter,
    QMessageBox, QMenu, QComboBox, QFrame
)
from PyQt5.QtWidgets import QToolTip
from PyQt5.QtGui import QIcon, QPainter, QColor
from PyQt5.QtWidgets import QSystemTrayIcon
from app.styles import apply_global_styles
from app.async_utils import run_blocking
from app.ui.workspace_tab import WorkspaceTab
from app.ui.all_projects_tab import AllProjectsTab
from app.ui.commit_notification_dialog import CommitNotificationDialog
from app.git_watcher import get_global_watcher

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.title = 'GitLab 快捷工具'
        self.left = 100
        self.top = 100
        self.width = 1000
        self.height = 800
        # 启动时恢复上次使用的 config 文件，找不到则回退到默认 config.xml
        self.config_file = self._load_last_config_file()
        self.config = self.load_config()
        self.git_watcher = get_global_watcher()
        # 设置主窗口引用，用于通知按钮点击时打开对话框
        self.git_watcher.set_main_window(self)
        self.tray_icon = None
        self.initUI()
        self.init_system_tray()
        # 启动定时器检查待处理的创建 MR 请求
        self._start_pending_mr_checker()

    _LAST_CONFIG_KEY = 'last_config_file'

    def _load_last_config_file(self):
        """读取上次使用的 config 文件名，文件不存在时回退到 config.xml。"""
        try:
            import shelve
            with shelve.open('cache.db') as db:
                name = db.get(self._LAST_CONFIG_KEY)
            if name and os.path.isfile(name):
                return name
        except Exception:
            pass
        return 'config.xml'

    def _save_last_config_file(self):
        """持久化当前 config 文件名，下次启动时恢复。"""
        try:
            import shelve
            with shelve.open('cache.db', writeback=True) as db:
                db[self._LAST_CONFIG_KEY] = self.config_file
        except Exception:
            pass

    def load_config(self):
        try:
            tree = ET.parse(self.config_file)
            root = tree.getroot()
            return root
        except (FileNotFoundError, ET.ParseError):
            root = ET.Element('config')
            ET.SubElement(root, 'gitlab')
            ET.SubElement(root, 'workspaces')
            tree = ET.ElementTree(root)
            tree.write(self.config_file, encoding='UTF-8', xml_declaration=True)
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
            tree.write(self.config_file, encoding='UTF-8', xml_declaration=True)

    def scan_config_files(self):
        """扫描当前目录下的配置文件"""
        files = glob.glob('config*.xml')
        # config.xml 排第一，其余按名称排序
        return sorted(files, key=lambda f: (f != 'config.xml', f))

    def refresh_config_combo(self):
        """刷新配置文件下拉列表"""
        self.config_combo.blockSignals(True)
        current = self.config_file
        self.config_combo.clear()
        for f in self.scan_config_files():
            self.config_combo.addItem(f)
        self.config_combo.setCurrentText(current)
        self.config_combo.blockSignals(False)
        # 禁止删除默认配置
        self.delete_config_button.setEnabled(self.config_file != 'config.xml')

    def on_config_changed(self, config_file: str):
        """配置文件切换回调"""
        if not config_file or config_file == self.config_file:
            return
        reply = QMessageBox.question(
            self, '切换配置',
            f'确定要切换到 "{config_file}" 吗？\n当前配置将自动保存。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.switch_config(config_file)
        else:
            self.refresh_config_combo()

    def switch_config(self, config_file: str):
        """切换到指定的配置文件"""
        if config_file == self.config_file:
            return
        self.save_config()
        self.clear_all_workspaces()
        self.config_file = config_file
        self.config = self.load_config()
        self.load_workspaces()
        self.refresh_config_combo()
        # 切换配置后，同步「所有项目」tab 的 config 引用与共享字段
        if hasattr(self, 'all_projects_tab'):
            self.all_projects_tab.reload_config(self.config, self.config_file)
            self.all_projects_tab.refresh_projects()
        self._save_last_config_file()

    def clear_all_workspaces(self):
        """清除所有工作区标签页"""
        for i in range(self.workspace_tabs.count() - 1, -1, -1):
            tab_widget = self.workspace_tabs.widget(i)
            if isinstance(tab_widget, WorkspaceTab):
                self.git_watcher.remove_repository(tab_widget.path)
                self.workspace_tabs.removeTab(i)
        # 重新添加欢迎标签页（如果已被移除）。all_projects_tab 始终在 index 0，
        # 欢迎页不可见，追加到末尾即可，避免挪动「所有项目」的位置。
        if self.workspace_tabs.indexOf(self.welcome_tab) == -1:
            self.workspace_tabs.addTab(self.welcome_tab, '')
            idx = self.workspace_tabs.indexOf(self.welcome_tab)
            if idx != -1:
                self.workspace_tabs.tabBar().setTabVisible(idx, False)
        if hasattr(self, 'all_projects_tab'):
            self.all_projects_tab.refresh_projects()

    def create_new_config(self):
        """创建新的配置文件"""
        name, ok = QInputDialog.getText(self, '新建配置文件', '输入配置名称:')
        if not (ok and name.strip()):
            return
        name = name.strip()
        if name.endswith('.xml'):
            name = name[:-4]
        filename = f'config-{name}.xml' if not name.startswith('config') else f'{name}.xml'
        if os.path.exists(filename):
            QMessageBox.warning(self, '错误', f'配置文件 "{filename}" 已存在')
            return
        # 从当前配置复制 gitlab 和前缀设置
        root = ET.Element('config')
        gitlab = self.config.find('gitlab')
        if gitlab is not None:
            root.append(copy.deepcopy(gitlab))
        prefix = self.config.find('new_branch_prefix')
        if prefix is not None:
            root.append(copy.deepcopy(prefix))
        ET.SubElement(root, 'workspaces')
        tree = ET.ElementTree(root)
        tree.write(filename, encoding='UTF-8', xml_declaration=True)
        # 切换到新配置
        self.switch_config(filename)

    def delete_current_config(self):
        """删除当前配置文件"""
        if self.config_file == 'config.xml':
            QMessageBox.warning(self, '错误', '不能删除默认配置文件')
            return
        config_to_delete = self.config_file
        reply = QMessageBox.question(
            self, '删除配置',
            f'确定要删除 "{config_to_delete}" 吗？\n此操作不可撤销。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        # 先清除工作区（不保存，避免重建被删文件）
        self.clear_all_workspaces()
        try:
            os.remove(config_to_delete)
        except OSError as e:
            QMessageBox.warning(self, '错误', f'删除失败: {e}')
        # 切回默认配置
        self.config_file = 'config.xml'
        self.config = self.load_config()
        self.load_workspaces()
        self.refresh_config_combo()
        if hasattr(self, 'all_projects_tab'):
            self.all_projects_tab.reload_config(self.config, self.config_file)
            self.all_projects_tab.refresh_projects()
        self._save_last_config_file()

    def initUI(self):
        self.setWindowTitle(self.title)
        # 自适应可用屏幕：默认尺寸 800x700，若超出可用区域则收缩；同时确保左上角可见
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.width = min(self.width, avail.width() - 40)
            self.height = min(self.height, avail.height() - 40)
            self.left = max(20, min(self.left, avail.width() - self.width - 20))
            self.top = max(20, min(self.top, avail.height() - self.height - 20))
        self.setGeometry(self.left, self.top, self.width, self.height)
        # 给内部紧凑布局留个底线，避免被压扁
        self.setMinimumSize(680, 480)

        main_layout = QVBoxLayout()

        workspace_buttons_layout = QHBoxLayout()
        self.add_workspace_button = QPushButton('添加工作目录')
        self.notification_button = QPushButton('新提交通知')
        workspace_buttons_layout.addWidget(self.add_workspace_button)
        workspace_buttons_layout.addWidget(self.notification_button)
        workspace_buttons_layout.addStretch()

        # 工作目录搜索框：输入名称/路径片段跳转到对应 tab
        self.workspace_search = QLineEdit()
        self.workspace_search.setPlaceholderText('搜索工作目录...')
        self.workspace_search.setMaximumWidth(240)
        self.workspace_search.setMinimumWidth(140)
        self.workspace_search.setToolTip('输入工作区名称或路径片段，回车或选中下拉项即跳转')
        self.workspace_search.setClearButtonEnabled(True)
        self._workspace_search_model = QStringListModel(self)
        self._workspace_search_completer = QCompleter(self._workspace_search_model, self)
        self._workspace_search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        try:
            self._workspace_search_completer.setFilterMode(Qt.MatchContains)
        except AttributeError:
            pass
        self._workspace_search_completer.setPopup(self._workspace_search_completer.popup())
        self.workspace_search.setCompleter(self._workspace_search_completer)
        workspace_buttons_layout.addWidget(self.workspace_search)
        # 缓存：displayText -> (tab_index, ws_path, ws_name)；用于激活时回查
        self._workspace_search_entries = []
        self._workspace_search_completer.activated.connect(self._on_workspace_search_activated)
        self.workspace_search.returnPressed.connect(self._on_workspace_search_return)
        self.workspace_search.textEdited.connect(self._on_workspace_search_text_edited)

        # 竖直分隔线：把工作区/通知 与 配置管理 视觉分组
        toolbar_sep = QFrame()
        toolbar_sep.setFrameShape(QFrame.VLine)
        toolbar_sep.setFrameShadow(QFrame.Sunken)
        toolbar_sep.setFixedWidth(2)
        toolbar_sep.setStyleSheet('QFrame { color: #d0d0d0; }')
        workspace_buttons_layout.addWidget(toolbar_sep)

        config_label = QLabel('配置:')
        self.config_combo = QComboBox()
        self.config_combo.setMinimumWidth(150)
        self.config_combo.setToolTip('切换配置文件')
        self.new_config_button = QPushButton('新建')
        self.new_config_button.setFixedWidth(50)
        self.new_config_button.clicked.connect(self.create_new_config)
        self.delete_config_button = QPushButton('删除')
        self.delete_config_button.setFixedWidth(50)
        self.delete_config_button.clicked.connect(self.delete_current_config)
        self.refresh_config_combo()
        self.config_combo.currentTextChanged.connect(self.on_config_changed)
        workspace_buttons_layout.addWidget(config_label)
        workspace_buttons_layout.addWidget(self.config_combo)
        workspace_buttons_layout.addWidget(self.new_config_button)
        workspace_buttons_layout.addWidget(self.delete_config_button)
        main_layout.addLayout(workspace_buttons_layout)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.tabCloseRequested.connect(self.remove_workspace_tab)
        self.workspace_tabs.currentChanged.connect(self.on_workspace_tab_changed)

        # 固定的「批量操作」tab，插入到最左侧（index 0）
        self.all_projects_tab = AllProjectsTab(self, self.config, self.config_file)
        self.workspace_tabs.addTab(self.all_projects_tab, '批量操作')
        # 隐藏该固定 tab 的关闭按钮，防止误删（延后到窗口显示后执行，避免在构造期触发底层绘制问题）
        QTimer.singleShot(0, lambda: self._hide_close_button_for_tab(self.all_projects_tab))

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
        # tab 悬浮 3 秒后显示项目路径 tooltip
        self._tab_hover_timer = QTimer(self)
        self._tab_hover_timer.setSingleShot(True)
        self._tab_hover_timer.setInterval(1500)
        self._tab_hover_timer.timeout.connect(self._show_tab_path_tooltip)
        self._tab_hover_pos = None
        self._tab_hover_index = -1
        self.workspace_tabs.tabBar().installEventFilter(self)
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
        # 加载完成后同步「所有项目」列表
        if hasattr(self, 'all_projects_tab'):
            self.all_projects_tab.refresh_projects()

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
        tab = WorkspaceTab(path, self.config, workspace_config, name, config_file=self.config_file)
        self.workspace_tabs.addTab(tab, name)
        if make_current:
            self.workspace_tabs.setCurrentWidget(tab)

        # 启动 Git 监听，传递 workspace name
        self.git_watcher.add_repository(path, name)

        # 同步刷新「所有项目」里的项目勾选列表
        if hasattr(self, 'all_projects_tab'):
            self.all_projects_tab.refresh_projects()

    def remove_workspace_tab(self, index):
        if index < 0:
            return
        tab_widget = self.workspace_tabs.widget(index)
        # 固定的「所有项目」tab 不可关闭
        if isinstance(tab_widget, AllProjectsTab):
            return
        tab_name = self.workspace_tabs.tabText(index)
        reply = QMessageBox.question(self, '确认移除',
                                     f"您确定要移除工作区 '{tab_name}'吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            if isinstance(tab_widget, WorkspaceTab):
                # 停止 Git 监听
                self.git_watcher.remove_repository(tab_widget.path)

            self.workspace_tabs.removeTab(index)
            self.save_config()
            if hasattr(self, 'all_projects_tab'):
                self.all_projects_tab.refresh_projects()

    def closeEvent(self, event):
        """关闭窗口事件 - 显示选择对话框"""
        if self.tray_icon and self.tray_icon.isVisible():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle('关闭窗口')
            msg_box.setText('您希望如何操作？')

            minimize_btn = msg_box.addButton("最小化到托盘", QMessageBox.YesRole)
            quit_btn = msg_box.addButton("退出程序", QMessageBox.NoRole)
            cancel_btn = msg_box.addButton("取消", QMessageBox.RejectRole)

            msg_box.setDefaultButton(minimize_btn)
            msg_box.exec_()

            if msg_box.clickedButton() == minimize_btn:
                # 最小化到托盘
                event.ignore()
                self.hide()
                self.tray_icon.showMessage(
                    self.title,
                    "程序已最小化到系统托盘",
                    QSystemTrayIcon.Information,
                    2000
                )
            elif msg_box.clickedButton() == quit_btn:
                # 退出程序
                self.quit_app()
                event.accept()
            else:
                # 取消
                event.ignore()
        else:
            # 没有托盘支持，直接退出
            self.quit_app()
            event.accept()

    def on_workspace_tab_changed(self, index):
        w = self.workspace_tabs.widget(index)
        if isinstance(w, WorkspaceTab):
            w.reload_new_branch_history()
            w.ensure_initialized()
            # 立即用缓存的分支显示，再异步刷新当前分支
            self._update_window_title(w, getattr(w, '_cached_branch', None))
            self._refresh_current_branch_async(w)
        elif isinstance(w, AllProjectsTab):
            # 切到「批量操作」恢复默认标题
            self.setWindowTitle(self.title)
        # 只要切到任何可见 tab（WorkspaceTab 或 AllProjectsTab）就移除隐藏的欢迎页。
        # 注意：initUI 中 addTab(AllProjectsTab) 会同步触发本回调，此时 welcome_tab
        # 可能尚未创建，需要 hasattr 兜底。
        if isinstance(w, (WorkspaceTab, AllProjectsTab)) and hasattr(self, 'welcome_tab'):
            for i in range(self.workspace_tabs.count()):
                if self.workspace_tabs.widget(i) is self.welcome_tab:
                    self.workspace_tabs.removeTab(i)
                    break

    def _update_window_title(self, tab, branch):
        """根据当前工作区 tab 更新窗口标题：标题 — 📂 路径  🌿 分支"""
        if not isinstance(tab, WorkspaceTab):
            self.setWindowTitle(self.title)
            return
        path = (tab.path or '').strip() or '(无路径)'
        branch_text = (branch or '').strip() or '(无分支)'
        self.setWindowTitle(f'{self.title}  —  📂 {path}  🌿 {branch_text}')

    def _refresh_current_branch_async(self, tab):
        """异步获取 tab 对应仓库的当前分支，完成后若仍在当前 tab 则更新标题。"""
        path = getattr(tab, 'path', None)
        if not path:
            return

        def _fetch():
            import subprocess
            try:
                r = subprocess.run(
                    ['git', 'branch', '--show-current'],
                    cwd=path, capture_output=True, text=True,
                    encoding='utf-8', errors='replace'
                )
                if r.returncode == 0:
                    return r.stdout.strip() or ''
                return None
            except Exception:
                return None

        def _on_done(branch):
            if not branch:
                return
            # 缓存，下次切换可立即显示
            tab._cached_branch = branch
            # 仍然在当前 tab 才更新标题
            if self.workspace_tabs.currentWidget() is tab:
                self._update_window_title(tab, branch)

        run_blocking(_fetch, on_success=_on_done, parent=self)

    def show_workspace_context_menu(self, position):
        tab_index = self.workspace_tabs.tabBar().tabAt(position)
        if tab_index != -1:
            tab_widget = self.workspace_tabs.widget(tab_index)
            # 固定的「所有项目」tab 不显示右键菜单
            if isinstance(tab_widget, AllProjectsTab):
                return
            context_menu = QMenu(self)
            rename_action = context_menu.addAction('重命名')
            rename_action.triggered.connect(lambda: self.rename_workspace_tab(tab_index))
            open_folder_action = context_menu.addAction('打开文件夹所在位置')
            open_folder_action.triggered.connect(
                lambda: self._open_workspace_folder(tab_widget)
            )
            show_path_action = context_menu.addAction('显示项目路径')
            show_path_action.triggered.connect(
                lambda: self._show_workspace_path(tab_widget)
            )
            context_menu.exec_(self.workspace_tabs.mapToGlobal(position))

    def _open_workspace_folder(self, tab_widget):
        """在系统文件管理器中打开工作区目录。"""
        if not isinstance(tab_widget, WorkspaceTab):
            return
        path = tab_widget.path
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, '提示', f'路径不存在：{path}')
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            QMessageBox.warning(self, '打开失败', str(e))

    def _show_workspace_path(self, tab_widget):
        """弹窗展示工作区名称与本地路径，方便复制。"""
        if not isinstance(tab_widget, WorkspaceTab):
            return
        name = tab_widget.workspace_name or ''
        path = tab_widget.path or ''
        QMessageBox.information(self, '项目路径', f'名称：{name}\n路径：{path}')

    def eventFilter(self, obj, event):
        """监听 workspace tab bar 的悬浮事件：进入/移动时启动 3 秒定时器，离开时取消。"""
        if obj is self.workspace_tabs.tabBar():
            etype = event.type()
            if etype == QEvent.ToolTip:
                # 让默认 tooltip 行为不触发，自行用定时器控制
                pos = event.pos()
                idx = self.workspace_tabs.tabBar().tabAt(pos)
                if idx != self._tab_hover_index:
                    self._tab_hover_index = idx
                    self._tab_hover_pos = event.globalPos()
                    if idx >= 0:
                        self._tab_hover_timer.start()
                    else:
                        self._tab_hover_timer.stop()
                else:
                    self._tab_hover_pos = event.globalPos()
                return True
            elif etype in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                self._tab_hover_timer.stop()
                self._tab_hover_index = -1
                QToolTip.hideText()
        return super().eventFilter(obj, event)

    def _show_tab_path_tooltip(self):
        """定时器触发：在鼠标位置显示项目路径 tooltip。"""
        idx = self._tab_hover_index
        if idx < 0 or idx >= self.workspace_tabs.count():
            return
        tab_widget = self.workspace_tabs.widget(idx)
        if not isinstance(tab_widget, WorkspaceTab):
            return
        name = tab_widget.workspace_name or ''
        path = tab_widget.path or ''
        text = f'{name}\n{path}' if name else path
        if self._tab_hover_pos is not None:
            QToolTip.showText(self._tab_hover_pos, text, self.workspace_tabs.tabBar())

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
            if isinstance(tab_widget, WorkspaceTab):
                tab_widget.workspace_name = new_name
            if self.config is not None:
                workspaces_node = self.config.find('workspaces')
                if workspaces_node is not None:
                    for ws in workspaces_node.findall('workspace'):
                        if ws.get('path') == tab_path:
                            ws.set('name', new_name)
                            break
            self.save_config()
            if hasattr(self, 'all_projects_tab'):
                self.all_projects_tab.refresh_projects()

    def apply_styles(self):
        apply_global_styles()

    def _hide_close_button_for_tab(self, tab_widget):
        """隐藏指定 tab 的关闭按钮（用于固定的「所有项目」tab）。"""
        from PyQt5.QtWidgets import QTabBar
        idx = self.workspace_tabs.indexOf(tab_widget)
        if idx < 0:
            return
        bar = self.workspace_tabs.tabBar()
        try:
            bar.setTabButton(idx, QTabBar.LeftSide, None)
            bar.setTabButton(idx, QTabBar.RightSide, None)
        except Exception:
            pass

    # ──────────────────────── 工作目录搜索框 ────────────────────────

    def _refresh_workspace_search_entries(self):
        """重建搜索框的候选项列表（每次聚焦/编辑时调用，确保最新）。"""
        entries = []
        display_texts = []
        for i in range(self.workspace_tabs.count()):
            w = self.workspace_tabs.widget(i)
            if not isinstance(w, WorkspaceTab):
                continue
            name = (w.workspace_name or '').strip()
            path = (w.path or '').strip()
            if not name and not path:
                continue
            display = f'{name}  —  {path}' if path else name
            entries.append((display, w))
            display_texts.append(display)
        self._workspace_search_entries = entries
        self._workspace_search_model.setStringList(display_texts)

    def _on_workspace_search_text_edited(self, _text):
        """用户开始输入时刷新候选项，保证与最新 tab 状态一致。"""
        self._refresh_workspace_search_entries()

    def _jump_to_workspace(self, tab_widget):
        """跳转到指定工作目录 tab，清空搜索框。"""
        if tab_widget is None:
            return
        self.workspace_tabs.setCurrentWidget(tab_widget)
        self.workspace_search.clear()
        try:
            tab_widget.setFocus()
        except Exception:
            pass

    def _on_workspace_search_activated(self, text):
        """补全项被选中：根据 displayText 找到对应 tab 跳转。"""
        for display, w in self._workspace_search_entries:
            if display == text:
                self._jump_to_workspace(w)
                return

    def _on_workspace_search_return(self):
        """回车：选第一个匹配项（优先 name 前缀匹配，否则包含匹配）。"""
        text = self.workspace_search.text().strip()
        if not text:
            return
        self._refresh_workspace_search_entries()
        lower = text.lower()
        matches = [(d, w) for d, w in self._workspace_search_entries if lower in d.lower()]
        if not matches:
            return
        target = matches[0]
        for d, w in matches:
            if d.lower().startswith(lower):
                target = (d, w)
                break
        self._jump_to_workspace(target[1])

    def create_tray_icon(self):
        """创建托盘图标"""
        pixmap = QIcon()
        # 创建一个简单的图标
        from PyQt5.QtGui import QPixmap
        from PyQt5.QtCore import QSize
        size = 32
        px = QPixmap(size, size)
        px.fill(Qt.transparent)
        painter = QPainter(px)
        painter.setRenderHint(QPainter.Antialiasing)
        # 绘制圆形背景
        painter.setBrush(QColor("#FC6D26"))  # GitLab 橙色
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        # 绘制 "G" 字
        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPixelSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(px.rect(), Qt.AlignCenter, "G")
        painter.end()
        return QIcon(px)

    def init_system_tray(self):
        """初始化系统托盘"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self.create_tray_icon(), self)
        self.tray_icon.setToolTip(self.title)

        # 创建托盘菜单
        tray_menu = QMenu()
        show_action = tray_menu.addAction("显示窗口")
        show_action.triggered.connect(self.show_window)
        quit_action = tray_menu.addAction("退出程序")
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)

        # 左键点击恢复窗口
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        """托盘图标被点击时的处理"""
        if reason == QSystemTrayIcon.Trigger:  # 左键点击
            self.show_window()

    def show_window(self):
        """显示主窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def quit_app(self):
        """完全退出程序"""
        self.save_config()
        self._save_last_config_file()
        if hasattr(self, '_pending_mr_timer'):
            self._pending_mr_timer.stop()
        self.git_watcher.stop_all()
        QApplication.instance().quit()

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
                from PyQt5.QtCore import Qt
                # 如果主窗口隐藏，先显示主窗口以避免对话框关闭时程序退出
                was_hidden = self.isHidden()
                if was_hidden:
                    self.show()

                dialog = CreateMRDialog(
                    repo_path=request.repo_path,
                    workspace_name=request.workspace_name,
                    config=self.config,
                    source_branch=request.branch,
                    parent=self,
                    config_file=self.config_file
                )
                # 设置为工具窗口，打开时置顶
                dialog.setWindowFlags(dialog.windowFlags() | Qt.Tool)
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                dialog.exec_()

                # 如果之前是隐藏的，再次隐藏
                if was_hidden:
                    self.hide()
            except Exception as e:
                QMessageBox.warning(self, '错误', f'打开创建 MR 对话框失败: {e}')

    def show_commit_notifications(self):
        """显示提交通知对话框"""
        # 如果主窗口隐藏，先显示主窗口以避免对话框关闭时程序退出
        was_hidden = self.isHidden()
        if was_hidden:
            self.show()

        # 直接传递 watcher 的 commits 列表的引用，而不是副本
        dialog = CommitNotificationDialog(self.git_watcher.commits, self)
        # 设置为工具窗口，打开时置顶
        dialog.setWindowFlags(dialog.windowFlags() | Qt.Tool)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec_()

        # 如果之前是隐藏的，再次隐藏
        if was_hidden:
            self.hide()

        # 如果用户在对话框中清空了记录，需要更新 watcher
        if not self.git_watcher.commits:
            self.git_watcher.clear_commits()

    def show_notification_from_watcher(self):
        """从 GitWatcher 调用的方法，用于在主线程中显示系统通知"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [MainWindow] show_notification_from_watcher 被调用")

        # 从 watcher 中获取待显示的 commit
        commit = getattr(self.git_watcher, '_pending_notification_commit', None)
        if commit:
            # 调用 watcher 的通知方法，现在在主线程中执行
            self.git_watcher._show_system_notification(commit)
            # 清除临时保存的 commit
            self.git_watcher._pending_notification_commit = None
        else:
            print(f"[{timestamp}] [MainWindow] 警告: _pending_notification_commit 为 None")

