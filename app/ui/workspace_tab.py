import shelve
import time
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import (
    QCheckBox,
    QWidget, QTabWidget, QFormLayout, QLineEdit, QHBoxLayout, QPushButton,
    QVBoxLayout, QListWidget, QAbstractItemView, QTextEdit, QComboBox, QMessageBox, QDialog,
    QFrame, QSizePolicy, QTableWidget, QTableWidgetItem, QDialogButtonBox, QHeaderView
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from app.async_utils import run_blocking
from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import (
    get_local_branches, get_all_local_branches, generate_mr, get_mr_defaults,
    parse_target_branch_from_source, get_gitlab_usernames, get_branch_diff,
    get_commits_between_branches
)
from app.widgets import NoWheelComboBox, enable_combo_search as util_enable_combo_search
from PyQt5.QtWidgets import QScrollArea, QLabel
from app.ui.commit_diff_dialog import CommitDiffDialog


class CollapsibleConsole(QWidget):
    """可折叠的控制台日志区"""

    def __init__(self, title='控制台日志', parent=None):
        super().__init__(parent)
        self.is_expanded = True
        self.title = title
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 标题栏（可点击）
        self.header = QPushButton(f'▼ {self.title}')
        self.header.setStyleSheet('''
            QPushButton {
                background: #34495e;
                color: white;
                border: none;
                border-radius: 4px 4px 0 0;
                padding: 8px 12px;
                text-align: left;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2c3e50;
            }
        ''')
        self.header.clicked.connect(self.toggle)
        self.main_layout.addWidget(self.header)

        # 内容区域
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet('''
            QTextEdit {
                background: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #34495e;
                border-top: none;
                border-radius: 0 0 4px 4px;
                font-family: Consolas, Monaco, monospace;
                font-size: 12px;
                padding: 8px;
            }
        ''')
        self.content_layout.addWidget(self.log_text)

        self.content_widget.setLayout(self.content_layout)
        self.main_layout.addWidget(self.content_widget)

        self.setLayout(self.main_layout)

    def toggle(self):
        """切换展开/折叠状态"""
        self.is_expanded = not self.is_expanded
        self.content_widget.setVisible(self.is_expanded)
        if self.is_expanded:
            self.header.setText(f'▼ {self.title}')
        else:
            self.header.setText(f'▶ {self.title}')

    def append(self, text):
        """追加日志文本"""
        self.log_text.setPlainText(self.log_text.toPlainText() + text + '\n')
        # 滚动到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.End)
        self.log_text.setTextCursor(cursor)

    def clear(self):
        """清空日志"""
        self.log_text.clear()

    def get_text(self):
        """获取日志文本"""
        return self.log_text.toPlainText()

    def set_text(self, text):
        """设置日志文本"""
        self.log_text.setPlainText(text)


class CherryPickConfirmDialog(QDialog):
    """Cherry-Pick 二阶段确认对话框，支持显示执行日志"""

    def __init__(self, source_branch, target_branch, commits, workspace_tab, parent=None):
        super().__init__(parent)
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.commits = commits
        self.workspace_tab = workspace_tab
        self.is_executing = False
        self.cherry_pick_current_index = 0
        self.cherry_pick_successful = 0
        self.cherry_pick_worktree_dir = None

        self.setWindowTitle('确认 Cherry-Pick 操作')
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(15)

        # 标题区域
        self.title_label = QLabel('<b>即将执行 Cherry-Pick 操作</b>')
        self.title_label.setStyleSheet('font-size: 16px; color: #2c3e50;')
        self.main_layout.addWidget(self.title_label)

        # 分支信息区域
        self.info_frame = QFrame()
        self.info_frame.setStyleSheet('''
            QFrame {
                background: #f8f9fa;
                border: 1px solid #e9ecef;
                border-radius: 6px;
                padding: 10px;
            }
        ''')
        info_layout = QVBoxLayout(self.info_frame)

        # 源分支 -> 目标分支（带箭头）
        branch_row = QHBoxLayout()
        source_label = QLabel(f'<b>源分支:</b> {self.source_branch}')
        source_label.setStyleSheet('color: #3498db;')
        arrow_label = QLabel('→')
        arrow_label.setStyleSheet('font-size: 18px; color: #27ae60; font-weight: bold;')
        target_label = QLabel(f'<b>目标分支:</b> {self.target_branch}')
        target_label.setStyleSheet('color: #27ae60;')

        branch_row.addWidget(source_label)
        branch_row.addStretch()
        branch_row.addWidget(arrow_label)
        branch_row.addStretch()
        branch_row.addWidget(target_label)
        info_layout.addLayout(branch_row)

        commit_count = QLabel(f'<b>提交数量:</b> {len(self.commits)} 个')
        info_layout.addWidget(commit_count)

        self.main_layout.addWidget(self.info_frame)

        # 提交列表表格
        table_label = QLabel('<b>选中的提交:</b>')
        self.main_layout.addWidget(table_label)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['Hash', '提交信息', '作者'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(2, 120)
        self.table.setRowCount(len(self.commits))
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        for row, commit in enumerate(self.commits):
            hash_item = QTableWidgetItem(commit['hash'][:8])
            hash_item.setForeground(Qt.blue)
            self.table.setItem(row, 0, hash_item)

            message_item = QTableWidgetItem(commit['message'][:60] + ('...' if len(commit['message']) > 60 else ''))
            self.table.setItem(row, 1, message_item)

            author_item = QTableWidgetItem(commit.get('author', 'Unknown'))
            self.table.setItem(row, 2, author_item)

        self.main_layout.addWidget(self.table)

        # 警告提示
        self.warning_label = QLabel('⚠️ 此操作将在目标分支上应用选中的提交。请确认操作无误。')
        self.warning_label.setStyleSheet('color: #f39c12; padding: 10px; background: #fff3cd; border-radius: 4px;')
        self.main_layout.addWidget(self.warning_label)

        # 控制台日志区域（初始隐藏）
        self.console = CollapsibleConsole('Cherry-Pick 执行日志')
        self.console.setMinimumHeight(200)
        self.console.setVisible(False)
        self.main_layout.addWidget(self.console)

        # 按钮区域
        self.button_box = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self.confirm_button = self.button_box.button(QDialogButtonBox.Yes)
        self.cancel_button = self.button_box.button(QDialogButtonBox.No)
        self.confirm_button.setText('确认执行')
        self.cancel_button.setText('取消')
        self.confirm_button.setStyleSheet('''
            QPushButton {
                background: #27ae60;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2ecc71;
            }
        ''')
        self.cancel_button.setStyleSheet('''
            QPushButton {
                background: #e74c3c;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background: #c0392b;
            }
        ''')
        self.button_box.accepted.connect(self.start_execution)
        self.button_box.rejected.connect(self.reject)
        self.main_layout.addWidget(self.button_box)

        self.setLayout(self.main_layout)

    def append_log(self, text):
        """追加日志文本"""
        self.console.append(text)
        QApplication.processEvents()

    def start_execution(self):
        """开始执行 cherry-pick"""
        import subprocess
        import tempfile
        import shutil

        # 切换到执行模式
        self.is_executing = True
        self.title_label.setText('<b>正在执行 Cherry-Pick 操作</b>')
        self.title_label.setStyleSheet('font-size: 16px; color: #27ae60;')
        self.warning_label.setVisible(False)
        self.table.setVisible(False)
        self.console.setVisible(True)

        # 禁用按钮
        self.confirm_button.setEnabled(False)
        self.cancel_button.setText('关闭')

        # 开始执行
        self.cherry_pick_current_index = 0
        self.cherry_pick_successful = 0

        # 创建临时 worktree 目录
        temp_dir = tempfile.mkdtemp(prefix='cherry-pick-')
        self.cherry_pick_worktree_dir = temp_dir

        self.append_log(f'--- 创建临时 worktree: {temp_dir} ---')

        # 检查目标分支是否存在于本地
        self.append_log(f'--- 检查目标分支: {self.target_branch} ---')
        local_branch_check = subprocess.run(
            ['git', 'branch', '--list', self.target_branch],
            cwd=self.workspace_tab.path,
            capture_output=True,
            text=True
        )

        # 检查目标分支是否存在于远程
        remote_branch_check = subprocess.run(
            ['git', 'branch', '-r', '--list', f'origin/{self.target_branch}'],
            cwd=self.workspace_tab.path,
            capture_output=True,
            text=True
        )

        # 确定使用哪个命令创建 worktree
        worktree_cmd = ['git', 'worktree', 'add', '-f', temp_dir]

        if local_branch_check.returncode == 0 and local_branch_check.stdout.strip():
            worktree_cmd.append(self.target_branch)
            self.append_log(f'使用本地分支: {self.target_branch}\n')
        elif remote_branch_check.returncode == 0 and remote_branch_check.stdout.strip():
            worktree_cmd.extend(['-b', self.target_branch, f'origin/{self.target_branch}'])
            self.append_log(f'从远程创建分支: origin/{self.target_branch}\n')
        else:
            worktree_cmd.append(self.target_branch)
            self.append_log(f'尝试使用分支: {self.target_branch}\n')

        worktree_result = subprocess.run(
            worktree_cmd,
            cwd=self.workspace_tab.path,
            capture_output=True,
            text=True
        )

        self.append_log(f'STDOUT:\n{worktree_result.stdout}')
        if worktree_result.stderr:
            self.append_log(f'STDERR:\n{worktree_result.stderr}')

        if worktree_result.returncode != 0:
            self.append_log(f'\n创建 worktree 失败！错误代码: {worktree_result.returncode}')
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.finish_execution(False)
            return

        # 验证 worktree 中的分支
        verify_result = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=temp_dir,
            capture_output=True,
            text=True
        )
        current_branch = verify_result.stdout.strip()
        self.append_log(f'Worktree 当前分支: {current_branch}\n')

        # 开始逐个执行 cherry-pick
        self.cherry_pick_next_commit()

    def cherry_pick_next_commit(self):
        """执行下一个 cherry-pick"""
        import subprocess
        import shutil

        if self.cherry_pick_current_index >= len(self.commits):
            # 全部完成，执行 push 和清理
            self.append_log(f'\n--- 完成！成功 cherry-pick 了 {self.cherry_pick_successful} 个提交 ---')

            # 执行 git push
            self.append_log(f'\n--- 正在推送到远程仓库 ---')
            push_result = subprocess.run(
                ['git', 'push', '-u', 'origin', self.target_branch],
                cwd=self.cherry_pick_worktree_dir,
                capture_output=True,
                text=True
            )
            self.append_log(f'STDOUT:\n{push_result.stdout}')
            if push_result.stderr:
                self.append_log(f'STDERR:\n{push_result.stderr}')

            if push_result.returncode == 0:
                self.append_log(f'\n--- 推送成功！---')
                self.finish_execution(True)
            else:
                self.append_log(f'\n--- 推送失败！错误代码: {push_result.returncode} ---')
                self.finish_execution(False)

            # 清理 worktree
            self.cleanup_worktree()
            return

        commit = self.commits[self.cherry_pick_current_index]
        commit_hash = commit['hash']
        commit_msg = commit['message'][:50]

        self.append_log(f'--- Cherry-pick ({self.cherry_pick_current_index + 1}/{len(self.commits)}): {commit_hash[:8]} - {commit_msg} ---')

        # 在 worktree 中执行 cherry-pick
        cherry_pick_result = subprocess.run(
            ['git', 'cherry-pick', commit_hash],
            cwd=self.cherry_pick_worktree_dir,
            capture_output=True,
            text=True
        )

        self.append_log(f'STDOUT:\n{cherry_pick_result.stdout}')
        if cherry_pick_result.stderr:
            self.append_log(f'STDERR:\n{cherry_pick_result.stderr}')

        if cherry_pick_result.returncode != 0:
            # 检查是否是空提交（内容已存在）
            is_empty = 'empty' in cherry_pick_result.stderr.lower() or 'empty' in cherry_pick_result.stdout.lower()

            if is_empty:
                # 空提交，跳过
                self.append_log('⚠️ 提交内容已存在，自动跳过（--skip）\n')
                subprocess.run(
                    ['git', 'cherry-pick', '--skip'],
                    cwd=self.cherry_pick_worktree_dir,
                    capture_output=True
                )
                # 继续下一个
                self.cherry_pick_current_index += 1
                self.cherry_pick_next_commit()
                return
            else:
                # 真正的冲突，需要手动处理
                self.append_log(f'Cherry-pick 失败！错误代码: {cherry_pick_result.returncode}')
                self.append_log('请手动解决冲突后继续。')
                self.cleanup_worktree()
                self.append_log('\n由于失败，worktree 已清理。')
                self.finish_execution(False)
                return
        else:
            self.cherry_pick_successful += 1
            self.append_log('Cherry-pick 成功！\n')

        # 继续下一个
        self.cherry_pick_current_index += 1
        self.cherry_pick_next_commit()

    def cleanup_worktree(self):
        """清理 worktree"""
        import subprocess
        import shutil

        if self.cherry_pick_worktree_dir:
            self.append_log(f'\n--- 清理临时 worktree ---')
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', self.cherry_pick_worktree_dir],
                cwd=self.workspace_tab.path,
                capture_output=True
            )
            shutil.rmtree(self.cherry_pick_worktree_dir, ignore_errors=True)
            # 清理可能残留的 worktree 记录
            subprocess.run(
                ['git', 'worktree', 'prune'],
                cwd=self.workspace_tab.path,
                capture_output=True
            )
            self.append_log('清理完成！')
            self.cherry_pick_worktree_dir = None

    def finish_execution(self, success):
        """完成执行"""
        self.is_executing = False
        if success:
            self.title_label.setText('<b>✅ Cherry-Pick 操作完成</b>')
            self.title_label.setStyleSheet('font-size: 16px; color: #27ae60;')
        else:
            self.title_label.setText('<b>❌ Cherry-Pick 操作失败</b>')
            self.title_label.setStyleSheet('font-size: 16px; color: #e74c3c;')
        self.confirm_button.setVisible(False)
        self.cancel_button.setText('关闭')
        self.cancel_button.setEnabled(True)

    def reject(self):
        """重写 reject 方法，执行中不允许关闭"""
        if self.is_executing:
            return
        super().reject()


class WorkspaceTab(QWidget):
    # 缓存 TTL：5分钟
    CACHE_TTL = 300

    def __init__(self, path, config, workspace_config, workspace_name=None):
        super().__init__()
        self.path = path
        self.config = config
        self.workspace_config = workspace_config
        self.workspace_name = workspace_name or ''
        self.initialized = False

        # 分支缓存：{branch_type: (data, timestamp)}
        self._branch_cache = {}
        # 预取状态
        self._is_prefetching = False
        self._last_fetch_time = 0

        self.initUI()

    def initUI(self):
        self.tools_tabs = QTabWidget()
        self.create_branch_tab = QWidget()
        self.create_mr_tab = QWidget()
        self.cherry_pick_tab = QWidget()

        self.tools_tabs.addTab(self.create_branch_tab, '创建分支')
        self.tools_tabs.addTab(self.cherry_pick_tab, '快速Cherry-pick')
        self.tools_tabs.addTab(self.create_mr_tab, '创建合并请求')

        self.init_create_branch_tab()
        self.init_create_mr_tab()
        self.init_cherry_pick_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tools_tabs)
        self.setLayout(layout)

    def init_create_branch_tab(self):
        layout = QFormLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        new_branch_prefix = self.get_default_new_branch_prefix()
        self.new_branch_combo = QComboBox()
        self.new_branch_combo.setEditable(True)
        self.new_branch_combo.setEditText(new_branch_prefix)
        self.clear_new_branch_history_button = QPushButton('清空历史')
        new_branch_row = QHBoxLayout()
        new_branch_row.addWidget(self.new_branch_combo)
        new_branch_row.addWidget(self.clear_new_branch_history_button)
        layout.addRow('新分支名:', new_branch_row)
        self.load_new_branch_history()

        self.branch_search_input = QLineEdit()
        self.branch_search_input.setPlaceholderText('搜索分支...')
        self.branch_search_input.textChanged.connect(self.filter_available_branches)

        shuttle_layout = QHBoxLayout()

        available_layout = QVBoxLayout()
        available_layout.addWidget(self.branch_search_input)
        self.available_branches_list = QListWidget()
        self.available_branches_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        available_layout.addWidget(self.available_branches_list)

        move_buttons_layout = QVBoxLayout()
        self.add_to_target_button = QPushButton('>>')
        self.remove_from_target_button = QPushButton('<<')
        move_buttons_layout.addStretch()
        move_buttons_layout.addWidget(self.add_to_target_button)
        move_buttons_layout.addWidget(self.remove_from_target_button)
        move_buttons_layout.addStretch()

        self.target_branch_list = QListWidget()
        self.target_branch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if self.workspace_config is not None:
            for branch_node in self.workspace_config.findall('target_branch'):
                self.target_branch_list.addItem(branch_node.text)

        shuttle_layout.addLayout(available_layout)
        shuttle_layout.addLayout(move_buttons_layout)
        shuttle_layout.addWidget(self.target_branch_list)

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
        self.clear_new_branch_history_button.clicked.connect(self.run_clear_new_branch_history)

        self.create_branch_tab.setLayout(layout)

    def filter_available_branches(self, text):
        for i in range(self.available_branches_list.count()):
            item = self.available_branches_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def move_to_target(self):
        selected_items = self.available_branches_list.selectedItems()
        for item in selected_items:
            if not item.isHidden():
                self.target_branch_list.addItem(item.text())
                self.available_branches_list.takeItem(self.available_branches_list.row(item))

    def remove_from_target(self):
        selected_items = self.target_branch_list.selectedItems()
        for item in selected_items:
            self.available_branches_list.addItem(item.text())
            self.target_branch_list.takeItem(self.target_branch_list.row(item))

    def init_create_mr_tab(self):
        layout = QFormLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        gitlab_config = self.config.find('gitlab') if self.config is not None else None
        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default

        self.gitlab_url_input = QLineEdit(get_config_value(gitlab_config, 'gitlab_url'))
        self.token_input = QLineEdit(get_config_value(gitlab_config, 'private_token'))
        self.token_input.setEchoMode(QLineEdit.Password)
        self.assignee_combo = NoWheelComboBox()
        self.reviewer_combo = NoWheelComboBox()
        self.refresh_users_button = QPushButton('刷新用户')

        self.source_branch_combo = NoWheelComboBox()
        self.refresh_branches_button = QPushButton('刷新本地分支')
        
        self.mr_target_branch_combo = NoWheelComboBox()
        self.refresh_mr_target_branches_button = QPushButton('刷新远程分支')

        self.mr_title_input = QLineEdit()
        self.mr_description_input = QTextEdit()

        # 创建按钮布局
        mr_button_layout = QHBoxLayout()
        self.view_commits_button = QPushButton('查看提交差异')
        self.create_mr_button = QPushButton('创建合并请求')
        mr_button_layout.addWidget(self.view_commits_button)
        mr_button_layout.addWidget(self.create_mr_button)

        self.mr_output = QTextEdit()
        self.mr_output.setReadOnly(True)

        layout.addRow('GitLab 地址:', self.gitlab_url_input)
        layout.addRow('私有 Token:', self.token_input)
        assignee_layout = QHBoxLayout()
        assignee_layout.addWidget(self.assignee_combo)
        assignee_layout.addWidget(self.refresh_users_button)
        layout.addRow('指派给:', assignee_layout)
        layout.addRow('审查者:', self.reviewer_combo)

        source_branch_layout = QHBoxLayout()
        source_branch_layout.addWidget(self.source_branch_combo)
        self.show_all_branches_checkbox = QCheckBox('显示所有分支')
        source_branch_layout.addWidget(self.refresh_branches_button)
        source_branch_layout.addWidget(self.show_all_branches_checkbox)
        layout.addRow('源分支:', source_branch_layout)

        target_branch_layout = QHBoxLayout()
        target_branch_layout.addWidget(self.mr_target_branch_combo)
        target_branch_layout.addWidget(self.refresh_mr_target_branches_button)
        layout.addRow('目标分支:', target_branch_layout)

        layout.addRow('标题:', self.mr_title_input)
        layout.addRow('描述:', self.mr_description_input)

        layout.addRow(mr_button_layout)
        layout.addRow(self.mr_output)

        self.gitlab_url_input.textChanged.connect(self.save_gitlab_basic_config)
        self.token_input.textChanged.connect(self.save_gitlab_basic_config)
        self.refresh_branches_button.clicked.connect(self.run_refresh_branches)
        self.refresh_mr_target_branches_button.clicked.connect(self.run_refresh_mr_target_branches)
        self.source_branch_combo.currentIndexChanged.connect(self.update_mr_fields)
        self.view_commits_button.clicked.connect(self.run_view_commits_diff)
        self.create_mr_button.clicked.connect(self.run_create_mr)
        self.refresh_users_button.clicked.connect(self.run_refresh_users)
        self.assignee_combo.currentTextChanged.connect(self.save_gitlab_user_selection)
        self.reviewer_combo.currentTextChanged.connect(self.save_gitlab_user_selection)
        self.show_all_branches_checkbox.stateChanged.connect(self.run_refresh_branches)

        self.create_mr_tab.setLayout(layout)

        self.enable_combo_search(self.source_branch_combo)
        self.enable_combo_search(self.mr_target_branch_combo)
        self.enable_combo_search(self.assignee_combo)
        self.enable_combo_search(self.reviewer_combo)
        self.init_users_selection()
    
    def ensure_initialized(self):
        if not self.initialized:
            self.run_refresh_remote_branches()
            self.run_refresh_branches()
            self.run_refresh_mr_target_branches()
            self.run_refresh_users()
            self.initialized = True

    def get_default_new_branch_prefix(self, tab_name=None):
        node = self.config.find('new_branch_prefix') if self.config is not None else None
        text = ''
        if node is not None and node.text:
            text = node.text
        tn = tab_name if tab_name is not None else self.workspace_name
        try:
            return text.format(tab_name=tn or '')
        except Exception:
            return text

    def run_create_branch(self):
        if self.target_branch_list.count() == 0:
            self.create_branch_output.setText('请至少选择一个目标分支。')
            return

        target_branches = [self.target_branch_list.item(i).text() for i in range(self.target_branch_list.count())]
        new_branch = self.new_branch_combo.currentText()

        self.create_branch_output.setText('处理中...')
        QApplication.processEvents()

        def _create_all_branches():
            """执行所有分支创建的阻塞函数"""
            all_output = []
            any_success = False
            for target_branch in target_branches:
                output = create_branch_func(self.path, target_branch, new_branch)
                all_output.append(f'--- 对于目标分支: {target_branch} ---\n{output}')
                if 'Branch created successfully!' in output:
                    any_success = True
            return all_output, any_success

        def on_success(result):
            all_output, any_success = result
            self.create_branch_output.setText('\n\n'.join(all_output))
            if any_success and new_branch:
                self.save_new_branch_to_history(new_branch)
                prefix = self.get_default_new_branch_prefix()
                self.new_branch_combo.setEditText(prefix)

        run_blocking(_create_all_branches, on_success=on_success, parent=self)

    def run_refresh_remote_branches(self):
        self.available_branches_list.clear()
        self.create_branch_output.setText('正在刷新远程分支...')
        QApplication.processEvents()

        target_branches = {self.target_branch_list.item(i).text() for i in range(self.target_branch_list.count())}

        def _fetch_branches():
            return get_remote_branches(self.path)

        def on_success(result):
            branches, message = result
            available_branches = [b for b in branches if b not in target_branches]
            self.available_branches_list.addItems(available_branches)
            self.create_branch_output.setText(message)

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def reload_new_branch_history(self):
        new_branch_text = self.new_branch_combo.currentText()
        try:
            with shelve.open('cache.db') as db:
                history = db.get('new_branch_history', [])
            self.new_branch_combo.clear()
            for item in history:
                if self.new_branch_combo.findText(item, Qt.MatchFixedString) < 0:
                    self.new_branch_combo.addItem(item)
        except Exception:
            pass
        if new_branch_text == '':
            new_branch_text = self.get_default_new_branch_prefix()
        self.new_branch_combo.setEditText(new_branch_text)

    def load_new_branch_history(self):
        try:
            with shelve.open('cache.db') as db:
                history = db.get('new_branch_history', [])
            for item in history:
                if self.new_branch_combo.findText(item, Qt.MatchFixedString) < 0:
                    self.new_branch_combo.addItem(item)
        except Exception:
            pass
        prefix = self.get_default_new_branch_prefix()
        self.new_branch_combo.setEditText(prefix)

    def save_new_branch_to_history(self, name):
        try:
            with shelve.open('cache.db', writeback=True) as db:
                history = db.get('new_branch_history', [])
                if name in history:
                    history.remove(name)
                history.insert(0, name)
                if len(history) > 20:
                    history = history[:20]
                db['new_branch_history'] = history
            if self.new_branch_combo.findText(name, Qt.MatchFixedString) < 0:
                self.new_branch_combo.addItem(name)
            prefix = self.get_default_new_branch_prefix()
            self.new_branch_combo.setEditText(prefix)
        except Exception:
            pass

    def get_new_branch_history(self):
        try:
            with shelve.open('cache.db') as db:
                return db.get('new_branch_history', [])
        except Exception:
            return []

    def sort_source_branches_by_history(self, branches):
        hist = self.get_new_branch_history()
        if not hist:
            return branches
        index_map = {h: i for i, h in enumerate(hist)}
        preferred = []
        others = []
        for b in branches:
            rank = None
            # 优先完全匹配，其次前缀匹配
            if b in index_map:
                rank = index_map[b]
            else:
                # 找最长的匹配前缀
                best_match = None
                best_len = 0
                for h in hist:
                    if b.startswith(h) and len(h) > best_len:
                        best_match = h
                        best_len = len(h)
                if best_match is not None:
                    rank = index_map[best_match]
            if rank is not None:
                preferred.append((rank, b))
            else:
                others.append(b)
        preferred.sort(key=lambda x: x[0])
        ordered = [b for _, b in preferred] + others
        return ordered

    def run_clear_new_branch_history(self):
        reply = QMessageBox.question(self, '清空历史记录',
                                "确认清空新分支历史记录吗？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.No:
            return
        try:
            with shelve.open('cache.db', writeback=True) as db:
                db['new_branch_history'] = []
            self.new_branch_combo.clear()
            prefix = self.get_default_new_branch_prefix()
            self.new_branch_combo.setEditText(prefix)
        except Exception:
            pass

    def run_refresh_branches(self):
        self.source_branch_combo.clear()
        self.mr_output.setText('正在加载本地分支...')
        QApplication.processEvents()

        show_all = hasattr(self, 'show_all_branches_checkbox') and self.show_all_branches_checkbox.isChecked()

        def _fetch_branches(use_all=show_all):
            if use_all:
                return get_all_local_branches(self.path)
            else:
                return get_local_branches(self.path)

        def on_success(result, use_all=show_all):
            valid_branches, message = result
            self.source_branch_combo.clear()  # 再次清空，防止重复
            if use_all:
                self.source_branch_combo.addItems(valid_branches)
            else:
                ordered = self.sort_source_branches_by_history(valid_branches)
                self.source_branch_combo.addItems(ordered)
            self.mr_output.setText(message)
            if valid_branches:
                self.update_mr_fields()

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def run_refresh_mr_target_branches(self):
        self.mr_target_branch_combo.clear()
        self.mr_output.setText('正在刷新远程分支...')
        QApplication.processEvents()

        def _fetch_branches():
            return get_remote_branches(self.path)

        def on_success(result):
            branches, message = result
            self.mr_target_branch_combo.addItems(branches)
            self.mr_output.setText(message)
            self.update_mr_fields()

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def enable_combo_search(self, combo):
        util_enable_combo_search(combo)

    def run_refresh_users(self):
        self.mr_output.setText('正在刷新用户...')
        QApplication.processEvents()

        url = self.gitlab_url_input.text()
        token = self.token_input.text()

        def _fetch_users():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            if error:
                self.mr_output.setText(error)
                return
            self.assignee_combo.addItems(users)
            self.reviewer_combo.addItems(users)
            self.init_users_selection()

        run_blocking(_fetch_users, on_success=on_success, parent=self)

    def init_users_selection(self):
        gitlab_config = self.config.find('gitlab') if self.config is not None else None
        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default
        assignee_default = get_config_value(gitlab_config, 'assignee')
        reviewer_default = get_config_value(gitlab_config, 'reviewer')
        if assignee_default and self.assignee_combo.findText(assignee_default, Qt.MatchFixedString) >= 0:
            self.assignee_combo.setCurrentText(assignee_default)
        elif assignee_default and self.assignee_combo.count() == 0:
            self.assignee_combo.addItem(assignee_default)
            self.assignee_combo.setCurrentText(assignee_default)
        if reviewer_default and self.reviewer_combo.findText(reviewer_default, Qt.MatchFixedString) >= 0:
            self.reviewer_combo.setCurrentText(reviewer_default)
        elif reviewer_default and self.reviewer_combo.count() == 0:
            self.reviewer_combo.addItem(reviewer_default)
            self.reviewer_combo.setCurrentText(reviewer_default)

    def save_gitlab_user_selection(self):
        gitlab_config = self.config.find('gitlab')
        if gitlab_config is None:
            gitlab_config = ET.SubElement(self.config, 'gitlab')
        def set_child_text(parent, tag, text):
            child = parent.find(tag)
            if child is None:
                child = ET.SubElement(parent, tag)
            child.text = text
        set_child_text(gitlab_config, 'assignee', self.assignee_combo.currentText())
        set_child_text(gitlab_config, 'reviewer', self.reviewer_combo.currentText())
        tree = ET.ElementTree(self.config)
        tree.write('config.xml', encoding='UTF-8', xml_declaration=True)

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
            else:
                self.mr_output.setText(f'警告: 从源分支解析的目标分支 "{parsed_target}" 在远程分支列表中未找到。')

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
        reply = QMessageBox.question(self, '确认创建Merge Request吗？',
                                     f"源分支: {self.source_branch_combo.currentText()}\n"
                                     f"目标分支: {self.mr_target_branch_combo.currentText()}\n"
                                     f"标题: {self.mr_title_input.text()}\n"
                                     f"描述: \n{self.mr_description_input.toPlainText()}",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.No:
            return
        self.mr_output.setText('处理中...')
        QApplication.processEvents()

        def _create_mr():
            return generate_mr(
                self.path,
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

        run_blocking(_create_mr, on_success=on_success, parent=self)

    def run_view_commits_diff(self):
        """查看源分支相对于目标分支的提交差异"""
        source_branch = self.source_branch_combo.currentText()
        target_branch = self.mr_target_branch_combo.currentText()

        if not source_branch:
            self.mr_output.setText('请先选择源分支。')
            return
        if not target_branch:
            self.mr_output.setText('请先选择目标分支。')
            return

        self.mr_output.setText('正在获取提交差异...')
        QApplication.processEvents()

        def _fetch_commits():
            return get_commits_between_branches(self.path, source_branch, target_branch)

        def on_success(result):
            commits, error = result
            if error:
                self.mr_output.setText(error)
                dialog = CommitDiffDialog(source_branch, target_branch, [], self)
                dialog.show_error(error)
                return

            # 显示对话框
            dialog = CommitDiffDialog(source_branch, target_branch, commits, self)
            dialog.exec_()

            # 在输出区域显示摘要
            if commits:
                self.mr_output.setText(f'找到 {len(commits)} 个新提交。')
            else:
                self.mr_output.setText('源分支与目标分支之间没有新的提交。')

        run_blocking(_fetch_commits, on_success=on_success, parent=self)

    def init_cherry_pick_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form_layout = QFormLayout()

        # 源分支选择
        self.cherry_pick_source_combo = NoWheelComboBox()
        self.enable_combo_search(self.cherry_pick_source_combo)
        # 刷新按钮
        self.refresh_cherry_pick_source_button = QPushButton('刷新')
        self.refresh_cherry_pick_source_button.setFixedHeight(28)
        self.refresh_cherry_pick_source_button.setToolTip('刷新源分支列表')
        self.refresh_cherry_pick_source_button.setStyleSheet('''
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 4px;
                background: transparent;
                font-size: 12px;
                color: #666;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: #f0f0f0;
                color: #333;
            }
        ''')

        # 源分支 Loading 状态标签
        self.source_loading_label = QLabel('')
        self.source_loading_label.setStyleSheet('color: #888; font-size: 11px;')

        source_layout = QHBoxLayout()
        source_layout.addWidget(self.cherry_pick_source_combo, 1)
        source_layout.addWidget(self.refresh_cherry_pick_source_button)
        source_layout.addWidget(self.source_loading_label)
        form_layout.addRow('选择源分支:', source_layout)

        # 流向箭头指示器
        arrow_label = QLabel('↓')
        arrow_label.setAlignment(Qt.AlignCenter)
        arrow_label.setStyleSheet('font-size: 20px; color: #3498db; font-weight: bold;')
        form_layout.addRow('', arrow_label)

        # 目标分支选择
        self.cherry_pick_target_combo = NoWheelComboBox()
        self.enable_combo_search(self.cherry_pick_target_combo)
        self.refresh_cherry_pick_target_button = QPushButton('刷新')
        self.refresh_cherry_pick_target_button.setFixedHeight(28)
        self.refresh_cherry_pick_target_button.setToolTip('刷新目标分支列表')
        self.refresh_cherry_pick_target_button.setStyleSheet('''
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 4px;
                background: transparent;
                font-size: 12px;
                color: #666;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: #f0f0f0;
                color: #333;
            }
        ''')

        # 目标分支 Loading 状态标签
        self.target_loading_label = QLabel('')
        self.target_loading_label.setStyleSheet('color: #888; font-size: 11px;')

        target_layout = QHBoxLayout()
        target_layout.addWidget(self.cherry_pick_target_combo, 1)
        target_layout.addWidget(self.refresh_cherry_pick_target_button)
        target_layout.addWidget(self.target_loading_label)
        form_layout.addRow('选择目标分支:', target_layout)

        # 按钮区域 - 分级样式
        button_layout = QHBoxLayout()

        # 刷新差异按钮 - 次要按钮
        self.cherry_pick_refresh_button = QPushButton('刷新提交记录')
        self.cherry_pick_refresh_button.setStyleSheet('''
            QPushButton {
                background: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 8px 16px;
                color: #555;
            }
            QPushButton:hover {
                background: #e8e8e8;
            }
        ''')

        # 执行按钮 - 主按钮（高亮）
        self.cherry_pick_execute_button = QPushButton('执行 Cherry-Pick')
        self.cherry_pick_execute_button.setStyleSheet('''
            QPushButton {
                background: #27ae60;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                color: white;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #2ecc71;
            }
            QPushButton:disabled {
                background: #bdc3c7;
                color: #ecf0f1;
            }
        ''')

        button_layout.addWidget(self.cherry_pick_refresh_button)
        button_layout.addStretch()
        button_layout.addWidget(self.cherry_pick_execute_button)
        form_layout.addRow('', button_layout)

        layout.addLayout(form_layout)

        # 差异显示区域
        self.cherry_pick_diff_scroll_area = QVBoxLayout()

        self.scroll_widget = QWidget()
        self.scroll_widget.setLayout(self.cherry_pick_diff_scroll_area)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.scroll_widget)
        self.scroll_area.setWidgetResizable(True)

        layout.addWidget(self.scroll_area)

        # 连接信号
        self.refresh_cherry_pick_source_button.clicked.connect(self.run_refresh_cherry_pick_source_branches)
        self.refresh_cherry_pick_target_button.clicked.connect(self.run_refresh_cherry_pick_target_branches)
        self.cherry_pick_source_combo.currentTextChanged.connect(self.run_refresh_cherry_pick_target_branches)
        self.cherry_pick_source_combo.currentTextChanged.connect(self.run_cherry_pick_refresh)
        self.cherry_pick_target_combo.currentTextChanged.connect(self.run_cherry_pick_dry_run_on_target_change)
        self.cherry_pick_refresh_button.clicked.connect(self.run_cherry_pick_refresh)
        self.cherry_pick_execute_button.clicked.connect(self.run_cherry_pick_execute)

        self.cherry_pick_tab.setLayout(layout)

        # 初始化时触发异步预取
        self.start_background_prefetch()
        # 立即显示本地数据
        self.load_local_branches_immediately()

    def _get_cached_branches(self, cache_key):
        """获取缓存的分支数据，如果过期则返回 None"""
        if cache_key in self._branch_cache:
            data, timestamp = self._branch_cache[cache_key]
            if time.time() - timestamp < self.CACHE_TTL:
                return data
        return None

    def _set_cached_branches(self, cache_key, data):
        """设置分支缓存"""
        self._branch_cache[cache_key] = (data, time.time())

    def start_background_prefetch(self):
        """后台静默预取 - 检查是否需要 git fetch"""
        current_time = time.time()
        # 如果 5 分钟内已进行过 fetch，则跳过
        if current_time - self._last_fetch_time < self.CACHE_TTL:
            return

        if self._is_prefetching:
            return

        self._is_prefetching = True

        def _do_fetch():
            import subprocess
            try:
                # 静默执行 git fetch，不阻塞 UI
                subprocess.run(
                    ['git', 'fetch', '--quiet'],
                    cwd=self.path,
                    capture_output=True,
                    timeout=60
                )
                return True
            except Exception:
                return False

        def on_fetch_done(success):
            self._is_prefetching = False
            if success:
                self._last_fetch_time = time.time()
                # 清除缓存，强制下次刷新获取新数据
                self._branch_cache.clear()

        run_blocking(_do_fetch, on_success=on_fetch_done, parent=self)

    def load_local_branches_immediately(self):
        """立即加载本地分支数据（本地优先原则）"""
        show_all = hasattr(self, 'cherry_pick_show_all_checkbox') and self.cherry_pick_show_all_checkbox.isChecked()

        # 尝试从缓存获取
        cache_key = f"branches_{'all' if show_all else 'local'}"
        cached = self._get_cached_branches(cache_key)

        if cached:
            # 使用缓存数据
            valid_branches, message = cached
            self._populate_source_combo(valid_branches, show_all)
            self.source_loading_label.setText('已从缓存加载')
            # 填充源分支后，触发目标分支过滤
            self.run_refresh_cherry_pick_target_branches()
        else:
            # 显示 Loading 状态
            self.source_loading_label.setText('正在加载...')
            self.target_loading_label.setText('正在加载...')

            def _fetch_branches():
                if show_all:
                    return get_all_local_branches(self.path)
                else:
                    return get_local_branches(self.path)

            def on_success(result):
                valid_branches, message = result
                # 存入缓存
                self._set_cached_branches(cache_key, (valid_branches, message))
                self._populate_source_combo(valid_branches, show_all)
                self.source_loading_label.setText('')
                # 填充源分支后，触发目标分支过滤
                self.run_refresh_cherry_pick_target_branches()

            run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def _populate_source_combo(self, valid_branches, show_all):
        """填充源分支下拉框"""
        self.cherry_pick_source_combo.clear()
        if show_all:
            self.cherry_pick_source_combo.addItems(valid_branches)
        else:
            ordered = self.sort_source_branches_by_history(valid_branches)
            self.cherry_pick_source_combo.addItems(ordered)

    def _populate_target_combo(self, valid_branches, show_all):
        """填充目标分支下拉框"""
        self.cherry_pick_target_combo.clear()
        if show_all:
            self.cherry_pick_target_combo.addItems(valid_branches)
        else:
            ordered = self.sort_source_branches_by_history(valid_branches)
            self.cherry_pick_target_combo.addItems(ordered)

    def run_refresh_cherry_pick_source_branches(self):
        """刷新源分支列表（强制从远程获取）"""
        # 清除缓存，强制刷新
        show_all = hasattr(self, 'cherry_pick_show_all_checkbox') and self.cherry_pick_show_all_checkbox.isChecked()
        cache_key = f"branches_{'all' if show_all else 'local'}"
        if cache_key in self._branch_cache:
            del self._branch_cache[cache_key]

        self.cherry_pick_source_combo.clear()
        self.source_loading_label.setText('正在检查远程更新...')

        for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
            item = self.cherry_pick_diff_scroll_area.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)

        loading_label = QLabel('正在加载源分支...')
        self.cherry_pick_diff_scroll_area.addWidget(loading_label)
        QApplication.processEvents()

        def _fetch_branches(use_all=show_all):
            if use_all:
                return get_all_local_branches(self.path)
            else:
                return get_local_branches(self.path)

        def on_success(result, use_all=show_all):
            valid_branches, message = result
            # 更新缓存
            self._set_cached_branches(f"branches_{'all' if use_all else 'local'}", (valid_branches, message))

            self.cherry_pick_source_combo.clear()
            if use_all:
                self.cherry_pick_source_combo.addItems(valid_branches)
            else:
                ordered = self.sort_source_branches_by_history(valid_branches)
                self.cherry_pick_source_combo.addItems(ordered)

            for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
                item = self.cherry_pick_diff_scroll_area.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget:
                        widget.setParent(None)

            result_label = QLabel(message)
            self.cherry_pick_diff_scroll_area.addWidget(result_label)
            self.source_loading_label.setText('已更新')

            # 3秒后清除提示
            QTimer.singleShot(3000, lambda: self.source_loading_label.setText(''))

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def run_refresh_cherry_pick_target_branches(self):
        """刷新目标分支列表，根据源分支前缀过滤"""
        self.cherry_pick_target_combo.clear()
        self.target_loading_label.setText('正在加载...')

        # 获取源分支前缀用于过滤
        source_branch = self.cherry_pick_source_combo.currentText()
        filter_prefix = None
        if source_branch and '__from__' in source_branch:
            filter_prefix = source_branch.split('__from__')[0]

        show_all = hasattr(self, 'cherry_pick_show_all_checkbox') and self.cherry_pick_show_all_checkbox.isChecked()

        # 尝试使用缓存
        cache_key = f"branches_{'all' if show_all else 'local'}"
        cached = self._get_cached_branches(cache_key)

        if cached:
            valid_branches, _ = cached
            self._populate_target_combo_filtered(valid_branches, show_all, filter_prefix, exclude_branch=source_branch)
            self.target_loading_label.setText('')
            return

        def _fetch_branches(use_all=show_all):
            if use_all:
                return get_all_local_branches(self.path)
            else:
                return get_local_branches(self.path)

        def on_success(result, use_all=show_all, prefix=filter_prefix, exclude=source_branch):
            valid_branches, _ = result
            self._populate_target_combo_filtered(valid_branches, use_all, prefix, exclude_branch=exclude)
            self.target_loading_label.setText('')

        run_blocking(_fetch_branches, on_success=on_success, parent=self)

    def _populate_target_combo_filtered(self, branches, show_all, filter_prefix, exclude_branch=None):
        """填充目标分支下拉框，支持前缀过滤和排除指定分支"""
        self.cherry_pick_target_combo.clear()

        # 排除源分支
        if exclude_branch:
            branches = [b for b in branches if b != exclude_branch]

        if filter_prefix:
            # 过滤出前缀匹配的分支
            filtered = [b for b in branches if b.startswith(filter_prefix)]
            if filtered:
                if show_all:
                    self.cherry_pick_target_combo.addItems(filtered)
                else:
                    ordered = self.sort_source_branches_by_history(filtered)
                    self.cherry_pick_target_combo.addItems(ordered)
            else:
                # 无匹配时显示提示
                self.cherry_pick_target_combo.addItem(f'(无匹配 "{filter_prefix}" 的分支)')
        else:
            # 无过滤条件，显示全部
            if show_all:
                self.cherry_pick_target_combo.addItems(branches)
            else:
                ordered = self.sort_source_branches_by_history(branches)
                self.cherry_pick_target_combo.addItems(ordered)

    def _set_execute_button_conflict(self, has_conflict, message=''):
        """设置执行按钮的冲突状态

        Args:
            has_conflict: 是否存在冲突
            message: 冲突信息（可选）
        """
        if has_conflict:
            self.cherry_pick_execute_button.setEnabled(False)
            self.cherry_pick_execute_button.setStyleSheet('''
                QPushButton {
                    background: #e74c3c;
                    border: none;
                    border-radius: 4px;
                    padding: 10px 20px;
                    color: white;
                    font-weight: bold;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: #c0392b;
                }
            ''')
            self.cherry_pick_execute_button.setToolTip(f'⚠️ 存在冲突: {message}')
        else:
            self.cherry_pick_execute_button.setEnabled(True)
            self.cherry_pick_execute_button.setStyleSheet('''
                QPushButton {
                    background: #27ae60;
                    border: none;
                    border-radius: 4px;
                    padding: 10px 20px;
                    color: white;
                    font-weight: bold;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: #2ecc71;
                }
                QPushButton:disabled {
                    background: #bdc3c7;
                    color: #ecf0f1;
                }
            ''')
            self.cherry_pick_execute_button.setToolTip('')

    def _set_all_checkboxes(self, checked):
        """设置所有复选框的选中状态"""
        if hasattr(self, 'cherry_pick_commit_checkboxes'):
            for checkbox, _ in self.cherry_pick_commit_checkboxes:
                checkbox.setChecked(checked)

    def run_cherry_pick_dry_run_on_target_change(self):
        """目标分支切换时重新执行预检"""
        # 检查是否有提交记录
        if not hasattr(self, 'cherry_pick_commit_checkboxes') or not self.cherry_pick_commit_checkboxes:
            return

        # 清除表格中的旧冲突标记
        if hasattr(self, 'commit_table') and self.commit_table:
            for row in range(self.commit_table.rowCount()):
                hash_item = self.commit_table.item(row, 1)
                if hash_item:
                    commit_hash = hash_item.text().replace('⚠️ ', '').replace('∅ ', '')
                    for col in range(self.commit_table.columnCount()):
                        item = self.commit_table.item(row, col)
                        if item:
                            item.setBackground(QColor('transparent'))
                            if col == 1:
                                item.setText(commit_hash)
                                item.setToolTip('')

        # 更新预检状态标签
        if hasattr(self, 'dry_run_status_label') and self.dry_run_status_label:
            self.dry_run_status_label.setText('🔍 正在进行冲突预检...')
            self.dry_run_status_label.setStyleSheet('color: #3498db; font-size: 12px; padding: 5px;')

        # 获取所有提交
        all_commits = [commit for _, commit in self.cherry_pick_commit_checkboxes]
        self._perform_dry_run_check(all_commits)

    def _perform_dry_run_check(self, commits):
        """执行 cherry-pick 预检（Dry Run）

        使用 git cherry-pick --no-commit 检测是否存在冲突
        """
        import subprocess
        import tempfile
        import shutil

        target_branch = self.cherry_pick_target_combo.currentText()
        if not target_branch:
            if hasattr(self, 'dry_run_status_label') and self.dry_run_status_label:
                self.dry_run_status_label.setText('⚠️ 请选择目标分支后再进行预检')
                self.dry_run_status_label.setStyleSheet('color: #f39c12; font-size: 12px; padding: 5px;')
            # 预检失败不阻止执行
            return

        # 提取提交哈希列表
        commit_hashes = [c['hash'] for c in commits]

        def _do_dry_run():
            try:
                # 创建临时目录用于 worktree
                temp_dir = tempfile.mkdtemp(prefix='cherry_pick_dryrun_')

                # 创建 worktree
                worktree_result = subprocess.run(
                    ['git', 'worktree', 'add', temp_dir, target_branch],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if worktree_result.returncode != 0:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    return {'success': False, 'error': f'无法创建 worktree: {worktree_result.stderr}'}

                conflicts = []
                empty_commits = []
                try:
                    # 逐个检查提交是否有冲突
                    for commit_hash in commit_hashes:
                        # 使用 --no-commit 进行预检
                        result = subprocess.run(
                            ['git', 'cherry-pick', '--no-commit', commit_hash],
                            cwd=temp_dir,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )

                        if result.returncode != 0:
                            # 检查是否是空提交（内容已存在）
                            is_empty = 'empty' in result.stdout.lower() or 'empty' in result.stderr.lower()
                            # 检查是否是冲突
                            is_conflict = 'conflict' in result.stdout.lower() or 'conflict' in result.stderr.lower()

                            if is_conflict and not is_empty:
                                conflicts.append(commit_hash[:8])
                            elif is_empty:
                                empty_commits.append(commit_hash[:8])

                            # 中止当前的 cherry-pick
                            subprocess.run(
                                ['git', 'cherry-pick', '--abort'],
                                cwd=temp_dir,
                                capture_output=True,
                                timeout=10
                            )
                        else:
                            # 成功，检查是否有实际更改（可能是空提交）
                            status_result = subprocess.run(
                                ['git', 'status', '--porcelain'],
                                cwd=temp_dir,
                                capture_output=True,
                                text=True,
                                timeout=10
                            )
                            if not status_result.stdout.strip():
                                # 没有更改，说明提交内容已存在
                                empty_commits.append(commit_hash[:8])

                            # 重置更改
                            subprocess.run(
                                ['git', 'reset', '--hard', 'HEAD'],
                                cwd=temp_dir,
                                capture_output=True,
                                timeout=10
                            )

                    return {'success': True, 'conflicts': conflicts, 'empty_commits': empty_commits}

                finally:
                    # 清理 worktree（使用 --force 强制删除）
                    subprocess.run(
                        ['git', 'worktree', 'remove', '--force', temp_dir],
                        cwd=self.path,
                        capture_output=True,
                        timeout=10
                    )
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    # 清理可能残留的 worktree 记录
                    subprocess.run(
                        ['git', 'worktree', 'prune'],
                        cwd=self.path,
                        capture_output=True,
                        timeout=10
                    )

            except Exception as e:
                return {'success': False, 'error': str(e)}

        def on_dry_run_done(result):
            if not hasattr(self, 'dry_run_status_label') or not self.dry_run_status_label:
                return

            if not result['success']:
                self.dry_run_status_label.setText(f'⚠️ 预检失败: {result.get("error", "未知错误")}')
                self.dry_run_status_label.setStyleSheet('color: #f39c12; font-size: 12px; padding: 5px;')
                self._set_execute_button_conflict(False)  # 预检失败不阻止执行
                return

            conflicts = result.get('conflicts', [])
            empty_commits = result.get('empty_commits', [])

            # 在表格中标记冲突和空提交
            if hasattr(self, 'commit_table') and self.commit_table:
                conflict_set = set(conflicts)
                empty_set = set(empty_commits)
                for row in range(self.commit_table.rowCount()):
                    hash_item = self.commit_table.item(row, 1)
                    if hash_item:
                        # 清理可能存在的标记前缀
                        raw_hash = hash_item.text().replace('⚠️ ', '').replace('∅ ', '')
                        if raw_hash in conflict_set:
                            # 标记冲突行 - 红色背景
                            for col in range(self.commit_table.columnCount()):
                                item = self.commit_table.item(row, col)
                                if item:
                                    item.setBackground(QColor('#ffcccc'))
                                    if col == 1:  # Hash 列添加冲突标记
                                        item.setText(f'⚠️ {raw_hash}')
                                        item.setToolTip('此提交可能存在冲突')
                        elif raw_hash in empty_set:
                            # 标记空提交行 - 深灰色背景
                            for col in range(self.commit_table.columnCount()):
                                item = self.commit_table.item(row, col)
                                if item:
                                    item.setBackground(QColor('#d0d0d0'))
                                    if col == 1:  # Hash 列添加空提交标记
                                        item.setText(f'∅ {raw_hash}')
                                        item.setToolTip('此提交内容已存在，将自动跳过')
                        else:
                            # 清除之前的标记
                            for col in range(self.commit_table.columnCount()):
                                item = self.commit_table.item(row, col)
                                if item:
                                    item.setBackground(QColor('transparent'))
                                    if col == 1:
                                        item.setText(raw_hash)
                                        item.setToolTip('')

            # 构建状态消息
            status_parts = []
            if conflicts:
                conflict_msg = f'{len(conflicts)} 个冲突'
                status_parts.append(f'⚠️ {conflict_msg}')
            if empty_commits:
                empty_msg = f'{len(empty_commits)} 个已存在（将跳过）'
                status_parts.append(f'∅ {empty_msg}')

            if status_parts:
                self.dry_run_status_label.setText(' | '.join(status_parts))
                if conflicts:
                    self.dry_run_status_label.setStyleSheet('color: #e74c3c; font-size: 12px; padding: 5px;')
                else:
                    self.dry_run_status_label.setStyleSheet('color: #7f8c8d; font-size: 12px; padding: 5px;')
            else:
                self.dry_run_status_label.setText('✅ 预检通过，未检测到冲突')
                self.dry_run_status_label.setStyleSheet('color: #27ae60; font-size: 12px; padding: 5px;')

            self._set_execute_button_conflict(False)

        run_blocking(_do_dry_run, on_success=on_dry_run_done, parent=self)

    def run_cherry_pick_refresh(self):
        """刷新源分支的提交记录（比较 __from__ 后源分支的差异）"""
        source_branch = self.cherry_pick_source_combo.currentText()

        if not source_branch:
            self.clear_cherry_pick_area()
            error_label = QLabel('请先选择源分支。')
            error_label.setStyleSheet('color: #e74c3c;')
            self.cherry_pick_diff_scroll_area.addWidget(error_label)
            return

        self.clear_cherry_pick_area()
        loading_label = QLabel(f'正在获取 "{source_branch}" 的提交差异...')
        self.cherry_pick_diff_scroll_area.addWidget(loading_label)
        QApplication.processEvents()

        def _fetch_commits():
            import subprocess

            # 调用 get_branch_diff 获取差异提交
            commits, error = get_branch_diff(self.path, source_branch)

            if error:
                return [], [source_branch]

            # 补充每个提交的详细信息（author, email, date）
            all_commits = []
            for commit in commits:
                commit_hash = commit['hash']
                # 获取提交的详细信息
                result = subprocess.run(
                    ['git', 'log', '--pretty=%an|%ae|%ai', '-1', commit_hash],
                    cwd=self.path,
                    capture_output=True,
                    text=True
                )

                author_name = ''
                author_email = ''
                author_date = ''

                if result.returncode == 0 and result.stdout.strip():
                    parts = result.stdout.strip().split('|')
                    if len(parts) >= 3:
                        author_name = parts[0]
                        author_email = parts[1]
                        author_date = parts[2]

                all_commits.append({
                    'hash': commit_hash,
                    'author': author_name,
                    'email': author_email,
                    'date': author_date,
                    'message': commit['message'],
                    'source_branch': source_branch
                })

            return all_commits, [source_branch]

        def on_success(result):
            all_commits, matching_branches = result

            # 清除加载标签
            for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
                item = self.cherry_pick_diff_scroll_area.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget:
                        widget.setParent(None)

            if not matching_branches:
                error_label = QLabel(f'找不到分支 "{source_branch}"')
                error_label.setStyleSheet('color: #e74c3c;')
                self.cherry_pick_diff_scroll_area.addWidget(error_label)
                self._set_execute_button_conflict(True, '找不到分支')
                return

            if not all_commits:
                info_label = QLabel(f'没有找到差异提交。')
                info_label.setStyleSheet('color: #7f8c8d;')
                self.cherry_pick_diff_scroll_area.addWidget(info_label)
                self._set_execute_button_conflict(False)
                return

            # 显示提交列表，带复选框
            title_label = QLabel(f'<b>找到 {len(all_commits)} 个提交:</b>')
            title_label.setStyleSheet('color: #2c3e50; font-size: 14px; margin-bottom: 8px;')
            self.cherry_pick_diff_scroll_area.addWidget(title_label)

            # 预检状态标签
            self.dry_run_status_label = QLabel('🔍 正在进行冲突预检...')
            self.dry_run_status_label.setStyleSheet('color: #3498db; font-size: 12px; padding: 5px;')
            self.cherry_pick_diff_scroll_area.addWidget(self.dry_run_status_label)

            # 创建表格
            self.commit_table = QTableWidget()
            self.commit_table.setColumnCount(5)
            self.commit_table.setHorizontalHeaderLabels(['选择', 'Hash', '提交信息', '作者', '时间'])
            self.commit_table.setRowCount(len(all_commits))
            self.commit_table.setAlternatingRowColors(True)
            self.commit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.commit_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.commit_table.verticalHeader().setVisible(False)
            self.commit_table.setShowGrid(False)  # 隐藏网格线
            self.commit_table.setStyleSheet('''
                QTableWidget {
                    border: 1px solid #ddd;
                    border-radius: 4px;
                }
                QTableWidget::item {
                    padding: 8px;
                }
                QTableWidget::item:selected {
                    background: #e8f4fc;
                    color: #333;
                }
                QHeaderView::section {
                    background: #f5f5f5;
                    padding: 8px;
                    border: none;
                    border-bottom: 2px solid #3498db;
                    font-weight: bold;
                }
            ''')

            # 设置列宽
            header = self.commit_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Fixed)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
            header.setSectionResizeMode(4, QHeaderView.Fixed)
            self.commit_table.setColumnWidth(0, 50)
            self.commit_table.setColumnWidth(1, 80)
            self.commit_table.setColumnWidth(3, 100)
            self.commit_table.setColumnWidth(4, 140)

            # 存储复选框的引用
            self.cherry_pick_commit_checkboxes = []

            for row, commit in enumerate(all_commits):
                # 复选框
                checkbox = QCheckBox()
                checkbox.setChecked(False)
                checkbox_widget = QWidget()
                checkbox_layout = QHBoxLayout(checkbox_widget)
                checkbox_layout.addWidget(checkbox)
                checkbox_layout.setAlignment(Qt.AlignCenter)
                checkbox_layout.setContentsMargins(0, 0, 0, 0)
                self.commit_table.setCellWidget(row, 0, checkbox_widget)

                # Hash
                hash_item = QTableWidgetItem(commit['hash'][:8])
                hash_item.setForeground(Qt.blue)
                self.commit_table.setItem(row, 1, hash_item)

                # 提交信息
                message = commit['message']
                if len(message) > 60:
                    message = message[:60] + '...'
                message_item = QTableWidgetItem(message)
                message_item.setToolTip(commit['message'])
                self.commit_table.setItem(row, 2, message_item)

                # 作者
                author_item = QTableWidgetItem(commit.get('author', 'Unknown'))
                author_item.setToolTip(commit.get('email', ''))
                self.commit_table.setItem(row, 3, author_item)

                # 时间
                date_str = commit.get('date', '')[:19] if commit.get('date') else ''
                date_item = QTableWidgetItem(date_str)
                self.commit_table.setItem(row, 4, date_item)

                # 保存复选框和对应的 commit 信息
                self.cherry_pick_commit_checkboxes.append((checkbox, commit))

            # 设置行高
            for row in range(len(all_commits)):
                self.commit_table.setRowHeight(row, 35)

            self.cherry_pick_diff_scroll_area.addWidget(self.commit_table)

            # 添加全选/取消全选按钮 (使用 QWidget 容器以便正确清理)
            select_buttons_widget = QWidget()
            select_buttons_layout = QHBoxLayout(select_buttons_widget)
            select_buttons_layout.setContentsMargins(0, 5, 0, 5)

            select_all_btn = QPushButton('全选')
            select_all_btn.setFixedHeight(28)
            select_all_btn.setStyleSheet('''
                QPushButton {
                    background: #3498db;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background: #2980b9;
                }
            ''')
            select_all_btn.clicked.connect(lambda: self._set_all_checkboxes(True))

            deselect_all_btn = QPushButton('取消全选')
            deselect_all_btn.setFixedHeight(28)
            deselect_all_btn.setStyleSheet('''
                QPushButton {
                    background: #95a5a6;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background: #7f8c8d;
                }
            ''')
            deselect_all_btn.clicked.connect(lambda: self._set_all_checkboxes(False))

            select_buttons_layout.addWidget(select_all_btn)
            select_buttons_layout.addWidget(deselect_all_btn)
            select_buttons_layout.addStretch()
            self.cherry_pick_diff_scroll_area.addWidget(select_buttons_widget)

            # 执行预检
            self._perform_dry_run_check(all_commits)

        run_blocking(_fetch_commits, on_success=on_success, parent=self)

    def run_cherry_pick_execute(self):
        """执行 cherry-pick 操作"""
        source_branch = self.cherry_pick_source_combo.currentText()
        target_branch = self.cherry_pick_target_combo.currentText()

        if not source_branch or not target_branch:
            QMessageBox.warning(self, '提示', '请先选择源分支和目标分支。')
            return

        # 获取选中的提交
        if not hasattr(self, 'cherry_pick_commit_checkboxes') or not self.cherry_pick_commit_checkboxes:
            QMessageBox.warning(self, '提示', '请先点击"刷新提交记录"查看提交列表。')
            return

        selected_commits = []
        for checkbox, commit in self.cherry_pick_commit_checkboxes:
            if checkbox.isChecked():
                selected_commits.append(commit)

        if not selected_commits:
            QMessageBox.warning(self, '提示', '请至少选择一个提交进行 Cherry-Pick。')
            return

        # 二阶段确认对话框（包含执行逻辑）
        source_branch = self.cherry_pick_source_combo.currentText()
        confirm_dialog = CherryPickConfirmDialog(
            source_branch=source_branch,
            target_branch=target_branch,
            commits=selected_commits,
            workspace_tab=self,
            parent=self
        )
        confirm_dialog.exec_()

    def clear_cherry_pick_area(self):
        """清空 cherry-pick 显示区域"""
        for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
            item = self.cherry_pick_diff_scroll_area.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)
        # 清除提交复选框数据引用
        if hasattr(self, 'cherry_pick_commit_checkboxes'):
            self.cherry_pick_commit_checkboxes = []
        # 清除预检状态标签引用
        if hasattr(self, 'dry_run_status_label'):
            self.dry_run_status_label = None
