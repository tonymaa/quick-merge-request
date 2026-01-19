"""
创建 Merge Request 弹出框 - 独立的 MR 创建对话框
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QTextEdit, QPushButton, QLabel, QCheckBox, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
import xml.etree.ElementTree as ET

from app.widgets import NoWheelComboBox, enable_combo_search as util_enable_combo_search
from quick_generate_mr_form import (
    get_local_branches, get_all_local_branches, generate_mr,
    get_mr_defaults, parse_target_branch_from_source, get_gitlab_usernames
)
from quick_create_branch import get_remote_branches
from app.async_utils import run_blocking


class CreateMRDialog(QDialog):
    """创建 Merge Request 对话框"""

    def __init__(self, repo_path: str, workspace_name: str, config, source_branch: str = None, parent=None):
        super().__init__(parent)
        self.repo_path = repo_path
        self.workspace_name = workspace_name
        self.config = config
        self.source_branch = source_branch
        self.initUI()

    def initUI(self):
        self.setWindowTitle(f'创建 Merge Request - {self.workspace_name}')
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout()

        # 创建表单布局
        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 16, 16, 16)
        form_layout.setSpacing(12)

        # GitLab 配置
        gitlab_config = self.config.find('gitlab') if self.config is not None else None

        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default

        # GitLab URL 和 Token
        self.gitlab_url_input = QLineEdit(get_config_value(gitlab_config, 'gitlab_url'))
        self.token_input = QLineEdit(get_config_value(gitlab_config, 'private_token'))
        self.token_input.setEchoMode(QLineEdit.Password)

        form_layout.addRow('GitLab 地址:', self.gitlab_url_input)
        form_layout.addRow('私有 Token:', self.token_input)

        # 指派人和审查者
        assignee_default = get_config_value(gitlab_config, 'assignee')
        reviewer_default = get_config_value(gitlab_config, 'reviewer')

        self.assignee_combo = NoWheelComboBox()
        self.reviewer_combo = NoWheelComboBox()
        self.refresh_users_button = QPushButton('刷新用户')

        # 设置初始值
        if assignee_default:
            self.assignee_combo.addItem(assignee_default)
            self.assignee_combo.setCurrentIndex(0)
        if reviewer_default:
            self.reviewer_combo.addItem(reviewer_default)
            self.reviewer_combo.setCurrentIndex(0)

        assignee_layout = QHBoxLayout()
        assignee_layout.addWidget(self.assignee_combo)
        assignee_layout.addWidget(self.refresh_users_button)
        form_layout.addRow('指派给:', assignee_layout)
        form_layout.addRow('审查者:', self.reviewer_combo)

        # 源分支
        self.source_branch_combo = NoWheelComboBox()
        self.refresh_branches_button = QPushButton('刷新本地分支')
        self.show_all_branches_checkbox = QCheckBox('显示所有分支')

        source_branch_layout = QHBoxLayout()
        source_branch_layout.addWidget(self.source_branch_combo)
        source_branch_layout.addWidget(self.refresh_branches_button)
        source_branch_layout.addWidget(self.show_all_branches_checkbox)
        form_layout.addRow('源分支:', source_branch_layout)

        # 目标分支
        self.mr_target_branch_combo = NoWheelComboBox()
        self.refresh_mr_target_branches_button = QPushButton('刷新远程分支')

        target_branch_layout = QHBoxLayout()
        target_branch_layout.addWidget(self.mr_target_branch_combo)
        target_branch_layout.addWidget(self.refresh_mr_target_branches_button)
        form_layout.addRow('目标分支:', target_branch_layout)

        # 标题和描述
        self.mr_title_input = QLineEdit()
        self.mr_description_input = QTextEdit()
        self.mr_description_input.setMaximumHeight(150)

        form_layout.addRow('标题:', self.mr_title_input)
        form_layout.addRow('描述:', self.mr_description_input)

        layout.addLayout(form_layout)

        # 输出区域
        self.mr_output = QTextEdit()
        self.mr_output.setReadOnly(True)
        self.mr_output.setMaximumHeight(100)
        layout.addWidget(self.mr_output)

        # 按钮
        button_layout = QHBoxLayout()
        self.create_mr_button = QPushButton('创建 Merge Request')
        self.create_mr_button.setStyleSheet('''
            QPushButton {
                background-color: #2980b9;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3498db;
            }
        ''')
        self.close_button = QPushButton('关闭')
        button_layout.addStretch()
        button_layout.addWidget(self.create_mr_button)
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # 连接信号
        self.gitlab_url_input.textChanged.connect(self.save_gitlab_basic_config)
        self.token_input.textChanged.connect(self.save_gitlab_basic_config)
        self.refresh_branches_button.clicked.connect(self.run_refresh_branches)
        self.refresh_mr_target_branches_button.clicked.connect(self.run_refresh_mr_target_branches)
        self.source_branch_combo.currentIndexChanged.connect(self.update_mr_fields)
        self.refresh_users_button.clicked.connect(self.run_refresh_users)
        self.show_all_branches_checkbox.stateChanged.connect(self.run_refresh_branches)
        self.create_mr_button.clicked.connect(self.run_create_mr)
        self.close_button.clicked.connect(self.accept)

        # 启用组合框搜索
        self.enable_combo_search(self.source_branch_combo)
        self.enable_combo_search(self.mr_target_branch_combo)
        self.enable_combo_search(self.assignee_combo)
        self.enable_combo_search(self.reviewer_combo)

        # 刷新数据（这会自动调用 init_users_selection）
        self.run_refresh_branches()
        self.run_refresh_mr_target_branches()
        self.run_refresh_users()

        # 如果有指定源分支，延迟设置
        if self.source_branch:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1000, lambda: self.set_source_branch(self.source_branch))

    def enable_combo_search(self, combo):
        util_enable_combo_search(combo)

    def set_source_branch(self, branch: str):
        """设置源分支"""
        branch_index = self.source_branch_combo.findText(branch, Qt.MatchFixedString)
        if branch_index >= 0:
            self.source_branch_combo.setCurrentIndex(branch_index)
        else:
            self.mr_output.append(f'警告: 未找到分支 {branch}')

    def run_refresh_branches(self):
        self.source_branch_combo.clear()
        self.mr_output.setText('正在加载本地分支...')
        QApplication.processEvents()

        show_all = self.show_all_branches_checkbox.isChecked()

        def _fetch_branches(use_all=show_all):
            if use_all:
                return get_all_local_branches(self.repo_path)
            else:
                return get_local_branches(self.repo_path)

        def on_success(result, use_all=show_all):
            valid_branches, message = result
            self.source_branch_combo.clear()
            if use_all:
                self.source_branch_combo.addItems(valid_branches)
            else:
                # 简单排序，按历史记录
                self.source_branch_combo.addItems(valid_branches)
            self.mr_output.append(message)
            if valid_branches:
                self.update_mr_fields()

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def run_refresh_mr_target_branches(self):
        self.mr_target_branch_combo.clear()
        self.mr_output.append('正在刷新远程分支...')
        QApplication.processEvents()

        def _fetch_branches():
            return get_remote_branches(self.repo_path)

        def on_success(result):
            branches, message = result
            self.mr_target_branch_combo.addItems(branches)
            self.mr_output.append(message)
            self.update_mr_fields()

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def run_refresh_users(self):
        self.mr_output.append('正在刷新用户...')
        QApplication.processEvents()

        url = self.gitlab_url_input.text()
        token = self.token_input.text()

        def _fetch_users():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            if error:
                self.mr_output.append(error)
                return

            # 保存当前选择的值
            current_assignee = self.assignee_combo.currentText()
            current_reviewer = self.reviewer_combo.currentText()

            self.assignee_combo.clear()
            self.reviewer_combo.clear()
            self.assignee_combo.addItems(users)
            self.reviewer_combo.addItems(users)

            # 恢复之前选择的值（如果存在）
            if current_assignee:
                index = self.assignee_combo.findText(current_assignee, Qt.MatchFixedString)
                if index >= 0:
                    self.assignee_combo.setCurrentIndex(index)
            if current_reviewer:
                index = self.reviewer_combo.findText(current_reviewer, Qt.MatchFixedString)
                if index >= 0:
                    self.reviewer_combo.setCurrentIndex(index)

        run_blocking(_fetch_users, on_success=on_success, parent=self)





    def save_gitlab_basic_config(self):
        gitlab_config = self.config.find('gitlab')
        if gitlab_config is None:
            gitlab_config = ET.SubElement(self.config, 'gitlab')

        def set_child_text(parent, tag, text):
            child = parent.find(tag)
            if child is None:
                child = ET.SubElement(parent, tag)
            child.text = text

        set_child_text(gitlab_config, 'gitlab_url', self.gitlab_url_input.text())
        set_child_text(gitlab_config, 'private_token', self.token_input.text())
        tree = ET.ElementTree(self.config)
        tree.write('config.xml', encoding='UTF-8', xml_declaration=True)

    def update_mr_fields(self):
        source_branch = self.source_branch_combo.currentText()
        if not source_branch:
            return

        parsed_target = parse_target_branch_from_source(source_branch)
        if parsed_target:
            index = self.mr_target_branch_combo.findText(parsed_target, Qt.MatchFixedString)
            if index >= 0:
                self.mr_target_branch_combo.setCurrentIndex(index)

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

        defaults, error = get_mr_defaults(self.repo_path, source_branch, title_template, description_template)
        if error:
            self.mr_output.append(error)
        else:
            self.mr_title_input.setText(defaults['title'])
            self.mr_description_input.setPlainText(defaults['description'])

    def run_create_mr(self):
        reply = QMessageBox.question(
            self,
            '确认创建Merge Request吗？',
            f"源分支: {self.source_branch_combo.currentText()}\n"
            f"目标分支: {self.mr_target_branch_combo.currentText()}\n"
            f"标题: {self.mr_title_input.text()}\n"
            f"描述: \n{self.mr_description_input.toPlainText()}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.No:
            return

        self.mr_output.setText('处理中...')
        QApplication.processEvents()

        def _create_mr():
            return generate_mr(
                self.repo_path,
                self.gitlab_url_input.text(),
                self.token_input.text(),
                self.assignee_combo.currentText(),
                self.reviewer_combo.currentText(),
                self.source_branch_combo.currentText(),
                self.mr_title_input.text(),
                self.mr_description_input.toPlainText(),
                self.mr_target_branch_combo.currentText()
            )

        def on_success(output):
            self.mr_output.setText(output)
            if 'successfully' in output.lower() or '成功' in output:
                QMessageBox.information(self, '成功', 'Merge Request 创建成功！')

        run_blocking(_create_mr, on_success=on_success, parent=self)
