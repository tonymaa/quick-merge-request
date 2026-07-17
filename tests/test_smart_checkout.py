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
