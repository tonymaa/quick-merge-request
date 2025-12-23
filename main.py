import sys
import os
import xml.etree.ElementTree as ET
import shelve
from PyQt5.QtWidgets import (
    QCheckBox,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit, 
    QPushButton, QFileDialog, QLabel, QTextEdit, QComboBox, QFormLayout, QInputDialog,
    QMessageBox, QListWidget, QAbstractItemView, QCompleter
)
from PyQt5.QtCore import Qt

# Import refactored functions
from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import get_local_branches, get_all_local_branches, generate_mr, get_mr_defaults, parse_target_branch_from_source, get_gitlab_usernames, get_branch_diff

class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()   # 或直接 return

def _read_stylesheet():
    try:
        with open('styles.qss', 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''

def _apply_global_styles():
    ss = _read_stylesheet()
    if ss:
        QApplication.instance().setStyleSheet(ss)

class WorkspaceTab(QWidget):
    """A widget for a single workspace, containing its own git tools."""
    def __init__(self, path, config, workspace_config, workspace_name=None):
        super().__init__()
        self.path = path
        self.config = config
        self.workspace_config = workspace_config
        self.workspace_name = workspace_name or ''
        self.initUI()

    def initUI(self):
        # This will contain the 'Create Branch' and 'Create MR' tabs
        self.tools_tabs = QTabWidget()
        self.create_branch_tab = QWidget()
        self.create_mr_tab = QWidget()
        self.cherry_pick_tab = QWidget()

        self.tools_tabs.addTab(self.create_branch_tab, '创建分支')
        self.tools_tabs.addTab(self.create_mr_tab, '创建合并请求')
        self.tools_tabs.addTab(self.cherry_pick_tab, '快速Cherry-pick')

        # Initialize the content of each tool tab
        self.init_create_branch_tab()
        self.init_create_mr_tab()
        self.init_cherry_pick_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tools_tabs)
        self.setLayout(layout)
        
        # Initial data load
        self.run_refresh_remote_branches()
        self.run_refresh_branches()
        self.run_refresh_mr_target_branches()
        self.run_refresh_users()

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
        self.clear_new_branch_history_button.clicked.connect(self.run_clear_new_branch_history)

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
        self.create_mr_button = QPushButton('创建合并请求')
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

        layout.addRow(self.create_mr_button)
        layout.addRow(self.mr_output)

        self.gitlab_url_input.textChanged.connect(self.save_gitlab_basic_config)
        self.token_input.textChanged.connect(self.save_gitlab_basic_config)
        self.refresh_branches_button.clicked.connect(self.run_refresh_branches)
        self.refresh_mr_target_branches_button.clicked.connect(self.run_refresh_mr_target_branches)
        self.source_branch_combo.currentIndexChanged.connect(self.update_mr_fields)
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
        
        all_output = []
        any_success = False
        for target_branch in target_branches:
            output = create_branch_func(self.path, target_branch, new_branch)
            all_output.append(f'--- 对于目标分支: {target_branch} ---\n{output}')
            if 'Branch created successfully!' in output:
                any_success = True
        
        self.create_branch_output.setText('\\n\\n'.join(all_output))
        if any_success and new_branch:
            self.save_new_branch_to_history(new_branch)
            # 保持默认值为 new_branch_prefix
            prefix = self.get_default_new_branch_prefix()
            self.new_branch_combo.setEditText(prefix)

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
            # 强制保持默认的编辑文本
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
            for h in hist:
                if b.startswith(h):
                    rank = index_map[h]
                    break
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

        if hasattr(self, 'show_all_branches_checkbox') and self.show_all_branches_checkbox.isChecked():
            valid_branches, message = get_all_local_branches(self.path)
            self.source_branch_combo.addItems(valid_branches)
        else:
            valid_branches, message = get_local_branches(self.path)
            ordered = self.sort_source_branches_by_history(valid_branches)
            self.source_branch_combo.addItems(ordered)
        self.mr_output.setText(message)
        if valid_branches:
            self.update_mr_fields()

    def run_refresh_mr_target_branches(self):
        self.mr_target_branch_combo.clear()
        self.mr_output.setText('正在刷新远程分支...')
        QApplication.processEvents()

        branches, message = get_remote_branches(self.path)
        self.mr_target_branch_combo.addItems(branches)
        self.mr_output.setText(message)
        # After refreshing, try to update fields again
        self.update_mr_fields()

    def enable_combo_search(self, combo):
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        completer = QCompleter(combo.model())
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        if hasattr(completer, 'setFilterMode'):
            completer.setFilterMode(Qt.MatchContains)
        combo.setCompleter(completer)

    def run_refresh_users(self):
        # self.assignee_combo.clear()
        # self.reviewer_combo.clear()
        self.mr_output.setText('正在刷新用户...')
        QApplication.processEvents()
        gitlab_config = self.config.find('gitlab') if self.config is not None else None
        def get_config_value(element, tag, default=''):
            if element is not None:
                found = element.find(tag)
                if found is not None and found.text:
                    return found.text.strip()
            return default
        url = self.gitlab_url_input.text()
        token = self.token_input.text()
        users, error = get_gitlab_usernames(url, token)
        if error:
            self.mr_output.setText(error)
            return
        self.assignee_combo.addItems(users)
        self.reviewer_combo.addItems(users)
        self.init_users_selection()

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
                # If not found, maybe add it or show a warning
                self.mr_output.setText(f'警告: 从源分支解析的目标分支 "{parsed_target}" 在远程分支列表中未找到。')

        # Update title and description defaults
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

        output = generate_mr(
            self.path,
            self.gitlab_url_input.text(),
            self.token_input.text(),
            self.assignee_combo.currentText(),
            self.reviewer_combo.currentText(),
            self.source_branch_combo.currentText(),
            self.mr_title_input.text(),
            self.mr_description_input.toPlainText(),
        )
        self.mr_output.setText(output)

    def init_cherry_pick_tab(self):
        layout = QFormLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 分支选择下拉框
        self.cherry_pick_branch_combo = NoWheelComboBox()
        self.refresh_cherry_pick_branches_button = QPushButton('刷新分支')
        
        branch_layout = QHBoxLayout()
        branch_layout.addWidget(self.cherry_pick_branch_combo)
        branch_layout.addWidget(self.refresh_cherry_pick_branches_button)
        layout.addRow('选择源分支:', branch_layout)
        
        # 差异显示区域
        self.cherry_pick_diff_display = QTextEdit()
        self.cherry_pick_diff_display.setReadOnly(True)
        layout.addRow('分支差异:', self.cherry_pick_diff_display)
        
        # 操作按钮
        self.cherry_pick_button = QPushButton('执行Cherry-pick')
        layout.addRow(self.cherry_pick_button)
        
        # 连接信号
        self.refresh_cherry_pick_branches_button.clicked.connect(self.run_refresh_cherry_pick_branches)
        self.cherry_pick_button.clicked.connect(self.run_cherry_pick)
        
        self.cherry_pick_tab.setLayout(layout)
        
        # 初始加载分支
        self.run_refresh_cherry_pick_branches()

    def run_refresh_cherry_pick_branches(self):
        self.cherry_pick_branch_combo.clear()
        self.cherry_pick_diff_display.setText('正在加载分支...')
        QApplication.processEvents()
        
        # 获取所有本地分支
        branches, message = get_local_branches(self.path)
        if not branches:
            branches, message = get_all_local_branches(self.path)
        
        # 过滤并处理分支名，移除 '__from__source' 部分
        processed_branches = []
        for branch in branches:
            if '__from__' in branch:
                # 移除 '__from__source' 部分
                processed_branch = branch.split('__from__')[0]
                if processed_branch and processed_branch not in processed_branches:
                    processed_branches.append(processed_branch)
            elif branch not in processed_branches:
                processed_branches.append(branch)
        
        self.cherry_pick_branch_combo.addItems(processed_branches)
        self.cherry_pick_diff_display.setText(message)

    def run_cherry_pick(self):
        selected_branch = self.cherry_pick_branch_combo.currentText()
        if not selected_branch:
            self.cherry_pick_diff_display.setText('请先选择一个分支')
            return
        
        # 首先尝试找到原始的完整分支名
        original_branch = None
        # 获取所有本地分支
        branches, message = get_local_branches(self.path)
        if not branches:
            branches, message = get_all_local_branches(self.path)
        
        # 查找对应的完整分支名（带有__from__模式）
        for branch in branches:
            if '__from__' in branch:
                # 提取分支名中__from__前的部分进行比较
                branch_prefix = branch.split('__from__')[0]
                if branch_prefix == selected_branch:
                    original_branch = branch
                    break
        
        if not original_branch:
            self.cherry_pick_diff_display.setText(f'找不到以 "{selected_branch}" 开头的完整分支名')
            return
        
        # 获取分支差异
        commits, error = get_branch_diff(self.path, original_branch)
        if error:
            self.cherry_pick_diff_display.setText(f'获取分支差异失败: {error}')
            return
        
        # 显示差异
        if commits:
            result = f'分支 "{original_branch}" 中的提交 (与源分支的差异):\n\n'
            for commit in commits:
                result += f'{commit["hash"]} {commit["message"]}\n'
        else:
            result = f'分支 "{original_branch}" 与源分支之间没有差异或无法获取差异信息。'
        
        self.cherry_pick_diff_display.setText(result)

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
        self.workspace_tabs.currentChanged.connect(self.on_workspace_tab_changed)
        main_layout.addWidget(self.workspace_tabs)

        self.setLayout(main_layout)

        # Connect buttons
        self.add_workspace_button.clicked.connect(self.add_workspace)

        # Load workspaces from config
        self.load_workspaces()
        self.apply_styles()

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
        tab = WorkspaceTab(path, self.config, workspace_config, name)
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
    
    def on_workspace_tab_changed(self, index):
        w = self.workspace_tabs.widget(index)
        if isinstance(w, WorkspaceTab):
            w.reload_new_branch_history()
    
    def apply_styles(self):
        _apply_global_styles()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())
