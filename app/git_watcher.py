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
        self._load_commits_from_cache()

    def add_commit_listener(self, callback: callable):
        """添加提交变化监听器"""
        if callback not in self.commit_listeners:
            self.commit_listeners.append(callback)

    def remove_commit_listener(self, callback: callable):
        """移除提交变化监听器"""
        if callback in self.commit_listeners:
            self.commit_listeners.remove(callback)

    def _notify_commit_listeners(self):
        """通知所有监听器有新提交"""
        with self.lock:
            commits_copy = self.commits.copy()
        for listener in self.commit_listeners:
            try:
                listener(commits_copy)
            except Exception:
                pass

    def _on_new_commit(self, commit_info: dict):
        """新提交回调"""
        with self.lock:
            # 避免重复记录
            for existing in self.commits:
                if existing.get('hash') == commit_info.get('hash'):
                    return
            self.commits.insert(0, commit_info)
            # 限制记录数量
            if len(self.commits) > self.max_commits:
                self.commits = self.commits[:self.max_commits]

        # 保存到缓存
        self._save_commits_to_cache()

        # 通知所有监听器
        self._notify_commit_listeners()

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
