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
