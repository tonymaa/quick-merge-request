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
                # Cap per-workspace (not global) so multi-project users keep
                # a meaningful MRU list in each workspace.
                ws_items = [it for it in items
                            if it['workspace_path'] == workspace_path]
                if len(ws_items) > 20:
                    drop_ids = {id(it) for it in ws_items[20:]}
                    items = [it for it in items if id(it) not in drop_ids]
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
