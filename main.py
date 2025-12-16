import sys
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit, 
    QPushButton, QFileDialog, QLabel, QTextEdit, QComboBox, QFormLayout, QInputDialog
)
from PyQt5.QtCore import Qt

# Import refactored functions
from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import get_local_branches, generate_mr, get_mr_defaults

class WorkspaceTab(QWidget):
    """A widget for a single workspace, containing its own git tools."""
    def __init__(self, path, config):
        super().__init__()
        self.path = path
        self.config = config
        self.initUI()

    def initUI(self):
        # This will contain the 'Create Branch' and 'Create MR' tabs
        self.tools_tabs = QTabWidget()
        self.create_branch_tab = QWidget()
        self.create_mr_tab = QWidget()

        self.tools_tabs.addTab(self.create_branch_tab, 'Create Branch')
        self.tools_tabs.addTab(self.create_mr_tab, 'Create Merge Request')

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

        self.target_branch_combo = QComboBox()
        self.refresh_remote_branches_button = QPushButton('Refresh Remote Branches')
        self.new_branch_input = QLineEdit('zhiming/xx1')
        self.create_branch_button = QPushButton('Create Branch')
        self.create_branch_output = QTextEdit()
        self.create_branch_output.setReadOnly(True)

        target_branch_layout = QHBoxLayout()
        target_branch_layout.addWidget(self.target_branch_combo)
        target_branch_layout.addWidget(self.refresh_remote_branches_button)

        layout.addRow('Target Branch:', target_branch_layout)
        layout.addRow('New Branch Name:', self.new_branch_input)
        layout.addRow(self.create_branch_button)
        layout.addRow(self.create_branch_output)

        self.create_branch_button.clicked.connect(self.run_create_branch)
        self.refresh_remote_branches_button.clicked.connect(self.run_refresh_remote_branches)

        self.create_branch_tab.setLayout(layout)

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
        self.refresh_branches_button = QPushButton('Refresh Branches')
        self.mr_title_input = QLineEdit()
        self.mr_description_input = QTextEdit()
        self.create_mr_button = QPushButton('Create MR')
        self.mr_output = QTextEdit()
        self.mr_output.setReadOnly(True)

        layout.addRow('GitLab URL:', self.gitlab_url_input)
        layout.addRow('Private Token:', self.token_input)
        layout.addRow('Assignee:', self.assignee_input)
        layout.addRow('Reviewer:', self.reviewer_input)

        source_branch_layout = QHBoxLayout()
        source_branch_layout.addWidget(self.source_branch_combo)
        source_branch_layout.addWidget(self.refresh_branches_button)
        layout.addRow('Source Branch:', source_branch_layout)

        layout.addRow('Title:', self.mr_title_input)
        layout.addRow('Description:', self.mr_description_input)

        layout.addRow(self.create_mr_button)
        layout.addRow(self.mr_output)

        self.refresh_branches_button.clicked.connect(self.run_refresh_branches)
        self.source_branch_combo.currentIndexChanged.connect(self.update_mr_defaults)
        self.create_mr_button.clicked.connect(self.run_create_mr)

        self.create_mr_tab.setLayout(layout)

    def run_create_branch(self):
        target_branch = self.target_branch_combo.currentText()
        new_branch = self.new_branch_input.text()

        self.create_branch_output.setText('Processing...')
        QApplication.processEvents()
        
        output = create_branch_func(self.path, target_branch, new_branch)
        self.create_branch_output.setText(output)

    def run_refresh_remote_branches(self):
        self.target_branch_combo.clear()
        self.create_branch_output.setText('Refreshing remote branches...')
        QApplication.processEvents()

        branches, message = get_remote_branches(self.path)
        self.target_branch_combo.addItems(branches)
        self.create_branch_output.setText(message)

    def run_refresh_branches(self):
        self.source_branch_combo.clear()
        self.mr_output.setText('Loading local branches...') 
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
        self.mr_output.setText('Processing...')
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
        self.title = 'GitLab Quick Tool'
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
                    ET.SubElement(workspaces_node, 'workspace', {
                        'name': self.workspace_tabs.tabText(i),
                        'path': tab_widget.path
                    })
            
            tree = ET.ElementTree(self.config)
            tree.write('config.xml', encoding='UTF-8', xml_declaration=True)

    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        main_layout = QVBoxLayout()

        # Workspace management buttons
        workspace_buttons_layout = QHBoxLayout()
        self.add_workspace_button = QPushButton('Add Workspace')
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
                for ws in workspaces_node.findall('workspace'):
                    name = ws.get('name')
                    path = ws.get('path')
                    if name and path:
                        self.add_workspace_tab(name, path)

    def add_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "Select Workspace Directory")
        if path:
            name, ok = QInputDialog.getText(self, 'Workspace Name', 'Enter a name for this workspace:', text=path.split('/')[-1])
            if ok and name:
                self.add_workspace_tab(name, path)
                self.save_config() # Save after adding

    def add_workspace_tab(self, name, path):
        tab = WorkspaceTab(path, self.config)
        self.workspace_tabs.addTab(tab, name)
        self.workspace_tabs.setCurrentWidget(tab)

    def remove_workspace_tab(self, index):
        if index >= 0:
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