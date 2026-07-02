"""「所有项目」固定 Tab — 跨所有 workspace 的批量操作。

批量操作 4 类：
  * 批量创建分支：聚合所有勾选项目的远程分支，按分支名分组并标注项目来源，勾选后批量创建
  * 批量创建合并请求：共用 GitLab 凭据和标题/描述模板，每项目独立选择源/目标分支
  * 批量分支管理：所有项目分支合并单表，带「项目」列
  * 批量合并请求列表：所有项目 MR 合并单表，带「项目」列

错误处理：单项目失败不打断其他项目，末尾汇总弹窗。
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QFormLayout
)

from app.async_utils import run_blocking
from app.widgets import NoWheelComboBox
from quick_create_branch import create_branch as create_branch_func
from quick_generate_mr_form import (
    generate_mr, get_merge_requests, merge_merge_request,
    get_gitlab_usernames, get_branch_details, get_remote_branch_details,
    get_branches_no_merged
)


def _enable_combo_search(combo):
    """内联的 enable_combo_search，避免依赖 app.widgets 的同名函数签名变化。"""
    from PyQt5.QtWidgets import QCompleter
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    completer = QCompleter(combo.model())
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    if hasattr(completer, 'setFilterMode'):
        completer.setFilterMode(Qt.MatchContains)
    combo.setCompleter(completer)


class AllProjectsTab(QWidget):
    """跨所有 workspace 的批量操作面板。"""

    MR_STATE_OPTIONS = [
        ('Open', 'opened'),
        ('已合并', 'merged'),
        ('已关闭', 'closed'),
        ('全部', 'all'),
    ]

    def __init__(self, main_window, config, config_file):
        super().__init__()
        self.main_window = main_window
        self.config = config
        self.config_file = config_file

        # 项目勾选状态：path -> bool。None 表示尚未初始化（默认全选）。
        self._checked_paths = None

        # MR 列表共享状态
        self._mr_list_users_loaded = False
        self._mr_list_refresh_seq = 0
        # MR 表格行 -> (workspace_tab, mr_dict) 映射，用于合并按钮回调
        self._mr_row_context = []

        # 分支管理共享状态
        self._branch_mgmt_all_data = []      # list[(project_name, branch_dict, type_str, workspace_tab)]
        self._branch_mgmt_checkboxes = []    # 并行 list[(checkbox, index_into_all_data)]
        self._branch_mgmt_mode = 'local'     # 'local' / 'remote'
        self._branch_mgmt_current = {}       # path -> current branch name
        self._branch_mgmt_protected = {}     # path -> set(protected branch names)

        # 批量创建 MR 的每项目行数据
        self._mr_form_rows = []              # list[(workspace_tab, source_combo, target_combo, status_label)]

        # 批量创建分支：聚合分支 → 拥有该分支的 WorkspaceTab 列表
        self._cb_branch_projects = {}
        self._cb_total_projects = 0

        self.initUI()

    # ─────────────────────────── UI 骨架 ───────────────────────────
    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._build_project_selector())

        body = QHBoxLayout()
        body.setSpacing(8)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(110)
        self.nav_list.setCurrentRow(0)
        self.nav_list.setMouseTracking(True)
        self.nav_list.setStyleSheet('''
            QListWidget {
                background: #f7f8fa; border: 1px solid #e0e3e6;
                border-radius: 6px; font-weight: 600; outline: none;
            }
            QListWidget::item { padding: 12px 14px; border-bottom: 1px solid #eef0f2; }
            QListWidget::item:selected { background: #1677ff; color: white; }
            QListWidget::item:hover { background: #e6f0ff; }
        ''')
        for label in ('创建分支', '创建 MR', '分支管理', 'MR 列表'):
            self.nav_list.addItem(QListWidgetItem(label))
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        self.content_stack = QStackedWidget()
        self.create_branch_tab = QWidget()
        self.create_mr_tab = QWidget()
        self.branch_mgmt_tab = QWidget()
        self.mr_list_tab = QWidget()
        self.content_stack.addWidget(self.create_branch_tab)
        self.content_stack.addWidget(self.create_mr_tab)
        self.content_stack.addWidget(self.branch_mgmt_tab)
        self.content_stack.addWidget(self.mr_list_tab)

        self.init_create_branch_tab()
        self.init_create_mr_tab()
        self.init_branch_mgmt_tab()
        self.init_mr_list_tab()

        body.addWidget(self.nav_list)
        body.addWidget(self.content_stack, stretch=1)
        layout.addLayout(body, stretch=1)
        self.setLayout(layout)

    def _on_nav_changed(self, row):
        if not (0 <= row < self.content_stack.count()):
            return
        self.content_stack.setCurrentIndex(row)
        # 进入「创建 MR」面板时，确保用户列表已加载
        if self.content_stack.widget(row) is self.create_mr_tab:
            self._ensure_mr_users_loaded()

    def _build_project_selector(self):
        group = QFrame()
        group.setObjectName('ProjectSelector')
        group.setStyleSheet(
            'QFrame#ProjectSelector { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; }'
        )
        box = QVBoxLayout(group)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)

        # 第一行：标题 + 计数 + chips + 展开/收起
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel('参与项目:')
        title.setStyleSheet('font-weight: 600; color: #333;')
        header.addWidget(title)

        self.projects_count_label = QLabel('0 / 0 已选')
        self.projects_count_label.setStyleSheet('color: #888; font-weight: 600;')
        header.addWidget(self.projects_count_label)

        header.addSpacing(6)

        self.project_chips_layout = QHBoxLayout()
        self.project_chips_layout.setSpacing(4)
        header.addLayout(self.project_chips_layout)

        header.addStretch()

        self.btn_toggle_projects = QPushButton('▼ 展开列表')
        self.btn_toggle_projects.setCheckable(True)
        self.btn_toggle_projects.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_projects.setStyleSheet(
            'QPushButton { background: transparent; border: none; color: #1677ff; padding: 0 4px; }'
            'QPushButton:hover { text-decoration: underline; }'
        )
        self.btn_toggle_projects.clicked.connect(self._toggle_project_list)
        header.addWidget(self.btn_toggle_projects)
        box.addLayout(header)

        # 第二行：操作按钮（仅展开时可见）
        ops_widget = QWidget()
        ops_row = QHBoxLayout(ops_widget)
        ops_row.setContentsMargins(0, 0, 0, 0)
        ops_row.setSpacing(6)
        self.btn_select_all = QPushButton('全选')
        self.btn_select_none = QPushButton('全不选')
        self.btn_refresh_projects = QPushButton('刷新列表')
        for b in (self.btn_select_all, self.btn_select_none, self.btn_refresh_projects):
            b.setFixedHeight(24)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                'QPushButton { background: white; border: 1px solid #d0d0d0; border-radius: 4px; '
                'padding: 0 12px; color: #444; }'
                'QPushButton:hover { background: #f0f7ff; border-color: #1677ff; color: #1677ff; }'
            )
            ops_row.addWidget(b)
        ops_row.addStretch()
        self.ops_widget = ops_widget
        self.ops_widget.setVisible(False)
        box.addWidget(self.ops_widget)

        self.project_list = QListWidget()
        self.project_list.setMaximumHeight(140)
        self.project_list.itemChanged.connect(self._on_project_item_changed)
        self.project_list.setVisible(False)
        box.addWidget(self.project_list)

        self.btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_select_none.clicked.connect(lambda: self._set_all_checked(False))
        self.btn_refresh_projects.clicked.connect(self.refresh_projects)

        return group

    def _toggle_project_list(self):
        expanded = self.btn_toggle_projects.isChecked()
        self.project_list.setVisible(expanded)
        self.ops_widget.setVisible(expanded)
        self.btn_toggle_projects.setText('▲ 收起列表' if expanded else '▼ 展开列表')

    def _refresh_project_chips(self):
        """根据勾选状态刷新 chips 与计数。最多显示 5 个，超出折叠为「+N 更多」。"""
        while self.project_chips_layout.count():
            child = self.project_chips_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        checked_items = []
        total = self.project_list.count()
        for i in range(total):
            item = self.project_list.item(i)
            if item.checkState() == Qt.Checked:
                checked_items.append(item)

        # 计数标签：未选任何项目时显红
        if total == 0:
            self.projects_count_label.setText('暂无工作区')
            self.projects_count_label.setStyleSheet('color: #e74c3c; font-weight: 600;')
        else:
            self.projects_count_label.setText(f'{len(checked_items)} / {total} 已选')
            if checked_items:
                self.projects_count_label.setStyleSheet('color: #1677ff; font-weight: 600;')
            else:
                self.projects_count_label.setStyleSheet('color: #e74c3c; font-weight: 600;')

        max_visible = 5
        for item in checked_items[:max_visible]:
            path = item.data(Qt.UserRole) or ''
            display = item.text().split('  —  ')[0].strip() if '  —  ' in item.text() else item.text()
            chip = QPushButton(f' {display}  ✕ ')
            chip.setFixedHeight(22)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f'点击移除: {path}')
            chip.setStyleSheet(
                'QPushButton { background: #e3f2fd; color: #1565c0; border: 1px solid #90caf9; '
                'border-radius: 11px; padding: 0 8px; font-size: 12px; }'
                'QPushButton:hover { background: #bbdefb; color: #0d47a1; }'
            )
            chip.clicked.connect(lambda _checked=False, p=path: self._remove_project_via_chip(p))
            self.project_chips_layout.addWidget(chip)

        more = len(checked_items) - max_visible
        if more > 0:
            more_chip = QPushButton(f' +{more} 更多 ')
            more_chip.setFixedHeight(22)
            more_chip.setCursor(Qt.PointingHandCursor)
            more_chip.setStyleSheet(
                'QPushButton { background: #fff3e0; color: #e65100; border: 1px solid #ffb74d; '
                'border-radius: 11px; padding: 0 8px; font-size: 12px; }'
                'QPushButton:hover { background: #ffe0b2; }'
            )
            more_chip.clicked.connect(self._show_more_projects)
            self.project_chips_layout.addWidget(more_chip)

    def _remove_project_via_chip(self, path):
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            if item.data(Qt.UserRole) == path:
                item.setCheckState(Qt.Unchecked)
                break

    def _show_more_projects(self):
        if not self.btn_toggle_projects.isChecked():
            self.btn_toggle_projects.setChecked(True)
            self._toggle_project_list()

    def _on_project_item_changed(self, item):
        path = item.data(Qt.UserRole)
        if path is None:
            return
        if self._checked_paths is None:
            self._checked_paths = set()
        if item.checkState() == Qt.Checked:
            self._checked_paths.add(path)
        else:
            self._checked_paths.discard(path)
        self._refresh_project_chips()

    def _set_all_checked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        self.project_list.blockSignals(True)
        for i in range(self.project_list.count()):
            self.project_list.item(i).setCheckState(state)
        self.project_list.blockSignals(False)
        self._checked_paths = set()
        if checked:
            for i in range(self.project_list.count()):
                self._checked_paths.add(self.project_list.item(i).data(Qt.UserRole))
        self._refresh_project_chips()

    def refresh_projects(self):
        """重建项目勾选列表。保留用户已有勾选状态（按 path 记忆），新增项目默认勾选。"""
        current = []
        for i in range(self.main_window.workspace_tabs.count()):
            w = self.main_window.workspace_tabs.widget(i)
            if hasattr(w, 'path') and hasattr(w, 'workspace_name') and hasattr(w, 'workspace_config'):
                current.append((w.path, w.workspace_name or '', w))

        if self._checked_paths is None:
            self._checked_paths = {p for p, _, _ in current}

        new_checked = set()
        for path, _name, _ws in current:
            if path in self._checked_paths:
                new_checked.add(path)
            else:
                # 新增的项目默认勾选
                new_checked.add(path)
        self._checked_paths = new_checked

        self.project_list.blockSignals(True)
        self.project_list.clear()
        for path, name, _ws in current:
            item = QListWidgetItem(f'{name}  —  {path}')
            item.setData(Qt.UserRole, path)
            item.setCheckState(Qt.Checked if path in self._checked_paths else Qt.Unchecked)
            self.project_list.addItem(item)
        self.project_list.blockSignals(False)

        self._refresh_project_chips()

        # 同步刷新「批量创建 MR」表格里的项目行
        self._rebuild_mr_form_rows()

    def _selected_workspace_tabs(self):
        """返回勾选的 WorkspaceTab 列表，顺序与 project_list 一致。"""
        selected_paths = []
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_paths.append(item.data(Qt.UserRole))
        result = []
        for path in selected_paths:
            for j in range(self.main_window.workspace_tabs.count()):
                w = self.main_window.workspace_tabs.widget(j)
                if hasattr(w, 'path') and w.path == path:
                    result.append(w)
                    break
        return result

    # ──────────────────────── Config helpers ────────────────────────
    def _gitlab_node(self):
        if self.config is None:
            return None
        node = self.config.find('gitlab')
        if node is None:
            node = ET.SubElement(self.config, 'gitlab')
        return node

    def _get_gitlab_value(self, tag, default=''):
        node = self._gitlab_node()
        if node is not None:
            found = node.find(tag)
            if found is not None and found.text:
                return found.text.strip()
        return default

    def _set_gitlab_value(self, tag, value):
        node = self._gitlab_node()
        found = node.find(tag)
        if found is None:
            found = ET.SubElement(node, tag)
        found.text = value
        self._save_config()

    def _save_config(self):
        try:
            tree = ET.ElementTree(self.config)
            tree.write(self.config_file, encoding='UTF-8', xml_declaration=True)
        except Exception:
            pass

    def reload_config(self, new_config):
        """配置文件切换后由 main_window 调用。"""
        self.config = new_config
        if hasattr(self, 'gitlab_url_input'):
            self.gitlab_url_input.blockSignals(True)
            self.token_input.blockSignals(True)
            self.gitlab_url_input.setText(self._get_gitlab_value('gitlab_url'))
            self.token_input.setText(self._get_gitlab_value('private_token'))
            self.gitlab_url_input.blockSignals(False)
            self.token_input.blockSignals(False)
        # 重置用户下拉加载标记，强制下次刷新重新拉取
        self._mr_list_users_loaded = False

    def _get_default_new_branch_prefix(self):
        node = self.config.find('new_branch_prefix') if self.config is not None else None
        text = ''
        if node is not None and node.text:
            text = node.text
        try:
            return text.format(tab_name='all')
        except Exception:
            return text

    # ──────────────────────── 批量创建分支 ──────────────────────────
    def init_create_branch_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel(
            '说明：聚合所有勾选项目的远程分支，按分支名分组。'
            '<b>所有项目共有</b>的分支只显示分支名（绿色加粗，排最前）；'
            '<b>部分项目独有</b>的分支会标注项目来源（例如 <i>sz_dev（项目A，项目B）</i>）。'
            '把要作为来源的分支从左侧穿梭到右侧，点击「批量创建分支」后，'
            '会按新分支名为每个拥有该分支的项目创建分支（格式：<i>新分支名__from__目标分支</i>）。'
        )
        tip.setWordWrap(True)
        tip.setStyleSheet('color: #555;')
        layout.addWidget(tip)

        form = QFormLayout()
        self.cb_new_branch_combo = QComboBox()
        self.cb_new_branch_combo.setEditable(True)
        self.cb_new_branch_combo.setEditText(self._get_default_new_branch_prefix())
        self._load_new_branch_history()
        form.addRow('新分支名:', self.cb_new_branch_combo)
        layout.addLayout(form)

        top_bar = QHBoxLayout()
        self.cb_refresh_branches_btn = QPushButton('刷新所有项目分支')
        self.cb_refresh_branches_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #e8e8e8; }'
        )
        self.cb_branches_status = QLabel('尚未加载分支。')
        self.cb_branches_status.setStyleSheet('color: #888;')
        top_bar.addWidget(self.cb_refresh_branches_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.cb_branches_status)
        layout.addLayout(top_bar)

        # 穿梭框：左=可选，中=移动按钮，右=已选
        shuttle = QHBoxLayout()

        left_box = QVBoxLayout()
        left_box.addWidget(QLabel('<b>可选分支</b>（绿色加粗=所有项目共有）'))
        self.cb_search_input = QLineEdit()
        self.cb_search_input.setPlaceholderText('搜索分支...')
        self.cb_search_input.textChanged.connect(self._filter_cb_available)
        left_box.addWidget(self.cb_search_input)
        self.cb_available_list = QListWidget()
        self.cb_available_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.cb_available_list.itemDoubleClicked.connect(lambda _i: self._cb_move_to_target())
        left_box.addWidget(self.cb_available_list)

        mid_box = QVBoxLayout()
        mid_box.addStretch()
        self.cb_add_button = QPushButton('>>')
        self.cb_add_button.setToolTip('将左侧选中的分支加入右侧')
        self.cb_remove_button = QPushButton('<<')
        self.cb_remove_button.setToolTip('将右侧选中的分支移回左侧')
        self.cb_add_button.clicked.connect(self._cb_move_to_target)
        self.cb_remove_button.clicked.connect(self._cb_remove_from_target)
        mid_box.addWidget(self.cb_add_button)
        mid_box.addWidget(self.cb_remove_button)
        mid_box.addStretch()

        right_box = QVBoxLayout()
        right_box.addWidget(QLabel('<b>已选目标分支</b>'))
        self.cb_selected_list = QListWidget()
        self.cb_selected_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.cb_selected_list.itemDoubleClicked.connect(lambda _i: self._cb_remove_from_target())
        right_box.addWidget(self.cb_selected_list)

        shuttle.addLayout(left_box, stretch=2)
        shuttle.addLayout(mid_box)
        shuttle.addLayout(right_box, stretch=1)
        layout.addLayout(shuttle, stretch=1)

        self.cb_create_button = QPushButton('批量创建分支')
        self.cb_create_button.setMinimumHeight(38)
        self.cb_create_button.setStyleSheet(
            'QPushButton { background: #27ae60; color: white; border: none; '
            'border-radius: 4px; font-weight: bold; font-size: 13px; }'
            'QPushButton:hover { background: #2ecc71; }'
            'QPushButton:disabled { background: #bdc3c7; }'
        )
        layout.addWidget(self.cb_create_button)

        self.cb_output = QTextEdit()
        self.cb_output.setReadOnly(True)
        layout.addWidget(self.cb_output, stretch=2)

        self.cb_refresh_branches_btn.clicked.connect(self.run_cb_refresh_branches)
        self.cb_create_button.clicked.connect(self.run_batch_create_branch)
        self.create_branch_tab.setLayout(layout)

    def _load_new_branch_history(self):
        try:
            import shelve
            with shelve.open('cache.db') as db:
                history = db.get('new_branch_history', [])
            for item in history:
                if self.cb_new_branch_combo.findText(item, Qt.MatchFixedString) < 0:
                    self.cb_new_branch_combo.addItem(item)
        except Exception:
            pass

    def _save_new_branch_to_history(self, name):
        if not name:
            return
        try:
            import shelve
            with shelve.open('cache.db', writeback=True) as db:
                history = db.get('new_branch_history', [])
                if name in history:
                    history.remove(name)
                history.insert(0, name)
                if len(history) > 20:
                    history = history[:20]
                db['new_branch_history'] = history
            if self.cb_new_branch_combo.findText(name, Qt.MatchFixedString) < 0:
                self.cb_new_branch_combo.addItem(name)
        except Exception:
            pass

    def _read_target_branches_for(self, ws):
        """读取 workspace 在 config.xml 里配置的 target_branch 列表。"""
        if not getattr(ws, 'workspace_config', None):
            return []
        return [node.text for node in ws.workspace_config.findall('target_branch') if node.text]

    def run_cb_refresh_branches(self):
        selected = self._selected_workspace_tabs()
        if not selected:
            QMessageBox.information(self, '提示', '请至少勾选一个项目。')
            return
        self.cb_refresh_branches_btn.setEnabled(False)
        self.cb_branches_status.setText('正在拉取分支...')

        total = len(selected)

        def _run():
            aggregate = {}  # branch_name -> list[WorkspaceTab]
            failed = []
            for ws in selected:
                try:
                    branches, msg = _safe_get_remote_branches(ws.path)
                    if not branches and msg and 'Error' in msg:
                        failed.append({'project': ws.workspace_name, 'error': msg})
                        continue
                    seen = set()
                    for name in branches:
                        if name in seen:
                            continue
                        seen.add(name)
                        aggregate.setdefault(name, []).append(ws)
                except Exception as e:
                    failed.append({'project': ws.workspace_name, 'error': str(e)})
            return aggregate, failed

        def on_success(result):
            aggregate, failed = result
            self._cb_branch_projects = aggregate
            self._cb_total_projects = total
            self._populate_cb_available_list(aggregate, total)
            self.cb_selected_list.clear()
            self.cb_refresh_branches_btn.setEnabled(True)
            common = sum(1 for ws_list in aggregate.values() if len(ws_list) == total)
            unique = len(aggregate) - common
            self.cb_branches_status.setText(
                f'共 {len(aggregate)} 个唯一分支：{common} 个所有项目共有，{unique} 个部分项目独有。'
            )
            self._report_failures(failed, '拉取分支')

        run_blocking(_run, on_success=on_success, parent=self)

    def _populate_cb_available_list(self, aggregate, total_projects):
        """填充左侧「可选分支」列表。共有分支排最前，再按字母序。"""
        self.cb_available_list.blockSignals(True)
        self.cb_available_list.clear()

        def _sort_key(item):
            name, ws_list = item
            return (len(ws_list) < total_projects, name.lower())

        for name, ws_list in sorted(aggregate.items(), key=_sort_key):
            self.cb_available_list.addItem(self._make_cb_branch_item(name, ws_list, total_projects))
        self.cb_available_list.blockSignals(False)
        self._filter_cb_available(self.cb_search_input.text())

    def _make_cb_branch_item(self, name, ws_list, total_projects):
        is_common = len(ws_list) == total_projects
        if is_common:
            label = name
            tooltip = f'所有 {total_projects} 个勾选项目都有此分支'
        else:
            proj_names = '，'.join(w.workspace_name or w.path for w in ws_list)
            label = f'{name}  （{proj_names}）'
            tooltip = f'仅 {len(ws_list)} / {total_projects} 个项目有此分支：\n{proj_names}'
        item = QListWidgetItem(label)
        item.setToolTip(tooltip)
        item.setData(Qt.UserRole, name)
        if is_common:
            item.setForeground(QColor('#27ae60'))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def _cb_sort_key_for(self, name):
        ws_list = self._cb_branch_projects.get(name, [])
        return (len(ws_list) < self._cb_total_projects, name.lower())

    def _filter_cb_available(self, text):
        text = (text or '').lower().strip()
        for i in range(self.cb_available_list.count()):
            item = self.cb_available_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _cb_move_to_target(self):
        selected_items = list(self.cb_available_list.selectedItems())
        for item in selected_items:
            if item.isHidden():
                continue
            name = item.data(Qt.UserRole)
            if name is None:
                continue
            new_item = QListWidgetItem(item.text())
            new_item.setToolTip(item.toolTip())
            new_item.setData(Qt.UserRole, name)
            self.cb_selected_list.addItem(new_item)
            self.cb_available_list.takeItem(self.cb_available_list.row(item))

    def _cb_remove_from_target(self):
        selected_items = list(self.cb_selected_list.selectedItems())
        for item in selected_items:
            name = item.data(Qt.UserRole)
            self.cb_selected_list.takeItem(self.cb_selected_list.row(item))
            if name is None:
                continue
            # 按「共有在前 + 字母序」插回左侧正确位置
            new_item = self._make_cb_branch_item(
                name, self._cb_branch_projects.get(name, []), self._cb_total_projects
            )
            insert_at = self.cb_available_list.count()
            new_key = self._cb_sort_key_for(name)
            for i in range(self.cb_available_list.count()):
                existing = self.cb_available_list.item(i)
                existing_name = existing.data(Qt.UserRole) or ''
                if self._cb_sort_key_for(existing_name) > new_key:
                    insert_at = i
                    break
            self.cb_available_list.insertItem(insert_at, new_item)
        # 插回后要重新应用搜索过滤
        self._filter_cb_available(self.cb_search_input.text())

    def run_batch_create_branch(self):
        if not self._cb_branch_projects:
            QMessageBox.information(
                self, '提示',
                '请先点「刷新所有项目分支」加载分支，再从左侧穿梭到右侧选择目标分支。'
            )
            return
        selected_branches = []
        for i in range(self.cb_selected_list.count()):
            item = self.cb_selected_list.item(i)
            name = item.data(Qt.UserRole)
            if name:
                selected_branches.append(name)
        if not selected_branches:
            QMessageBox.information(self, '提示', '请从左侧穿梭至少一个目标分支到右侧。')
            return
        new_branch = self.cb_new_branch_combo.currentText().strip()
        if not new_branch:
            QMessageBox.warning(self, '提示', '请输入新分支名。')
            return

        tasks = []
        task_projects = set()
        for target in selected_branches:
            for ws in self._cb_branch_projects.get(target, []):
                tasks.append((ws, target))
                task_projects.add(ws.path)

        if not tasks:
            QMessageBox.warning(self, '无任务', '未找到对应项目的分支信息，请重新刷新。')
            return

        self.cb_create_button.setEnabled(False)
        self.cb_output.setText(
            f'开始批量创建 {len(tasks)} 个分支（跨 {len(task_projects)} 个项目，'
            f'目标分支 {len(selected_branches)} 个）...\n'
        )

        def _run():
            outputs = []
            failed = []
            success_any = False
            for ws, target in tasks:
                header = f'## {ws.workspace_name} / {target}'
                try:
                    out = create_branch_func(ws.path, target, new_branch)
                    outputs.append(f'{header}\n{out}')
                    if 'Branch created successfully!' in out:
                        success_any = True
                    else:
                        last = out.strip().splitlines()[-1] if out.strip() else '未知错误'
                        failed.append({'project': ws.workspace_name, 'target': target, 'error': last})
                except Exception as e:
                    msg = f'异常: {e}'
                    outputs.append(f'{header}\n{msg}')
                    failed.append({'project': ws.workspace_name, 'target': target, 'error': msg})
            return outputs, failed, success_any

        def on_success(result):
            outputs, failed, success_any = result
            self.cb_output.setText('\n\n'.join(outputs))
            self.cb_create_button.setEnabled(True)
            if success_any:
                self._save_new_branch_to_history(new_branch)
                self.cb_new_branch_combo.setEditText(self._get_default_new_branch_prefix())
            self._report_failures(failed, '批量创建分支')

        run_blocking(_run, on_success=on_success, parent=self)

    # ──────────────────────── 批量创建合并请求 ──────────────────────
    def init_create_mr_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 共享字段
        shared_form = QFormLayout()
        self.gitlab_url_input = QLineEdit(self._get_gitlab_value('gitlab_url'))
        self.gitlab_url_input.textChanged.connect(self._on_url_or_token_changed)
        self.token_input = QLineEdit(self._get_gitlab_value('private_token'))
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.textChanged.connect(self._on_url_or_token_changed)

        self.mr_assignee_combo = NoWheelComboBox()
        self.mr_reviewer_combo = NoWheelComboBox()
        _enable_combo_search(self.mr_assignee_combo)
        _enable_combo_search(self.mr_reviewer_combo)
        # 用户手动切换选择 → 立即保存为默认
        self.mr_assignee_combo.currentTextChanged.connect(self._save_user_selection)
        self.mr_reviewer_combo.currentTextChanged.connect(self._save_user_selection)
        self.mr_refresh_users_button = QPushButton('刷新用户')

        assignee_row = QHBoxLayout()
        assignee_row.addWidget(self.mr_assignee_combo)
        assignee_row.addWidget(self.mr_refresh_users_button)

        # 模板：保存原始模板（含变量），与展示文本分离
        self._title_template = self._get_gitlab_value('title_template', 'Draft: {commit_message}')
        self._desc_template = self._get_gitlab_value('description_template', '{commit_message}')
        self._template_substituting = False  # 防止程序化赋值被误认为是用户编辑

        self.mr_title_input = QLineEdit()
        self.mr_title_input.setText(self._title_template)
        self.mr_title_input.setPlaceholderText('支持 {source} / {target} / {commit_message} 模板')
        self.mr_title_input.textChanged.connect(self._on_title_edited)
        self.mr_desc_input = QTextEdit()
        self.mr_desc_input.setText(self._desc_template)
        self.mr_desc_input.setPlaceholderText('支持 {source} / {target} / {commit_message} 模板')
        self.mr_desc_input.textChanged.connect(self._on_desc_edited)
        self.mr_desc_input.setMaximumHeight(80)

        shared_form.addRow('GitLab 地址:', self.gitlab_url_input)
        shared_form.addRow('私有 Token:', self.token_input)
        shared_form.addRow('指派给:', assignee_row)
        shared_form.addRow('审查者:', self.mr_reviewer_combo)
        shared_form.addRow('标题模板:', self.mr_title_input)
        shared_form.addRow('描述模板:', self.mr_desc_input)
        layout.addLayout(shared_form)

        # 共有分支选择器
        common_branches_layout = QHBoxLayout()
        common_branches_layout.setSpacing(12)
        self.mr_common_source_combo = NoWheelComboBox()
        self.mr_common_source_combo.setEditable(True)
        self.mr_common_source_combo.setMinimumWidth(160)
        self.mr_common_source_combo.setPlaceholderText('默认源分支')
        _enable_combo_search(self.mr_common_source_combo)
        self.mr_common_target_combo = NoWheelComboBox()
        self.mr_common_target_combo.setEditable(True)
        self.mr_common_target_combo.setMinimumWidth(160)
        self.mr_common_target_combo.setPlaceholderText('默认目标分支')
        _enable_combo_search(self.mr_common_target_combo)
        self.mr_apply_common_btn = QPushButton('应用到所有项目')
        self.mr_apply_common_btn.setStyleSheet(
            'QPushButton { background: #1677ff; color: white; border: none; border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4096ff; }'
            'QPushButton:disabled { background: #bdc3c7; }'
        )
        self.mr_apply_common_btn.clicked.connect(self._apply_common_branches)
        self.mr_apply_common_btn.setEnabled(False)
        common_branches_layout.addWidget(QLabel('共有分支:'))
        common_branches_layout.addWidget(QLabel('源:'))
        common_branches_layout.addWidget(self.mr_common_source_combo)
        common_branches_layout.addWidget(QLabel('目标:'))
        common_branches_layout.addWidget(self.mr_common_target_combo)
        common_branches_layout.addWidget(self.mr_apply_common_btn)
        common_branches_layout.addStretch()
        layout.addLayout(common_branches_layout)

        # 操作按钮
        ops = QHBoxLayout()
        self.mr_refresh_branches_btn = QPushButton('刷新所有项目分支')
        self.mr_refresh_branches_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #e8e8e8; }'
        )
        self.mr_create_btn = QPushButton('批量创建合并请求')
        self.mr_create_btn.setStyleSheet(
            'QPushButton { background: #27ae60; color: white; border: none; border-radius: 4px; padding: 6px 14px; font-weight: bold; }'
            'QPushButton:hover { background: #2ecc71; }'
            'QPushButton:disabled { background: #bdc3c7; }'
        )
        ops.addWidget(self.mr_refresh_branches_btn)
        ops.addStretch()
        ops.addWidget(self.mr_create_btn)
        layout.addLayout(ops)

        # 每项目行表格
        self.mr_form_table = QTableWidget()
        self.mr_form_table.setColumnCount(4)
        self.mr_form_table.setHorizontalHeaderLabels(['项目', '源分支', '目标分支', '状态'])
        self.mr_form_table.setAlternatingRowColors(True)
        self.mr_form_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mr_form_table.verticalHeader().setVisible(False)
        self.mr_form_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hdr = self.mr_form_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        self.mr_form_table.setColumnWidth(0, 160)
        self.mr_form_table.setColumnWidth(3, 220)
        layout.addWidget(self.mr_form_table, stretch=1)

        self.mr_refresh_branches_btn.clicked.connect(self.run_batch_refresh_branches)
        self.mr_create_btn.clicked.connect(self.run_batch_create_mr)
        self.mr_refresh_users_button.clicked.connect(self.run_refresh_users)

        # 自动加载用户列表，并在加载完成后应用 config 中保存的默认值
        self._load_users_into_combos_async()

        self.create_mr_tab.setLayout(layout)

    def _rebuild_mr_form_rows(self):
        """根据当前勾选项目，重建「批量创建 MR」表格行。保留已选分支（按 path+branch 缓存）。"""
        selected = self._selected_workspace_tabs()
        # 保留旧选择
        old_cache = {}
        for ws, src_combo, tgt_combo, _status in self._mr_form_rows:
            try:
                old_cache[ws.path] = (src_combo.currentText(), tgt_combo.currentText())
            except Exception:
                pass

        self.mr_form_table.setRowCount(len(selected))
        self._mr_form_rows = []
        for row, ws in enumerate(selected):
            name_item = QTableWidgetItem(ws.workspace_name or ws.path)
            self.mr_form_table.setItem(row, 0, name_item)

            src_combo = NoWheelComboBox()
            tgt_combo = NoWheelComboBox()
            _enable_combo_search(src_combo)
            _enable_combo_search(tgt_combo)
            src_combo.addItems(['(待刷新)'])
            tgt_combo.addItems(['(待刷新)'])
            # 分支切换时实时更新标题/描述模板的变量替换
            src_combo.currentIndexChanged.connect(self._update_template_preview)
            tgt_combo.currentIndexChanged.connect(self._update_template_preview)

            status_label = QLabel('—')
            status_label.setAlignment(Qt.AlignCenter)

            self.mr_form_table.setCellWidget(row, 1, src_combo)
            self.mr_form_table.setCellWidget(row, 2, tgt_combo)
            self.mr_form_table.setCellWidget(row, 3, status_label)
            self._mr_form_rows.append((ws, src_combo, tgt_combo, status_label))

    def run_refresh_users(self, save_default=False, silent=False):
        url = self.gitlab_url_input.text().strip()
        token = self.token_input.text().strip()
        if not url or not token:
            if not silent:
                QMessageBox.information(self, '提示', '请先填写 GitLab 地址和 Token。')
            return
        self.mr_refresh_users_button.setEnabled(False)

        def _run():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            self.mr_refresh_users_button.setEnabled(True)
            if error:
                if not silent:
                    QMessageBox.warning(self, '加载用户失败', error)
                return
            self._populate_user_combos(users)
            # 保存默认值
            if save_default:
                assignee = self.mr_assignee_combo.currentText().strip()
                reviewer = self.mr_reviewer_combo.currentText().strip()
                if assignee:
                    self._set_gitlab_value('mr_assignee', assignee)
                if reviewer:
                    self._set_gitlab_value('mr_reviewer', reviewer)

        run_blocking(_run, on_success=on_success, parent=self)

    def _load_users_into_combos_async(self):
        url = self._get_gitlab_value('gitlab_url')
        token = self._get_gitlab_value('private_token')
        if not url or not token:
            return

        def _run():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            if error:
                return
            self._populate_user_combos(users)

        run_blocking(_run, on_success=on_success, parent=self)

    def _ensure_mr_users_loaded(self):
        """切换到「创建 MR」面板时调用：若用户列表为空，尝试自动拉取一次。"""
        if self.mr_assignee_combo.count() > 0 or self.mr_reviewer_combo.count() > 0:
            return
        # 优先用输入框当前值（用户可能刚填完），其次用 config
        url = self.gitlab_url_input.text().strip() or self._get_gitlab_value('gitlab_url')
        token = self.token_input.text().strip() or self._get_gitlab_value('private_token')
        if not url or not token:
            return

        def _run():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            if error:
                return
            self._populate_user_combos(users)

        run_blocking(_run, on_success=on_success, parent=self)

    def _on_url_or_token_changed(self):
        """GitLab 地址 / Token 变化时：保存到 config，并自动重新拉取用户列表。"""
        url = self.gitlab_url_input.text().strip()
        token = self.token_input.text().strip()
        self._set_gitlab_value('gitlab_url', url)
        self._set_gitlab_value('private_token', token)
        if not url or not token:
            return
        # 防抖：用 _mr_list_users_loaded 之类的标记可不行，直接 silent 拉取
        self.run_refresh_users(save_default=False, silent=True)

    def _save_user_selection(self, _value=None):
        """用户切换 assignee / reviewer 时立即保存为默认，下次自动选中。"""
        assignee = self.mr_assignee_combo.currentText().strip()
        reviewer = self.mr_reviewer_combo.currentText().strip()
        if assignee:
            self._set_gitlab_value('mr_assignee', assignee)
        if reviewer:
            self._set_gitlab_value('mr_reviewer', reviewer)

    def _on_title_edited(self, text):
        if self._template_substituting:
            return
        self._title_template = text
        self._set_gitlab_value('title_template', text)

    def _on_desc_edited(self):
        if self._template_substituting:
            return
        text = self.mr_desc_input.toPlainText()
        self._desc_template = text
        self._set_gitlab_value('description_template', text)

    def _update_template_preview(self):
        """根据当前第一行已选分支，实时把模板中的变量替换并写回输入框。"""
        source = ''
        target = ''
        for ws, src_combo, tgt_combo, _status in self._mr_form_rows:
            s = src_combo.currentText().strip()
            t = tgt_combo.currentText().strip()
            if s and s != '(待刷新)':
                source = s
            if t and t != '(待刷新)':
                target = t
            if source and target:
                break

        try:
            title = self._title_template.format(
                source=source, target=target,
                commit_message=source or '<commit>', tab_name='<workspace>'
            )
        except Exception:
            title = self._title_template
        try:
            desc = self._desc_template.format(
                source=source, target=target,
                commit_message=source or '<commit>', tab_name='<workspace>'
            )
        except Exception:
            desc = self._desc_template

        self._template_substituting = True
        self.mr_title_input.setText(title)
        self.mr_desc_input.setText(desc)
        self._template_substituting = False

    def _populate_user_combos(self, users):
        # 批量创建 MR 的指派给/审查者：优先保留当前选择；
        # 为空则回退到 mr_assignee/mr_reviewer，再回退到单 workspace 的 assignee/reviewer。
        for combo, primary_key, fallback_key in (
            (self.mr_assignee_combo, 'mr_assignee', 'assignee'),
            (self.mr_reviewer_combo, 'mr_reviewer', 'reviewer'),
        ):
            current = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            for u in users:
                combo.addItem(u)
            if not current:
                current = (
                    self._get_gitlab_value(primary_key, '').strip()
                    or self._get_gitlab_value(fallback_key, '').strip()
                )
            if current:
                # 默认值可能已不在用户列表里（例如离职），仍加入候选以保证可被选中
                if combo.findText(current, Qt.MatchFixedString) < 0:
                    combo.addItem(current)
                combo.setCurrentText(current)
            combo.blockSignals(False)

        # 顺带把 MR 列表 tab 的用户下拉也填充
        self._mr_list_users_loaded = True
        if hasattr(self, 'mr_list_author_combo'):
            for combo in (self.mr_list_author_combo, self.mr_list_assignee_combo, self.mr_list_reviewer_combo):
                current = combo.currentText()
                combo.blockSignals(True)
                for u in users:
                    if combo.findText(u, Qt.MatchFixedString) < 0:
                        combo.addItem(u)
                combo.setCurrentText(current)
                combo.blockSignals(False)

    def run_batch_refresh_branches(self):
        if not self._mr_form_rows:
            self._rebuild_mr_form_rows()
        rows = self._mr_form_rows
        if not rows:
            QMessageBox.information(self, '提示', '请先勾选至少一个项目。')
            return

        # 先刷新用户数据
        url = self.gitlab_url_input.text().strip()
        token = self.token_input.text().strip()
        if url and token:
            # 尝试加载用户（silent模式，不弹出提示）
            self.run_refresh_users(save_default=True, silent=True)

        self.mr_refresh_branches_btn.setEnabled(False)
        for _ws, _s, _t, status in rows:
            status.setText('加载中...')

        def _run():
            results = []
            all_branch_sets = []
            for ws, _s, _t, _status in rows:
                try:
                    # 远程分支作为源和目标候选
                    branches, msg = _safe_get_remote_branches(ws.path)
                    results.append((ws, branches, None))
                    all_branch_sets.append(set(branches))
                except Exception as e:
                    results.append((ws, [], str(e)))
                    all_branch_sets.append(set())
            # 计算共有分支
            common_branches = set()
            if all_branch_sets:
                common_branches = all_branch_sets[0]
                for branch_set in all_branch_sets[1:]:
                    common_branches &= branch_set
            return results, sorted(common_branches)

        def on_success(results_and_common):
            results, common_branches = results_and_common
            self.mr_refresh_branches_btn.setEnabled(True)
            # 填充共有分支选择器
            self.mr_common_source_combo.blockSignals(True)
            self.mr_common_target_combo.blockSignals(True)
            self.mr_common_source_combo.clear()
            self.mr_common_target_combo.clear()
            self.mr_common_source_combo.addItems(common_branches)
            self.mr_common_target_combo.addItems(common_branches)
            self.mr_common_source_combo.blockSignals(False)
            self.mr_common_target_combo.blockSignals(False)
            self.mr_apply_common_btn.setEnabled(bool(common_branches))

            for (ws, src, _t, status), result in zip(rows, results):
                _ws2, branches, err = result
                if err:
                    status.setText(f'失败: {err[:40]}')
                    status.setStyleSheet('color: #e74c3c;')
                    continue
                # 设置源分支和目标分支候选
                src.blockSignals(True)
                _t.blockSignals(True)
                src.clear()
                _t.clear()
                src.addItems(branches)
                _t.addItems(branches)
                src.blockSignals(False)
                _t.blockSignals(False)
                # 尝试默认选 config 里第一个 target_branch
                targets = self._read_target_branches_for(ws)
                if targets:
                    idx = _t.findText(targets[0])
                    if idx >= 0:
                        _t.setCurrentIndex(idx)
                status.setText('就绪')
                status.setStyleSheet('color: #27ae60;')
            # 分支填充完毕后，触发一次模板变量替换
            self._update_template_preview()

        run_blocking(_run, on_success=on_success, parent=self)

    def _apply_common_branches(self):
        """将共有分支选择器的值应用到所有项目的源/目标分支下拉。"""
        source = self.mr_common_source_combo.currentText().strip()
        target = self.mr_common_target_combo.currentText().strip()
        applied_source = 0
        applied_target = 0
        for ws, src_combo, tgt_combo, status in self._mr_form_rows:
            if source:
                idx = src_combo.findText(source)
                if idx >= 0:
                    src_combo.setCurrentIndex(idx)
                    applied_source += 1
            if target:
                idx = tgt_combo.findText(target)
                if idx >= 0:
                    tgt_combo.setCurrentIndex(idx)
                    applied_target += 1
            status.setText('已应用默认分支')
            status.setStyleSheet('color: #1677ff;')
        self._update_template_preview()
        QMessageBox.information(
            self, '已应用',
            f'已为 {len(self._mr_form_rows)} 个项目应用默认分支。\n'
            f'源分支: {source or "未选择"}\n'
            f'目标分支: {target or "未选择"}'
        )

    def run_batch_create_mr(self):
        if not self._mr_form_rows:
            QMessageBox.information(self, '提示', '请先点「刷新所有项目分支」。')
            return
        url = self.gitlab_url_input.text().strip()
        token = self.token_input.text().strip()
        if not url or not token:
            QMessageBox.warning(self, '提示', '请先填写 GitLab 地址和 Token。')
            return
        assignee = self.mr_assignee_combo.currentText().strip()
        reviewer = self.mr_reviewer_combo.currentText().strip()
        title_tpl = self.mr_title_input.text().strip() or '{source}'
        desc_tpl = self.mr_desc_input.toPlainText().strip()

        tasks = []
        for ws, src, tgt, status in self._mr_form_rows:
            source = src.currentText().strip()
            target = tgt.currentText().strip()
            if source in ('', '(待刷新)') or target in ('', '(待刷新)'):
                status.setText('跳过：未选分支')
                status.setStyleSheet('color: #888;')
                continue
            tasks.append((ws, source, target, status))

        if not tasks:
            QMessageBox.information(self, '提示', '所有项目都未选源/目标分支，无可执行任务。')
            return

        self.mr_create_btn.setEnabled(False)
        for _ws, _s, _t, status in tasks:
            status.setText('排队中...')
            status.setStyleSheet('color: #888;')

        def _run():
            from quick_create_branch import run_command
            failed = []
            outputs = []
            for ws, source, target, _status in tasks:
                # 获取源分支的最后一次commit message
                commit_message = ''
                try:
                    stdout, stderr = run_command(['git', 'log', source, '-1', '--pretty=%B'], ws.path)
                    if not stderr:
                        commit_message = stdout.strip()
                except Exception:
                    pass

                try:
                    title = title_tpl.format(
                        source=source,
                        target=target,
                        tab_name=ws.workspace_name,
                        commit_message=commit_message
                    )
                    desc = desc_tpl.format(
                        source=source,
                        target=target,
                        tab_name=ws.workspace_name,
                        commit_message=commit_message
                    )
                except Exception as e:
                    msg = f'模板格式错误: {e}'
                    failed.append({'project': ws.workspace_name, 'error': msg})
                    outputs.append((ws, source, target, msg, False))
                    continue
                try:
                    out = generate_mr(ws.path, url, token, assignee, reviewer, source, title, desc, target)
                    ok = 'Successfully created MR!' in out
                    if not ok:
                        failed.append({'project': ws.workspace_name, 'error': out})
                    outputs.append((ws, source, target, out, ok))
                except Exception as e:
                    msg = f'异常: {e}'
                    failed.append({'project': ws.workspace_name, 'error': msg})
                    outputs.append((ws, source, target, msg, False))
            return outputs, failed

        def on_success(result):
            outputs, failed = result
            self.mr_create_btn.setEnabled(True)
            for ws, source, target, msg, ok in outputs:
                # 找到对应行更新状态
                for _w, _s, _t, status in self._mr_form_rows:
                    if _w is ws and _s.currentText().strip() == source and _t.currentText().strip() == target:
                        if ok:
                            status.setText('✅ 已创建')
                            status.setStyleSheet('color: #27ae60;')
                        else:
                            short = msg.strip().splitlines()[-1] if msg.strip() else '失败'
                            status.setText(f'❌ {short[:40]}')
                            status.setStyleSheet('color: #e74c3c;')
                        break
            self._report_failures(failed, '批量创建 MR')

        run_blocking(_run, on_success=on_success, parent=self)

    # ──────────────────────── 批量分支管理 ──────────────────────────
    def init_branch_mgmt_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 顶部过滤器
        top = QHBoxLayout()
        self.bm_local_btn = QPushButton('本地分支')
        self.bm_local_btn.setCheckable(True)
        self.bm_local_btn.setChecked(True)
        self.bm_local_btn.clicked.connect(lambda: self._switch_bm_mode('local'))
        self.bm_remote_btn = QPushButton('远程分支')
        self.bm_remote_btn.setCheckable(True)
        self.bm_remote_btn.clicked.connect(lambda: self._switch_bm_mode('remote'))
        self._update_bm_mode_buttons()

        self.bm_project_combo = NoWheelComboBox()
        self.bm_project_combo.addItem('(全部项目)')
        self.bm_project_combo.setMinimumWidth(160)
        self.bm_project_combo.currentIndexChanged.connect(self.apply_bm_filters)

        self.bm_text_filter = QLineEdit()
        self.bm_text_filter.setPlaceholderText('搜索分支名...')
        self.bm_text_filter.textChanged.connect(self.apply_bm_filters)

        self.bm_prefix_combo = NoWheelComboBox()
        self.bm_prefix_combo.addItem('(全部前缀)')
        self.bm_prefix_combo.setMinimumWidth(140)
        self.bm_prefix_combo.currentIndexChanged.connect(self.apply_bm_filters)

        self.bm_time_combo = NoWheelComboBox()
        self.bm_time_combo.addItems(['全部', '今天', '7天内', '30天内', '90天内', '超过30天', '超过90天'])
        self.bm_time_combo.setCurrentIndex(5)
        self.bm_time_combo.currentIndexChanged.connect(self.apply_bm_filters)

        top.addWidget(self.bm_local_btn)
        top.addWidget(self.bm_remote_btn)
        top.addSpacing(8)
        top.addWidget(QLabel('项目:'))
        top.addWidget(self.bm_project_combo)
        top.addWidget(QLabel('前缀:'))
        top.addWidget(self.bm_prefix_combo)
        top.addWidget(QLabel('时间:'))
        top.addWidget(self.bm_time_combo)
        top.addWidget(self.bm_text_filter, stretch=1)
        layout.addLayout(top)

        # 操作栏
        ops = QHBoxLayout()
        self.bm_refresh_btn = QPushButton('刷新')
        self.bm_select_all_btn = QPushButton('全选')
        self.bm_invert_btn = QPushButton('反选')
        self.bm_delete_btn = QPushButton('删除选中')
        self.bm_delete_btn.setStyleSheet(
            'QPushButton { background: #e74c3c; color: white; border: none; border-radius: 4px; padding: 6px 14px; font-weight: bold; }'
            'QPushButton:hover { background: #c0392b; }'
            'QPushButton:disabled { background: #bdc3c7; }'
        )
        self.bm_status = QLabel('')
        self.bm_status.setStyleSheet('color: #888;')
        ops.addWidget(self.bm_refresh_btn)
        ops.addWidget(self.bm_select_all_btn)
        ops.addWidget(self.bm_invert_btn)
        ops.addWidget(self.bm_delete_btn)
        ops.addStretch()
        ops.addWidget(self.bm_status)
        layout.addLayout(ops)

        # 表格
        self.bm_table = QTableWidget()
        self.bm_table.setColumnCount(7)
        self.bm_table.setHorizontalHeaderLabels(
            ['', '项目', '分支名', '最后提交时间', '提交者', '最后提交信息', '已合并']
        )
        self.bm_table.setAlternatingRowColors(True)
        self.bm_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bm_table.verticalHeader().setVisible(False)
        self.bm_table.setShowGrid(False)
        hdr = self.bm_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.bm_table.setColumnWidth(0, 36)
        self.bm_table.setColumnWidth(1, 140)
        self.bm_table.setColumnWidth(3, 160)
        self.bm_table.setColumnWidth(4, 100)
        self.bm_table.setColumnWidth(6, 100)
        layout.addWidget(self.bm_table, stretch=1)

        self.bm_refresh_btn.clicked.connect(self.run_bm_refresh)
        self.bm_select_all_btn.clicked.connect(self._bm_select_all)
        self.bm_invert_btn.clicked.connect(self._bm_invert)
        self.bm_delete_btn.clicked.connect(self.run_bm_delete)
        self.branch_mgmt_tab.setLayout(layout)

    def _update_bm_mode_buttons(self):
        active = 'background: rgb(22,119,255); color: white; border: none; border-radius: 4px; padding: 6px 16px; font-weight: bold;'
        inactive = 'background: #f5f5f5; color: #555; border: 1px solid #ddd; border-radius: 4px; padding: 6px 16px;'
        self.bm_local_btn.setStyleSheet(active if self._branch_mgmt_mode == 'local' else inactive)
        self.bm_remote_btn.setStyleSheet(active if self._branch_mgmt_mode == 'remote' else inactive)

    def _switch_bm_mode(self, mode):
        if self._branch_mgmt_mode == mode:
            return
        self._branch_mgmt_mode = mode
        self._update_bm_mode_buttons()
        self.run_bm_refresh()

    def _refresh_bm_project_combo(self):
        """根据勾选项目，重建分支管理顶部的「项目」过滤器。"""
        current = self.bm_project_combo.currentText()
        self.bm_project_combo.blockSignals(True)
        self.bm_project_combo.clear()
        self.bm_project_combo.addItem('(全部项目)')
        for ws in self._selected_workspace_tabs():
            self.bm_project_combo.addItem(ws.workspace_name or ws.path)
        # 尽量恢复之前选择
        idx = self.bm_project_combo.findText(current)
        if idx >= 0:
            self.bm_project_combo.setCurrentIndex(idx)
        self.bm_project_combo.blockSignals(False)

    def run_bm_refresh(self):
        selected = self._selected_workspace_tabs()
        if not selected:
            QMessageBox.information(self, '提示', '请至少勾选一个项目。')
            return
        self._refresh_bm_project_combo()
        self.bm_refresh_btn.setEnabled(False)
        self.bm_status.setText('正在加载分支...')

        mode = self._branch_mgmt_mode
        targets_map = {ws.path: self._read_target_branches_for(ws) for ws in selected}

        def _run():
            all_rows = []
            current_map = {}
            protected_map = {}
            failed = []
            for ws in selected:
                try:
                    if mode == 'local':
                        branches, err = get_branch_details(ws.path)
                    else:
                        branches, err = get_remote_branch_details(ws.path)
                    if err:
                        failed.append({'project': ws.workspace_name, 'error': err})
                        continue
                except Exception as e:
                    failed.append({'project': ws.workspace_name, 'error': str(e)})
                    continue
                # 记录当前分支与保护分支
                cur = ''
                prot = set(targets_map.get(ws.path, []))
                if mode == 'local':
                    for b in branches:
                        if b.get('is_current'):
                            cur = b['name']
                            break
                current_map[ws.path] = cur
                protected_map[ws.path] = prot
                # 计算合并状态：对每个 target 调 get_branches_no_merged，未合并集合的并集（相对所有 target）
                unmerged_union = set()
                if mode == 'local':
                    for t in targets_map.get(ws.path, []):
                        try:
                            unmerged, _e = get_branches_no_merged(ws.path, t)
                            unmerged_union |= set(unmerged)
                        except Exception:
                            pass
                for b in branches:
                    merged = '—' if mode == 'remote' else ('否' if b['name'] in unmerged_union else '是')
                    all_rows.append({
                        'ws': ws,
                        'project': ws.workspace_name or ws.path,
                        'branch': b['name'],
                        'date': b.get('last_commit_date', ''),
                        'author': b.get('author', ''),
                        'subject': b.get('subject', ''),
                        'merged': merged,
                        'is_current': b.get('is_current', False),
                        'type': mode,
                    })
            return all_rows, current_map, protected_map, failed

        def on_success(result):
            all_rows, current_map, protected_map, failed = result
            self._branch_mgmt_all_data = all_rows
            self._branch_mgmt_current = current_map
            self._branch_mgmt_protected = protected_map
            self._populate_bm_table()
            self.bm_refresh_btn.setEnabled(True)
            total = len(all_rows)
            self.bm_status.setText(f'共 {total} 个分支（{len(selected)} 个项目）。')
            self._report_failures(failed, '分支管理刷新')

        run_blocking(_run, on_success=on_success, parent=self)

    def _populate_bm_table(self):
        # 先重建前缀下拉
        prefixes = set()
        for row in self._branch_mgmt_all_data:
            name = row['branch']
            prefix = name.split('/')[0] if '/' in name else name
            prefixes.add(prefix)
        current_prefix = self.bm_prefix_combo.currentText()
        self.bm_prefix_combo.blockSignals(True)
        self.bm_prefix_combo.clear()
        self.bm_prefix_combo.addItem('(全部前缀)')
        for p in sorted(prefixes):
            self.bm_prefix_combo.addItem(p)
        idx = self.bm_prefix_combo.findText(current_prefix)
        if idx >= 0:
            self.bm_prefix_combo.setCurrentIndex(idx)
        self.bm_prefix_combo.blockSignals(False)
        self.apply_bm_filters()

    def apply_bm_filters(self):
        text = self.bm_text_filter.text().lower().strip()
        prefix = self.bm_prefix_combo.currentText()
        prefix_filter = None if prefix in ('', '(全部前缀)') else prefix
        time_idx = self.bm_time_combo.currentIndex()
        project_filter = self.bm_project_combo.currentText()
        project_filter = None if project_filter in ('', '(全部项目)') else project_filter

        now = datetime.now()
        ranges = {
            0: None,
            1: now - timedelta(days=1),       # 今天：>= today-1
            2: now - timedelta(days=7),
            3: now - timedelta(days=30),
            4: now - timedelta(days=90),
            5: 'before_30',
            6: 'before_90',
        }
        cutoff = ranges.get(time_idx)

        # 先清空旧行
        self.bm_table.setRowCount(0)
        self._branch_mgmt_checkboxes = []

        for idx, row in enumerate(self._branch_mgmt_all_data):
            if text and text not in row['branch'].lower():
                continue
            if prefix_filter:
                rp = row['branch'].split('/')[0] if '/' in row['branch'] else row['branch']
                if rp != prefix_filter:
                    continue
            if project_filter and row['project'] != project_filter:
                continue
            # 时间过滤
            if cutoff is not None and cutoff != 'before_30' and cutoff != 'before_90':
                if not _date_within(row['date'], cutoff, now):
                    continue
            elif cutoff == 'before_30':
                d = _parse_date(row['date'])
                if d is None or d >= now - timedelta(days=30):
                    continue
            elif cutoff == 'before_90':
                d = _parse_date(row['date'])
                if d is None or d >= now - timedelta(days=90):
                    continue

            # 添加新行
            r = self.bm_table.rowCount()
            self.bm_table.insertRow(r)

            cb = QCheckBox()
            cb_container = QWidget()
            cb_layout = QHBoxLayout(cb_container)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.addWidget(cb)
            self.bm_table.setCellWidget(r, 0, cb_container)

            proj_item = QTableWidgetItem(row['project'])
            self.bm_table.setItem(r, 1, proj_item)

            name = row['branch']
            name_item = QTableWidgetItem(name)
            ws = row['ws']
            protected = self._branch_mgmt_protected.get(ws.path, set())
            is_current = row.get('is_current', False)
            if is_current or name in protected:
                name_item.setForeground(QColor('#e74c3c'))
                cb.setEnabled(False)
                if is_current:
                    name_item.setText(name + '  (当前)')
                else:
                    name_item.setText(name + '  (保护)')
            self.bm_table.setItem(r, 2, name_item)
            self.bm_table.setItem(r, 3, QTableWidgetItem(row['date']))
            self.bm_table.setItem(r, 4, QTableWidgetItem(row['author']))
            self.bm_table.setItem(r, 5, QTableWidgetItem(row['subject']))

            merged_item = QTableWidgetItem(row['merged'])
            if row['merged'] == '否':
                merged_item.setForeground(QColor('#e67e22'))
            self.bm_table.setItem(r, 6, merged_item)

            self._branch_mgmt_checkboxes.append((cb, idx))

    def _bm_select_all(self):
        for cb, _idx in self._branch_mgmt_checkboxes:
            if cb.isEnabled():
                cb.setChecked(True)

    def _bm_invert(self):
        for cb, _idx in self._branch_mgmt_checkboxes:
            if cb.isEnabled():
                cb.setChecked(not cb.isChecked())

    def run_bm_delete(self):
        selected_idxs = [idx for cb, idx in self._branch_mgmt_checkboxes if cb.isChecked()]
        if not selected_idxs:
            QMessageBox.information(self, '提示', '请先勾选要删除的分支。')
            return
        mode = self._branch_mgmt_mode
        rows = [self._branch_mgmt_all_data[i] for i in selected_idxs]
        # 二次确认
        names_preview = '\n'.join(f"- [{r['project']}] {r['branch']} ({mode})" for r in rows[:20])
        more = '' if len(rows) <= 20 else f'\n... 共 {len(rows)} 个'
        reply = QMessageBox.question(
            self, '确认批量删除',
            f'即将删除 {len(rows)} 个{mode}分支：\n\n{names_preview}{more}\n\n'
            f'本地分支用 git branch -D 强删；远程分支用 git push origin --delete。'
            f'操作不可撤销，是否继续？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.bm_delete_btn.setEnabled(False)
        self.bm_status.setText(f'正在删除 {len(rows)} 个分支...')

        def _run():
            failed = []
            success = 0
            for r in rows:
                ws = r['ws']
                name = r['branch']
                try:
                    if mode == 'local':
                        from quick_create_branch import run_command
                        ok, _out, err = run_command(['git', 'branch', '-D', name], ws.path)
                        if not ok:
                            failed.append({'project': r['project'], 'error': f'删除 {name} 失败: {err}'})
                            continue
                    else:
                        from quick_create_branch import run_command
                        ok, _out, err = run_command(['git', 'push', 'origin', '--delete', name], ws.path)
                        if not ok:
                            failed.append({'project': r['project'], 'error': f'删除远程 {name} 失败: {err}'})
                            continue
                    success += 1
                except Exception as e:
                    failed.append({'project': r['project'], 'error': f'删除 {name} 异常: {e}'})
            return success, failed

        def on_success(result):
            success, failed = result
            self.bm_delete_btn.setEnabled(True)
            self.bm_status.setText(f'成功删除 {success} 个分支。')
            self._report_failures(failed, '批量删除分支')
            self.run_bm_refresh()

        run_blocking(_run, on_success=on_success, parent=self)

    # ──────────────────────── 批量合并请求列表 ──────────────────────
    def init_mr_list_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.mr_list_state_combo = NoWheelComboBox()
        for label, _v in self.MR_STATE_OPTIONS:
            self.mr_list_state_combo.addItem(label)
        self.mr_list_state_combo.setCurrentIndex(0)
        self.mr_list_state_combo.setMinimumWidth(90)

        self.mr_list_project_combo = NoWheelComboBox()
        self.mr_list_project_combo.addItem('(全部项目)')
        self.mr_list_project_combo.setMinimumWidth(160)

        self.mr_list_author_combo = NoWheelComboBox()
        self.mr_list_assignee_combo = NoWheelComboBox()
        self.mr_list_reviewer_combo = NoWheelComboBox()
        for combo in (self.mr_list_author_combo, self.mr_list_assignee_combo, self.mr_list_reviewer_combo):
            combo.addItem('(全部)')
            combo.setMinimumWidth(120)
            _enable_combo_search(combo)

        top.addWidget(QLabel('状态:'))
        top.addWidget(self.mr_list_state_combo)
        top.addWidget(QLabel('项目:'))
        top.addWidget(self.mr_list_project_combo)
        top.addWidget(QLabel('创建人:'))
        top.addWidget(self.mr_list_author_combo)
        top.addWidget(QLabel('指派:'))
        top.addWidget(self.mr_list_assignee_combo)
        top.addWidget(QLabel('审查者:'))
        top.addWidget(self.mr_list_reviewer_combo)
        top.addStretch()

        self.mr_list_refresh_btn = QPushButton('刷新')
        self.mr_list_refresh_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #e8e8e8; }'
        )
        top.addWidget(self.mr_list_refresh_btn)
        layout.addLayout(top)

        self.mr_list_state_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_project_combo.currentIndexChanged.connect(self._apply_mr_list_project_filter)
        self.mr_list_author_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_assignee_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_reviewer_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_refresh_btn.clicked.connect(self.run_batch_refresh_mr_list)

        self.mr_list_status = QLabel('')
        self.mr_list_status.setStyleSheet('color: #888; font-size: 12px;')
        layout.addWidget(self.mr_list_status)

        self.mr_list_table = QTableWidget()
        self.mr_list_table.setColumnCount(9)
        self.mr_list_table.setHorizontalHeaderLabels(
            ['标题', '项目', '源分支 → 目标分支', '作者', '指派', '审查者', '创建时间', '合并状态', '操作']
        )
        self.mr_list_table.setAlternatingRowColors(True)
        self.mr_list_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mr_list_table.verticalHeader().setVisible(False)
        self.mr_list_table.verticalHeader().setDefaultSectionSize(40)
        self.mr_list_table.setShowGrid(False)
        hdr = self.mr_list_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.Fixed)
        self.mr_list_table.setColumnWidth(1, 140)
        self.mr_list_table.setColumnWidth(3, 90)
        self.mr_list_table.setColumnWidth(4, 90)
        self.mr_list_table.setColumnWidth(5, 90)
        self.mr_list_table.setColumnWidth(6, 150)
        self.mr_list_table.setColumnWidth(7, 100)
        self.mr_list_table.setColumnWidth(8, 80)
        layout.addWidget(self.mr_list_table, stretch=1)

        self.mr_list_tab.setLayout(layout)

    def _current_mr_state(self):
        idx = self.mr_list_state_combo.currentIndex()
        if 0 <= idx < len(self.MR_STATE_OPTIONS):
            return self.MR_STATE_OPTIONS[idx][1]
        return 'opened'

    def _refresh_mr_list_project_combo(self):
        current = self.mr_list_project_combo.currentText()
        self.mr_list_project_combo.blockSignals(True)
        self.mr_list_project_combo.clear()
        self.mr_list_project_combo.addItem('(全部项目)')
        for ws in self._selected_workspace_tabs():
            self.mr_list_project_combo.addItem(ws.workspace_name or ws.path)
        idx = self.mr_list_project_combo.findText(current)
        if idx >= 0:
            self.mr_list_project_combo.setCurrentIndex(idx)
        self.mr_list_project_combo.blockSignals(False)

    def _ensure_mr_list_users_loaded(self):
        if self._mr_list_users_loaded:
            return
        url = self._get_gitlab_value('gitlab_url')
        token = self._get_gitlab_value('private_token')
        if not url or not token:
            return
        self._mr_list_users_loaded = True

        def _run():
            return get_gitlab_usernames(url, token)

        def on_success(result):
            users, error = result
            if error:
                self._mr_list_users_loaded = False
                return
            self._populate_user_combos(users)

        run_blocking(_run, on_success=on_success, parent=self)

    def run_batch_refresh_mr_list(self):
        selected = self._selected_workspace_tabs()
        if not selected:
            QMessageBox.information(self, '提示', '请至少勾选一个项目。')
            return
        url = self._get_gitlab_value('gitlab_url')
        token = self._get_gitlab_value('private_token')
        if not url or not token:
            self.mr_list_status.setText('请先在「批量创建合并请求」tab 配置 GitLab 地址和 Token。')
            self.mr_list_table.setRowCount(0)
            return

        self._ensure_mr_list_users_loaded()
        self._refresh_mr_list_project_combo()

        state = self._current_mr_state()

        def _filter_value(combo):
            text = combo.currentText().strip()
            return None if (not text or text == '(全部)') else text

        author = _filter_value(self.mr_list_author_combo)
        assignee = _filter_value(self.mr_list_assignee_combo)
        reviewer = _filter_value(self.mr_list_reviewer_combo)

        self._mr_list_refresh_seq += 1
        seq = self._mr_list_refresh_seq

        self.mr_list_status.setText('正在加载合并请求...')
        self.mr_list_refresh_btn.setEnabled(False)

        def _run():
            all_mrs = []  # list[(ws, mr_dict)]
            failed = []
            for ws in selected:
                try:
                    mrs, err = get_merge_requests(
                        ws.path, url, token,
                        state=state, author=author, assignee=assignee, reviewer=reviewer
                    )
                    if err:
                        failed.append({'project': ws.workspace_name, 'error': err})
                        continue
                    for mr in mrs:
                        all_mrs.append((ws, mr))
                except Exception as e:
                    failed.append({'project': ws.workspace_name, 'error': str(e)})
            return all_mrs, failed

        def on_success(result):
            if seq != self._mr_list_refresh_seq:
                return
            self.mr_list_refresh_btn.setEnabled(True)
            all_mrs, failed = result
            self._populate_mr_list_table(all_mrs)
            total = len(all_mrs)
            if total:
                self.mr_list_status.setText(f'共 {total} 个 MR（{len(selected)} 个项目）。')
            else:
                self.mr_list_status.setText('没有符合条件的 MR。')
            self._report_failures(failed, '批量 MR 列表')

        run_blocking(_run, on_success=on_success, parent=self)

    def _populate_mr_list_table(self, all_mrs):
        # 按项目过滤
        project_filter = self.mr_list_project_combo.currentText()
        if project_filter in ('', '(全部项目)'):
            project_filter = None

        filtered = [(ws, mr) for ws, mr in all_mrs if not project_filter or (ws.workspace_name or ws.path) == project_filter]

        self.mr_list_table.setRowCount(len(filtered))
        self._mr_row_context = []
        url = self._get_gitlab_value('gitlab_url')
        token = self._get_gitlab_value('private_token')
        for row, (ws, mr) in enumerate(filtered):
            self.mr_list_table.setItem(row, 0, QTableWidgetItem(mr.get('title', '')))
            self.mr_list_table.setItem(row, 1, QTableWidgetItem(ws.workspace_name or ws.path))
            self.mr_list_table.setItem(row, 2, QTableWidgetItem(f"{mr.get('source_branch', '')} → {mr.get('target_branch', '')}"))
            self.mr_list_table.setItem(row, 3, QTableWidgetItem(mr.get('author', '')))
            self.mr_list_table.setItem(row, 4, QTableWidgetItem(mr.get('assignees', '')))
            self.mr_list_table.setItem(row, 5, QTableWidgetItem(mr.get('reviewers', '')))
            self.mr_list_table.setItem(row, 6, QTableWidgetItem(_format_mr_datetime(mr.get('created_at', ''))))

            status_item = QTableWidgetItem(mr.get('merge_status', ''))
            if mr.get('merge_status') == 'can_be_merged':
                status_item.setForeground(QColor('#27ae60'))
            elif mr.get('merge_status') == 'cannot_be_merged':
                status_item.setForeground(QColor('#e74c3c'))
            self.mr_list_table.setItem(row, 7, status_item)

            merge_btn = QPushButton('合并')
            merge_btn.setMinimumHeight(30)
            merge_btn.setStyleSheet(
                'QPushButton { background: #27ae60; color: white; border: none; border-radius: 4px; padding: 6px 12px; font-weight: bold; }'
                'QPushButton:hover { background: #2ecc71; }'
                'QPushButton:disabled { background: #bdc3c7; }'
            )
            if mr.get('state', 'opened') != 'opened':
                merge_btn.setEnabled(False)
                merge_btn.setToolTip('仅 Open 状态的 MR 可合并')
            merge_btn.clicked.connect(lambda _checked=False, w=ws, m=mr: self._merge_mr(w, m, url, token))
            self.mr_list_table.setCellWidget(row, 8, merge_btn)

            self._mr_row_context.append((ws, mr))

    def _apply_mr_list_project_filter(self):
        """仅刷新可见行（不过后端），用上次缓存。简化：直接重新拉取。"""
        # 重新拉取成本较高，这里改为：如果已有数据，重新渲染；否则不动
        if not self._mr_row_context:
            return
        # 用上次拉取结果重新渲染
        self._populate_mr_list_table([ctx for ctx in self._mr_row_context])

    def _merge_mr(self, ws, mr, url, token):
        reply = QMessageBox.question(
            self, '确认合并 Merge Request 吗？',
            f"项目: {ws.workspace_name}\nMR: !{mr.get('iid')}\n标题: {mr.get('title')}\n"
            f"源分支: {mr.get('source_branch')}\n目标分支: {mr.get('target_branch')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.No:
            return
        self.mr_list_status.setText(f"正在合并 {ws.workspace_name} !{mr.get('iid')}...")

        mr_iid = mr.get('iid')

        def _run():
            return merge_merge_request(ws.path, url, token, mr_iid)

        def on_success(output):
            self.mr_list_status.setText(f'{ws.workspace_name}: {output}')
            self.run_batch_refresh_mr_list()

        run_blocking(_run, on_success=on_success, parent=self)

    # ──────────────────────── 工具方法 ──────────────────────────────
    def _report_failures(self, failed, op_name):
        if not failed:
            return
        msg_lines = []
        for f in failed[:20]:
            extra = f" (target={f['target']})" if 'target' in f else ''
            msg_lines.append(f"- [{f['project']}]{extra} {f['error']}")
        more = '' if len(failed) <= 20 else f'\n... 共 {len(failed)} 项失败'
        QMessageBox.warning(
            self, f'{op_name} - 部分项目失败',
            f'以下 {len(failed)} 项失败，其余操作已完成：\n\n' + '\n'.join(msg_lines) + more
        )


# ──────────────────────── 模块级辅助函数 ──────────────────────────
def _safe_get_remote_branches(path):
    from quick_create_branch import get_remote_branches
    return get_remote_branches(path)


def _format_mr_datetime(iso_str):
    if not iso_str:
        return ''
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return iso_str.replace('T', ' ')[:19]


def _parse_date(s):
    """解析 git for-each-ref 的 iso8601 日期。失败返回 None。"""
    if not s:
        return None
    try:
        # 例: 2025-06-12 14:30:00 +0800
        return datetime.strptime(s.split(' ')[0] + ' ' + s.split(' ')[1], '%Y-%m-%d %H:%M:%S')
    except Exception:
        try:
            return datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception:
            return None


def _date_within(s, cutoff, now):
    d = _parse_date(s)
    if d is None:
        return True  # 解析失败则保留
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d >= cutoff
