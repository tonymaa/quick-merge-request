# Checkout 最近创建分支 — 设计文档

**Date:** 2026-07-17
**Project:** quick_merge_request
**Author:** zhiming

## 1. 背景与目标

`quick_create_branch.py` 只创建本地分支，不切换工作区。用户创建完分支后需手动 `git checkout` 才能开始工作，体验割裂。

**目标：** 创建分支成功后，让用户能"一键切换"到刚创建的分支；同时提供独立的"切换最近分支"入口，支持切换历史中任意一条。

**非目标：** 不实现 stash 自动 pop（保留用户控制）；不做远程分支 checkout（只处理本地刚创建的）。

## 2. 用户场景

- **场景 A（单项目创建）：** 在 `workspace_tab` 创建分支成功 → 主窗口右上角弹非模态 toast 5 秒，点 `[切换]` 立即切换；超时后通过界面按钮也能切
- **场景 B（批量创建）：** 在 `all_projects_tab` 批量创建不弹 toast（避免多 toast 干扰），但界面加入口按钮，先选 workspace 再选分支
- **场景 C（稍后切换）：** 任何时刻点 `切换到最近分支` 按钮 → 显示当前 workspace 最近分支列表 → 选一条切换

## 3. Smart Checkout 算法

核心思想：优先直接 checkout（git 会自动把无冲突的改动带到新分支）；失败（冲突）时 stash → 重试；stash 后仍失败则回滚 stash。

```python
def smart_checkout(directory, target_branch):
    cur = current_branch(directory)
    if cur == target_branch:
        return Result("skip", "已在目标分支")

    # 尝试 1：直接 checkout（无冲突时 git 自动把改动带到新分支）
    r = run(['git', 'checkout', target_branch], directory)
    if r.ok:
        return Result("ok", f"已切换到 {target_branch}，工作区改动已保留")

    # 失败 → stash 后重试
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f'auto-stash: {cur} → {target_branch} [{ts}]'
    s = run(['git', 'stash', 'push', '-m', msg], directory)
    if not s.ok:
        return Result("fail", f"stash 失败: {s.stderr}")

    r2 = run(['git', 'checkout', target_branch], directory)
    if r2.ok:
        stash_list = run(['git', 'stash', 'list'], directory).stdout.splitlines()
        ref = stash_list[0].split(':')[0] if stash_list else 'stash@{0}'
        return Result("ok_stash",
                      f"已切换到 {target_branch}\n"
                      f"原工作区有冲突已 stash: {ref}\n"
                      f"stash 消息: {msg}\n"
                      f"恢复: git stash pop")

    # checkout 仍失败 → 回滚 stash 避免改动被锁
    run(['git', 'stash', 'pop'], directory)
    return Result("fail", f"切换失败（改动已恢复）: {r2.stderr}")
```

### 状态语义

| status | 含义 | 通知样式 |
|--------|------|---------|
| `skip` | 已在目标分支 | info |
| `ok` | 切换成功，改动保留 | success |
| `ok_stash` | 切换成功，改动已 stash | success + stash ref |
| `fail` | 切换失败（已回滚） | error |

## 4. Stash 消息格式

```
auto-stash: <source_branch> → <target_branch> [<YYYY-MM-DD HH:MM:SS>]
```

示例：`auto-stash: SZ_dev → zhiming/feature__from__SZ_dev [2026-07-17 18:30:15]`

通知文案直接带出整条 stash 消息，方便用户在终端手动 `git stash list` 找回。

## 5. Toast 通知（仅单项目模式）

### 实现

- `QFrame` 派生，`Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`
- `WA_ShowWithoutActivating` + `WA_TransparentForMouseEvents=False`（按钮可点）
- 固定宽度 ~360px，位置：主窗口右上角（`QApplication.desktop().availableGeometry()` 计算）
- `QTimer.singleShot(5000, hide)` 自动隐藏
- 子控件：`QLabel`（文案）+ `QPushButton("切换")` + `QPushButton("×")`

### 行为

- 创建分支成功 → 触发 toast 显示
- 点 `[切换]` → 立即隐藏 → 调用 `smart_checkout` → 用普通 `QMessageBox` 显示结果
- 点 `[×]` 或超时 → 仅隐藏，不 checkout
- 单次创建多条目标分支时（`run_create_branch` 多 target 情况）：toast 文案改为"已创建 N 条分支，见最近列表"，不提供直接 checkout 按钮

### 文件位置

新增 `app/ui/toast_notification.py`，类 `CheckoutToast`。主窗口持有单实例，复用避免重复创建。

## 6. "切换到最近分支" 入口按钮

### 单项目模式（`workspace_tab`）

- 位置：`init_create_branch_tab` 内，创建按钮同一行右侧
- 点击 → `QMenu` 列出当前 workspace 最近 10 条分支
- 每项：`<branch>  (<相对时间，如"3分钟前">)`
- 选中 → `smart_checkout(self.path, branch)`

### 批量模式（`all_projects_tab`）

- 位置：`init_create_branch_tab` 内，创建按钮旁
- 点击 → 两级菜单：
  1. 第一级：列出"有最近分支记录的 workspace"（路径 + 名称）
  2. 第二级：该 workspace 最近 10 条分支
- 选中 → `smart_checkout(workspace_path, branch)`

### 路径校验

- 列表加载时检查 `os.path.isdir(workspace_path)`
- 失效项保留显示但置灰，点击提示"workspace 路径不存在"

## 7. 最近分支存储

**沿用现有 `shelve` (`cache.db`) 约定**（与 `save_new_branch_to_history` 一致），新增 key `recent_branches`。不引入新文件。

### Schema（存于 `cache.db['recent_branches']`）

```python
# list[dict]，按 created_at 倒序
[
  {
    "workspace_path": "E:/lowcode/fe/tecq-lowcode-editor",
    "branch": "zhiming/feature__from__SZ_dev",
    "created_at": "2026-07-17 18:30:15"
  }
]
```

### 管理类

```python
class RecentBranchStore:
    def __init__(self, shelve_path: str = 'cache.db'): ...
    def add(self, workspace_path: str, branch: str, created_at: str) -> None: ...
    def list_by_workspace(self, workspace_path: str, limit: int = 10) -> list[Entry]: ...
    def list_workspaces(self) -> list[str]: ...
```

内部实现用 `with shelve.open(self.shelve_path, writeback=True) as db: ...`，异常捕获静默降级（与现有 history 读写一致）。

### 规则

- 最多保留 20 条（按时间倒序），超出截断
- 同 `(workspace_path, branch)` 重复创建时更新 `created_at`，不新增条目
- 读写失败时静默降级（不阻塞主流程），与现有 history 行为一致

### 写入时机

在 `workspace_tab.run_create_branch` / `all_projects_tab.run_batch_create_branch` 中，对每条创建成功的分支调用 `store.add(...)`。**判断成功：** 输出文本包含 `'Branch created successfully!'`。

## 8. 边界情况

| 情况 | 处理 |
|------|------|
| 工作区干净（无改动） | 第一次 checkout 直接成功，无需 stash |
| 当前分支 == 目标分支 | 返回 `skip`，toast/通知不显示或显示 info |
| stash 成功但 checkout 仍失败 | 自动 `git stash pop` 回滚，返回 `fail` |
| stash 本身失败 | 直接返回 `fail`，不重试 checkout |
| `cache.db['recent_branches']` 读写异常 | 捕获异常 → 入口按钮显示"最近分支列表为空" |
| workspace 路径已不存在 | 列表中置灰，点击提示 |
| 单次创建多 target 分支 | toast 不直接切，仅提示"已加入最近列表" |
| 批量模式多 workspace 多分支 | 只存记录，不弹 toast |

## 9. 模块组织

```
quick_create_branch.py
  + def smart_checkout(directory, target_branch) -> Result
  + def current_branch(directory) -> str | None  (辅助)

app/recent_branch_store.py  (新建)
  class RecentBranchStore, Entry  (内部读写 cache.db)

app/ui/toast_notification.py  (新建)
  class CheckoutToast(QFrame)

app/ui/workspace_tab.py
  + self.checkout_recent_btn
  + self.toast  (持有 CheckoutToast 单例)
  - init_create_branch_tab: 加按钮
  - run_create_branch 成功分支后 store.add + toast.show
  - run_checkout_recent: 弹 QMenu

app/ui/all_projects_tab.py
  + self.checkout_recent_btn
  - init_create_branch_tab: 加按钮
  - run_batch_create_branch 成功分支后 store.add
  - run_checkout_recent: 两级 QMenu
```

## 10. 测试计划

`quick_create_branch.smart_checkout` 是纯 subprocess 编排，用 `unittest.mock.patch` 模拟 subprocess.run 测试：

- `test_skip_when_already_on_target`
- `test_ok_direct_checkout_no_local_changes`
- `test_ok_direct_checkout_with_non_conflicting_changes`
- `test_ok_stash_when_conflict`
- `test_fail_rollback_when_checkout_fails_after_stash`
- `test_fail_when_stash_fails`

`RecentBranchStore` 用 `tmp_path` fixture（指向临时 shelve 文件）：

- `test_add_and_list_by_workspace`
- `test_dedup_updates_timestamp`
- `test_truncate_at_20`
- `test_workspace_filter`

UI 层暂不写自动化测试（PyQt 自动化收益低），靠手动测试清单覆盖。

## 11. 手动测试清单

1. 单项目模式，工作区干净 → 创建分支 → toast 出现 → 点切换 → 切换成功，无 stash
2. 单项目模式，工作区有改动且无冲突 → 同上，切换后改动在新分支可见
3. 单项目模式，工作区有冲突改动 → toast → 点切换 → 提示已 stash，带 stash 消息
4. toast 超时消失 → 通过入口按钮仍能切换
5. 单项目模式，多 target 创建 → toast 提示"已加入列表"，不直接 checkout
6. 批量模式创建 → 不弹 toast，入口按钮可见
7. 入口按钮列表：当前 workspace 的最近 10 条，时间倒序，置灰失效项
8. 路径不存在项点击 → 提示
9. 重复创建同一分支 → 列表只一条，时间更新

## 12. 风险与权衡

- **不做 stash auto-pop**：用户可能忘记 pop，但避免自动 pop 引发的新冲突。权衡：通知里明确给出 `git stash pop` 命令
- **Toast 非模态**：用户可能错过提示。权衡：入口按钮作为持久兜底
- **shelve 存最近分支**：未做并发写保护，但本工具是单进程 GUI，无并发风险；`cache.db` 已被现有 history 使用，沿用同一存储
- **smart_checkout 失败回滚 stash**：若 `stash pop` 本身冲突会留下半回滚状态。极端情况，文案中提示用户检查 `git status`
