# 🧠 Quick Merge Request

快速创建 GitLab/GitHub 合并请求（Merge Request / Pull Request）的命令行 + 可视化交互工具。

A command-line + interactive tool to quickly create GitLab/GitHub Merge Requests (MR / PR) without manually visiting the web UI.

---

## 📌 Features | 功能特点

* ⭐ 一键快速生成 Merge Request / Pull Request
* 🛠️ 支持分支创建与 MR 信息交互填写
* 📋 自动缓存常用信息，减少重复输入
* 💻 支持 Python 脚本启动 / Windows 批处理启动

---

## 📦 安装 | Installation

1. 克隆仓库 Clone the repo：

```bash
git clone https://github.com/tonymaa/quick-merge-request.git
cd quick-merge-request
```

2. 安装 Python 依赖 Install dependencies:

```bash
pip install -r requirements.txt
```

如果你不使用全局 Python 环境，建议先创建虚拟环境（如 `venv`）后再安装。

---

## 🚀 快速开始 | Quick Start

### 🔹 启动主程序

```bash
python main.py
```

程序将提示你输入：

* 当前分支或新分支名称
* Merge Request / Pull Request 标题与描述

然后自动生成并打开合并请求（或输出生成链接）。

### 🔹 Windows 一键启动

双击项目根目录下的 `quick_MR.bat` 也可以快速启动。

---

## 📄 配置文件 | Configuration

项目会在首次运行时生成本地配置文件；

* `config.xml`: 本地配置（如默认分支、用户名等），请不要被提交到 Git
* 缓存文件 `cache.db*`: 用于记录历史输入

建议复制示例配置并自行修改：

```bash
cp config.example.xml config.xml
```

将 `config.xml` 添加到 `.gitignore` 中已避免泄露配置。

---

## 📁 推荐 .gitignore 配置

以下内容建议加入你的 `.gitignore`：

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/

# OS
.DS_Store

# local config / cache
cache.db*
config.xml
```

这样可以忽略本地缓存与用户专用配置。

---

## 🧠 工作流程 | How It Works

本工具通过提示与配置自动执行以下流程：

1. 输入或选择 Git 分支
2. 填写合并请求标题与描述
3. 调用 Git 命令推送分支
4. 生成并打开对应的 Merge Request / Pull Request 页面链接

---

## 📌 注意事项 | Notes

* 请确保本地已经配置好 Git 并能够正常访问远程仓库（SSH 或 HTTP）
* 仅负责生成请求内容，代码仍需你自行推动审核与合并
* 配置文件请使用示例并自行修改

---

## 🛠 示例用法 | Usage Example

一个典型的使用流程：

1. 新建分支（示例）

```bash
python quick_create_branch.py feature/add-login
```

2. 填写 MR 表单

```bash
python quick_generate_mr_form.py
```

3. 或直接运行主入口

```bash
python main.py
```

---

## 💡 如何提交MR / PR（参考）

GitHub 上的 Pull Request / Merge Request 是提交代码变更的关键机制，通过比较两个分支并请求合并，以便团队协作与代码审查。 ([GitHub RSP][1])

---

## 🤝 贡献 | Contributing

欢迎通过 Issues 或 Pull Requests 贡献你的改进建议与功能增强！

贡献步骤：

1. Fork 本仓库
2. 创建新分支 `feat/xxx`
3. 提交修改并 push
4. 发起 Pull Request

---

## 📄 许可 | License

本项目采用 **MIT License**，详见 LICENSE 文件。

---

## 📣 联系作者 | Contact

如果你在使用过程中遇到任何问题，欢迎发起 Issue 或联系仓库作者。

---

