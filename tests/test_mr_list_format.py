"""Tests for MR list approval / merge status formatting and API wrapper."""
from unittest.mock import MagicMock, patch

from app.ui.all_projects_tab import (
    _format_mr_approval,
    _format_mr_merge_status,
)
import quick_generate_mr_form as qgm


# ───────────────────── _format_mr_merge_status ─────────────────────

def test_merge_status_detailed_mergeable():
    text, color, tip = _format_mr_merge_status({'detailed_merge_status': 'mergeable'})
    assert text == '可合并'
    assert color == '#27ae60'
    assert 'mergeable' in tip


def test_merge_status_detailed_conflict_with_has_conflicts():
    text, color, tip = _format_mr_merge_status({
        'detailed_merge_status': 'blocked_status',
        'has_conflicts': True,
    })
    assert text == '冲突'
    assert color == '#e74c3c'
    assert 'has_conflicts: true' in tip


def test_merge_status_detailed_unknown_value_falls_back_to_raw():
    text, color, _ = _format_mr_merge_status({'detailed_merge_status': 'some_new_gitlab_status'})
    assert text == 'some_new_gitlab_status'
    assert color == '#e74c3c'


def test_merge_status_legacy_can_be_merged():
    text, color, _ = _format_mr_merge_status({'merge_status': 'can_be_merged'})
    assert text == '可合并'
    assert color == '#27ae60'


def test_merge_status_legacy_cannot_be_merged():
    text, color, _ = _format_mr_merge_status({'merge_status': 'cannot_be_merged'})
    assert text == '不可合并'
    assert color == '#e74c3c'


def test_merge_status_empty_returns_blank():
    text, color, tip = _format_mr_merge_status({})
    assert text == ''
    assert color is None
    assert tip is None


# ───────────────────── _format_mr_approval ─────────────────────

def test_approval_not_loaded_returns_dash():
    text, color = _format_mr_approval({})
    assert text == '—'
    assert color == '#888'


def test_approval_approved():
    text, color = _format_mr_approval({'approved': True, 'approvals_required': 2, 'approvals_left': 0})
    assert text == '✓ 已批'
    assert color == '#27ae60'


def test_approval_not_approved_with_progress():
    text, color = _format_mr_approval({'approved': False, 'approvals_required': 2, 'approvals_left': 1})
    assert text == '✗ 1/2'
    assert color == '#e74c3c'


def test_approval_not_required():
    text, color = _format_mr_approval({'approved': False, 'approvals_required': 0, 'approvals_left': 0})
    assert text == '无需审批'
    assert color == '#888'


# ───────────────────── get_merge_requests field mapping ─────────────────────

def _make_fake_mr(
    iid=1, title='MR', source='feat', target='main',
    approved=False, approvals_left=1, approvals_required=1,
    detailed='blocked_status', has_conflicts=False,
    state='opened', legacy_status='cannot_be_merged',
):
    """Build a MagicMock that mimics python-gitlab MR object."""
    mr = MagicMock()
    mr.iid = iid
    mr.title = title
    mr.source_branch = source
    mr.target_branch = target
    mr.author = {'name': 'Alice'}
    mr.assignees = []
    mr.reviewers = []
    mr.created_at = '2026-07-17T10:00:00Z'
    mr.web_url = 'http://gitlab.example/mr/1'
    mr.merge_status = legacy_status
    mr.detailed_merge_status = detailed
    mr.has_conflicts = has_conflicts
    mr.state = state

    appr = MagicMock()
    appr.approved = approved
    appr.approvals_left = approvals_left
    appr.approvals_required = approvals_required
    mr.approvals.get.return_value = appr
    return mr


def _patch_project(mr_list):
    project = MagicMock()
    project.mergerequests.list.return_value = mr_list
    return project


@patch.object(qgm, '_resolve_gitlab_project')
def test_get_merge_requests_without_approvals_returns_none_fields(mock_resolve):
    mock_resolve.return_value = (_patch_project([_make_fake_mr()]), None)
    mrs, err = qgm.get_merge_requests('/repo', 'http://gl', 'tok')
    assert err is None
    assert len(mrs) == 1
    m = mrs[0]
    assert m['detailed_merge_status'] == 'blocked_status'
    assert m['has_conflicts'] is False
    assert m['approved'] is None
    assert m['approvals_left'] is None
    assert m['approvals_required'] is None
    m['iid']._mock_mock.assert_not_called if hasattr(m['iid'], '_mock_mock') else None  # noop guard
    # approvals API should NOT be called when with_approvals=False
    assert not mock_resolve.return_value[0].mergerequests.list.return_value[0].approvals.get.called


@patch.object(qgm, '_resolve_gitlab_project')
def test_get_merge_requests_with_approvals_populates_approval_fields(mock_resolve):
    mock_resolve.return_value = (_patch_project([_make_fake_mr(
        approved=True, approvals_left=0, approvals_required=2
    )]), None)
    mrs, err = qgm.get_merge_requests('/repo', 'http://gl', 'tok', with_approvals=True)
    assert err is None
    m = mrs[0]
    assert m['approved'] is True
    assert m['approvals_left'] == 0
    assert m['approvals_required'] == 2


@patch.object(qgm, '_resolve_gitlab_project')
def test_get_merge_requests_approvals_failure_is_silent(mock_resolve):
    mr = _make_fake_mr()
    mr.approvals.get.side_effect = RuntimeError('api error')
    mock_resolve.return_value = (_patch_project([mr]), None)
    mrs, err = qgm.get_merge_requests('/repo', 'http://gl', 'tok', with_approvals=True)
    assert err is None
    assert mrs[0]['approved'] is None
    assert mrs[0]['approvals_left'] is None
