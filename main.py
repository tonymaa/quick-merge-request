import sys
import os
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit, 
    QPushButton, QFileDialog, QLabel, QTextEdit, QComboBox, QFormLayout, QInputDialog,
    QMessageBox, QListWidget, QAbstractItemView
)
from PyQt5.QtCore import Qt

# Import refactored functions
from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import get_local_branches, generate_mr, get_mr_defaults

class WorkspaceTab(QWidget):
    """A widget for a single workspace, containing its own git tools."""
    def __init__(self, path, config, workspace_config):
        super().__init__()
        self.path = path
        self.config = config
        self.workspace_config = workspace_config
        self.initUI()

    def initUI(self):
        # This will contain the 'Create Branch' and 'Create MR' tabs
        self.tools_tabs = QTabWidget()
        self.create_branch_tab = QWidget()
        self.create_mr_tab = QWidget()

        self.tools_tabs.addTab(self.create_branch_tab, '创建分支')
        self.tools_tabs.addTab(self.create_mr_tab, '创建合并请求')

        # Initialize the content of each tool tab
        self.init_create_branch_tab()
        self.init_create_mr_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tools_tabs)
        self.setLayout(layout)
        
        # Initial data load
        self.run_refresh_remote_branches()
        self.run_refresh_branches()

    def init_create_branch_tab(self):
        layout = QFormLayout()
        new_branch_prefix = ''
        if self.config.find('new_branch_prefix') is not None:
            new_branch_prefix = self.config.find('new_branch_prefix').text
        self.new_branch_input = QLineEdit(new_branch_prefix)
        layout.addRow('新分支名:', self.new_branch_input)

        # Search box for available branches
        self.branch_search_input = QLineEdit()
        self.branch_search_input.setPlaceholderText('搜索分支...')
        self.branch_search_input.textChanged.connect(self.filter_available_branches)

        # Shuttle box for target branches
        shuttle_layout = QHBoxLayout()

        # Available branches list with search
        available_layout = QVBoxLayout()
        available_layout.addWidget(self.branch_search_input)
        self.available_branches_list = QListWidget()
        self.available_branches_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        available_layout.addWidget(self.available_branches_list)

        # Buttons for moving items
        move_buttons_layout = QVBoxLayout()
        self.add_to_target_button = QPushButton('>>')
        self.remove_from_target_button = QPushButton('<<')
        move_buttons_layout.addStretch()
        move_buttons_layout.addWidget(self.add_to_target_button)
        move_buttons_layout.addWidget(self.remove_from_target_button)
        move_buttons_layout.addStretch()

        # Selected target branches list
        self.target_branch_list = QListWidget()
        self.target_branch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if self.workspace_config is not None:
            for branch_node in self.workspace_config.findall('target_branch'):
                self.target_branch_list.addItem(branch_node.text)

        shuttle_layout.addLayout(available_layout)
        shuttle_layout.addLayout(move_buttons_layout)
        shuttle_layout.addWidget(self.target_branch_list)

        # Branch control buttons
        branch_buttons_layout = QHBoxLayout()
        self.refresh_remote_branches_button = QPushButton('刷新远程分支')
        branch_buttons_layout.addWidget(self.refresh_remote_branches_button)
        branch_buttons_layout.addStretch()

        layout.addRow('可选远程分支', branch_buttons_layout)
        layout.addRow(shuttle_layout)

        self.create_branch_button = QPushButton('创建分支')
        self.create_branch_output = QTextEdit()
        self.create_branch_output.setReadOnly(True)

        layout.addRow(self.create_branch_button)
        layout.addRow(self.create_branch_output)

        self.create_branch_button.clicked.connect(self.run_create_branch)
        self.refresh_remote_branches_button.clicked.connect(self.run_refresh_remote_branches)
        self.add_to_target_button.clicked.connect(self.move_to_target)
        self.remove_from_target_button.clicked.connect(self.remove_from_target)

        self.create_branch_tab.setLayout(layout)

    def filter_available_branches(self, text):
        for i in range(self.available_branches_list.count()):
            item = self.available_branches_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def move_to_target(self):
        selected_items = self.available_branches_list.selectedItems()
        for item in selected_items:
            # Move item from available to target
            if not item.isHidden():
                self.target_branch_list.addItem(item.text())
                self.available_branches_list.takeItem(self.available_branches_list.row(item))

    def remove_from_target(self):
        selected_items = self.target_branch_list.selectedItems()
        for item in selected_items:
            # Move item from target back to available
            self.available_branches_list.addItem(item.text())
            self.target_branch_list.takeItem(self.target_branch_list.row(item))

    def init_create_mr_tab(self):
        layout = QFormLayout()

        gitlab_config = self.config.find('gitlab') if self.config is not None else None
        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default

        self.gitlab_url_input = QLineEdit(get_config_value(gitlab_config, 'gitlab_url'))
        self.token_input = QLineEdit(get_config_value(gitlab_config, 'private_token'))
        self.assignee_input = QLineEdit(get_config_value(gitlab_config, 'assignee'))
        self.reviewer_input = QLineEdit(get_config_value(gitlab_config, 'reviewer'))

        self.source_branch_combo = QComboBox()
        self.refresh_branches_button = QPushButton('刷新本地分支')
        self.mr_title_input = QLineEdit()
        self.mr_description_input = QTextEdit()
        self.create_mr_button = QPushButton('创建合并请求')
        self.mr_output = QTextEdit()
        self.mr_output.setReadOnly(True)

        layout.addRow('GitLab 地址:', self.gitlab_url_input)
        layout.addRow('私有 Token:', self.token_input)
        layout.addRow('指派给:', self.assignee_input)
        layout.addRow('审查者:', self.reviewer_input)

        source_branch_layout = QHBoxLayout()
        source_branch_layout.addWidget(self.source_branch_combo)
        source_branch_layout.addWidget(self.refresh_branches_button)
        layout.addRow('源分支:', source_branch_layout)

        layout.addRow('标题:', self.mr_title_input)
        layout.addRow('描述:', self.mr_description_input)

        layout.addRow(self.create_mr_button)
        layout.addRow(self.mr_output)

        self.refresh_branches_button.clicked.connect(self.run_refresh_branches)
        self.source_branch_combo.currentIndexChanged.connect(self.update_mr_defaults)
        self.create_mr_button.clicked.connect(self.run_create_mr)

        self.create_mr_tab.setLayout(layout)

    def run_create_branch(self):
        if self.target_branch_list.count() == 0:
            self.create_branch_output.setText('请至少选择一个目标分支。')
            return
            
        target_branches = [self.target_branch_list.item(i).text() for i in range(self.target_branch_list.count())]
        new_branch = self.new_branch_input.text()

        self.create_branch_output.setText('处理中...')
        QApplication.processEvents()
        
        all_output = []
        for target_branch in target_branches:
            output = create_branch_func(self.path, target_branch, new_branch)
            all_output.append(f'--- 对于目标分支: {target_branch} ---\n{output}')
        
        self.create_branch_output.setText('\n\n'.join(all_output))

    def run_refresh_remote_branches(self):
        self.available_branches_list.clear()
        # Don't clear the target list, as it's loaded from config
        # self.target_branch_list.clear()
        self.create_branch_output.setText('正在刷新远程分支...')
        QApplication.processEvents()

        branches, message = get_remote_branches(self.path)
        
        # Get current target branches to exclude them from the available list
        target_branches = {self.target_branch_list.item(i).text() for i in range(self.target_branch_list.count())}
        
        available_branches = [b for b in branches if b not in target_branches]
        self.available_branches_list.addItems(available_branches)
        
        self.create_branch_output.setText(message)

    def run_refresh_branches(self):
        self.source_branch_combo.clear()
        self.mr_output.setText('正在加载本地分支...') 
        QApplication.processEvents()

        valid_branches, message = get_local_branches(self.path)
        self.source_branch_combo.addItems(valid_branches)
        self.mr_output.setText(message)
        if valid_branches:
            self.update_mr_defaults()

    def update_mr_defaults(self):
        source_branch = self.source_branch_combo.currentText()
        if not source_branch:
            return

        gitlab_config = self.config.find('gitlab') if self.config is not None else None
        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default

        title_template = get_config_value(gitlab_config, 'title_template', 'Draft: {commit_message}')
        description_template = get_config_value(gitlab_config, 'description_template', '{commit_message}')

        defaults, error = get_mr_defaults(self.path, source_branch, title_template, description_template)
        if error:
            self.mr_output.setText(error)
        else:
            self.mr_title_input.setText(defaults['title'])
            self.mr_description_input.setPlainText(defaults['description'])

    def run_create_mr(self):
        self.mr_output.setText('处理中...')
        QApplication.processEvents()

        output = generate_mr(
            self.path,
            self.gitlab_url_input.text(),
            self.token_input.text(),
            self.assignee_input.text(),
            self.reviewer_input.text(),
            self.source_branch_combo.currentText(),
            self.mr_title_input.text(),
            self.mr_description_input.toPlainText()
        )
        self.mr_output.setText(output)

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.title = 'GitLab 快捷工具'
        self.left = 100
        self.top = 100
        self.width = 800
        self.height = 700
        self.config = self.load_config()
        self.initUI()

    def load_config(self):
        try:
            tree = ET.parse('config.xml')
            root = tree.getroot()
            return root
        except (FileNotFoundError, ET.ParseError):
            # Create a default config if not found
            root = ET.Element('config')
            ET.SubElement(root, 'gitlab')
            workspaces = ET.SubElement(root, 'workspaces')
            tree = ET.ElementTree(root)
            tree.write('config.xml', encoding='UTF-8', xml_declaration=True)
            return root

    def save_config(self):
        if self.config is not None:
            workspaces_node = self.config.find('workspaces')
            if workspaces_node is None:
                workspaces_node = ET.SubElement(self.config, 'workspaces')
            
            # Clear existing workspace nodes
            for ws in workspaces_node.findall('workspace'):
                workspaces_node.remove(ws)

            # Add current workspaces from tabs
            for i in range(self.workspace_tabs.count()):
                tab_widget = self.workspace_tabs.widget(i)
                if isinstance(tab_widget, WorkspaceTab):
                    ws_node = ET.SubElement(workspaces_node, 'workspace', {
                        'name': self.workspace_tabs.tabText(i),
                        'path': tab_widget.path
                    })
                    # Save target branches
                    for j in range(tab_widget.target_branch_list.count()):
                        branch_name = tab_widget.target_branch_list.item(j).text()
                        ET.SubElement(ws_node, 'target_branch').text = branch_name
            
            tree = ET.ElementTree(self.config)
            tree.write('config.xml', encoding='UTF-8', xml_declaration=True)

    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        main_layout = QVBoxLayout()

        # Workspace management buttons
        workspace_buttons_layout = QHBoxLayout()
        self.add_workspace_button = QPushButton('添加工作目录')
        workspace_buttons_layout.addWidget(self.add_workspace_button)
        main_layout.addLayout(workspace_buttons_layout)

        # Workspace Tabs
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.tabCloseRequested.connect(self.remove_workspace_tab)
        main_layout.addWidget(self.workspace_tabs)

        self.setLayout(main_layout)

        # Connect buttons
        self.add_workspace_button.clicked.connect(self.add_workspace)

        # Load workspaces from config
        self.load_workspaces()

    def load_workspaces(self):
        if self.config is not None:
            workspaces_node = self.config.find('workspaces')
            if workspaces_node is not None:
                removed_workspaces = []
                for ws in list(workspaces_node.findall('workspace')): # Create a copy for safe removal
                    name = ws.get('name')
                    path = ws.get('path')
                    if name and path and os.path.isdir(path):
                        self.add_workspace_tab(name, path, ws)
                    else:
                        removed_workspaces.append(name or path or '未命名工作区')
                        workspaces_node.remove(ws)
                
                if removed_workspaces:
                    self.save_config() # Persist the removal
                    QMessageBox.warning(self, '移除无效的工作区',
                                        '以下工作区的路径无效，已被自动移除：\n\n' + '\n'.join(removed_workspaces))

    def add_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if path:
            name, ok = QInputDialog.getText(self, '工作区名称', '为这个工作区输入一个名称:', text=path.split('/')[-1])
            if ok and name:
                self.add_workspace_tab(name, path, None)
                self.save_config() # Save after adding

    def add_workspace_tab(self, name, path, workspace_config):
        tab = WorkspaceTab(path, self.config, workspace_config)
        self.workspace_tabs.addTab(tab, name)
        self.workspace_tabs.setCurrentWidget(tab)

    def remove_workspace_tab(self, index):
        if index < 0:
            return

        tab_name = self.workspace_tabs.tabText(index)
        reply = QMessageBox.question(self, '确认移除',
                                     f"您确定要移除工作区 '{tab_name}'吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.workspace_tabs.removeTab(index)
            self.save_config() # Save after removing

    def closeEvent(self, event):
        self.save_config()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())