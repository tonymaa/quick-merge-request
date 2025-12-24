# 🧠 Quick Merge Request

快速创建 GitLab 合并请求（Merge Request）的桌面工具（PyQt5 GUI）。

---

## 📌 主要特性

- 创建分支与合并请求的整合界面
- MR 确认框优化：关键信息简洁展示、详细信息可展开
- 新分支名历史缓存（`cache.db`），历史可一键清空
- 源分支下拉支持“显示所有分支”开关；默认按历史前缀优先排序
- 新分支前缀支持动态模板（支持 `{tab_name}`）
- 工作区欢迎页与延迟初始化：不自动打开历史工作区，首次选择时加载
- 快速 Cherry-pick 辅助：按前缀聚合，显示差异摘要
- 全局样式（QSS）与下拉搜索增强（可模糊匹配）

---

## 📦 安装

```bash
pip install -r requirements.txt
```

建议在虚拟环境中安装依赖。

---

## 🚀 启动

- Python 启动：`python main.py`
- Windows 一键：双击 `quick_MR.bat`

---

## � 项目结构（关键模块）

- `app/ui/main_window.py`：主窗口 `App`（工作区标签、配置读写、样式应用）
- `app/ui/workspace_tab.py`：工作区页签 `WorkspaceTab`（创建分支、创建 MR、Cherry-pick）
- `app/widgets.py`：通用控件与交互（如 `NoWheelComboBox`、下拉搜索增强）
- `app/styles.py`：全局样式加载与应用（读取 `styles.qss`）
- `quick_create_branch.py`：分支创建与远程分支获取
- `quick_generate_mr_form.py`：本地分支获取、默认值生成、MR 创建、用户获取
- `config.xml`：本地配置（工作区与 GitLab 配置）
- `cache.db`：本地缓存（新分支名历史）

---

## ⚙️ 配置说明（`config.xml`）

- `gitlab`：
  - `gitlab_url`：GitLab 地址
  - `private_token`：私有 Token
  - `assignee`：默认指派人
  - `reviewer`：默认审查者
  - `title_template`：标题模板，示例：`Draft: {commit_message}`
  - `description_template`：描述模板，示例：`{commit_message}`
- `new_branch_prefix`：新分支前缀模板，支持 `{tab_name}` 占位符
- `workspaces/workspace`：工作区配置
  - 属性 `name` 工作区名，`path` 本地路径
  - 嵌套 `target_branch` 用于保存目标分支列表

首次运行会自动创建最小化 `config.xml`。

---

## 🧰 使用要点

- 创建分支成功后，分支名会保存到 `cache.db:new_branch_history`，并保持编辑框默认值为模板前缀
- 源分支下拉默认按历史前缀排序；勾选“显示所有分支”后显示全部本地分支
- 工作区标签右键支持重命名；移除无效工作区路径时会给出提示并自动清理配置

---

## �️ 忽略文件建议（`.gitignore`）

```gitignore
__pycache__/
*.py[cod]
.venv/
.DS_Store
cache.db*
config.xml
```

---

## 🤝 贡献

欢迎通过 Issues 或 Pull Requests 贡献功能与改进：

1. Fork 仓库
2. 创建分支 `feat/xxx`
3. 提交并 push
4. 发起 Pull Request

---

## 📄 许可

MIT License（详见 `LICENSE`）。

