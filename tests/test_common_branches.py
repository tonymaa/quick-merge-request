"""Tests for AllProjectsTab._collect_common_branches intersection logic.

Avoids spinning up the full Qt UI — only exercises the pure logic via a
minimal stub object that supplies the same `_recent_branch_store` interface.
"""
from app.recent_branch_store import RecentBranchStore
from app.ui.all_projects_tab import AllProjectsTab


class _Stub:
    """Minimal stub mimicking AllProjectsTab for _collect_common_branches."""
    def __init__(self, store):
        self._recent_branch_store = store

    # Bind the real method from the class (not the instance) so `self`
    # inside the method receives our stub.
    _collect_common_branches = AllProjectsTab._collect_common_branches


class _FakeWorkspace:
    def __init__(self, path):
        self.path = path


def _make_store(tmp_path):
    return RecentBranchStore(str(tmp_path / 'cache.db'))


def test_common_branches_intersection_basic(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/projA', 'feat/login', '2026-07-17 10:00:00')
    store.add('E:/projA', 'feat/x', '2026-07-17 11:00:00')
    store.add('E:/projB', 'feat/login', '2026-07-17 12:00:00')
    store.add('E:/projB', 'feat/y', '2026-07-17 13:00:00')

    stub = _Stub(store)
    result = stub._collect_common_branches([_FakeWorkspace('E:/projA'), _FakeWorkspace('E:/projB')])
    assert len(result) == 1
    branch, ts = result[0]
    assert branch == 'feat/login'
    # latest ts across both projects
    assert ts == '2026-07-17 12:00:00'


def test_common_branches_empty_when_no_intersection(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/projA', 'a1', '2026-07-17 10:00:00')
    store.add('E:/projB', 'b1', '2026-07-17 11:00:00')

    stub = _Stub(store)
    result = stub._collect_common_branches([_FakeWorkspace('E:/projA'), _FakeWorkspace('E:/projB')])
    assert result == []


def test_common_branches_three_projects(tmp_path):
    store = _make_store(tmp_path)
    store.add('E:/A', 'shared', '2026-07-17 10:00:00')
    store.add('E:/A', 'onlyA', '2026-07-17 11:00:00')
    store.add('E:/B', 'shared', '2026-07-17 12:00:00')
    store.add('E:/B', 'onlyB', '2026-07-17 13:00:00')
    store.add('E:/C', 'shared', '2026-07-17 14:00:00')
    store.add('E:/C', 'onlyC', '2026-07-17 15:00:00')

    stub = _Stub(store)
    result = stub._collect_common_branches([
        _FakeWorkspace('E:/A'), _FakeWorkspace('E:/B'), _FakeWorkspace('E:/C')
    ])
    assert len(result) == 1
    assert result[0][0] == 'shared'
    # latest of the three timestamps
    assert result[0][1] == '2026-07-17 14:00:00'


def test_common_branches_sorted_by_latest_desc(tmp_path):
    store = _make_store(tmp_path)
    # two shared branches, different "latest" timestamps
    store.add('E:/A', 'older', '2026-07-17 09:00:00')
    store.add('E:/A', 'newer', '2026-07-17 10:00:00')
    store.add('E:/B', 'older', '2026-07-17 12:00:00')  # later than A's newer
    store.add('E:/B', 'newer', '2026-07-17 11:00:00')

    stub = _Stub(store)
    result = stub._collect_common_branches([_FakeWorkspace('E:/A'), _FakeWorkspace('E:/B')])
    # 'older' has latest 12:00, 'newer' has latest 11:00 → older first
    assert [b for b, _ in result] == ['older', 'newer']


def test_common_branches_empty_workspaces(tmp_path):
    store = _make_store(tmp_path)
    stub = _Stub(store)
    assert stub._collect_common_branches([]) == []


def test_common_branches_single_workspace_returns_all(tmp_path):
    """单项目时，「共有」退化为该项目全部最近分支。"""
    store = _make_store(tmp_path)
    store.add('E:/A', 'a1', '2026-07-17 10:00:00')
    store.add('E:/A', 'a2', '2026-07-17 11:00:00')

    stub = _Stub(store)
    result = stub._collect_common_branches([_FakeWorkspace('E:/A')])
    assert [b for b, _ in result] == ['a2', 'a1']  # sorted desc


def test_common_branches_caps_at_20(tmp_path):
    store = _make_store(tmp_path)
    for i in range(30):
        b = f'b{i:02d}'
        store.add('E:/A', b, f'2026-07-17 10:{i:02d}:00')
        store.add('E:/B', b, f'2026-07-17 11:{i:02d}:00')
    stub = _Stub(store)
    result = stub._collect_common_branches([_FakeWorkspace('E:/A'), _FakeWorkspace('E:/B')])
    assert len(result) == 20
