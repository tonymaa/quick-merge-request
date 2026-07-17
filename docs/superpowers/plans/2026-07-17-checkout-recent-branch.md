# Checkout 最近创建分支 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在创建分支成功后能一键切换到刚创建的分支，并提供"切换最近分支"入口按钮（单项目和批量模式都支持），无冲突时保留工作区改动，有冲突时自动 stash。

**Architecture:** 新增 `smart_checkout()` 编排 git 命令（直接 checkout → 失败 stash → 仍失败回滚 stash）；新增 `RecentBranchStore` 类基于现有 `cache.db` shelve 持久化最近分支；新增 `CheckoutToast` 非模态悬浮通知。两个 tab（workspace_tab / all_projects_tab）各加一个"切换最近分支"入口按钮，单项目模式额外触发 toast。

**Tech Stack:** Python 3, PyQt5, shelve, subprocess, pytest

## Global Constraints

- Python 项目根目录：`E:/project/quick_merge_request`
- 现有 shelve 存储约定：`shelve.open('cache.db')` 相对路径
- 现有 history key：`new_branch_history`（仅存分支名前缀），本特性新增 key：`recent_branches`（存 dict 列表）
- 创建分支成功判断：输出文本包含 `'Branch created successfully!'`
- `quick_create_branch.run_command(command, directory)` 现有签名：返回 `(success, stdout, stderr)`，命令为 list 形式，`shell=True`
- 分支命名规则：`{new_branch}__from__{target_branch.replace('/', '@')}`
- 提交消息格式遵循现有风格（中文 + 偶尔 emoji），无 Co-Authored-By
- 类型注解（PEP 8 / PEP 484）
- 错误消息用中文

## File Structure

| 文件 | 责任 |
|------|------|
| `quick_create_branch.py` | 新增 `current_branch()` 和 `smart_checkout()` |
| `app/recent_branch_store.py` (新建) | `RecentBranchStore` 读写 `cache.db['recent_branches']` |
| `app/ui/toast_notification.py` (新建) | `CheckoutToast(QFrame)` 非模态悬浮通知 |
| `app/ui/workspace_tab.py` | 单项目模式集成：toast + 入口按钮 |
| `app/ui/all_projects_tab.py` | 批量模式集成：入口按钮（无 toast） |
| `tests/test_smart_checkout.py` (新建) | smart_checkout 单测 |
| `tests/test_recent_branch_store.py` (新建) | RecentBranchStore 单测 |

---

## Task 1: 扩展 `quick_create_branch.py` — 新增 `current_branch()` 和 `smart_checkout()`

**Files:**
- Modify: `quick_create_branch.py` (末尾追加，保持现有代码不变)
- Test: `tests/test_smart_checkout.py` (新建)

**Interfaces:**
- Consumes: 现有 `run_command(command, directory) -> (success, stdout, stderr)`
- Produces:
  - `current_branch(directory: str) -> str | None` — 返回当前分支名，detached HEAD 或失败返回 None
  - `smart_checkout(directory: str, target_branch: str) -> tuple[str, str]` — 返回 `(status, message)`，status 取值：`"skip" | "ok" | "ok_stash" | "fail"`

### Step 1.1: 创建测试目录和 conftest

- [ ] 创建 `tests/__init__.py`（空文件）和 `tests/conftest.py`（确保项目根在 sys.path）

```python
# tests/conftest.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **提交**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: 初始化测试目录"
```

### Step 1.2: 写 `current_branch` 失败测试

- [ ] **写测试** — 在 `tests/test_smart_checkout.py`：

```python
from unittest.mock import patch
from quick_create_branch import current_branch


@patch('quick_create_branch.run_command')
def test_current_branch_returns_branch_name(mock_run):
    mock_run.return_value = (True, '* main\n  feature\n', '')
    assert current_branch('/fake') == 'main'


@patch('quick_create_branch.run_command')
def test_current_branch_returns_none_on_failure(mock_run):
    mock_run.return_value = (False, '', 'fatal: not a git repo')
    assert current_branch('/fake') is None


@patch('quick_create_branch.run_command')
def test_current_branch_returns_none_on_detached(mock_run):
    mock_run.return_value = (True, '  (HEAD detached at abc123)\n', '')
    assert current_branch('/fake') is None
```

- [ ] **运行测试验证失败**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: FAIL — `ImportError: cannot import name 'current_branch'`

### Step 1.3: 实现 `current_branch`

- [ ] **实现** — 在 `quick_create_branch.py` 末尾追加：

```python
def current_branch(directory: str) -> str | None:
    """返回当前分支名；detached HEAD 或失败返回 None。"""
    success, stdout, stderr = run_command(
        ['git', 'branch', '--show-current'], directory
    )
    if not success:
        return None
    name = stdout.strip()
    return name or None
```

- [ ] **运行测试验证通过**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: 3 PASS

- [ ] **提交**

```bash
git add quick_create_branch.py tests/test_smart_checkout.py
git commit -m "feat: 新增 current_branch 获取当前分支名"
```

### Step 1.4: 写 `smart_checkout` 的 skip / ok_direct 测试

- [ ] **追加测试** — 在 `tests/test_smart_checkout.py` 追加：

```python
from quick_create_branch import smart_checkout


@patch('quick_create_branch.current_branch')
def test_smart_checkout_skip_when_already_on_target(mock_cur):
    mock_cur.return_value = 'feature'
    status, msg = smart_checkout('/fake', 'feature')
    assert status == 'skip'
    assert '已在目标分支' in msg


@patch('quick_create_branch.run_command')
@patch('quick_create_branch.current_branch')
def test_smart_checkout_ok_direct_when_checkout_succeeds(mock_cur, mock_run):
    mock_cur.return_value = 'main'
    # 第一次调用：git checkout feature → 成功
    mock_run.return_value = (True, 'Switching to branch feature\n', '')
    status, msg = smart_checkout('/fake', 'feature')
    assert status == 'ok'
    assert '已切换' in msg and 'feature' in msg
    # 只调用了一次 run_command（不应 stash）
    assert mock_run.call_count == 1
```

- [ ] **运行测试验证失败**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: FAIL — `ImportError: cannot import name 'smart_checkout'`

### Step 1.5: 实现 `smart_checkout` 的直接 checkout 路径

- [ ] **实现** — 在 `quick_create_branch.py` 末尾追加：

```python
from datetime import datetime


def smart_checkout(directory: str, target_branch: str) -> tuple[str, str]:
    """智能切换分支：直接 checkout → 失败 stash → 仍失败回滚。

    返回 (status, message)。status:
      - 'skip':     已在目标分支
      - 'ok':       切换成功，改动保留
      - 'ok_stash': 切换成功，改动已 stash
      - 'fail':     切换失败（已回滚 stash 或 stash 本身失败）
    """
    cur = current_branch(directory)
    if cur == target_branch:
        return ('skip', f'已在目标分支 {target_branch}')

    # 第一次尝试：直接 checkout
    success, stdout, stderr = run_command(
        ['git', 'checkout', target_branch], directory
    )
    if success:
        return ('ok', f'已切换到 {target_branch}，工作区改动已保留')

    # 占位：失败 → stash 重试（后续步骤实现）
    return ('fail', '尚未实现')
```

- [ ] **运行测试验证通过**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: 5 PASS（含 skip + ok_direct 两个新测试）

- [ ] **暂不提交**（下一步继续扩展 smart_checkout）

### Step 1.6: 写 `smart_checkout` 的 stash + 成功测试

- [ ] **追加测试** — 在 `tests/test_smart_checkout.py` 追加：

```python
@patch('quick_create_branch.run_command')
@patch('quick_create_branch.current_branch')
def test_smart_checkout_ok_stash_when_direct_fails(mock_cur, mock_run):
    mock_cur.return_value = 'main'
    # 依次：checkout 失败 → stash 成功 → checkout 成功 → stash list
    mock_run.side_effect = [
        (False, '', 'local changes would be overwritten'),
        (True, 'Saved working directory and index state', ''),
        (True, 'Switching to branch feature\n', ''),
        (True, 'stash@{0}: On main: auto-stash\n', ''),
    ]
    status, msg = smart_checkout('/fake', 'feature')
    assert status == 'ok_stash'
    assert 'feature' in msg
    assert 'stash@{0}' in msg
    assert 'git stash pop' in msg
    assert mock_run.call_count == 4
```

- [ ] **运行测试验证失败**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: FAIL — 返回 `('fail', '尚未实现')`

### Step 1.7: 实现 stash + 重试路径

- [ ] **替换 smart_checkout 的占位 return** — 把 `# 占位：失败 → stash 重试（后续步骤实现）return ('fail', '尚未实现')` 替换为：

```python
    # 失败 → stash 后重试
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stash_msg = f'auto-stash: {cur} → {target_branch} [{ts}]'
    s_ok, s_out, s_err = run_command(
        ['git', 'stash', 'push', '-m', stash_msg], directory
    )
    if not s_ok:
        return ('fail', f'stash 失败: {s_err}')

    c2_ok, c2_out, c2_err = run_command(
        ['git', 'checkout', target_branch], directory
    )
    if c2_ok:
        l_ok, l_out, l_err = run_command(
            ['git', 'stash', 'list'], directory
        )
        ref = 'stash@{0}'
        if l_ok:
            lines = [ln for ln in l_out.splitlines() if ln.strip()]
            if lines:
                ref = lines[0].split(':')[0]
        return ('ok_stash',
                f'已切换到 {target_branch}\n'
                f'原工作区有冲突已 stash: {ref}\n'
                f'stash 消息: {stash_msg}\n'
                f'恢复: git stash pop')

    # checkout 仍失败 → 回滚 stash
    run_command(['git', 'stash', 'pop'], directory)
    return ('fail', f'切换失败（改动已恢复）: {c2_err}')
```

- [ ] **运行测试验证通过**

Run: `pytest tests/test_smart_checkout.py -v`
Expected: 6 PASS

- [ ] **提交**

```bash
git add quick_create_branch.py tests/test_smart_checkout.py
git commit -m "feat: smart_checkout 支持 stash 回退路径"
```

### Step 1.8: 写 stash 失败 / stash 后仍失败需回滚 测试

- [ ] **追加测试** — 在 `tests/test_smart_checkout.py` 追加：

```python
@patch('quick_create_branch.run_command')
@patch('quick_create_branch.current_branch')
def test_smart_checkout_fail_when_stash_fails(mock_cur, mock_run):
    mock_cur.return_value = 'main'
    # checkout 失败 → stash 失败
    mock_run.side_effect = [
        (False, '', 'conflict'),
        (False, '', 'stash error'),
    ]
    status, msg = smart_checkout('/fake', 'feature')
    assert status == 'fail'
    assert 'stash 失败' in msg
    assert mock_run.call_count == 2  # 不应继续 checkout / list / pop


@patch('quick_create_branch.run_command')
@patch('quick_create_branch.current_branch')
def test_smart_checkout_fail_rolls_back_stash(mock_cur, mock_run):
    mock_cur.return_value = 'main'
    # checkout 失败 → stash 成功 → checkout 仍失败 → stash pop
    mock_run.side_effect = [
        (False, '', 'conflict'),
        (True, 'Saved', ''),
        (False, '', 'still conflicting'),
        (True, 'restored', ''),  # git stash pop
    ]
    status, msg = smart_checkout('/fake', 'feature')
    assert status == 'fail'
    assert '改动已恢复' in msg
    # 最后一次调用必须是 stash pop
    last_call_args = mock_run.call_args_list[-1][0][0]
    assert last_call_args == ['git', 'stash', 'pop']
```

- [ ] **运行测试验证通过**（实现已覆盖这些路径）

Run: `pytest tests/test_smart_checkout.py -v`
Expected: 8 PASS

- [ ] **提交**

```bash
git add tests/test_smart_checkout.py
git commit -m "test: smart_checkout stash 失败和回滚路径"
```

---

## Task 2: `RecentBranchStore` — 基于 shelve 的最近分支存储

**Files:**
- Create: `app/recent_branch_store.py`
- Test: `tests/test_recent_branch_store.py`

**Interfaces:**
- Consumes: shelve（路径可注入，便于测试）
- Produces:
  - `Entry` — `@dataclass(frozen=True)` 含 `workspace_path: str, branch: str, created_at: str`
  - `RecentBranchStore` — `__init__(self, shelve_path: str = 'cache.db')`, `add(workspace_path, branch, created_at=None)`, `list_by_workspace(workspace_path, limit=10) -> list[Entry]`, `list_workspaces() -> list[str]`

### Step 2.1: 写 add / list_by_workspace 失败测试

- [ ] **写测试** — 在 `tests/test_recent_branch_store.py`：

```python
from datetime import datetime
from app.recent_branch_store import RecentBranchStore


def _make_store(tmp_path):
    return RecentBranchStore(str(tmp_path / 'test_cache.db'))


def test_add_and_list_by_workspace(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/proj1', 'zhiming/feat__from__main', '2026-07-17 10:00:00')
    store.add('E:/proj1', 'zhiming/fix__from__dev', '2026-07-17 11:00:00')
    store.add('E:/proj2', 'zhiming/other__from__main', '2026-07-17 12:00:00')

    items = store.list_by_workspace('E:/proj1')
    assert len(items) == 2
    # 倒序：最新的在前
    assert items[0].branch == 'zhiming/fix__from__dev'
    assert items[1].branch == 'zhiming/feat__from__main'


def test_list_by_workspace_limit(tmp_path):
    store = _make_store(tmp_path)
    for i in range(15):
        store.add('E:/proj1', f'branch{i}', f'2026-07-17 10:{i:02d}:00')
    items = store.list_by_workspace('E:/proj1', limit=10)
    assert len(items) == 10
    # 最新的（branch14）应在最前
    assert items[0].branch == 'branch14'


def test_list_by_workspace_empty(tmp_path):
    store = _make_store(tmp_path)
    assert store.list_by_workspace('E:/unknown') == []
```

- [ ] **运行测试验证失败**

Run: `pytest tests/test_recent_branch_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

### Step 2.2: 实现 `RecentBranchStore` 基础 add / list

- [ ] **创建** `app/recent_branch_store.py`：

```python
"""最近创建分支的持久化存储，基于 shelve（与 cache.db 一致）。"""
from __future__ import annotations

import shelve
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Entry:
    workspace_path: str
    branch: str
    created_at: str


class RecentBranchStore:
    KEY = 'recent_branches'

    def __init__(self, shelve_path: str = 'cache.db') -> None:
        self._shelve_path = shelve_path

    def add(self,
            workspace_path: str,
            branch: str,
            created_at: Optional[str] = None) -> None:
        if created_at is None:
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with shelve.open(self._shelve_path, writeback=True) as db:
                items = list(db.get(self.KEY, []))
                # 去重：同 (workspace_path, branch) 更新时间戳
                items = [
                    it for it in items
                    if not (it['workspace_path'] == workspace_path
                            and it['branch'] == branch)
                ]
                items.insert(0, {
                    'workspace_path': workspace_path,
                    'branch': branch,
                    'created_at': created_at,
                })
                db[self.KEY] = items
        except Exception:
            pass

    def list_by_workspace(self,
                          workspace_path: str,
                          limit: int = 10) -> list[Entry]:
        try:
            with shelve.open(self._shelve_path) as db:
                items = db.get(self.KEY, [])
        except Exception:
            return []
        filtered = [
            Entry(
                workspace_path=it['workspace_path'],
                branch=it['branch'],
                created_at=it['created_at'],
            )
            for it in items
            if it['workspace_path'] == workspace_path
        ]
        return filtered[:limit]

    def list_workspaces(self) -> list[str]:
        try:
            with shelve.open(self._shelve_path) as db:
                items = db.get(self.KEY, [])
        except Exception:
            return []
        seen: list[str] = []
        for it in items:
            if it['workspace_path'] not in seen:
                seen.append(it['workspace_path'])
        return seen
```

- [ ] **运行测试验证通过**

Run: `pytest tests/test_recent_branch_store.py -v`
Expected: 3 PASS

- [ ] **暂不提交**（下一步加去重测试）

### Step 2.3: 写去重 + 截断 20 条 + list_workspaces 测试

- [ ] **追加测试** — 在 `tests/test_recent_branch_store.py`：

```python
def test_dedup_updates_timestamp(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/proj1', 'branch1', '2026-07-17 10:00:00')
    store.add('E:/proj1', 'branch2', '2026-07-17 11:00:00')
    # 重新添加 branch1，时间戳更新
    store.add('E:/proj1', 'branch1', '2026-07-17 12:00:00')
    items = store.list_by_workspace('E:/proj1')
    assert len(items) == 2
    assert items[0].branch == 'branch1'
    assert items[0].created_at == '2026-07-17 12:00:00'


def test_truncate_at_20(tmp_path):
    store = _make_store(tmp_path)
    for i in range(25):
        store.add('E:/proj1', f'branch{i}', f'2026-07-17 10:{i:02d}:00')
    items = store.list_by_workspace('E:/proj1', limit=100)
    assert len(items) == 20
    # branch24 是最后添加的
    assert items[0].branch == 'branch24'


def test_list_workspaces(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/proj1', 'b1', '2026-07-17 10:00:00')
    store.add('E:/proj2', 'b2', '2026-07-17 11:00:00')
    store.add('E:/proj1', 'b3', '2026-07-17 12:00:00')
    ws_list = store.list_workspaces()
    # 按最近活动排序（proj1 最后添加应在前面）
    assert ws_list == ['E:/proj1', 'E:/proj2']
```

- [ ] **更新实现以支持 20 条截断** — 在 `RecentBranchStore.add` 中 `db[self.KEY] = items` 之前加：

```python
                if len(items) > 20:
                    items = items[:20]
```

- [ ] **运行测试验证通过**

Run: `pytest tests/test_recent_branch_store.py -v`
Expected: 6 PASS

- [ ] **提交**

```bash
git add app/recent_branch_store.py tests/test_recent_branch_store.py
git commit -m "feat: RecentBranchStore 基于 cache.db 持久化最近分支"
```

---

## Task 3: `CheckoutToast` — 非模态悬浮通知

**Files:**
- Create: `app/ui/toast_notification.py`

**Interfaces:**
- Consumes: PyQt5（QFrame、QTimer、QApplication、QPushButton、QLabel）
- Produces:
  - `CheckoutToast(QFrame)` —
    - `__init__(parent_window: QWidget, on_checkout: Callable[[], None])`
    - `show_message(text: str, branch: str | None = None)` — `branch=None` 时隐藏切换按钮，只显示文案
    - 内部 `QTimer` 5 秒后自动 hide

**说明：** PyQt5 控件难以做有意义的自动化测试，本任务仅做实现 + 手动测试。

### Step 3.1: 实现 CheckoutToast

- [ ] **创建** `app/ui/toast_notification.py`：

```python
"""非模态 Toast 通知：用于创建分支成功后提示是否切换。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)


class CheckoutToast(QFrame):
    """右上角悬浮通知，5 秒自动隐藏。

    点 [切换] 触发 on_checkout 并立即 hide；
    点 [×] 或超时只 hide。
    """

    DURATION_MS = 5000

    def __init__(self,
                 parent_window: QWidget,
                 on_checkout: Callable[[], None]) -> None:
        super().__init__(parent_window)
        self._on_checkout = on_checkout
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self._build_ui()
        self._position_at_top_right(parent_window)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setStyleSheet('color: #222; font-size: 13px;')
        layout.addWidget(self._label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._checkout_btn = QPushButton('切换')
        self._checkout_btn.clicked.connect(self._handle_checkout)
        self._close_btn = QPushButton('×')
        self._close_btn.setFixedWidth(28)
        self._close_btn.clicked.connect(self.hide)
        btn_row.addWidget(self._checkout_btn)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self.setStyleSheet(
            'CheckoutToast {'
            '  background-color: #fff8dc;'
            '  border: 1px solid #d4b870;'
            '  border-radius: 6px;'
            '}'
        )
        self.setFixedWidth(360)

    def _position_at_top_right(self, window: QWidget) -> None:
        geo = window.geometry()
        screen_geo = QApplication.desktop().availableGeometry(window)
        # 相对屏幕，不相对父窗口（因为用了 Qt.Tool）
        x = screen_geo.x() + screen_geo.width() - self.width() - 24
        y = screen_geo.y() + 24
        self.move(x, y)

    def show_message(self, text: str, branch: Optional[str] = None) -> None:
        """显示通知。branch=None 时隐藏切换按钮（仅信息提示）。"""
        self._label.setText(text)
        self._checkout_btn.setVisible(branch is not None)
        self._branch = branch
        self.adjustSize()
        # 重新定位（宽度可能变化）
        parent = self.parentWidget()
        if parent is not None:
            self._position_at_top_right(parent)
        self.show()
        self._timer.start(self.DURATION_MS)

    def _handle_checkout(self) -> None:
        self._timer.stop()
        self.hide()
        try:
            self._on_checkout()
        except Exception:
            pass
```

- [ ] **冒烟测试** — 在项目根执行 Python REPL 或临时脚本：

```bash
python -c "
from PyQt5.QtWidgets import QApplication, QWidget
from app.ui.toast_notification import CheckoutToast
app = QApplication([])
w = QWidget(); w.resize(400, 300); w.show()
def cb(): print('checkout clicked')
t = CheckoutToast(w, cb); t.show_message('分支 xxx 创建成功，是否切换？', 'xxx')
app.exec_()
"
```

Expected: 黄色 toast 显示在右上角，5 秒消失，点切换打印 `checkout clicked`

- [ ] **提交**

```bash
git add app/ui/toast_notification.py
git commit -m "feat: CheckoutToast 非模态悬浮通知组件"
```

---

## Task 4: `workspace_tab` 集成 — toast + 入口按钮

**Files:**
- Modify: `app/ui/workspace_tab.py:16` (import)
- Modify: `app/ui/workspace_tab.py:581-594` (`init_create_branch_tab` 加按钮)
- Modify: `app/ui/workspace_tab.py:741-749` (`run_create_branch` 的 `on_success` 改写)

**Interfaces:**
- Consumes:
  - `quick_create_branch.smart_checkout`
  - `app.recent_branch_store.RecentBranchStore`
  - `app.ui.toast_notification.CheckoutToast`
- Produces:
  - `WorkspaceTab._recent_branch_store: RecentBranchStore`
  - `WorkspaceTab._toast: CheckoutToast`
  - `WorkspaceTab._checkout_recent(branch: str)` — 调 smart_checkout 并 QMessageBox 显示结果
  - `WorkspaceTab._show_checkout_toast(branch: str)` — 显示 toast
  - `WorkspaceTab._open_recent_branch_menu()` — 弹 QMenu 列出当前 workspace 最近分支

### Step 4.1: 加 import 和 init 字段

- [ ] **修改 imports** — 在 `app/ui/workspace_tab.py:16` 那一行之后追加：

```python
from quick_create_branch import create_branch as create_branch_func, get_remote_branches, smart_checkout as smart_checkout_func
```
（替换原来的 `from quick_create_branch import create_branch as create_branch_func, get_remote_branches`）

- [ ] **在 imports 末尾追加**（`from app.ui.commit_diff_dialog import CommitDiffDialog` 之后）：

```python
from app.recent_branch_store import RecentBranchStore
from app.ui.toast_notification import CheckoutToast
from PyQt5.QtWidgets import QMenu
```

### Step 4.2: 在 `__init__` 或 tab 初始化中创建 store 和 toast

- [ ] **找到 `WorkspaceTab.__init__`**（约 line 470-500），在 `self.init_create_branch_tab()` 调用之前插入：

```python
        self._recent_branch_store = RecentBranchStore()
        self._toast: CheckoutToast | None = None  # 延迟到主窗口可用时创建
```

### Step 4.3: 加入口按钮

- [ ] **在 `init_create_branch_tab`** 中，找到 `self.create_branch_button = QPushButton('创建分支')` 那一段（约 line 581），替换为：

```python
        btn_row = QHBoxLayout()
        self.create_branch_button = QPushButton('创建分支')
        self.checkout_recent_btn = QPushButton('切换到最近分支')
        self.checkout_recent_btn.clicked.connect(self._open_recent_branch_menu)
        btn_row.addWidget(self.create_branch_button)
        btn_row.addWidget(self.checkout_recent_btn)
        btn_row.addStretch()
        layout.addRow(btn_row)

        self.create_branch_output = QTextEdit()
        self.create_branch_output.setReadOnly(True)

        layout.addRow(self.create_branch_output)

        self.create_branch_button.clicked.connect(self.run_create_branch)
        self.refresh_remote_branches_button.clicked.connect(self.run_refresh_remote_branches)
        self.add_to_target_button.clicked.connect(self.move_to_target)
        self.remove_from_target_button.clicked.connect(self.remove_from_target)
        self.clear_new_branch_history_button.clicked.connect(self.run_clear_new_branch_history)

        self.create_branch_tab.setLayout(layout)
```

（原 `layout.addRow(self.create_branch_button)` 一行删掉）

### Step 4.4: 改写 `run_create_branch` 的 `on_success`，加 store.add + toast

- [ ] **替换** `on_success` 函数（约 line 741-748）为：

```python
        def on_success(result):
            all_output, any_success = result
            self.create_branch_output.setText('\n\n'.join(all_output))
            if any_success and new_branch:
                self.save_new_branch_to_history(new_branch)
                prefix = self.get_default_new_branch_prefix()
                self.new_branch_combo.setEditText(prefix)

                # 记录每条成功创建的分支到 recent_branches
                from datetime import datetime
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                new_branch_full_list = []
                for output, target in zip(all_output, target_branches):
                    if 'Branch created successfully!' in output:
                        full = new_branch + '__from__' + target.replace('/', '@')
                        new_branch_full_list.append(full)
                        self._recent_branch_store.add(self.path, full, ts)

                # 弹 toast（多分支时不直接切，仅提示）
                if len(new_branch_full_list) == 1:
                    self._show_checkout_toast(new_branch_full_list[0])
                elif len(new_branch_full_list) > 1:
                    self._show_checkout_toast_multi(len(new_branch_full_list))
```

### Step 4.5: 加 toast / menu / checkout 辅助方法

- [ ] **在 `run_create_branch` 方法之后**追加这些方法到 `WorkspaceTab` 类内：

```python
    def _show_checkout_toast(self, branch: str) -> None:
        def do_checkout():
            self._checkout_recent(branch)

        # 每次重建 toast（绑定独立闭包；旧的隐藏即可被 GC）
        top_window = self.window()
        self._toast = CheckoutToast(top_window, on_checkout=do_checkout)
        self._toast.show_message(
            f'分支 {branch} 创建成功，是否切换？', branch=branch,
        )

    def _show_checkout_toast_multi(self, count: int) -> None:
        top_window = self.window()
        self._toast = CheckoutToast(top_window, on_checkout=lambda: None)
        self._toast.show_message(
            f'已创建 {count} 条分支，可通过"切换到最近分支"按钮选择。',
            branch=None,
        )

    def _open_recent_branch_menu(self) -> None:
        entries = self._recent_branch_store.list_by_workspace(self.path, limit=10)
        if not entries:
            QMessageBox.information(self, '最近分支', '当前 workspace 暂无最近创建的分支记录。')
            return
        menu = QMenu(self)
        for e in entries:
            label = f'{e.branch}  ({e.created_at})'
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False, b=e.branch: self._checkout_recent(b))
        menu.exec_(self.checkout_recent_btn.mapToGlobal(self.checkout_recent_btn.rect().bottomLeft()))

    def _checkout_recent(self, branch: str) -> None:
        from PyQt5.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def _run():
            return smart_checkout_func(self.path, branch)

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

        from app.async_utils import run_blocking
        run_blocking(_run, on_success=on_success, parent=self)
```

- [ ] **手动测试**

  1. 启动程序，在 `workspace_tab` 选择一个 workspace 和目标分支，输入新分支名
  2. 点击"创建分支"
  3. Expected：分支创建成功后右上角弹 toast，点切换 → smart_checkout 执行 → QMessageBox 显示结果
  4. 重复一次创建另一条分支
  5. 点击"切换到最近分支"按钮 → QMenu 列出两条，选中一条 → 切换成功
  6. 工作区有冲突改动时测试 stash 路径（修改 target_branch 独有的文件，然后切换）

- [ ] **提交**

```bash
git add app/ui/workspace_tab.py
git commit -m "feat: workspace_tab 集成 smart_checkout + toast + 入口按钮"
```

---

## Task 5: `all_projects_tab` 集成 — 仅入口按钮（无 toast）

**Files:**
- Modify: `app/ui/all_projects_tab.py:27` (import)
- Modify: `app/ui/all_projects_tab.py:498-585` (`init_create_branch_tab` 加按钮)
- Modify: `app/ui/all_projects_tab.py:805-820` (`on_success` 末尾加 store.add)

**Interfaces:**
- Consumes: 同 Task 4（除 toast 外）
- Produces:
  - `AllProjectsTab._recent_branch_store`
  - `AllProjectsTab._open_recent_branch_menu()` — 两级 QMenu（workspace → 分支）

### Step 5.1: 加 imports 和字段

- [ ] **修改** `app/ui/all_projects_tab.py:27` 那一行之后追加：

```python
from quick_create_branch import smart_checkout as smart_checkout_func
from app.recent_branch_store import RecentBranchStore
from PyQt5.QtWidgets import QMenu
```

- [ ] **在 `__init__`** 中（或 `initUI` 开始处）加：

```python
        self._recent_branch_store = RecentBranchStore()
```

### Step 5.2: 加入口按钮

- [ ] **找到** `init_create_branch_tab` 中 `self.cb_create_button.clicked.connect(self.run_batch_create_branch)` 那一行（约 line 584），在其之前加：

```python
        self.cb_checkout_recent_btn = QPushButton('切换到最近分支')
        self.cb_checkout_recent_btn.clicked.connect(self._open_recent_branch_menu)
        # 把按钮加到 layout（找到 self.cb_create_button 所在 layout 之后）
```

- [ ] **找到** `self.cb_create_button` 被 addWidget 到 layout 的位置（约 line 575-583），在其旁边加 `self.cb_checkout_recent_btn`，例如：

```python
        cb_btn_row = QHBoxLayout()
        cb_btn_row.addWidget(self.cb_create_button)
        cb_btn_row.addWidget(self.cb_checkout_recent_btn)
        cb_btn_row.addStretch()
        layout.addLayout(cb_btn_row)
```
（替换原 `layout.addWidget(self.cb_create_button, ...)`）

### Step 5.3: `on_success` 中加 store.add

- [ ] **在 `run_batch_create_branch` 的 `_run` 函数中**，找到 `if 'Branch created successfully!' in out:` 那段（约 line 794），改为：

```python
                    if 'Branch created successfully!' in out:
                        success_any = True
                        # 记录到 recent_branches
                        full = new_branch + '__from__' + target.replace('/', '@')
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        try:
                            self._recent_branch_store.add(ws.path, full, ts)
                        except Exception:
                            pass
```

（`datetime` 已在文件顶部 import）

### Step 5.4: 加菜单辅助方法

- [ ] **在 `run_batch_create_branch` 之后**追加：

```python
    def _open_recent_branch_menu(self) -> None:
        workspaces = self._recent_branch_store.list_workspaces()
        if not workspaces:
            QMessageBox.information(self, '最近分支', '暂无最近创建的分支记录。')
            return
        menu = QMenu(self)
        import os
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

        run_blocking(_run, on_success=on_success, parent=self)
```

- [ ] **手动测试**

  1. 启动程序，切到"所有项目"tab，批量创建几条分支（跨 2+ workspace）
  2. 点击"切换到最近分支"按钮 → 两级菜单显示 workspace 子菜单
  3. 选择一个 workspace 子菜单中的分支 → smart_checkout 执行 → QMessageBox 显示结果
  4. 测试路径缺失项置灰（把某个 workspace 路径改名或先记一条假数据再启动）

- [ ] **提交**

```bash
git add app/ui/all_projects_tab.py
git commit -m "feat: all_projects_tab 集成 smart_checkout 入口按钮（无 toast）"
```

---

## Task 6: 集成测试 — 完整端到端验证

**Files:** 无修改

### Step 6.1: 全量单元测试

- [ ] **运行所有单测**

Run: `pytest tests/ -v`
Expected: 14 PASS（smart_checkout 8 + recent_branch_store 6）

### Step 6.2: 端到端手动测试清单

- [ ] **逐项验证**（参照 spec §11 手动测试清单）：

  1. ☐ 单项目模式，工作区干净 → 创建分支 → toast 出现 → 点切换 → 切换成功，无 stash
  2. ☐ 单项目模式，工作区有改动且无冲突 → 切换后改动在新分支可见
  3. ☐ 单项目模式，工作区有冲突改动 → toast → 点切换 → 提示已 stash，带 stash 消息
  4. ☐ toast 超时消失 → 通过入口按钮仍能切换
  5. ☐ 单项目模式，多 target 创建 → toast 提示"已加入列表"，不直接 checkout
  6. ☐ 批量模式创建 → 不弹 toast，入口按钮可见
  7. ☐ 入口按钮列表：当前 workspace 的最近 10 条，时间倒序，置灰失效项
  8. ☐ 路径不存在项点击 → 提示
  9. ☐ 重复创建同一分支 → 列表只一条，时间更新

### Step 6.3: 最终提交（如有修复）

- [ ] 如果手动测试发现问题并修复，单独 commit

```bash
git add ...
git commit -m "fix: ..."
```

- [ ] **完成**

---

## Self-Review 已完成

**Spec 覆盖检查：**
- §3 Smart Checkout 算法 → Task 1 ✓
- §4 Stash 消息格式 → Task 1.7 实现 ✓
- §5 Toast → Task 3 实现 + Task 4 集成 ✓
- §6 入口按钮（单项目 + 批量） → Task 4 + Task 5 ✓
- §7 最近分支存储 → Task 2 ✓
- §8 边界情况（已在 smart_checkout 测试 + store 测试覆盖）→ Tasks 1-2 ✓
- §11 手动测试清单 → Task 6.2 ✓

**Placeholder 扫描：** 无 TBD / TODO / "实现后补"

**类型一致性：** `smart_checkout(directory, target_branch) -> tuple[str, str]` 在所有调用点一致；`RecentBranchStore` 方法签名在 Task 2 定义、Task 4/5 调用一致；`CheckoutToast.__init__(parent_window, on_checkout)` 在 Task 3 定义、Task 4 调用一致。
