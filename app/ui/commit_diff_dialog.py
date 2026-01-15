"""
提交差异对话框 - 显示源分支相对于目标分支的新提交
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QLabel, QPushButton, QMessageBox
)
from PyQt5.QtCore import Qt


class CommitDiffDialog(QDialog):
    """显示两个分支间提交差异的对话框"""

    def __init__(self, source_branch, target_branch, commits, parent=None):
        super().__init__(parent)
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.commits = commits
        self.initUI()

    def initUI(self):
        self.setWindowTitle('提交差异')
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout()

        # 标题
        title_label = QLabel(
            f'<b>源分支:</b> {self.source_branch} &rarr; <b>目标分支:</b> {self.target_branch}'
        )
        title_label.setTextFormat(Qt.RichText)
        layout.addWidget(title_label)

        # 提交列表
        self.commits_text = QTextEdit()
        self.commits_text.setReadOnly(True)

        if self.commits:
            content = f'共有 {len(self.commits)} 个新提交:\n\n'
            for commit in self.commits:
                content += f"{commit['hash']} {commit['message']}\n"
            self.commits_text.setPlainText(content)
        else:
            self.commits_text.setPlainText('源分支与目标分支之间没有新的提交。')

        layout.addWidget(self.commits_text)

        # 关闭按钮
        close_button = QPushButton('关闭')
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)

    def show_error(self, error_message):
        """显示错误信息"""
        error_dialog = QMessageBox(self)
        error_dialog.setIcon(QMessageBox.Critical)
        error_dialog.setWindowTitle('错误')
        error_dialog.setText(error_message)
        error_dialog.exec_()
