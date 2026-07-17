"""「所有项目」固定 Tab — 跨所有 workspace 的批量操作。

批量操作 4 类：
  * 批量创建分支：聚合所有勾选项目的远程分支，按分支名分组并标注项目来源，勾选后批量创建
  * 批量创建合并请求：共用 GitLab 凭据和标题/描述模板，每项目独立选择源/目标分支
  * 批量分支管理：所有项目分支合并单表，带「项目」列
  * 批量合并请求列表：所有项目 MR 合并单表，带「项目」列

错误处理：单项目失败不打断其他项目，末尾汇总弹窗。
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QFormLayout
)

from app.async_utils import run_blocking
from app.recent_branch_store import RecentBranchStore
from app.widgets import NoWheelComboBox
from quick_create_branch import create_branch as create_branch_func
from quick_create_branch import smart_checkout as smart_checkout_func
from quick_generate_mr_form import (
    generate_mr, get_merge_requests, merge_merge_request,
    get_gitlab_usernames, get_current_gitlab_username, get_branch_details, get_remote_branch_details,
    get_branches_no_merged, truncate_mr_title, parse_target_branch_from_source
)


class _SafeFormatDict(dict):
    """str.format_map 用：未知占位符返回空字符串而非 KeyError。"""

    def __missing__(self, key):
        return ''


def _safe_format(template, **kwargs):
    """格式化模板：未知 {var} 替换为空字符串；模板本身格式错误（如未闭合大括号）返回原文。"""
    if not template:
        return ''
    try:
        return template.format_map(_SafeFormatDict(kwargs))
    except (ValueError, IndexError):
        # 模板本身格式错误（例如 { 未闭合），返回原文，保留占位符让用户可见可改
        return template


def _extract_tg_number_from_title(title):
    """从已格式化的标题里提取 taiga 编号（如 'tg-123 xxx' -> '123'）。找不到返回空串。"""
    if not title:
        return ''
    m = re.search(r'tg-(\d+)', title, re.IGNORECASE)
    return m.group(1) if m else ''


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
        # 当前 token 对应的用户名（"自己"筛选）
        self._current_username = None

        # 分支管理共享状态
        self._branch_mgmt_all_data = []      # list[(project_name, branch_dict, type_str, workspace_tab)]
        self._branch_mgmt_checkboxes = []    # 并行 list[(checkbox, index_into_all_data)]
        self._branch_mgmt_mode = 'local'     # 'local' / 'remote'
        self._branch_mgmt_current = {}       # path -> current branch name
        self._branch_mgmt_protected = {}     # path -> set(protected branch names)

        # 批量创建 MR 的每项目行数据
        # list[(ws, checkbox, src_combo, tgt_combo, commit_msg_label, commit_time_label, status_label)]
        self._mr_form_rows = []
        # 重建行时保留的旧选择（path -> (source_text, target_text)）
        self._mr_form_old_cache = {}
        # 缓存最近一次刷新得到的"共有分支(交集) / 所有分支(并集)"
        self._mr_last_common_branches = []
        self._mr_last_all_branches = []
        # 分支出现次数（branch -> count）和项目总数，用于下拉排序与显示
        self._mr_last_branch_counts = {}
        self._mr_last_total_projects = 0
        # 批量 commit 信息刷新的代际同步：用于"全部刷新完成后自动选中最热门提交"
        self._mr_commit_refresh_gen = 0
        self._mr_commit_refresh_target = 0
        self._mr_commit_refresh_done = 0
        self._mr_auto_select_max_pending = False

        # 批量创建分支：聚合分支 → 拥有该分支的 WorkspaceTab 列表
        self._cb_branch_projects = {}
        self._cb_total_projects = 0

        # 最近分支记录（跨 workspace，按 workspace 路径分组）
        self._recent_branch_store = RecentBranchStore()

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
            QListWidget::item:hover:!selected { background: #e6f0ff; }
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
        # 进入「创建 MR」面板时：确保用户列表已加载，并自动刷新一次分支
        if self.content_stack.widget(row) is self.create_mr_tab:
            self._ensure_mr_users_loaded()
            if not self._mr_branches_auto_loaded:
                self._mr_branches_auto_loaded = True
                # 确保表格行已就绪，再触发一次分支拉取
                if not self._mr_form_rows:
                    self._rebuild_mr_form_rows()
                self.run_batch_refresh_branches()

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
            # 容器：左侧名称（不可点）+ 右侧 ✕ 按钮（点击移除）
            chip = QFrame()
            chip.setFixedHeight(22)
            chip.setCursor(Qt.ArrowCursor)
            chip.setStyleSheet(
                'QFrame { background: #e3f2fd; color: #1565c0; border: 1px solid #90caf9; '
                'border-radius: 11px; }'
            )
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 0, 2, 0)
            chip_layout.setSpacing(4)
            name_label = QLabel(display)
            name_label.setStyleSheet('QLabel { background: transparent; border: none; color: #1565c0; font-size: 12px; }')
            name_label.setCursor(Qt.ArrowCursor)
            name_label.setToolTip(path)
            chip_layout.addWidget(name_label)
            close_btn = QPushButton('✕')
            close_btn.setFixedSize(16, 16)
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setToolTip(f'移除: {path}')
            close_btn.setStyleSheet(
                'QPushButton { background: transparent; border: none; color: #1565c0; '
                'font-size: 12px; padding: 0; }'
                'QPushButton:hover { color: #e74c3c; font-weight: bold; }'
            )
            close_btn.clicked.connect(lambda _checked=False, p=path: self._remove_project_via_chip(p))
            chip_layout.addWidget(close_btn)
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
        # 同步刷新「批量创建 MR」表格里的项目行
        self._rebuild_mr_form_rows()
        # 若用户已经加载过分支，自动重新刷新分支（恢复旧的源/目标选择）
        if self._mr_branches_auto_loaded:
            self.run_batch_refresh_branches()

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
        # 同步刷新「批量创建 MR」表格里的项目行
        self._rebuild_mr_form_rows()
        if self._mr_branches_auto_loaded:
            self.run_batch_refresh_branches()

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

    def reload_config(self, new_config, new_config_file=None):
        """配置文件切换后由 main_window 调用。"""
        self.config = new_config
        if new_config_file:
            self.config_file = new_config_file
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

        self.cb_checkout_recent_btn = QPushButton('切换到最近分支')
        self.cb_checkout_recent_btn.setMinimumHeight(38)
        self.cb_checkout_recent_btn.clicked.connect(self._open_recent_branch_menu)

        cb_btn_row = QHBoxLayout()
        cb_btn_row.addWidget(self.cb_create_button)
        cb_btn_row.addWidget(self.cb_checkout_recent_btn)
        cb_btn_row.addStretch()
        layout.addLayout(cb_btn_row)

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
                        # 记录到 recent_branches（每条成功分支独立写入）
                        full = new_branch + '__from__' + target.replace('/', '@')
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        try:
                            self._recent_branch_store.add(ws.path, full, ts)
                        except Exception:
                            pass
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

    def _open_recent_branch_menu(self) -> None:
        """两级菜单：顶层为 workspace 子菜单，每个子菜单列出该 workspace 的最近分支。"""
        import os
        workspaces = self._recent_branch_store.list_workspaces()
        if not workspaces:
            QMessageBox.information(self, '最近分支', '暂无最近创建的分支记录。')
            return
        menu = QMenu(self)
        for ws_path in workspaces:
            exists = os.path.isdir(ws_path)
            label = ws_path + ('' if exists else '  [路径缺失]')
            submenu = menu.addMenu(label)
            if not exists:
                submenu.setEnabled(False)
                continue
            entries = self._recent_branch_store.list_by_workspace(ws_path, limit=10)
            for e in entries:
                act = submenu.addAction(f'{e.branch}  ({e.created_at})')
                act.triggered.connect(
                    lambda _=False, p=ws_path, b=e.branch: self._checkout_recent_for(p, b)
                )
        menu.exec_(self.cb_checkout_recent_btn.mapToGlobal(
            self.cb_checkout_recent_btn.rect().bottomLeft()
        ))

    def _checkout_recent_for(self, workspace_path: str, branch: str) -> None:
        """对指定 workspace 执行 smart_checkout，并按状态弹出 QMessageBox。"""
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def _run():
            return smart_checkout_func(workspace_path, branch)

        def on_success(result):
            QApplication.restoreOverrideCursor()
            status, msg = result
            if status == 'ok':
                QMessageBox.information(self, '切换成功', msg)
            elif status == 'ok_stash':
                QMessageBox.warning(self, '已切换（已 stash）', msg)
            elif status == 'skip':
                QMessageBox.information(self, '提示', msg)
            else:
                QMessageBox.critical(self, '切换失败', msg)

        def on_error(err):
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, '切换异常', f'切换过程中发生错误: {err}')

        run_blocking(_run, on_success=on_success, on_error=on_error, parent=self)

    # ──────────────────────── 批量创建合并请求 ──────────────────────
    def init_create_mr_tab(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 状态：哪些行的源/目标分支是用户手动选择的，自动应用时跳过
        self._mr_form_manual_source = set()  # ws.path 集合
        self._mr_form_manual_target = set()
        self._applying_common_branches = False
        self._mr_branches_auto_loaded = False

        # 共享字段（GitLab / Token / 指派 / 审查者）
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

        shared_form.addRow('GitLab 地址:', self.gitlab_url_input)
        shared_form.addRow('私有 Token:', self.token_input)
        shared_form.addRow('指派给:', assignee_row)
        shared_form.addRow('审查者:', self.mr_reviewer_combo)
        layout.addLayout(shared_form)

        # 共有分支选择器 + 刷新按钮（放在标题模板上方）
        common_branches_layout = QHBoxLayout()
        common_branches_layout.setSpacing(12)
        # 切换"共有分支(交集)/所有分支(并集)"，默认勾选（显示并集）
        self.mr_show_all_branches_cb = QCheckBox('显示所有分支')
        self.mr_show_all_branches_cb.setToolTip(
            '勾选：下拉框显示所有项目的分支并集；不勾选：只显示所有项目共有的分支（交集）。'
        )
        # 先设默认状态，再连信号，避免初始化时无谓触发
        self.mr_show_all_branches_cb.setChecked(True)
        self.mr_show_all_branches_cb.toggled.connect(self._on_show_all_branches_toggled)
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
        # 选择即自动应用（手动选过的项目不会被覆盖）
        self.mr_common_source_combo.currentIndexChanged.connect(self._auto_apply_common_branches)
        self.mr_common_target_combo.currentIndexChanged.connect(self._auto_apply_common_branches)
        # 刷新所有项目分支按钮（移到这里，与单 MR 侧风格一致）
        self.mr_refresh_branches_btn = QPushButton('刷新所有项目分支')
        # 默认勾选"显示所有分支" -> 初始 label 即为"所有分支:"
        self.mr_branches_label = QLabel('所有分支:')
        self.mr_branches_label.setMinimumWidth(72)
        src_label = QLabel('源:')
        tgt_label = QLabel('目标:')
        # 布局：checkbox + 固定 label + [源: 拉伸] + [目标: 拉伸] + 刷新按钮
        common_branches_layout.addWidget(self.mr_show_all_branches_cb)
        common_branches_layout.addWidget(self.mr_branches_label)
        common_branches_layout.addWidget(src_label)
        common_branches_layout.addWidget(self.mr_common_source_combo, stretch=1)
        common_branches_layout.addWidget(tgt_label)
        common_branches_layout.addWidget(self.mr_common_target_combo, stretch=1)
        common_branches_layout.addWidget(self.mr_refresh_branches_btn)
        layout.addLayout(common_branches_layout)

        # 标题/描述模板（移到共有分支下方）
        self._title_template = self._get_gitlab_value('title_template', 'Draft: {commit_message}')
        self._desc_template = self._get_gitlab_value('description_template', '{commit_message}')

        tpl_form = QFormLayout()
        self.mr_title_input = QLineEdit()
        self.mr_title_input.setText(self._title_template)
        self.mr_title_input.setPlaceholderText('支持 {source} / {target} / {commit_message} 模板')
        self.mr_title_input.textChanged.connect(self._on_title_edited)
        self.mr_desc_input = QTextEdit()
        self.mr_desc_input.setText(self._desc_template)
        self.mr_desc_input.setPlaceholderText('支持 {source} / {target} / {commit_message} 模板')
        self.mr_desc_input.textChanged.connect(self._on_desc_edited)
        self.mr_desc_input.setMaximumHeight(80)
        tpl_form.addRow('标题模板:', self.mr_title_input)
        tpl_form.addRow('描述模板:', self.mr_desc_input)
        layout.addLayout(tpl_form)

        # 表格上方工具栏：勾选控制
        table_ops = QHBoxLayout()
        table_ops.setSpacing(6)
        self.mr_select_all_btn = QPushButton('全选')
        self.mr_select_none_btn = QPushButton('全不选')
        for b in (self.mr_select_all_btn, self.mr_select_none_btn):
            b.setFixedHeight(24)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                'QPushButton { background: white; border: 1px solid #d0d0d0; border-radius: 4px; '
                'padding: 0 12px; color: #444; }'
                'QPushButton:hover { background: #f0f7ff; border-color: #1677ff; color: #1677ff; }'
            )
            table_ops.addWidget(b)
        self.mr_row_count_label = QLabel('')
        self.mr_row_count_label.setStyleSheet('color: #888;')
        table_ops.addWidget(self.mr_row_count_label)
        table_ops.addStretch()

        # 快速选择：列出表格中所有提交信息去重后的数据，按出现次数排序
        self.mr_commit_quick_combo = NoWheelComboBox()
        self.mr_commit_quick_combo.setEditable(True)
        self.mr_commit_quick_combo.setMinimumWidth(280)
        self.mr_commit_quick_combo.addItem('(快速选择提交信息)')
        _enable_combo_search(self.mr_commit_quick_combo)
        self.mr_commit_quick_combo.currentIndexChanged.connect(self._on_commit_quick_selected)
        table_ops.addWidget(QLabel('快速选择:'))
        table_ops.addWidget(self.mr_commit_quick_combo, stretch=1)

        layout.addLayout(table_ops)

        # 每项目行表格
        self.mr_form_table = QTableWidget()
        self.mr_form_table.setColumnCount(7)
        self.mr_form_table.setHorizontalHeaderLabels(
            ['', '项目', '源分支', '目标分支', '最新提交', '提交时间', '状态']
        )
        self.mr_form_table.setAlternatingRowColors(True)
        self.mr_form_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mr_form_table.verticalHeader().setVisible(False)
        self.mr_form_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hdr = self.mr_form_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.mr_form_table.setColumnWidth(0, 36)
        self.mr_form_table.setColumnWidth(1, 160)
        self.mr_form_table.setColumnWidth(5, 160)
        self.mr_form_table.setColumnWidth(6, 220)
        layout.addWidget(self.mr_form_table, stretch=1)

        # 预览 + 批量创建按钮（移到表格下方）
        bottom_btns = QHBoxLayout()
        self.mr_preview_btn = QPushButton('预览 MR 内容')
        self.mr_preview_btn.setMinimumHeight(38)
        self.mr_preview_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; color: #444; border: 1px solid #d0d0d0; '
            'border-radius: 4px; font-weight: bold; font-size: 13px; padding: 0 18px; }'
            'QPushButton:hover { background: #e6f0ff; border-color: #1677ff; color: #1677ff; }'
            'QPushButton:disabled { background: #bdc3c7; color: white; border: none; }'
        )
        bottom_btns.addWidget(self.mr_preview_btn)
        bottom_btns.addStretch()
        self.mr_create_btn = QPushButton('批量创建合并请求')
        self.mr_create_btn.setMinimumHeight(38)
        self.mr_create_btn.setStyleSheet(
            'QPushButton { background: #27ae60; color: white; border: none; '
            'border-radius: 4px; font-weight: bold; font-size: 13px; }'
            'QPushButton:hover { background: #2ecc71; }'
            'QPushButton:disabled { background: #bdc3c7; }'
        )
        bottom_btns.addWidget(self.mr_create_btn)
        layout.addLayout(bottom_btns)

        self.mr_refresh_branches_btn.clicked.connect(self.run_batch_refresh_branches)
        self.mr_create_btn.clicked.connect(self.run_batch_create_mr)
        self.mr_preview_btn.clicked.connect(self.run_preview_mr_content)
        self.mr_refresh_users_button.clicked.connect(self.run_refresh_users)
        self.mr_select_all_btn.clicked.connect(lambda: self._set_all_mr_rows_checked(True))
        self.mr_select_none_btn.clicked.connect(lambda: self._set_all_mr_rows_checked(False))

        # 自动加载用户列表，并在加载完成后应用 config 中保存的默认值
        self._load_users_into_combos_async()

        self.create_mr_tab.setLayout(layout)

    def _rebuild_mr_form_rows(self):
        """根据当前勾选项目，重建「批量创建 MR」表格行。保留已选分支（按 path+branch 缓存）。"""
        selected = self._selected_workspace_tabs()
        # 保留旧选择（path -> (source_text, target_text)），refresh 完成后重新应用
        old_cache = {}
        old_check_state = {}
        for row_data in self._mr_form_rows:
            ws, cb, src_combo, tgt_combo, _msg, _time, _status = row_data
            try:
                old_cache[ws.path] = (src_combo.currentText(), tgt_combo.currentText())
                old_check_state[ws.path] = cb.isChecked()
            except Exception:
                pass
        self._mr_form_old_cache = old_cache

        # 先清空再重建：避免 setRowCount 收缩时 cell widget 残留导致行数不对
        self.mr_form_table.setRowCount(0)
        self._mr_form_rows = []

        for row, ws in enumerate(selected):
            self.mr_form_table.insertRow(row)

            # 复选框（默认勾选；重建时尽量保留之前的状态）
            cb = QCheckBox()
            cb.toggled.connect(lambda _v=False: self._refresh_mr_row_count())
            cb_container = QWidget()
            cb_layout = QHBoxLayout(cb_container)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.addWidget(cb)
            self.mr_form_table.setCellWidget(row, 0, cb_container)
            # setChecked 放到 addCellWidget 之后，避免信号在挂上控件前触发
            cb.setChecked(old_check_state.get(ws.path, True))

            name_item = QTableWidgetItem(ws.workspace_name or ws.path)
            self.mr_form_table.setItem(row, 1, name_item)

            src_combo = NoWheelComboBox()
            tgt_combo = NoWheelComboBox()
            _enable_combo_search(src_combo)
            _enable_combo_search(tgt_combo)
            src_combo.addItems(['(待刷新)'])
            tgt_combo.addItems(['(待刷新)'])
            # 分支切换：标记手动选择 + 实时更新 commit 信息
            src_combo.currentIndexChanged.connect(lambda _i, w=ws: self._on_row_src_changed(w))
            tgt_combo.currentIndexChanged.connect(lambda _i, w=ws: self._on_row_tgt_changed(w))

            commit_msg_label = QLabel('—')
            commit_msg_label.setStyleSheet('color: #888;')
            commit_msg_label.setWordWrap(False)
            commit_time_label = QLabel('—')
            commit_time_label.setStyleSheet('color: #888;')

            status_label = QLabel('—')
            status_label.setAlignment(Qt.AlignCenter)

            self.mr_form_table.setCellWidget(row, 2, src_combo)
            self.mr_form_table.setCellWidget(row, 3, tgt_combo)
            self.mr_form_table.setCellWidget(row, 4, commit_msg_label)
            self.mr_form_table.setCellWidget(row, 5, commit_time_label)
            self.mr_form_table.setCellWidget(row, 6, status_label)
            self._mr_form_rows.append(
                (ws, cb, src_combo, tgt_combo, commit_msg_label, commit_time_label, status_label)
            )

        self._refresh_mr_row_count()

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
        self._title_template = text
        self._set_gitlab_value('title_template', text)

    def _on_desc_edited(self):
        text = self.mr_desc_input.toPlainText()
        self._desc_template = text
        self._set_gitlab_value('description_template', text)

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
        for combo in (self.mr_list_assignee_combo, self.mr_list_reviewer_combo):
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
        for row_data in rows:
            row_data[6].setText('加载中...')

        # 取出旧选择缓存，供 on_success 重新应用
        old_cache = getattr(self, '_mr_form_old_cache', {}) or {}

        def _run():
            results = []
            all_branch_sets = []
            branch_counter = {}  # branch -> 出现的项目数
            for row_data in rows:
                ws = row_data[0]
                try:
                    # 远程分支作为源和目标候选
                    branches, msg = _safe_get_remote_branches(ws.path)
                    results.append((ws, branches, None))
                    branch_set = set(branches)
                    all_branch_sets.append(branch_set)
                    for b in branch_set:
                        branch_counter[b] = branch_counter.get(b, 0) + 1
                except Exception as e:
                    results.append((ws, [], str(e)))
                    all_branch_sets.append(set())
            # 计算共有分支（交集）和所有分支（并集）
            common_branches = set()
            all_branches_union = set()
            if all_branch_sets:
                common_branches = set(all_branch_sets[0])
                all_branches_union = set(all_branch_sets[0])
                for branch_set in all_branch_sets[1:]:
                    common_branches &= branch_set
                    all_branches_union |= branch_set
            total_projects = len(all_branch_sets)
            return (
                results,
                sorted(common_branches),
                sorted(all_branches_union),
                branch_counter,
                total_projects,
            )

        def on_success(payload):
            results, common_branches, all_branches_union, branch_counter, total_projects = payload
            self.mr_refresh_branches_btn.setEnabled(True)
            # 缓存两种列表 + 出现次数，并填充下拉
            self._mr_last_common_branches = common_branches
            self._mr_last_all_branches = all_branches_union
            self._mr_last_branch_counts = branch_counter
            self._mr_last_total_projects = total_projects
            self._populate_common_branch_combos()

            self._applying_common_branches = True
            # 用 path -> result 映射，按当前 _mr_form_rows 迭代，避免使用已被销毁的旧控件
            result_by_path = {r[0].path: r for r in results}
            for row_data in self._mr_form_rows:
                ws, _cb, src, _t, _msg, _time, status = row_data
                result = result_by_path.get(ws.path)
                if result is None:
                    continue
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
                # 优先恢复旧选择；否则默认选 config 里第一个 target_branch
                old_src, old_tgt = old_cache.get(ws.path, ('', ''))
                # 程序化设置，不算用户手动
                apply_src_idx = -1
                apply_tgt_idx = -1
                if old_src and old_src != '(待刷新)':
                    apply_src_idx = src.findText(old_src)
                if old_tgt and old_tgt != '(待刷新)':
                    apply_tgt_idx = _t.findText(old_tgt)
                else:
                    targets = self._read_target_branches_for(ws)
                    if targets:
                        apply_tgt_idx = _t.findText(targets[0])
                if apply_src_idx >= 0:
                    src.setCurrentIndex(apply_src_idx)
                if apply_tgt_idx >= 0:
                    _t.setCurrentIndex(apply_tgt_idx)
                src.blockSignals(False)
                _t.blockSignals(False)
                status.setText('就绪')
                status.setStyleSheet('color: #27ae60;')
            self._applying_common_branches = False

            # 旧缓存已应用，清空避免下次误用
            self._mr_form_old_cache = {}

            # 拉取每行的 commit 信息，并自动选中最大次数的提交
            self._trigger_all_rows_commit_refresh(auto_select_max=True)

        run_blocking(_run, on_success=on_success, parent=self)

    def _on_row_src_changed(self, ws):
        """单行源分支被切换：记录手动选择（除非正在自动应用共有分支），并刷新 commit 信息。"""
        if not self._applying_common_branches:
            self._mr_form_manual_source.add(ws.path)
        # 拉取最新 commit 信息
        for row_data in self._mr_form_rows:
            if row_data[0] is ws:
                self._update_commit_info_for_row(row_data)
                break

    def _on_row_tgt_changed(self, ws):
        """单行目标分支被切换：记录手动选择（除非正在自动应用共有分支）。"""
        if not self._applying_common_branches:
            self._mr_form_manual_target.add(ws.path)

    def _update_commit_info_for_row(self, row_data, batch_gen=None):
        """异步获取源分支最新 commit 主题与时间，更新到对应行的标签。

        batch_gen: 批量刷新代际号；非 None 时表示是批量流程的一部分，
                    完成后会参与代际计数，所有完成后会触发自动选中最大提交。
        """
        ws, _cb, src_combo, _tgt, msg_label, time_label, _status = row_data
        source = src_combo.currentText().strip()
        if not source or source == '(待刷新)':
            msg_label.setText('—')
            msg_label.setToolTip('')
            msg_label.setStyleSheet('color: #888;')
            time_label.setText('—')
            time_label.setStyleSheet('color: #888;')
            self._on_row_commit_done(batch_gen)
            return

        msg_label.setText('加载中...')
        msg_label.setStyleSheet('color: #888;')

        def _run():
            from quick_create_branch import run_command
            commit_subject = ''
            commit_time = ''
            try:
                ok, stdout, _stderr = run_command(
                    ['git', 'log', source, '-1', '--pretty=%s%n%ci'], ws.path
                )
                if ok:
                    lines = stdout.strip().split('\n', 1)
                    commit_subject = lines[0] if lines else ''
                    commit_time = lines[1].strip() if len(lines) > 1 else ''
            except Exception:
                pass
            return commit_subject, commit_time

        def on_success(result):
            commit_subject, commit_time = result
            if commit_subject:
                msg_label.setText(commit_subject)
                msg_label.setToolTip(commit_subject)
                msg_label.setStyleSheet('color: #333;')
            else:
                msg_label.setText('（无）')
                msg_label.setToolTip('')
                msg_label.setStyleSheet('color: #999;')
            if commit_time:
                time_label.setText(commit_time[:19].replace('T', ' '))
                time_label.setToolTip(commit_time)
                time_label.setStyleSheet('color: #333;')
            else:
                time_label.setText('—')
                time_label.setStyleSheet('color: #888;')
            self._on_row_commit_done(batch_gen)

        run_blocking(_run, on_success=on_success, parent=self)

    def _trigger_all_rows_commit_refresh(self, auto_select_max=False):
        """对表格所有行触发 commit 信息刷新，可选全部完成后自动选中最大提交。"""
        rows = list(self._mr_form_rows)
        if not rows:
            self._refresh_commit_quick_combo()
            if auto_select_max:
                self._auto_select_max_commit()
            return
        self._mr_commit_refresh_gen += 1
        gen = self._mr_commit_refresh_gen
        self._mr_commit_refresh_target = len(rows)
        self._mr_commit_refresh_done = 0
        self._mr_auto_select_max_pending = auto_select_max
        for row_data in rows:
            self._update_commit_info_for_row(row_data, batch_gen=gen)

    def _on_row_commit_done(self, batch_gen=None):
        """单行 commit 信息刷新完成：刷新快速选择下拉，必要时触发自动选中。"""
        self._refresh_commit_quick_combo()
        if batch_gen is None or batch_gen != self._mr_commit_refresh_gen:
            return
        self._mr_commit_refresh_done += 1
        if (self._mr_commit_refresh_done >= self._mr_commit_refresh_target
                and self._mr_auto_select_max_pending):
            self._mr_auto_select_max_pending = False
            self._auto_select_max_commit()

    def _refresh_commit_quick_combo(self):
        """根据表格各行的提交信息重建快速选择下拉，按出现次数倒序排序。"""
        counter = {}
        for row_data in self._mr_form_rows:
            msg_label = row_data[4]
            text = msg_label.text().strip()
            if not text or text in ('—', '（无）', '加载中...'):
                continue
            counter[text] = counter.get(text, 0) + 1
        if not counter:
            sorted_items = []
        else:
            sorted_items = sorted(counter.items(), key=lambda x: (-x[1], x[0]))

        current_data = self.mr_commit_quick_combo.currentData()
        self.mr_commit_quick_combo.blockSignals(True)
        self.mr_commit_quick_combo.clear()
        self.mr_commit_quick_combo.addItem('(快速选择提交信息)', userData=None)
        for msg, count in sorted_items:
            label = msg if count == 1 else f'{msg}  ({count})'
            self.mr_commit_quick_combo.addItem(label, userData=msg)
        # 恢复之前的选择（按 userData 比对原始提交信息）
        restore_idx = -1
        if current_data:
            for i in range(self.mr_commit_quick_combo.count()):
                if self.mr_commit_quick_combo.itemData(i) == current_data:
                    restore_idx = i
                    break
        if restore_idx >= 0:
            self.mr_commit_quick_combo.setCurrentIndex(restore_idx)
        else:
            self.mr_commit_quick_combo.setCurrentIndex(0)
        self.mr_commit_quick_combo.blockSignals(False)

    def _on_commit_quick_selected(self, _idx=None):
        """选择某条提交信息后：匹配行勾选，其余行取消勾选（替换式选择）。"""
        idx = self.mr_commit_quick_combo.currentIndex()
        if idx <= 0:
            return
        target_msg = self.mr_commit_quick_combo.itemData(idx)
        if not target_msg:
            return
        for row_data in self._mr_form_rows:
            cb = row_data[1]
            should_check = row_data[4].text().strip() == target_msg
            if cb.isChecked() != should_check:
                cb.setChecked(should_check)
        self._refresh_mr_row_count()

    def _auto_select_max_commit(self):
        """自动选中"出现次数最多"的提交信息，并勾选对应项目。"""
        if self.mr_commit_quick_combo.count() <= 1:
            return
        self.mr_commit_quick_combo.blockSignals(True)
        self.mr_commit_quick_combo.setCurrentIndex(1)  # 占位之后第 1 项即最大次数
        self.mr_commit_quick_combo.blockSignals(False)
        # blockSignals 后手动触发一次勾选
        self._on_commit_quick_selected()

    def _set_all_mr_rows_checked(self, checked):
        for row_data in self._mr_form_rows:
            row_data[1].setChecked(checked)
        self._refresh_mr_row_count()

    def _refresh_mr_row_count(self):
        total = len(self._mr_form_rows)
        if total == 0:
            self.mr_row_count_label.setText('')
            return
        checked = sum(1 for r in self._mr_form_rows if r[1].isChecked())
        self.mr_row_count_label.setText(f'{checked} / {total} 行将创建')

    def _on_show_all_branches_toggled(self, _checked):
        """切换"显示所有分支"：更新 label 文案并刷新下拉候选。"""
        show_all = self.mr_show_all_branches_cb.isChecked()
        self.mr_branches_label.setText('所有分支:' if show_all else '共有分支:')
        self._populate_common_branch_combos()

    def _populate_common_branch_combos(self):
        """依据复选框状态填充共有/所有分支下拉。

        排序：按"分支出现的项目数"倒序（次数多的放上面），次数相同的按名字升序。
        显示：次数 < 总项目数时，追加 "(count/total)" 后缀；次数 = 总项目数时不追加。
        userData 始终为原始分支名，便于按名查找与恢复选择。
        """
        show_all = self.mr_show_all_branches_cb.isChecked()
        branches = self._mr_last_all_branches if show_all else self._mr_last_common_branches
        counts = self._mr_last_branch_counts or {}
        total = self._mr_last_total_projects or 0
        # 排序：(出现次数倒序, 分支名升序)
        sorted_branches = sorted(branches, key=lambda b: (-(counts.get(b, 0)), b))

        def _make_label(b):
            c = counts.get(b, 0)
            if total and c and c < total:
                return f'{b}  ({c}/{total})'
            return b

        # 保留当前选择（按 userData 比对原始分支名）
        prev_src = self.mr_common_source_combo.currentData()
        prev_tgt = self.mr_common_target_combo.currentData()
        self.mr_common_source_combo.blockSignals(True)
        self.mr_common_target_combo.blockSignals(True)
        self.mr_common_source_combo.clear()
        self.mr_common_target_combo.clear()
        for b in sorted_branches:
            self.mr_common_source_combo.addItem(_make_label(b), userData=b)
            self.mr_common_target_combo.addItem(_make_label(b), userData=b)
        for combo, prev in (
            (self.mr_common_source_combo, prev_src),
            (self.mr_common_target_combo, prev_tgt),
        ):
            if prev:
                idx = combo.findData(prev)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self.mr_common_source_combo.blockSignals(False)
        self.mr_common_target_combo.blockSignals(False)

    def _auto_apply_common_branches(self):
        """共有分支变化时自动应用到所有项目行（手动选过的项目除外）。"""
        source = (self.mr_common_source_combo.currentData() or '').strip()
        # 源分支带 __from__ 标记时，自动解析并设置共有目标分支
        parsed_target = parse_target_branch_from_source(source) if source else None
        if parsed_target:
            idx = self.mr_common_target_combo.findData(parsed_target)
            if idx >= 0 and self.mr_common_target_combo.currentIndex() != idx:
                self.mr_common_target_combo.blockSignals(True)
                self.mr_common_target_combo.setCurrentIndex(idx)
                self.mr_common_target_combo.blockSignals(False)
        target = (self.mr_common_target_combo.currentData() or '').strip()
        if not source and not target:
            return
        self._applying_common_branches = True
        for row_data in self._mr_form_rows:
            ws, _cb, src_combo, tgt_combo, _msg, _time, status = row_data
            if source and ws.path not in self._mr_form_manual_source:
                idx = src_combo.findText(source)
                if idx >= 0 and src_combo.currentIndex() != idx:
                    src_combo.setCurrentIndex(idx)
            if target and ws.path not in self._mr_form_manual_target:
                idx = tgt_combo.findText(target)
                if idx >= 0 and tgt_combo.currentIndex() != idx:
                    tgt_combo.setCurrentIndex(idx)
            status.setText('已应用默认分支')
            status.setStyleSheet('color: #1677ff;')
        self._applying_common_branches = False
        # 共有分支变化后，重新拉取每行 commit 信息，并自动选中最大次数的提交
        self._trigger_all_rows_commit_refresh(auto_select_max=True)

    def run_preview_mr_content(self):
        """弹出对话框，展示每个项目最终生成的 MR 标题/描述。"""
        if not self._mr_form_rows:
            QMessageBox.information(self, '提示', '请先点「刷新所有项目分支」。')
            return
        title_tpl = self._title_template or '{source}'
        desc_tpl = self._desc_template
        url = self.gitlab_url_input.text().strip()
        token = self.token_input.text().strip()
        assignee = self.mr_assignee_combo.currentText().strip()
        reviewer = self.mr_reviewer_combo.currentText().strip()

        # 双重过滤：行复选框勾选 + 顶部「参与项目」勾选（防止 _mr_form_rows 缓存陈旧）
        participating_paths = {w.path for w in self._selected_workspace_tabs()}
        rows_data = [
            r for r in self._mr_form_rows
            if r[1].isChecked() and r[0].path in participating_paths
        ]
        if not rows_data:
            QMessageBox.information(self, '提示', '请至少勾选一个项目行（左侧复选框）。')
            return

        self.mr_preview_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def _run():
            from quick_create_branch import run_command
            results = []
            for row_data in rows_data:
                ws, _cb, src, tgt, _msg, _time, _status = row_data
                source = src.currentText().strip()
                target = tgt.currentText().strip()
                if source in ('', '(待刷新)') or target in ('', '(待刷新)'):
                    results.append({
                        'project': ws.workspace_name or ws.path,
                        'source': source, 'target': target,
                        'title': '（跳过：未选分支）', 'desc': '', 'skipped': True,
                    })
                    continue
                commit_message = ''
                try:
                    ok, stdout, stderr = run_command(
                        ['git', 'log', source, '-1', '--pretty=%B'], ws.path
                    )
                    if ok:
                        commit_message = stdout.strip()
                except Exception:
                    pass
                title = truncate_mr_title(_safe_format(
                    title_tpl, source=source, target=target,
                    tab_name=ws.workspace_name or '', commit_message=commit_message,
                ))
                tg_number_from_title = _extract_tg_number_from_title(title)
                desc = _safe_format(
                    desc_tpl, source=source, target=target,
                    tab_name=ws.workspace_name or '', commit_message=commit_message,
                    tg_number_from_title=tg_number_from_title,
                )
                results.append({
                    'project': ws.workspace_name or ws.path,
                    'source': source, 'target': target,
                    'title': title, 'desc': desc, 'skipped': False,
                })
            return results

        def on_success(results):
            self.mr_preview_btn.setEnabled(True)
            QApplication.restoreOverrideCursor()
            self._show_mr_preview_dialog(results, assignee, reviewer, url)

        def on_error(_exc):
            self.mr_preview_btn.setEnabled(True)
            QApplication.restoreOverrideCursor()

        run_blocking(_run, on_success=on_success, on_error=on_error, parent=self)

    def _show_mr_preview_dialog(self, results, assignee, reviewer, url):
        """渲染预览对话框：左侧项目列表，右侧标题/描述。"""
        dialog = QDialog(self)
        dialog.setWindowTitle('MR 内容预览')
        dialog.resize(900, 600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        meta = QLabel(
            f'GitLab: {url or "<未填>"}    '
            f'指派给: {assignee or "<未选>"}    '
            f'审查者: {reviewer or "<未选>"}'
        )
        meta.setStyleSheet('color: #666;')
        layout.addWidget(meta)

        splitter_layout = QHBoxLayout()
        splitter_layout.setSpacing(8)

        project_list = QListWidget()
        project_list.setMaximumWidth(220)
        for i, r in enumerate(results):
            label = r['project']
            if r.get('skipped'):
                label += '（跳过）'
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            if r.get('skipped'):
                item.setForeground(QColor('#999'))
            project_list.addItem(item)
        splitter_layout.addWidget(project_list)

        right_box = QVBoxLayout()
        right_box.setSpacing(6)
        title_label = QLabel('<b>标题:</b>')
        title_value = QLabel('')
        title_value.setWordWrap(True)
        title_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_value.setStyleSheet(
            'QLabel { background: #f8f9fa; border: 1px solid #e9ecef;'
            ' border-radius: 4px; padding: 8px; }'
        )
        title_value.setMinimumHeight(36)
        title_value.setAlignment(Qt.AlignTop)
        desc_label = QLabel('<b>描述:</b>')
        desc_value = QTextEdit()
        desc_value.setReadOnly(True)
        right_box.addWidget(title_label)
        right_box.addWidget(title_value, stretch=0)
        right_box.addWidget(desc_label)
        right_box.addStretch(0)
        right_box.addWidget(desc_value, stretch=1)
        splitter_layout.addLayout(right_box, stretch=1)
        layout.addLayout(splitter_layout, stretch=1)

        def _show(idx):
            if not 0 <= idx < len(results):
                return
            r = results[idx]
            title_value.setText(r['title'])
            desc_value.setPlainText(r['desc'])

        project_list.currentRowChanged.connect(_show)
        if results:
            project_list.setCurrentRow(0)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton('关闭')
        close_btn.setMinimumHeight(32)
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec_()

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
        # 用模板原文（_title_template / _desc_template），不是预览替换后的输入框文本，
        # 这样 {commit_message} 等占位符才能在创建时用真实 git log 替换。
        title_tpl = self._title_template or '{source}'
        desc_tpl = self._desc_template

        tasks = []
        participating_paths = {w.path for w in self._selected_workspace_tabs()}
        for row_data in self._mr_form_rows:
            ws, cb, src, tgt, _msg, _time, status = row_data
            if not cb.isChecked():
                status.setText('跳过：未勾选')
                status.setStyleSheet('color: #888;')
                continue
            if ws.path not in participating_paths:
                status.setText('跳过：未参与')
                status.setStyleSheet('color: #888;')
                continue
            source = src.currentText().strip()
            target = tgt.currentText().strip()
            if source in ('', '(待刷新)') or target in ('', '(待刷新)'):
                status.setText('跳过：未选分支')
                status.setStyleSheet('color: #888;')
                continue
            tasks.append((ws, source, target, status))

        if not tasks:
            QMessageBox.information(self, '提示', '所有项目都未勾选或未选源/目标分支，无可执行任务。')
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
                    ok, stdout, _stderr = run_command(
                        ['git', 'log', source, '-1', '--pretty=%B'], ws.path
                    )
                    if ok:
                        commit_message = stdout.strip()
                except Exception:
                    pass

                title = _safe_format(
                    title_tpl, source=source, target=target,
                    tab_name=ws.workspace_name or '', commit_message=commit_message,
                )
                tg_number_from_title = _extract_tg_number_from_title(title)
                desc = _safe_format(
                    desc_tpl, source=source, target=target,
                    tab_name=ws.workspace_name or '', commit_message=commit_message,
                    tg_number_from_title=tg_number_from_title,
                )
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
                for row_data in self._mr_form_rows:
                    _w, _cb, _s, _t, _msg_lbl, _time_lbl, status = row_data
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
        """批量删除选中的分支，带实时进度的对话框（跨多项目）。"""
        selected_idxs = [idx for cb, idx in self._branch_mgmt_checkboxes if cb.isChecked()]
        if not selected_idxs:
            QMessageBox.information(self, '提示', '请先勾选要删除的分支。')
            return

        is_remote = self._branch_mgmt_mode == 'remote'
        branch_type = '远程' if is_remote else '本地'
        rows = [self._branch_mgmt_all_data[i] for i in selected_idxs]

        # ── 构建对话框 ──
        dialog = QDialog(self)
        dialog.setWindowTitle(f'批量删除{branch_type}分支')
        dialog.setMinimumWidth(650)
        dialog.setMinimumHeight(300)
        dialog.setMaximumHeight(650)
        dialog._deleting = False

        def close_event(event):
            if dialog._deleting:
                event.ignore()
            else:
                event.accept()

        dialog.closeEvent = close_event

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setSpacing(10)

        title_label = QLabel(f'确认删除以下 {len(rows)} 个{branch_type}分支？')
        title_label.setWordWrap(True)
        title_label.setStyleSheet('font-weight: bold; font-size: 13px;')
        dlg_layout.addWidget(title_label)

        progress_label = QLabel('')
        progress_label.setStyleSheet('color: #7f8c8d; font-size: 12px;')
        progress_label.setVisible(False)
        dlg_layout.addWidget(progress_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: 1px solid #ddd; border-radius: 4px; }')

        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.setSpacing(3)

        row_widgets = []
        for r in rows:
            row = QHBoxLayout()
            row.setSpacing(6)

            status_label = QLabel('  ')
            status_label.setFixedWidth(20)
            status_label.setAlignment(Qt.AlignCenter)

            proj_label = QLabel(r['project'])
            proj_label.setStyleSheet('color: #1677ff; font-size: 11px;')
            proj_label.setMinimumWidth(120)
            proj_label.setMaximumWidth(180)

            name_label = QLabel(r['branch'])
            name_label.setStyleSheet('font-weight: bold; font-size: 12px;')
            name_label.setMinimumWidth(160)

            subject = r.get('subject', '')
            subject_display = subject[:30] + '...' if len(subject) > 30 else subject
            date_str = r.get('date', '')[:10]
            info_label = QLabel(f'{subject_display}  ({date_str})')
            info_label.setStyleSheet('color: #7f8c8d; font-size: 11px;')

            row.addWidget(status_label)
            row.addWidget(proj_label)
            row.addWidget(name_label)
            row.addWidget(info_label)
            row.addStretch()
            list_layout.addLayout(row)
            row_widgets.append((status_label, proj_label, name_label, r))

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        dlg_layout.addWidget(scroll)

        # ── 按钮栏 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        confirm_buttons = []
        if is_remote:
            confirm_btn = QPushButton(f'确认删除 ({len(rows)} 个)')
            confirm_btn.setStyleSheet(
                'QPushButton { background: #e74c3c; color: white; border: none;'
                ' border-radius: 4px; padding: 8px 16px; font-weight: bold; }'
                'QPushButton:hover { background: #c0392b; }'
            )
            confirm_buttons.append(('force', confirm_btn))
        else:
            safe_btn = QPushButton(f'安全删除 ({len(rows)} 个)')
            safe_btn.setStyleSheet(
                'QPushButton { background: #f39c12; color: white; border: none;'
                ' border-radius: 4px; padding: 8px 16px; font-weight: bold; }'
                'QPushButton:hover { background: #e67e22; }'
            )
            force_btn = QPushButton(f'强制删除 ({len(rows)} 个)')
            force_btn.setStyleSheet(
                'QPushButton { background: #e74c3c; color: white; border: none;'
                ' border-radius: 4px; padding: 8px 16px; font-weight: bold; }'
                'QPushButton:hover { background: #c0392b; }'
            )
            confirm_buttons.append(('safe', safe_btn))
            confirm_buttons.append(('force', force_btn))

        cancel_confirm_btn = QPushButton('取消')
        cancel_confirm_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; border: 1px solid #ddd;'
            ' border-radius: 4px; padding: 8px 16px; color: #555; }'
            'QPushButton:hover { background: #e8e8e8; }'
        )

        for _, btn in confirm_buttons:
            btn_layout.addWidget(btn)
        btn_layout.addWidget(cancel_confirm_btn)

        cancel_delete_btn = QPushButton('取消删除')
        cancel_delete_btn.setStyleSheet(
            'QPushButton { background: #7f8c8d; color: white; border: none;'
            ' border-radius: 4px; padding: 8px 16px; font-weight: bold; }'
            'QPushButton:hover { background: #636e72; }'
        )
        cancel_delete_btn.setVisible(False)

        close_btn = QPushButton('关闭')
        close_btn.setStyleSheet(
            'QPushButton { background: #f5f5f5; border: 1px solid #ddd;'
            ' border-radius: 4px; padding: 8px 16px; color: #555; }'
            'QPushButton:hover { background: #e8e8e8; }'
        )
        close_btn.setVisible(False)

        btn_layout.addWidget(cancel_delete_btn)
        btn_layout.addWidget(close_btn)
        dlg_layout.addLayout(btn_layout)

        # ── 交互逻辑 ──
        delete_mode = {'action': 'cancel'}
        cancel_flag = [False]

        def start_delete(action):
            delete_mode['action'] = action
            for _, btn in confirm_buttons:
                btn.setVisible(False)
            cancel_confirm_btn.setVisible(False)
            cancel_delete_btn.setVisible(True)
            cancel_delete_btn.setEnabled(True)
            cancel_delete_btn.setText('取消删除')
            title_label.setText(f'正在批量删除{branch_type}分支...')
            title_label.setStyleSheet('font-weight: bold; font-size: 13px; color: #f39c12;')
            progress_label.setVisible(True)
            dialog._deleting = True
            self.bm_delete_btn.setEnabled(False)
            self.bm_status.setText(f'正在删除 {len(rows)} 个分支...')

            flag = '-D' if action == 'force' else '-d'
            total = len(row_widgets)
            counters = {'deleted': 0, 'failed': 0, 'index': 0}
            failed_list = []

            def delete_next():
                i = counters['index']
                if i >= total or cancel_flag[0]:
                    finish_delete()
                    return

                status_lbl, _proj_lbl, name_lbl, r = row_widgets[i]
                name = r['branch']
                directory = r['ws'].path

                progress_label.setText(
                    f'正在处理 {i + 1}/{total}: [{r["project"]}] {name}'
                )
                name_lbl.setStyleSheet(
                    'font-weight: bold; font-size: 12px; color: #f39c12;'
                )

                if is_remote:
                    cmd = ['git', 'push', 'origin', '--delete', name]
                else:
                    cmd = ['git', 'branch', flag, name]

                def _run_delete():
                    import subprocess
                    try:
                        result_del = subprocess.run(
                            cmd, cwd=directory, capture_output=True, text=True,
                            encoding='utf-8', errors='replace'
                        )
                        if result_del.returncode == 0:
                            return True, None
                        return False, result_del.stderr.strip()[:200]
                    except Exception as e:
                        return False, str(e)[:200]

                def on_branch_done(result):
                    success, error = result
                    if success:
                        status_lbl.setText('✓')
                        status_lbl.setStyleSheet(
                            'color: #27ae60; font-size: 14px; font-weight: bold;'
                        )
                        name_lbl.setStyleSheet(
                            'font-weight: bold; font-size: 12px; color: #27ae60;'
                            ' text-decoration: line-through;'
                        )
                        counters['deleted'] += 1
                    else:
                        status_lbl.setText('✗')
                        status_lbl.setStyleSheet(
                            'color: #e74c3c; font-size: 14px; font-weight: bold;'
                        )
                        name_lbl.setStyleSheet(
                            'font-weight: bold; font-size: 12px; color: #e74c3c;'
                        )
                        if error:
                            name_lbl.setToolTip(error)
                        failed_list.append({
                            'project': r['project'],
                            'error': f"删除 {name} 失败: {error or '未知错误'}"
                        })
                        counters['failed'] += 1

                    counters['index'] += 1
                    delete_next()

                def on_branch_error(err):
                    status_lbl.setText('✗')
                    status_lbl.setStyleSheet(
                        'color: #e74c3c; font-size: 14px; font-weight: bold;'
                    )
                    name_lbl.setStyleSheet(
                        'font-weight: bold; font-size: 12px; color: #e74c3c;'
                    )
                    name_lbl.setToolTip(str(err)[:200])
                    failed_list.append({
                        'project': r['project'],
                        'error': f'删除 {name} 异常: {err}'
                    })
                    counters['failed'] += 1
                    counters['index'] += 1
                    delete_next()

                run_blocking(
                    _run_delete, on_success=on_branch_done,
                    on_error=on_branch_error, parent=self
                )

            def finish_delete():
                for j in range(counters['index'], total):
                    s, _p, _n, _r = row_widgets[j]
                    s.setText('–')
                    s.setStyleSheet('color: #7f8c8d; font-size: 14px;')

                dialog._deleting = False
                cancel_delete_btn.setVisible(False)

                deleted_count = counters['deleted']
                failed_count = counters['failed']
                skipped = total - deleted_count - failed_count

                summary_parts = []
                if deleted_count:
                    summary_parts.append(f'✓ 成功 {deleted_count}')
                if failed_count:
                    summary_parts.append(f'✗ 失败 {failed_count}')
                if skipped:
                    summary_parts.append(f'– 跳过 {skipped}')
                summary_text = '  '.join(summary_parts)

                if failed_count or skipped:
                    title_label.setText(f'删除完成 — {summary_text}')
                    title_label.setStyleSheet(
                        'font-weight: bold; font-size: 13px; color: #e74c3c;'
                    )
                else:
                    title_label.setText(
                        f'删除完成 — 全部成功 ({deleted_count} 个)'
                    )
                    title_label.setStyleSheet(
                        'font-weight: bold; font-size: 13px; color: #27ae60;'
                    )

                progress_label.setText('')
                close_btn.setVisible(True)

                self.bm_delete_btn.setEnabled(True)
                self.bm_status.setText(
                    f'删除完成：成功 {deleted_count} / 失败 {failed_count}'
                    + (f' / 跳过 {skipped}' if skipped else '')
                )
                self._report_failures(failed_list, '批量删除分支')
                self.run_bm_refresh()

            delete_next()

        def on_cancel_confirm():
            dialog.reject()

        def on_cancel_delete():
            cancel_delete_btn.setEnabled(False)
            cancel_delete_btn.setText('正在取消...')
            cancel_flag[0] = True

        def on_close():
            dialog.accept()

        for action, btn in confirm_buttons:
            btn.clicked.connect(lambda checked=False, a=action: start_delete(a))
        cancel_confirm_btn.clicked.connect(on_cancel_confirm)
        cancel_delete_btn.clicked.connect(on_cancel_delete)
        close_btn.clicked.connect(on_close)

        dialog.exec_()

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

        self.mr_list_assignee_combo = NoWheelComboBox()
        self.mr_list_reviewer_combo = NoWheelComboBox()
        for combo in (self.mr_list_assignee_combo, self.mr_list_reviewer_combo):
            combo.addItem('(全部)')
            combo.setMinimumWidth(120)
            _enable_combo_search(combo)

        top.addWidget(QLabel('状态:'))
        top.addWidget(self.mr_list_state_combo)
        top.addWidget(QLabel('项目:'))
        top.addWidget(self.mr_list_project_combo)
        top.addWidget(QLabel('指派:'))
        top.addWidget(self.mr_list_assignee_combo)
        top.addWidget(QLabel('审查者:'))
        top.addWidget(self.mr_list_reviewer_combo)
        top.addStretch()

        self.mr_list_refresh_btn = QPushButton('刷新')
        self.mr_list_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.mr_list_refresh_btn.setStyleSheet(
            'QPushButton { background: white; border: 1px solid #d0d0d0; border-radius: 4px; '
            'padding: 6px 14px; color: #444; }'
            'QPushButton:hover { background: #f0f7ff; border-color: #1677ff; color: #1677ff; }'
            'QPushButton:disabled { background: #f5f5f5; color: #aaa; border-color: #ddd; }'
        )
        top.addWidget(self.mr_list_refresh_btn)
        layout.addLayout(top)

        self.mr_list_state_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_project_combo.currentIndexChanged.connect(self._apply_mr_list_project_filter)
        self.mr_list_assignee_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_reviewer_combo.currentIndexChanged.connect(self.run_batch_refresh_mr_list)
        self.mr_list_refresh_btn.clicked.connect(self.run_batch_refresh_mr_list)

        self.mr_list_status = QLabel('')
        self.mr_list_status.setStyleSheet('color: #888; font-size: 12px;')
        layout.addWidget(self.mr_list_status)

        self.mr_list_table = QTableWidget()
        self.mr_list_table.setColumnCount(8)
        self.mr_list_table.setHorizontalHeaderLabels(
            ['标题', '项目', '源分支 → 目标分支', '指派', '审查者', '创建时间', '合并状态', '操作']
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
        self.mr_list_table.setColumnWidth(1, 140)
        self.mr_list_table.setColumnWidth(3, 90)
        self.mr_list_table.setColumnWidth(4, 90)
        self.mr_list_table.setColumnWidth(5, 150)
        self.mr_list_table.setColumnWidth(6, 100)
        self.mr_list_table.setColumnWidth(7, 80)
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

    def _ensure_current_username_loaded(self):
        """异步加载当前 token 对应的用户名（用于"自己"筛选）。"""
        if self._current_username:
            return
        url = self._get_gitlab_value('gitlab_url')
        token = self._get_gitlab_value('private_token')
        if not url or not token:
            return

        def _run():
            return get_current_gitlab_username(url, token)

        def on_success(result):
            username, error = result
            if error or not username:
                return
            self._current_username = username

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

        # 创建人固定为当前用户（"自己"），username 在后台线程内同步获取避免竞态
        cached_username = self._current_username
        assignee = _filter_value(self.mr_list_assignee_combo)
        reviewer = _filter_value(self.mr_list_reviewer_combo)

        self._mr_list_refresh_seq += 1
        seq = self._mr_list_refresh_seq

        self.mr_list_status.setText('正在加载合并请求...')
        self.mr_list_refresh_btn.setEnabled(False)

        def _run():
            # 后台线程同步获取当前用户名（首次），避免主线程异步竞态
            author = cached_username
            if not author:
                username, user_err = get_current_gitlab_username(url, token)
                if not user_err and username:
                    author = username
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
            return all_mrs, failed, author

        def on_success(result):
            if seq != self._mr_list_refresh_seq:
                return
            self.mr_list_refresh_btn.setEnabled(True)
            all_mrs, failed, author = result
            # 缓存 username，后续刷新直接复用
            if author and not self._current_username:
                self._current_username = author
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
            self.mr_list_table.setItem(row, 3, QTableWidgetItem(mr.get('assignees', '')))
            self.mr_list_table.setItem(row, 4, QTableWidgetItem(mr.get('reviewers', '')))
            self.mr_list_table.setItem(row, 5, QTableWidgetItem(_format_mr_datetime(mr.get('created_at', ''))))

            status_item = QTableWidgetItem(mr.get('merge_status', ''))
            if mr.get('merge_status') == 'can_be_merged':
                status_item.setForeground(QColor('#27ae60'))
            elif mr.get('merge_status') == 'cannot_be_merged':
                status_item.setForeground(QColor('#e74c3c'))
            self.mr_list_table.setItem(row, 6, status_item)

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
            self.mr_list_table.setCellWidget(row, 7, merge_btn)

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
