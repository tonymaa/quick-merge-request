import shelve
import xml.etree.ElementTree as ET
from PyQt5.QtWidgets import (
    QCheckBox,
    QWidget, QTabWidget, QFormLayout, QLineEdit, QHBoxLayout, QPushButton,
    QVBoxLayout, QListWidget, QAbstractItemView, QTextEdit, QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from quick_create_branch import create_branch as create_branch_func, get_remote_branches
from quick_generate_mr_form import (
    get_local_branches, get_all_local_branches, generate_mr, get_mr_defaults,
    parse_target_branch_from_source, get_gitlab_usernames, get_branch_diff
)
from app.widgets import NoWheelComboBox, enable_combo_search as util_enable_combo_search
from PyQt5.QtWidgets import QScrollArea, QLabel

class WorkspaceTab(QWidget):
    def __init__(self, path, config, workspace_config, workspace_name=None):
        super().__init__()
        self.path = path
        self.config = config
        self.workspace_config = workspace_config
        self.workspace_name = workspace_name or ''
        self.initialized = False
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
        
        all_output = []
        any_success = False
        for target_branch in target_branches:
            output = create_branch_func(self.path, target_branch, new_branch)
            all_output.append(f'--- 对于目标分支: {target_branch} ---\n{output}')
            if 'Branch created successfully!' in output:
                any_success = True
        
        self.create_branch_output.setText('\n\n'.join(all_output))
        if any_success and new_branch:
            self.save_new_branch_to_history(new_branch)
            prefix = self.get_default_new_branch_prefix()
            self.new_branch_combo.setEditText(prefix)

    def run_refresh_remote_branches(self):
        self.available_branches_list.clear()
        self.create_branch_output.setText('正在刷新远程分支...')
        QApplication.processEvents()

        branches, message = get_remote_branches(self.path)
        
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
        self.update_mr_fields()

    def enable_combo_search(self, combo):
        util_enable_combo_search(combo)

    def run_refresh_users(self):
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
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        form_layout = QFormLayout()
        self.cherry_pick_branch_combo = NoWheelComboBox()
        self.refresh_cherry_pick_branches_button = QPushButton('刷新分支')
        
        branch_layout = QHBoxLayout()
        branch_layout.addWidget(self.cherry_pick_branch_combo)
        branch_layout.addWidget(self.refresh_cherry_pick_branches_button)
        form_layout.addRow('选择源分支:', branch_layout)
        
        self.cherry_pick_button = QPushButton('刷新差异')
        form_layout.addRow(self.cherry_pick_button)
        
        layout.addLayout(form_layout)
        
        self.cherry_pick_diff_scroll_area = QVBoxLayout()
        
        self.scroll_widget = QWidget()
        self.scroll_widget.setLayout(self.cherry_pick_diff_scroll_area)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.scroll_widget)
        self.scroll_area.setWidgetResizable(True)
        
        layout.addWidget(self.scroll_area)
        
        self.refresh_cherry_pick_branches_button.clicked.connect(self.run_refresh_cherry_pick_branches)
        self.cherry_pick_button.clicked.connect(self.run_cherry_pick)
        
        self.cherry_pick_tab.setLayout(layout)
        
        self.run_refresh_cherry_pick_branches()

    def run_refresh_cherry_pick_branches(self):
        self.cherry_pick_branch_combo.clear()
        
        for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
            self.cherry_pick_diff_scroll_area.itemAt(i).widget().setParent(None)
        
        loading_label = QLabel('正在加载分支...')
        self.cherry_pick_diff_scroll_area.addWidget(loading_label)
        QApplication.processEvents()
        
        branches, message = get_local_branches(self.path)
        if not branches:
            branches, message = get_all_local_branches(self.path)
        
        processed_branches = []
        for branch in branches:
            if '__from__' in branch:
                processed_branch = branch.split('__from__')[0]
                if processed_branch and processed_branches.count(processed_branch) == 0:
                    processed_branches.append(processed_branch)
            elif branch not in processed_branches:
                processed_branches.append(branch)
        
        self.cherry_pick_branch_combo.addItems(processed_branches)
        
        for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
            self.cherry_pick_diff_scroll_area.itemAt(i).widget().setParent(None)
        
        result_label = QLabel(message)
        self.cherry_pick_diff_scroll_area.addWidget(result_label)

    def run_cherry_pick(self):
        selected_branch = self.cherry_pick_branch_combo.currentText()
        if not selected_branch:
            return
        
        for i in reversed(range(self.cherry_pick_diff_scroll_area.count())):
            self.cherry_pick_diff_scroll_area.itemAt(i).widget().setParent(None)
        
        branches, message = get_local_branches(self.path)
        if not branches:
            branches, message = get_all_local_branches(self.path)
        
        matching_branches = []
        for branch in branches:
            if '__from__' in branch:
                branch_prefix = branch.split('__from__')[0]
                if branch_prefix == selected_branch:
                    matching_branches.append(branch)
        
        if not matching_branches:
            error_label = QLabel(f'找不到以 "{selected_branch}" 开头的完整分支名')
            self.cherry_pick_diff_scroll_area.addWidget(error_label)
            return
        
        for branch in matching_branches:
            commits, error = get_branch_diff(self.path, branch)
            
            branch_title = QLabel(f'<b>分支 "{branch}" 的新提交:</b>')
            branch_title.setStyleSheet('color: #2c3e50; font-size: 14px; margin-top: 10px; margin-bottom: 5px;')
            self.cherry_pick_diff_scroll_area.addWidget(branch_title)
            
            if error:
                error_label = QLabel(f'获取分支差异失败: {error}')
                error_label.setStyleSheet('color: #e74c3c;')
                self.cherry_pick_diff_scroll_area.addWidget(error_label)
                continue
            
            if commits:
                diff_text = QTextEdit()
                diff_text.setReadOnly(True)
                diff_content = ''
                for commit in commits:
                    diff_content += f'{commit["hash"]} {commit["message"]}\n'
                diff_text.setPlainText(diff_content)
                diff_text.setMaximumHeight(100)
                self.cherry_pick_diff_scroll_area.addWidget(diff_text)
            else:
                no_diff_label = QLabel('此分支与源分支之间没有差异或无法获取差异信息。')
                no_diff_label.setStyleSheet('color: #7f8c8d;')
                self.cherry_pick_diff_scroll_area.addWidget(no_diff_label)
