"""
合并冲突解决对话框 - 类似 IntelliJ IDEA 的三列合并界面
逐行显示差异，用户手动选择每一处差异
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel,
    QPushButton, QMessageBox, QSplitter, QWidget, QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QSizePolicy, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextCharFormat, QColor, QBrush, QTextCursor, QFont, QSyntaxHighlighter, QTextDocument
import subprocess
import os
import re
import difflib


class ConflictHighlighter(QSyntaxHighlighter):
    """高亮显示冲突标记"""

    def __init__(self, document):
        super().__init__(document)

    def highlightBlock(self, text):
        if text.startswith('<<<<<<<'):
            format = QTextCharFormat()
            format.setBackground(QColor('#ff6b6b'))
            format.setForeground(QColor('#ffffff'))
            self.setFormat(0, len(text), format)
        elif text.startswith('======='):
            format = QTextCharFormat()
            format.setBackground(QColor('#ffd93d'))
            self.setFormat(0, len(text), format)
        elif text.startswith('>>>>>>>'):
            format = QTextCharFormat()
            format.setBackground(QColor('#6bcf7f'))
            self.setFormat(0, len(text), format)


class DiffBlockWidget(QFrame):
    """单个差异块的控件"""

    def __init__(self, left_lines, right_lines, block_type, parent_dialog):
        super().__init__()
        self.left_lines = left_lines
        self.right_lines = right_lines
        self.block_type = block_type  # 'conflict', 'add', 'remove', 'change'
        self.parent_dialog = parent_dialog
        self.selected = None  # None, 'left', 'right'
        self.initUI()

    def initUI(self):
        # 根据类型设置不同的边框颜色
        if self.block_type == 'conflict':
            border_color = '#ff6b6b'
        elif self.block_type == 'add':
            border_color = '#50c878'
        elif self.block_type == 'remove':
            border_color = '#ffa500'
        else:
            border_color = '#ddd'

        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setStyleSheet(f'''
            DiffBlockWidget {{
                background: #ffffff;
                border: 2px solid {border_color};
                border-radius: 5px;
                margin: 3px;
                padding: 5px;
            }}
        ''')

        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        # 添加类型标签
        type_labels = {
            'conflict': ('--- 冲突区域 ---', '#e74c3c'),
            'add': ('+++ 新增代码 (仅 Cherry-pick 版本) +++', '#50c878'),
            'remove': ('--- 删除代码 (仅本地版本) ---', '#ffa500'),
            'change': ('~~~ 修改的代码 ~~~', '#4a90d9')
        }
        title_text, title_color = type_labels.get(self.block_type, ('--- 差异 ---', '#ddd'))
        title = QLabel(title_text)
        title.setStyleSheet(f'color: {title_color}; font-weight: bold;')
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 内容布局
        content_layout = QHBoxLayout()

        # 左侧代码
        left_panel = QVBoxLayout()
        if self.block_type in ['conflict', 'change', 'remove']:
            left_label = QLabel('本地版本 (Current)')
            left_label.setStyleSheet('color: #4a90d9; font-weight: bold; font-size: 11px;')
            left_panel.addWidget(left_label)

            left_text = '\n'.join(self.left_lines)
            self.left_edit = QTextEdit()
            self.left_edit.setPlainText(left_text)
            self.left_edit.setReadOnly(True)
            self.left_edit.setMaximumHeight(80)
            self.left_edit.setStyleSheet('''
                QTextEdit {
                    background: #e3f2fd;
                    border: 1px solid #4a90d9;
                    border-radius: 3px;
                    font-family: Consolas, Monaco, monospace;
                    font-size: 10px;
                }
            ''')
            left_panel.addWidget(self.left_edit)
        content_layout.addLayout(left_panel, 1)

        # 选择按钮
        if self.block_type == 'conflict':
            # 冲突：两边都有选择按钮
            button_panel = QVBoxLayout()
            button_panel.addStretch()

            self.accept_left_btn = QPushButton('→')
            self.accept_left_btn.setToolTip('选择本地版本')
            self.accept_left_btn.setFixedSize(35, 35)
            self.accept_left_btn.setCheckable(True)
            self.accept_left_btn.setStyleSheet('''
                QPushButton {
                    background: #4a90d9;
                    color: white;
                    border: none;
                    border-radius: 17px;
                    font-size: 16px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background: #1a5490;
                    border: 2px solid #0d3d7a;
                }
            ''')
            self.accept_left_btn.clicked.connect(lambda: self.select_side('left'))
            button_panel.addWidget(self.accept_left_btn)

            button_panel.addStretch()

            self.accept_right_btn = QPushButton('←')
            self.accept_right_btn.setToolTip('选择 Cherry-pick 版本')
            self.accept_right_btn.setFixedSize(35, 35)
            self.accept_right_btn.setCheckable(True)
            self.accept_right_btn.setStyleSheet('''
                QPushButton {
                    background: #50c878;
                    color: white;
                    border: none;
                    border-radius: 17px;
                    font-size: 16px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background: #1e7e34;
                    border: 2px solid #145a32;
                }
            ''')
            self.accept_right_btn.clicked.connect(lambda: self.select_side('right'))
            button_panel.addWidget(self.accept_right_btn)

            button_panel.addStretch()
            content_layout.addLayout(button_panel)

            # 右侧代码
            right_panel = QVBoxLayout()
            right_label = QLabel('Cherry-pick 版本 (Incoming)')
            right_label.setStyleSheet('color: #50c878; font-weight: bold; font-size: 11px;')
            right_panel.addWidget(right_label)

            right_text = '\n'.join(self.right_lines)
            self.right_edit = QTextEdit()
            self.right_edit.setPlainText(right_text)
            self.right_edit.setReadOnly(True)
            self.right_edit.setMaximumHeight(80)
            self.right_edit.setStyleSheet('''
                QTextEdit {
                    background: #d4edda;
                    border: 1px solid #50c878;
                    border-radius: 3px;
                    font-family: Consolas, Monaco, monospace;
                    font-size: 10px;
                }
            ''')
            right_panel.addWidget(self.right_edit)
            content_layout.addLayout(right_panel, 1)

        elif self.block_type == 'add':
            # 新增：只有右侧，但用户可以选择是否要
            content_layout.addStretch()

            button_panel = QVBoxLayout()
            button_panel.addStretch()

            self.accept_add_btn = QCheckBox('包含此代码')
            self.accept_add_btn.setStyleSheet('''
                QCheckBox {
                    font-size: 12px;
                    color: #50c878;
                    font-weight: bold;
                }
                QCheckBox::indicator {
                    width: 20px;
                    height: 20px;
                }
            ''')
            self.accept_add_btn.setChecked(False)  # 默认不勾选
            self.accept_add_btn.stateChanged.connect(lambda: self.select_side('right'))
            button_panel.addWidget(self.accept_add_btn)

            button_panel.addStretch()
            content_layout.addLayout(button_panel)

            # 右侧代码
            right_panel = QVBoxLayout()
            right_label = QLabel('Cherry-pick 版本 (Incoming)')
            right_label.setStyleSheet('color: #50c878; font-weight: bold; font-size: 11px;')
            right_panel.addWidget(right_label)

            right_text = '\n'.join(self.right_lines)
            self.right_edit = QTextEdit()
            self.right_edit.setPlainText(right_text)
            self.right_edit.setReadOnly(True)
            self.right_edit.setMaximumHeight(80)
            self.right_edit.setStyleSheet('''
                QTextEdit {
                    background: #d4edda;
                    border: 1px solid #50c878;
                    border-radius: 3px;
                    font-family: Consolas, Monaco, monospace;
                    font-size: 10px;
                }
            ''')
            right_panel.addWidget(self.right_edit)
            content_layout.addLayout(right_panel, 1)

        elif self.block_type == 'remove':
            # 删除：只有左侧，用户可以选择是否保留
            left_panel = QVBoxLayout()
            left_label = QLabel('本地版本 (Current)')
            left_label.setStyleSheet('color: #ffa500; font-weight: bold; font-size: 11px;')
            left_panel.addWidget(left_label)

            left_text = '\n'.join(self.left_lines)
            self.left_edit = QTextEdit()
            self.left_edit.setPlainText(left_text)
            self.left_edit.setReadOnly(True)
            self.left_edit.setMaximumHeight(80)
            self.left_edit.setStyleSheet('''
                QTextEdit {
                    background: #fff3cd;
                    border: 1px solid #ffa500;
                    border-radius: 3px;
                    font-family: Consolas, Monaco, monospace;
                    font-size: 10px;
                }
            ''')
            left_panel.addWidget(self.left_edit)
            content_layout.addLayout(left_panel, 1)

            content_layout.addStretch()

            button_panel = QVBoxLayout()
            button_panel.addStretch()

            self.accept_remove_btn = QCheckBox('保留此代码')
            self.accept_remove_btn.setStyleSheet('''
                QCheckBox {
                    font-size: 12px;
                    color: #ffa500;
                    font-weight: bold;
                }
                QCheckBox::indicator {
                    width: 20px;
                    height: 20px;
                }
            ''')
            self.accept_remove_btn.setChecked(False)  # 默认不勾选
            self.accept_remove_btn.stateChanged.connect(lambda: self.select_side('left'))
            button_panel.addWidget(self.accept_remove_btn)

            button_panel.addStretch()
            content_layout.addLayout(button_panel)

        elif self.block_type == 'change':
            # 修改：两边都有，用户可以选择
            button_panel = QVBoxLayout()
            button_panel.addStretch()

            self.accept_left_btn = QPushButton('→')
            self.accept_left_btn.setToolTip('选择本地版本')
            self.accept_left_btn.setFixedSize(35, 35)
            self.accept_left_btn.setCheckable(True)
            self.accept_left_btn.setStyleSheet('''
                QPushButton {
                    background: #4a90d9;
                    color: white;
                    border: none;
                    border-radius: 17px;
                    font-size: 16px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background: #1a5490;
                    border: 2px solid #0d3d7a;
                }
            ''')
            self.accept_left_btn.clicked.connect(lambda: self.select_side('left'))
            button_panel.addWidget(self.accept_left_btn)

            button_panel.addStretch()

            self.accept_right_btn = QPushButton('←')
            self.accept_right_btn.setToolTip('选择 Cherry-pick 版本')
            self.accept_right_btn.setFixedSize(35, 35)
            self.accept_right_btn.setCheckable(True)
            self.accept_right_btn.setStyleSheet('''
                QPushButton {
                    background: #50c878;
                    color: white;
                    border: none;
                    border-radius: 17px;
                    font-size: 16px;
                    font-weight: bold;
                }
                QPushButton:checked {
                    background: #1e7e34;
                    border: 2px solid #145a32;
                }
            ''')
            self.accept_right_btn.clicked.connect(lambda: self.select_side('right'))
            button_panel.addWidget(self.accept_right_btn)

            button_panel.addStretch()
            content_layout.addLayout(button_panel)

            # 右侧代码
            right_panel = QVBoxLayout()
            right_label = QLabel('Cherry-pick 版本 (Incoming)')
            right_label.setStyleSheet('color: #50c878; font-weight: bold; font-size: 11px;')
            right_panel.addWidget(right_label)

            right_text = '\n'.join(self.right_lines)
            self.right_edit = QTextEdit()
            self.right_edit.setPlainText(right_text)
            self.right_edit.setReadOnly(True)
            self.right_edit.setMaximumHeight(80)
            self.right_edit.setStyleSheet('''
                QTextEdit {
                    background: #d4edda;
                    border: 1px solid #50c878;
                    border-radius: 3px;
                    font-family: Consolas, Monaco, monospace;
                    font-size: 10px;
                }
            ''')
            right_panel.addWidget(self.right_edit)
            content_layout.addLayout(right_panel, 1)

        layout.addLayout(content_layout)
        self.setLayout(layout)

    def select_side(self, side):
        """选择一侧"""
        self.selected = side
        # 如果是按钮，更新按钮状态
        if hasattr(self, 'accept_left_btn') and isinstance(self.accept_left_btn, QPushButton):
            if side == 'left':
                self.accept_right_btn.setChecked(False)
            elif hasattr(self, 'accept_right_btn') and isinstance(self.accept_right_btn, QPushButton):
                if side == 'right':
                    self.accept_left_btn.setChecked(False)

        # 通知父对话框更新结果
        if side == 'left' and hasattr(self, 'left_lines'):
            code = '\n'.join(self.left_lines)
        elif side == 'right' and hasattr(self, 'right_lines'):
            code = '\n'.join(self.right_lines)
        else:
            return

        # 对于 checkbox 类型，勾选时才添加
        if self.block_type == 'add' and not self.accept_add_btn.isChecked():
            self.parent_dialog.remove_from_result(self)
            return
        if self.block_type == 'remove' and not self.accept_remove_btn.isChecked():
            self.parent_dialog.remove_from_result(self)
            return

        self.parent_dialog.append_to_result(code)

    def get_selected_code(self):
        """获取选中的代码"""
        if self.selected == 'left' and hasattr(self, 'left_lines'):
            return '\n'.join(self.left_lines)
        elif self.selected == 'right' and hasattr(self, 'right_lines'):
            return '\n'.join(self.right_lines)
        return ''


class MergeConflictDialog(QDialog):
    """三列合并冲突解决对话框"""

    def __init__(self, conflict_files, repo_path, parent=None):
        super().__init__(parent)
        self.conflict_files = conflict_files
        self.repo_path = repo_path
        self.current_file_index = 0
        self.resolved_files = {}
        self.diff_blocks = []  # 存储当前文件的所有差异块
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Cherry-pick 冲突解决')
        self.setMinimumSize(1400, 800)

        layout = QVBoxLayout()

        # 顶部信息
        info_label = QLabel(
            f'<b>发现 {len(self.conflict_files)} 个文件有冲突</b><br>'
            '默认所有代码都不勾选，点击 <b style="color: #4a90d9;">→</b> 选择本地版本，'
            '点击 <b style="color: #50c878;">←</b> 选择 Cherry-pick 版本，'
            '或勾选复选框来决定是否包含'
        )
        info_label.setTextFormat(Qt.RichText)
        layout.addWidget(info_label)

        # 主内容区域
        main_splitter = QSplitter(Qt.Horizontal)

        # 左侧：文件列表
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel('<b>冲突文件:</b>'))

        self.file_list = QListWidget()
        for i, file_path in enumerate(self.conflict_files):
            item = QListWidgetItem(os.path.basename(file_path))
            item.setData(Qt.UserRole, file_path)
            if i == 0:
                item.setSelected(True)
            self.file_list.addItem(item)
        self.file_list.currentRowChanged.connect(self.load_current_file)
        left_layout.addWidget(self.file_list)

        left_panel.setLayout(left_layout)
        main_splitter.addWidget(left_panel)

        # 右侧：合并区域
        right_panel = QWidget()
        right_layout = QVBoxLayout()

        # 当前文件标签
        self.current_file_label = QLabel()
        self.current_file_label.setStyleSheet('font-weight: bold; font-size: 12px; padding: 5px;')
        right_layout.addWidget(self.current_file_label)

        # 快速操作按钮
        quick_actions = QHBoxLayout()
        self.select_all_left_btn = QPushButton('全部选择本地版本')
        self.select_all_right_btn = QPushButton('全部选择 Cherry-pick 版本')
        self.clear_selection_btn = QPushButton('清除所有选择')

        self.select_all_left_btn.setStyleSheet('background: #4a90d9; color: white;')
        self.select_all_right_btn.setStyleSheet('background: #50c878; color: white;')
        self.clear_selection_btn.setStyleSheet('background: #95a5a6; color: white;')

        self.select_all_left_btn.clicked.connect(self.select_all_left)
        self.select_all_right_btn.clicked.connect(self.select_all_right)
        self.clear_selection_btn.clicked.connect(self.clear_all_selection)

        quick_actions.addWidget(self.select_all_left_btn)
        quick_actions.addWidget(self.select_all_right_btn)
        quick_actions.addWidget(self.clear_selection_btn)
        quick_actions.addStretch()
        right_layout.addLayout(quick_actions)

        # 创建滚动区域来放置差异块
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet('QScrollArea { border: none; }')

        self.diff_blocks_container = QWidget()
        self.diff_blocks_layout = QVBoxLayout()
        self.diff_blocks_layout.setAlignment(Qt.AlignTop)
        self.diff_blocks_container.setLayout(self.diff_blocks_layout)
        scroll_area.setWidget(self.diff_blocks_container)

        right_layout.addWidget(scroll_area, 1)

        # 底部：预览结果
        right_layout.addWidget(QLabel('<b>合并结果预览:</b>'))
        self.result_preview = QTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setStyleSheet('''
            QTextEdit {
                background: #f8f9fa;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 5px;
                font-family: Consolas, Monaco, monospace;
                font-size: 11px;
            }
        ''')
        self.result_preview.setMaximumHeight(150)
        right_layout.addWidget(self.result_preview)

        # 操作按钮
        button_layout = QHBoxLayout()
        self.mark_resolved_button = QPushButton('✓ 标记当前文件已解决')
        self.mark_resolved_button.setStyleSheet('background: #4CAF50; color: white; padding: 8px 20px; font-weight: bold;')

        self.mark_resolved_button.clicked.connect(self.mark_current_resolved)
        button_layout.addStretch()
        button_layout.addWidget(self.mark_resolved_button)
        right_layout.addLayout(button_layout)

        right_panel.setLayout(right_layout)
        main_splitter.addWidget(right_panel)

        layout.addWidget(main_splitter, 1)

        # 底部按钮
        bottom_buttons = QHBoxLayout()
        self.abort_button = QPushButton('✕ 中止 Cherry-pick')
        self.abort_button.setStyleSheet('background: #f44336; color: white; padding: 8px 20px;')
        self.continue_button = QPushButton('✓ 继续完成 Cherry-pick')
        self.continue_button.setStyleSheet('background: #4CAF50; color: white; padding: 8px 20px;')
        self.continue_button.setEnabled(False)

        self.abort_button.clicked.connect(self.reject)
        self.continue_button.clicked.connect(self.accept_continue)

        bottom_buttons.addStretch()
        bottom_buttons.addWidget(self.abort_button)
        bottom_buttons.addWidget(self.continue_button)
        layout.addLayout(bottom_buttons)

        self.setLayout(layout)

        # 用于存储当前文件的 diff blocks
        self.current_diff_blocks = []

    def load_current_file(self, index):
        """当选择不同文件时加载"""
        if 0 <= index < len(self.conflict_files):
            self.load_file(index)

    def load_file(self, index):
        """加载指定索引的冲突文件"""
        if index >= len(self.conflict_files):
            return

        self.current_file_index = index
        file_path = self.conflict_files[index]
        self.current_file_label.setText(f'当前文件: {file_path}')

        # 保存当前编辑结果
        if self.result_preview.toPlainText():
            self.resolved_files[self.conflict_files[index - 1]] = self.result_preview.toPlainText()

        # 获取三个版本的内容
        # 本地版本
        local_result = subprocess.run(
            ['git', 'show', ':2:' + file_path],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        local_content = local_result.stdout if local_result.returncode == 0 else ''

        # 基础版本
        base_result = subprocess.run(
            ['git', 'show', ':1:' + file_path],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        base_content = base_result.stdout if base_result.returncode == 0 else ''

        # Cherry-pick 版本
        incoming_result = subprocess.run(
            ['git', 'show', ':3:' + file_path],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        incoming_content = incoming_result.stdout if incoming_result.returncode == 0 else ''

        # 分析差异
        self.diff_blocks = []
        self.analyze_diff(base_content, local_content, incoming_content)

        # 清空之前的差异块
        for i in reversed(range(self.diff_blocks_layout.count())):
            item = self.diff_blocks_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)

        # 创建差异块控件
        for block in self.diff_blocks:
            diff_widget = DiffBlockWidget(block['left'], block['right'], block['type'], self)
            self.diff_blocks_layout.addWidget(diff_widget)

        # 设置预览结果
        if file_path in self.resolved_files:
            self.result_preview.setPlainText(self.resolved_files[file_path])
        else:
            self.result_preview.clear()

    def analyze_diff(self, base, local, incoming):
        """分析三个版本之间的差异"""
        base_lines = base.split('\n') if base else []
        local_lines = local.split('\n') if local else []
        incoming_lines = incoming.split('\n') if incoming else []

        # 使用 difflib 对比
        local_diff = list(difflib.unified_diff(base_lines, local_lines, lineterm=''))
        incoming_diff = list(difflib.unified_diff(base_lines, incoming_lines, lineterm=''))

        # 解析 diff 来找到差异块
        local_changes = self.parse_diff(local_diff)
        incoming_changes = self.parse_diff(incoming_diff)

        # 合并差异并创建差异块
        local_set = set(local_changes)
        incoming_set = set(incoming_changes)

        # 处理冲突区域（两边都修改的行）
        for line_num in local_set:
            if line_num in incoming_set:
                # 找到差异块
                left_block = self.extract_block(local_lines, line_num, local_changes)
                right_block = self.extract_block(incoming_lines, line_num, incoming_changes)

                if left_block or right_block:
                    self.diff_blocks.append({
                        'left': left_block,
                        'right': right_block,
                        'type': 'conflict'
                    })
                local_set.discard(line_num)
                incoming_set.discard(line_num)

        # 处理只有本地修改（删除）
        for line_num in list(local_set):
            block = self.extract_block(local_lines, line_num, local_changes)
            if block:
                self.diff_blocks.append({
                    'left': block,
                    'right': [],
                    'type': 'remove'
                })

        # 处理只有 cherry-pick 修改（新增）
        for line_num in list(incoming_set):
            block = self.extract_block(incoming_lines, line_num, incoming_changes)
            if block:
                self.diff_blocks.append({
                    'left': [],
                    'right': block,
                    'type': 'add'
                })

        # 如果没有检测到差异（可能是完全相同的文件）
        if not self.diff_blocks:
            self.diff_blocks.append({
                'left': [],
                'right': [],
                'type': 'unchanged'
            })

    def parse_diff(self, diff_lines):
        """解析 diff 输出，返回修改的行号集合"""
        changed_lines = set()
        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith('@@') and ' +' in line and ' -' in line:
                # 解析 @@ -from,to +from,to @@
                match = re.search(r'-(\d+),?\d* \+(\d+),?\d*', line)
                if match:
                    local_start = int(match.group(1))
                    incoming_start = int(match.group(2))
                    i += 1
                    # 收集所有修改的行
                    while i < len(diff_lines):
                        line = diff_lines[i]
                        if line.startswith('@@') or line.startswith('diff'):
                            break
                        elif line.startswith('+'):
                            incoming_start += 1
                        elif line.startswith('-'):
                            local_start += 1
                        elif not line.startswith('\\'):
                            if line.startswith('+'):
                                changed_lines.add(incoming_start)
                                incoming_start += 1
                            elif line.startswith('-'):
                                changed_lines.add(local_start)
                                local_start += 1
                            else:
                                local_start += 1
                                incoming_start += 1
                        i += 1
            else:
                i += 1
        return changed_lines

    def extract_block(self, lines, start_line, changed_lines):
        """提取包含变化行的代码块"""
        if not lines:
            return []

        block = []
        # 向后查找连续的变化
        for i in range(start_line - 1, min(start_line + 20, len(lines))):
            # 检查这行是否在变化范围内
            is_in_range = False
            for changed in changed_lines:
                if abs(i - changed) <= 2:  # 允许一定的容差
                    is_in_range = True
                    break

            if is_in_range or i == start_line - 1:
                block.append(lines[i])
            elif block:
                break

        return block

    def select_all_left(self):
        """选择所有本地版本"""
        for i in range(self.diff_blocks_layout.count()):
            item = self.diff_blocks_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, DiffBlockWidget):
                    if widget.block_type in ['conflict', 'change', 'remove']:
                        if hasattr(widget, 'accept_left_btn') and isinstance(widget.accept_left_btn, QPushButton):
                            widget.accept_left_btn.setChecked(True)
                        elif hasattr(widget, 'accept_remove_btn') and isinstance(widget.accept_remove_btn, QCheckBox):
                            widget.accept_remove_btn.setChecked(True)

    def select_all_right(self):
        """选择所有 cherry-pick 版本"""
        for i in range(self.diff_blocks_layout.count()):
            item = self.diff_blocks_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, DiffBlockWidget):
                    if widget.block_type in ['conflict', 'change', 'add']:
                        if hasattr(widget, 'accept_right_btn') and isinstance(widget.accept_right_btn, QPushButton):
                            widget.accept_right_btn.setChecked(True)
                        elif hasattr(widget, 'accept_add_btn') and isinstance(widget.accept_add_btn, QCheckBox):
                            widget.accept_add_btn.setChecked(True)

    def clear_all_selection(self):
        """清除所有选择"""
        for i in range(self.diff_blocks_layout.count()):
            item = self.diff_blocks_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, DiffBlockWidget):
                    if hasattr(widget, 'accept_left_btn') and isinstance(widget.accept_left_btn, QPushButton):
                        widget.accept_left_btn.setChecked(False)
                    if hasattr(widget, 'accept_right_btn') and isinstance(widget.accept_right_btn, QPushButton):
                        widget.accept_right_btn.setChecked(False)
                    if hasattr(widget, 'accept_add_btn') and isinstance(widget.accept_add_btn, QCheckBox):
                        widget.accept_add_btn.setChecked(False)
                    if hasattr(widget, 'accept_remove_btn') and isinstance(widget.accept_remove_btn, QCheckBox):
                        widget.accept_remove_btn.setChecked(False)
                widget.selected = None

        self.result_preview.clear()

    def append_to_result(self, code):
        """将代码追加到结果预览"""
        current = self.result_preview.toPlainText()
        if current:
            current += '\n'
        current += code
        self.result_preview.setPlainText(current)

    def remove_from_result(self):
        """从结果预览中移除最后添加的代码"""
        current = self.result_preview.toPlainText()
        if '\n' in current:
            # 移除最后一行
            current = '\n'.join(current.split('\n')[:-1])
            self.result_preview.setPlainText(current)
        else:
            self.result_preview.clear()

    def mark_current_resolved(self):
        """标记当前文件已解决"""
        current_file = self.conflict_files[self.current_file_index]
        self.resolved_files[current_file] = self.result_preview.toPlainText()

        # 更新文件列表项
        item = self.file_list.item(self.current_file_index)
        item.setText(f'✓ {os.path.basename(current_file)}')

        # 检查是否所有文件都已解决
        self.check_all_resolved()

        # 移动到下一个文件
        for i in range(self.current_file_index + 1, len(self.conflict_files)):
            if self.conflict_files[i] not in self.resolved_files:
                self.file_list.setCurrentRow(i)
                return

    def check_all_resolved(self):
        """检查是否所有文件都已解决"""
        self.continue_button.setEnabled(len(self.resolved_files) == len(self.conflict_files))

        if len(self.resolved_files) == len(self.conflict_files):
            QMessageBox.information(
                self,
                '完成',
                '所有冲突文件已解决！点击"继续完成 Cherry-pick"完成操作。'
            )

    def accept_continue(self):
        """继续完成 cherry-pick"""
        # 保存最后一个文件的修改
        if self.result_preview.toPlainText():
            current_file = self.conflict_files[self.current_file_index]
            self.resolved_files[current_file] = self.result_preview.toPlainText()

        # 将解决后的内容写回文件
        for file_path, content in self.resolved_files.items():
            full_path = os.path.join(self.repo_path, file_path)
            try:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    '错误',
                    f'写入文件 {file_path} 失败: {e}'
                )
                return

        # 执行 git add 标记冲突已解决
        for file_path in self.resolved_files:
            result = subprocess.run(
                ['git', 'add', file_path],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                QMessageBox.critical(
                    self,
                    '错误',
                    f'git add {file_path} 失败: {result.stderr}'
                )
                return

        self.accept()

    @staticmethod
    def detect_conflicts(repo_path):
        """检测冲突文件列表"""
        result = subprocess.run(
            ['git', 'diff', '--name-only', '--diff-filter=U'],
            cwd=repo_path,
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split('\n')
        return []

    @staticmethod
    def show_and_resolve(repo_path, parent=None):
        """显示冲突解决对话框并返回是否继续"""
        conflict_files = MergeConflictDialog.detect_conflicts(repo_path)

        if not conflict_files:
            return True

        dialog = MergeConflictDialog(conflict_files, repo_path, parent)
        result = dialog.exec_()
        return result == QDialog.Accepted
