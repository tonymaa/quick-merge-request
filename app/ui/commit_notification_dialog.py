"""
提交通知对话框 - 显示监听到的 Git 提交记录
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QLabel, QPushButton, QHBoxLayout,
    QMessageBox, QScrollArea, QWidget
)
from PyQt5.QtCore import Qt
from typing import List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.ui.main_window import App


class CommitNotificationDialog(QDialog):
    """显示 Git 提交通知的对话框"""

    def __init__(self, commits: List[Dict], parent=None):
        super().__init__(parent)
        self.commits = commits
        self.main_window: 'App' = parent
        self.initUI()

    def initUI(self):
        self.setWindowTitle('新提交通知')
        self.setMinimumSize(900, 600)

        layout = QVBoxLayout()

        # 标题和统计信息
        header_layout = QHBoxLayout()

        if self.commits:
            count = len(self.commits)
            title_label = QLabel(f'<b>监听到 {count} 条新提交</b>')
        else:
            title_label = QLabel('<b>暂无新提交记录</b>')

        title_label.setTextFormat(Qt.RichText)
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        # 清空按钮
        self.clear_button = QPushButton('清空记录')
        self.clear_button.clicked.connect(self.clear_records)
        header_layout.addWidget(self.clear_button)

        layout.addLayout(header_layout)

        # 提交列表区域 - 使用滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # 创建内容容器
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setAlignment(Qt.AlignTop)
        self.content_widget.setLayout(self.content_layout)
        scroll_area.setWidget(self.content_widget)

        # 填充提交信息
        self._populate_commits()

        layout.addWidget(scroll_area)

        # 关闭按钮
        close_button = QPushButton('关闭')
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)

    def _populate_commits(self):
        """填充提交信息到界面"""
        # 清空现有内容
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.commits:
            no_commit_label = QLabel('暂无新提交记录。请确保已开始监听工作目录。')
            no_commit_label.setAlignment(Qt.AlignCenter)
            no_commit_label.setStyleSheet('color: #7f8c8d; font-style: italic; padding: 50px;')
            self.content_layout.addWidget(no_commit_label)
            return

        # 添加每个提交的信息
        for i, commit in enumerate(self.commits):
            commit_widget = self._create_commit_widget(commit, i)
            self.content_layout.addWidget(commit_widget)

    def _create_commit_widget(self, commit: Dict, index: int) -> QWidget:
        """创建单个提交信息组件"""
        widget = QWidget()
        widget.setStyleSheet('''
            QWidget {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                background-color: #f9f9f9;
                padding: 12px;
                margin: 6px;
            }
            QWidget:hover {
                background-color: #f0f0f0;
                border-color: #d0d0d0;
            }
        ''')

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 提交哈希、仓库和分支
        header_layout = QHBoxLayout()

        hash_label = QLabel(f'<b>提交:</b> <code style="background: #e8e8e8; padding: 2px 6px; border-radius: 3px;">{commit.get("hash", "N/A")[:12]}</code>')
        hash_label.setTextFormat(Qt.RichText)
        header_layout.addWidget(hash_label)

        header_layout.addStretch()

        repo_label = QLabel(f'<b>仓库:</b> {commit.get("repo", "N/A")}')
        repo_label.setTextFormat(Qt.RichText)
        header_layout.addWidget(repo_label)

        if commit.get('branch'):
            branch_label = QLabel(f'<b style="color: #2980b9;">分支:</b> <span style="color: #2980b9;">{commit.get("branch")}</span>')
            branch_label.setTextFormat(Qt.RichText)
            header_layout.addWidget(branch_label)

        layout.addLayout(header_layout)

        # 提交信息
        message_label = QLabel(f'<b>信息:</b> {commit.get("message", "N/A")}')
        message_label.setTextFormat(Qt.RichText)
        message_label.setWordWrap(True)
        message_label.setStyleSheet('font-size: 13px;')
        layout.addWidget(message_label)

        # 作者和日期
        footer_layout = QHBoxLayout()

        author_label = QLabel(f'<b>作者:</b> {commit.get("author", "N/A")}')
        author_label.setTextFormat(Qt.RichText)
        footer_layout.addWidget(author_label)

        footer_layout.addStretch()

        date_label = QLabel(f'<b>日期:</b> {commit.get("date", "N/A")}')
        date_label.setTextFormat(Qt.RichText)
        footer_layout.addWidget(date_label)

        layout.addLayout(footer_layout)

        # 创建 MR 按钮
        mr_button = QPushButton('创建 Merge Request')
        mr_button.setStyleSheet('''
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
            QPushButton:pressed {
                background-color: #21618c;
            }
        ''')
        mr_button.setCursor(Qt.PointingHandCursor)

        # 绑定点击事件，传递 commit 信息
        mr_button.clicked.connect(lambda checked, c=commit: self._on_create_mr_clicked(c))
        layout.addWidget(mr_button)

        widget.setLayout(layout)
        return widget

    def _on_create_mr_clicked(self, commit: Dict):
        """处理创建 MR 按钮点击事件 - 打开创建 MR 对话框"""
        if not self.main_window:
            QMessageBox.warning(self, '错误', '无法访问主窗口，请重启应用。')
            return

        repo_path = commit.get('repo_path')
        branch = commit.get('branch')
        workspace_name = commit.get('repo', '')

        if not repo_path:
            QMessageBox.warning(self, '错误', '该提交缺少仓库路径信息。')
            return

        if not branch or branch == 'HEAD':
            QMessageBox.warning(self, '警告', '该提交不在任何分支上（detached HEAD），无法创建 MR。')
            return

        # 导入创建 MR 对话框
        from app.ui.create_mr_dialog import CreateMRDialog

        # 打开创建 MR 对话框
        dialog = CreateMRDialog(
            repo_path=repo_path,
            workspace_name=workspace_name,
            config=self.main_window.config,
            source_branch=branch,
            parent=self
        )
        dialog.exec_()

    def clear_records(self):
        """清空记录"""
        reply = QMessageBox.question(
            self,
            '确认清空',
            '确定要清空所有提交记录吗？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # 清空数据
            self.commits.clear()
            # 重新渲染界面
            self._populate_commits()
