"""
Git 仓库监听模块 - 监听 Git 仓库的提交变化
"""
import os
import subprocess
import shelve
from threading import Thread, Lock
from typing import Dict, List, Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent


class CreateMRRequest:
    """创建 MR 请求"""
    def __init__(self, repo_path: str, branch: str, workspace_name: str):
        self.repo_path = repo_path
        self.branch = branch
        self.workspace_name = workspace_name


class GitEventHandler(FileSystemEventHandler):
    """Git 文件变化事件处理器"""

    def __init__(self, repo_path: str, workspace_name: str, on_new_commit: Callable[[dict], None]):
        super().__init__()
        self.repo_path = repo_path
        self.workspace_name = workspace_name
        self.on_new_commit = on_new_commit
        self.last_commit = self._get_current_commit()
        self.lock = Lock()
        self.debounce_timer = None

    def _get_current_commit(self) -> Optional[dict]:
        """获取当前最新提交信息"""
        try:
            # 获取当前分支名
            branch_result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'HEAD'

            result = subprocess.run(
                ['git', 'log', '-1', '--pretty=%H|%s|%an|%ai'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split('|')
                if len(parts) >= 4:
                    return {
                        'hash': parts[0],
                        'message': parts[1],
                        'author': parts[2],
                        'date': parts[3],
                        'repo': self.workspace_name,  # 使用 workspace_name 而不是文件夹名
                        'repo_path': self.repo_path,
                        'branch': current_branch
                    }
        except Exception:
            pass
        return None

    def on_modified(self, event):
        """文件修改事件处理"""
        # 标准化路径分隔符，兼容 Windows 和 Unix
        normalized_path = event.src_path.replace('\\', '/')

        # 只监听 Git 相关文件和目录
        # HEAD 文件、refs/heads 下的分支文件、logs/HEAD 和 logs/refs 下的日志文件
        git_ref_patterns = [
            '.git/HEAD',
            '.git/refs/heads/',
            '.git/logs/HEAD',
            '.git/logs/refs/'
        ]

        if not any(pattern in normalized_path for pattern in git_ref_patterns):
            return

        with self.lock:
            current_commit = self._get_current_commit()
            if current_commit and current_commit != self.last_commit:
                if self.last_commit is None or current_commit['hash'] != self.last_commit['hash']:
                    self.last_commit = current_commit
                    self.on_new_commit(current_commit)


class GitWatcher:
    """Git 仓库监听器 - 管理多个仓库的监听"""

    CACHE_KEY = 'git_commits_history'

    def __init__(self):
        self.observers: Dict[str, Observer] = {}
        self.commits: List[dict] = []
        self.lock = Lock()
        self.max_commits = 100  # 最多保存100条提交记录
        self.repo_workspace_names: Dict[str, str] = {}  # repo_path -> workspace_name 映射
        self.commit_listeners: List[callable] = []  # 新提交监听器列表
        self.initial_commit_count = 0  # 记录初始化时的提交数量，用于检测新提交
        self.main_window = None  # 主窗口引用，用于打开通知对话框
        self.pending_create_mr_requests: List[CreateMRRequest] = []  # 待处理的创建 MR 请求
        self._load_commits_from_cache()
        self.initial_commit_count = len(self.commits)

    def set_main_window(self, main_window):
        """设置主窗口引用，用于通知按钮点击时打开对话框"""
        self.main_window = main_window

    def add_commit_listener(self, callback: callable):
        """添加提交变化监听器"""
        if callback not in self.commit_listeners:
            self.commit_listeners.append(callback)

    def remove_commit_listener(self, callback: callable):
        """移除提交变化监听器"""
        if callback in self.commit_listeners:
            self.commit_listeners.remove(callback)

    def _notify_commit_listeners(self, is_new: bool = False):
        """通知所有监听器有新提交

        Args:
            is_new: 是否是真正的新提交（非缓存加载的）
        """
        with self.lock:
            commits_copy = self.commits.copy()
        for listener in self.commit_listeners:
            try:
                # 检查监听器是否接受两个参数（commits, is_new）
                import inspect
                sig = inspect.signature(listener)
                if len(sig.parameters) >= 2:
                    listener(commits_copy, is_new)
                else:
                    listener(commits_copy)
            except Exception:
                pass

    def _on_new_commit(self, commit_info: dict):
        """新提交回调"""
        is_new_commit = False
        with self.lock:
            # 避免重复记录
            for existing in self.commits:
                if existing.get('hash') == commit_info.get('hash'):
                    return
            self.commits.insert(0, commit_info)
            # 限制记录数量
            if len(self.commits) > self.max_commits:
                self.commits = self.commits[:self.max_commits]
            # 判断是否是真正的新提交（非缓存加载的）
            is_new_commit = len(self.commits) > self.initial_commit_count
            # 如果是真正的新提交（非缓存加载），显示系统通知
            print(is_new_commit)
            if is_new_commit and self.commits:
                self._show_system_notification(self.commits[0])

        # 保存到缓存
        self._save_commits_to_cache()

        # 通知所有监听器，传递是否是新提交的标志
        self._notify_commit_listeners(is_new_commit)

    def _load_commits_from_cache(self):
        """从缓存加载提交历史"""
        try:
            with shelve.open('cache.db') as db:
                cached_commits = db.get(self.CACHE_KEY, [])
                # 只保留最近的 max_commits 条
                self.commits = cached_commits[:self.max_commits] if cached_commits else []
        except Exception:
            self.commits = []

    def _save_commits_to_cache(self):
        """保存提交历史到缓存"""
        try:
            with shelve.open('cache.db', writeback=False) as db:
                db[self.CACHE_KEY] = self.commits.copy()
        except Exception:
            pass

    def add_repository(self, repo_path: str, workspace_name: str) -> bool:
        """
        添加要监听的仓库

        Args:
            repo_path: 仓库路径
            workspace_name: 工作区名称

        Returns:
            是否成功添加
        """
        repo_path = os.path.abspath(repo_path)
        git_dir = os.path.join(repo_path, '.git')

        # 保存 workspace_name 映射
        self.repo_workspace_names[repo_path] = workspace_name

        # 检查是否是 Git 仓库
        if not os.path.exists(git_dir):
            return False

        # 如果已经在监听，先停止
        if repo_path in self.observers:
            self.remove_repository(repo_path)

        try:
            # 创建事件处理器，传递 workspace_name
            event_handler = GitEventHandler(
                repo_path,
                workspace_name,
                lambda commit: self._on_new_commit(commit)
            )

            # 创建观察者
            observer = Observer()
            observer.schedule(event_handler, git_dir, recursive=True)
            observer.start()

            self.observers[repo_path] = observer
            return True
        except Exception:
            return False

    def remove_repository(self, repo_path: str):
        """移除监听的仓库"""
        repo_path = os.path.abspath(repo_path)
        if repo_path in self.observers:
            try:
                self.observers[repo_path].stop()
                self.observers[repo_path].join()
            except Exception:
                pass
            del self.observers[repo_path]
        # 清理 workspace_name 映射
        if repo_path in self.repo_workspace_names:
            del self.repo_workspace_names[repo_path]

    def get_commits(self) -> List[dict]:
        """获取所有监听到的提交记录"""
        with self.lock:
            return self.commits.copy()

    def clear_commits(self):
        """清空提交记录"""
        with self.lock:
            self.commits.clear()
        # 同时清空缓存
        self._save_commits_to_cache()

    def get_repo_name(self, repo_path: str) -> str:
        """获取仓库名称"""
        return os.path.basename(os.path.abspath(repo_path))

    def stop_all(self):
        """停止所有监听"""
        for repo_path in list(self.observers.keys()):
            self.remove_repository(repo_path)

    def __del__(self):
        """析构函数 - 确保所有观察者都被正确停止"""
        self.stop_all()


    def _show_system_notification(self, commit: Dict):
        """显示 Windows 系统通知（带按钮）"""
        # 优先尝试使用 windows_toast（支持按钮）
        try:
            from windows_toasts import InteractableWindowsToaster, Toast, ToastButton
            import ctypes

            # 必须在主线程中创建和显示 Windows 通知
            # 检查当前是否在主线程
            current_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            main_thread_id = getattr(self, '_main_thread_id', None)

            if main_thread_id is None:
                # 首次运行，记录主线程 ID
                self._main_thread_id = current_thread_id
                main_thread_id = current_thread_id

            if current_thread_id != main_thread_id:
                # 不在主线程，使用 QTimer 切换到主线程执行
                if self.main_window:
                    from PyQt5.QtCore import QTimer
                    # 延迟执行，避免阻塞
                    QTimer.singleShot(100, lambda c=commit: self._show_system_notification(c))
                    return

            # 使用 InteractableWindowsToaster 以支持按钮
            toaster = InteractableWindowsToaster('GitLab 快捷工具')
            newToast = Toast()

            # 设置通知文本
            title = f"新提交检测 - {commit.get('repo', 'Unknown')}"
            message = f"{commit.get('message', 'No message')[:50]}\n作者: {commit.get('author', 'Unknown')}"

            newToast.text_fields = [title, message]

            # 添加按钮 - 点击后打开通知对话框或创建 MR
            newToast.AddAction(ToastButton('查看详情', 'view_details'))
            newToast.AddAction(ToastButton('创建MR', 'create_mr'))

            # 处理通知激活事件（按钮点击或通知本身被点击）
            # 将回调保存为实例属性，避免被垃圾回收
            def on_activated(event_args):
                """通知被激活时的回调"""
                print(f"[DEBUG] 通知被触发，event_args: {event_args}")
                print(f"[DEBUG] event_args 类型: {type(event_args)}")

                # 检查是否点击了按钮
                args = None
                if hasattr(event_args, 'arguments'):
                    args = event_args.arguments
                    print(f"[DEBUG] arguments: {args}")
                elif hasattr(event_args, 'input'):
                    args = event_args.input
                    print(f"[DEBUG] input: {args}")

                if args == 'view_details':
                    print(f"[DEBUG] 匹配到 view_details，main_window: {self.main_window}")
                    if self.main_window:
                        print(f"[DEBUG] 准备调用 show_commit_notifications")
                        # 必须在主线程中执行 UI 操作，使用 QTimer 切换线程
                        from PyQt5.QtCore import QTimer, QCoreApplication                        # 确保在主线程中执行
                        app = QCoreApplication.instance()
                        if app:
                            QTimer.singleShot(0, self.main_window.show_commit_notifications)
                            print(f"[DEBUG] 已调度 show_commit_notifications 到主线程")
                        else:
                            print(f"[DEBUG] 错误: 无法获取 QCoreApplication 实例")

                elif args == 'create_mr':
                    print(f"[DEBUG] 匹配到 create_mr，main_window: {self.main_window}")
                    if self.main_window:
                        print(f"[DEBUG] 准备创建 MR")

                        # 获取 commit 信息
                        repo_path = commit.get('repo_path')
                        branch = commit.get('branch')
                        workspace_name = commit.get('repo', '')

                        print(f"[DEBUG] repo_path: {repo_path}, branch: {branch}, workspace_name: {workspace_name}")

                        # 验证参数
                        if not repo_path:
                            print(f"[DEBUG] 错误: 缺少仓库路径信息")
                            return

                        if not branch or branch == 'HEAD':
                            print(f"[DEBUG] 错误: 不在任何分支上")
                            return

                        # 将请求添加到队列，由主窗口处理
                        request = CreateMRRequest(repo_path, branch, workspace_name)
                        self.pending_create_mr_requests.append(request)
                        print(f"[DEBUG] 已将创建 MR 请求添加到队列，队列长度: {len(self.pending_create_mr_requests)}")

            newToast.on_activated = on_activated
            # 保存回调引用，防止被垃圾回收
            self._toast_callback = newToast.on_activated

            # 显示通知（不需要线程，windows_toasts 内部处理异步）
            toaster.show_toast(newToast)
            return
        except ImportError:
            # windows_toast 未安装，尝试降级方案
            pass
        except Exception:
            # 其他错误，尝试降级方案
            import traceback
            traceback.print_exc()  # 打印错误信息用于调试

        # 降级方案：使用 win10toast
        self._show_fallback_notification(commit)

    def _show_fallback_notification(self, commit: Dict):
        """降级方案：使用 win10toast 显示通知（不支持按钮）"""
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()

            # 在后台线程中显示通知，避免阻塞 UI
            def show_toast():
                try:
                    title = f"新提交检测 - {commit.get('repo', 'Unknown')}"
                    message = f"{commit.get('message', 'No message')[:50]}\n作者: {commit.get('author', 'Unknown')}"
                    # 显示通知，持续5秒
                    toaster.show_toast(
                        title,
                        message,
                        icon_path=None,  # 可以指定图标路径
                        duration=5,
                        threaded=True
                    )
                except Exception:
                    pass

            # 在单独的线程中执行，避免阻塞
            thread = Thread(target=show_toast, daemon=True)
            thread.start()
        except ImportError:
            # win10toast 也未安装，静默忽略
            pass
        except Exception:
            pass


# 全局单例
_global_watcher: Optional[GitWatcher] = None
_watcher_lock = Lock()


def get_global_watcher() -> GitWatcher:
    """获取全局 Git 监听器单例"""
    global _global_watcher
    with _watcher_lock:
        if _global_watcher is None:
            _global_watcher = GitWatcher()
        return _global_watcher
