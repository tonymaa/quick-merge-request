import sys
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit, QPushButton, QFileDialog, QLabel, QTextEdit, QComboBox, QFormLayout

# Import refactored functions
from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import get_local_branches, generate_mr, get_mr_defaults

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
            return None

    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        main_layout = QVBoxLayout()

        # Workspace Directory Selector
        dir_layout = QHBoxLayout()
        self.dir_label = QLabel('Workspace Directory:')
        self.dir_input = QLineEdit()
        self.dir_button = QPushButton('Browse...')
        self.dir_button.clicked.connect(self.browse_directory)
        dir_layout.addWidget(self.dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(self.dir_button)
        main_layout.addLayout(dir_layout)

        # Tabs
        self.tabs = QTabWidget()
        self.tab1 = QWidget()
        self.tab2 = QWidget()
        self.tabs.addTab(self.tab1, 'Create Branch')
        self.tabs.addTab(self.tab2, 'Create Merge Request')

        # Tab 1: Create Branch
        self.init_create_branch_tab()

        # Tab 2: Create Merge Request
        self.init_create_mr_tab()

        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

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

        self.tab1.setLayout(layout)

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

        self.tab2.setLayout(layout)

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Workspace Directory")
        if directory:
            self.dir_input.setText(directory)
            self.run_refresh_branches()
            self.run_refresh_remote_branches()

    def run_create_branch(self):
        directory = self.dir_input.text()
        if not directory:
            self.create_branch_output.setText('Please select a workspace directory first.')
            return

        target_branch = self.target_branch_combo.currentText()
        new_branch = self.new_branch_input.text()

        self.create_branch_output.setText('Processing...')
        QApplication.processEvents()
        
        output = create_branch_func(directory, target_branch, new_branch)
        self.create_branch_output.setText(output)

    def run_refresh_remote_branches(self):
        directory = self.dir_input.text()
        if not directory:
            self.create_branch_output.setText('Please select a workspace directory first.')
            return

        self.target_branch_combo.clear()
        self.create_branch_output.setText('Refreshing remote branches...')
        QApplication.processEvents()

        branches, message = get_remote_branches(directory)
        self.target_branch_combo.addItems(branches)
        self.create_branch_output.setText(message)

    def run_refresh_branches(self):
        directory = self.dir_input.text()
        if not directory:
            self.mr_output.setText('Please select a workspace directory first.')
            return
        
        self.source_branch_combo.clear()
        self.mr_output.setText('Loading local branches...') 
        QApplication.processEvents()

        valid_branches, message = get_local_branches(directory)
        self.source_branch_combo.addItems(valid_branches)
        self.mr_output.setText(message)
        if valid_branches:
            self.update_mr_defaults()

    def update_mr_defaults(self):
        directory = self.dir_input.text()
        source_branch = self.source_branch_combo.currentText()
        if not directory or not source_branch:
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

        defaults, error = get_mr_defaults(directory, source_branch, title_template, description_template)
        if error:
            self.mr_output.setText(error)
        else:
            self.mr_title_input.setText(defaults['title'])
            self.mr_description_input.setPlainText(defaults['description'])

    def run_create_mr(self):
        directory = self.dir_input.text()
        if not directory:
            self.mr_output.setText('Please select a workspace directory first.')
            return

        self.mr_output.setText('Processing...')
        QApplication.processEvents()

        output = generate_mr(
            directory,
            self.gitlab_url_input.text(),
            self.token_input.text(),
            self.assignee_input.text(),
            self.reviewer_input.text(),
            self.source_branch_combo.currentText(),
            self.mr_title_input.text(),
            self.mr_description_input.toPlainText()
        )
        self.mr_output.setText(output)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())