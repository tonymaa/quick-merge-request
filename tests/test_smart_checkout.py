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



