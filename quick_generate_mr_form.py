import gitlab
import re
from urllib.parse import urlparse
import subprocess

MR_TITLE_MAX_LENGTH = 72


def truncate_mr_title(title):
    """强制截断 MR 标题，最多 MR_TITLE_MAX_LENGTH 字符。"""
    if not title:
        return ''
    title = title.strip()
    if len(title) <= MR_TITLE_MAX_LENGTH:
        return title
    return title[:MR_TITLE_MAX_LENGTH]


def run_command(command, directory):
    try:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True, shell=False, encoding='utf-8', errors='replace')
        return result.stdout, None
    except subprocess.CalledProcessError as e:
        return None, e.stderr

def _clean_branch_name(branch):
    """清理分支名，去除 git branch 输出的前缀标记"""
    branch = branch.strip()
    # 去除当前分支标记 (*) 和 worktree 检出分支标记 (+)
    if branch.startswith('* '):
        branch = branch[2:]
    elif branch.startswith('+ '):
        branch = branch[2:]
    return branch

def get_local_branches(directory):
    stdout, stderr = run_command(['git', 'branch'], directory)
    if stderr:
        return [], f"Error loading branches:\n{stderr}"
    branches = stdout.strip().split('\n')
    valid_branches = [_clean_branch_name(b) for b in branches if '__from__' in b]
    return valid_branches, "Branches loaded."

def get_all_local_branches(directory):
    stdout, stderr = run_command(['git', 'branch'], directory)
    if stderr:
        return [], f"Error loading branches:\n{stderr}"
    branches = stdout.strip().split('\n')
    all_branches = [_clean_branch_name(b) for b in branches if b.strip()]
    return all_branches, "All branches loaded."

def get_mr_defaults(project_path, source_branch, title_template, description_template):
    # Get last commit message
    stdout, stderr = run_command(['git', 'log', source_branch, '-1', '--pretty=%B'], project_path)
    if stderr:
        return None, f'Could not get last commit message: {stderr}'
    last_commit_message = stdout.strip()

    title = title_template.format(commit_message=last_commit_message)
    match_tg_number = re.search(r'tg-(\d+)', title, re.IGNORECASE)
    tg_number_from_title = ''
    if match_tg_number:
        tg_number_from_title = match_tg_number.group(1)

    # The description from config parser might have \n as literal strings, so replace them.
    description = description_template.replace('\n', '\n').format(commit_message=last_commit_message, tg_number_from_title=tg_number_from_title)
    return {'title': title, 'description': description}, None

def parse_target_branch_from_source(source_branch):
    """Parses the target branch from the source branch name (e.g., 'target_feature' -> 'target')."""
    try:
        return source_branch.split('__from__')[1].replace('@', '/')
    except Exception:
        return None

def _resolve_gitlab_project(directory, gitlab_url, token):
    """根据本地仓库的 remote URL 解析出对应的 GitLab 项目。

    Returns:
        (project, error): 成功时 error 为 None；失败时 project 为 None。
    """
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=token)
        gl.auth()
    except Exception as e:
        return None, f'GitLab authentication failed: {e}'

    stdout, stderr = run_command(['git', 'remote', '-v'], directory)
    if stderr:
        return None, f'Could not get remote URL: {stderr}'

    match = re.search(r'https?://[^\s]+', stdout or '')
    if not match:
        return None, 'Could not find an HTTP(S) remote URL.'

    remote_url = match.group(0)
    project_path = urlparse(remote_url).path.strip('/').replace('.git', '')
    try:
        project = gl.projects.get(project_path)
    except Exception as e:
        return None, f'Could not find GitLab project "{project_path}": {e}'
    return project, None


def _extract_username(user_info):
    """从 GitLab user 字段（dict）中提取展示名。"""
    if isinstance(user_info, dict):
        return user_info.get('name') or user_info.get('username') or ''
    return ''


def get_merge_requests(directory, gitlab_url, token, state='opened',
                       author=None, assignee=None, reviewer=None):
    """按筛选条件获取当前工作区对应 GitLab 项目的 MR 列表。

    Args:
        state: 'opened' / 'merged' / 'closed' / 'all'
        author: 创建人 username，None 或空表示不筛选
        assignee: 指派人 username，None 或空表示不筛选
        reviewer: 审查者 username，None 或空表示不筛选

    Returns:
        (mr_list, error): 成功时 error 为 None；失败时 mr_list 为 []。
        每个元素为 dict: {iid, title, source_branch, target_branch, author,
                          assignees, reviewers, created_at, web_url,
                          merge_status, state}
    """
    project, error = _resolve_gitlab_project(directory, gitlab_url, token)
    if error:
        return [], error

    params = {'state': state, 'all': True}
    if author:
        params['author_username'] = author
    if assignee:
        params['assignee_username'] = assignee
    if reviewer:
        params['reviewer_username'] = reviewer

    try:
        mrs = project.mergerequests.list(**params)
    except Exception as e:
        return [], f'Failed to load merge requests: {e}'

    result = []
    for mr in mrs:
        assignees = ', '.join(
            filter(None, (_extract_username(u) for u in (getattr(mr, 'assignees', None) or [])))
        )
        reviewers = ', '.join(
            filter(None, (_extract_username(u) for u in (getattr(mr, 'reviewers', None) or [])))
        )
        result.append({
            'iid': mr.iid,
            'title': getattr(mr, 'title', ''),
            'source_branch': getattr(mr, 'source_branch', ''),
            'target_branch': getattr(mr, 'target_branch', ''),
            'author': _extract_username(getattr(mr, 'author', None)),
            'assignees': assignees,
            'reviewers': reviewers,
            'created_at': getattr(mr, 'created_at', ''),
            'web_url': getattr(mr, 'web_url', ''),
            'merge_status': getattr(mr, 'merge_status', ''),
            'state': getattr(mr, 'state', ''),
        })
    return result, None


def merge_merge_request(directory, gitlab_url, token, mr_iid):
    """合并指定 iid 的 Merge Request。

    Returns:
        结果字符串（成功或友好的错误信息）。
    """
    project, error = _resolve_gitlab_project(directory, gitlab_url, token)
    if error:
        return error

    try:
        mr = project.mergerequests.get(mr_iid)
    except Exception as e:
        return f'Could not find MR !{mr_iid}: {e}'

    try:
        mr.merge()
        return f'MR !{mr_iid} 合并成功！'
    except Exception as e:
        return f'MR !{mr_iid} 合并失败: {e}'


def generate_mr(directory, gitlab_url, token, assignee_user, reviewer_user, source_branch, title, description, target_branch):
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=token)
        gl.auth()
    except Exception as e:
        return f'GitLab authentication failed: {e}'

    if not source_branch:
        return 'Please select a source branch.'

    # 强制截断标题：GitLab/Git 习惯标题不超过 72 字符
    title = truncate_mr_title(title)

    # Get project
    stdout, stderr = run_command(['git', 'remote', '-v'], directory)
    if stderr:
        return f'Could not get remote URL: {stderr}'

    remote_url = re.search(r'https?://[^\s]+', stdout).group(0)
    project_path = urlparse(remote_url).path.strip('/').replace('.git', '')
    project = gl.projects.get(project_path)

    try:
        assignee = gl.users.list(username=assignee_user)[0]
        reviewer = gl.users.list(username=reviewer_user)[0]
    except IndexError:
        return "Assignee or Reviewer not found."

    mr_data = {
        'source_branch': source_branch,
        'target_branch': target_branch,
        'title': title,
        'description': description,
        'assignee_id': assignee.id,
        'reviewer_ids': [reviewer.id]
    }

    try:
        mr = project.mergerequests.create(mr_data)
        return f'Successfully created MR!\nURL: {mr.web_url}'
    except Exception as e:
        return f'Failed to create MR: {e}'

def get_gitlab_usernames(gitlab_url, token):
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=token)
        gl.auth()
    except Exception as e:
        return [], f'GitLab authentication failed: {e}'
    try:
        users = gl.users.list(all=True)
        usernames = [u.username for u in users if getattr(u, 'username', None)]
        return usernames, None
    except Exception as e:
        return [], f'Failed to load users: {e}'


def get_branch_diff(directory, feature_branch):
    """获取feature分支和其对应的source分支之间的差异"""
    # 检查分支是否包含__from__模式
    if '__from__' not in feature_branch:
        return [], f'分支 {feature_branch} 不包含 __from__ 模式，无法比较差异'

    # 先执行 git fetch 更新远程分支信息
    # fetch_cmd = ['git', 'fetch', 'origin']
    # run_command(fetch_cmd, directory)

    # 从feature分支名中提取source分支名
    try:
        parts = feature_branch.split('__from__')
        feature_part = parts[0]
        source_part = parts[1].replace('@', '/')  # 将@替换回/

        # 获取feature分支的提交列表
        feature_cmd = ['git', 'log', '--oneline', f'origin/{source_part}..{feature_branch}']
        feature_stdout, feature_stderr = run_command(feature_cmd, directory)

        if feature_stderr:
            return [], f'获取 {feature_branch} 分支差异失败: {feature_stderr}'

        # 解析提交列表
        commits = []
        if feature_stdout.strip():
            for line in feature_stdout.strip().split('\n'):
                if line.strip():
                    commit_hash = line.split()[0]
                    commit_msg = ' '.join(line.split()[1:]) if len(line.split()) > 1 else ''
                    commits.append({
                        'hash': commit_hash,
                        'message': commit_msg,
                        'branch': feature_branch
                    })

        return commits, None
    except Exception as e:
        return [], f'解析分支名失败: {str(e)}'


def get_commits_between_branches(directory, source_branch, target_branch):
    """获取源分支相对于目标分支的新提交列表（源分支有但目标分支没有的提交）"""
    # 先执行 git fetch 更新远程分支信息
    fetch_cmd = ['git', 'fetch', 'origin']
    run_command(fetch_cmd, directory)

    try:
        # 获取源分支相对于目标分支的新提交
        # 使用 git log target_branch..source_branch 获取source分支有但target分支没有的提交
        log_cmd = ['git', 'log', '--oneline',  f'origin/{target_branch}..{source_branch}']
        stdout, stderr = run_command(log_cmd, directory)

        if stderr:
            return [], f'获取分支间提交失败: {stderr}'

        commits = []
        if stdout.strip():
            for line in stdout.strip().split('\n'):
                if line.strip():
                    parts = line.split(maxsplit=1)
                    commit_hash = parts[0] if parts else ''
                    commit_msg = parts[1] if len(parts) > 1 else ''
                    commits.append({
                        'hash': commit_hash,
                        'message': commit_msg
                    })

        return commits, None
    except Exception as e:
        return [], f'获取提交失败: {str(e)}'


def get_branch_details(directory):
    """一次性获取所有本地分支的详细信息（名称、提交时间、提交者、提交信息）。
    使用 git for-each-ref 避免 N 次子进程调用。

    Returns:
        (branches_list, error_message): 成功时 error_message 为 None
        每个 branch dict 包含: name, last_commit_date, author, subject, is_current
    """
    # 获取当前分支名
    current_stdout, current_stderr = run_command(
        ['git', 'branch', '--show-current'], directory
    )
    current_branch = current_stdout.strip() if current_stdout else ''

    # 一次性获取所有本地分支详情，按提交时间倒序排列
    fmt = '%(refname:short)|%(committerdate:iso8601)|%(authorname)|%(subject)'
    stdout, stderr = run_command(
        ['git', 'for-each-ref', f'--format={fmt}', '--sort=-committerdate', 'refs/heads/'],
        directory
    )
    if stderr:
        return [], f'获取分支详情失败: {stderr}'

    branches = []
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|', 3)
        if len(parts) < 4:
            continue
        name = parts[0]
        branches.append({
            'name': name,
            'last_commit_date': parts[1],
            'author': parts[2],
            'subject': parts[3],
            'is_current': name == current_branch,
        })
    return branches, None


def get_branches_no_merged(directory, target_branch):
    """获取相对于指定目标分支尚未合并的本地分支列表。

    Returns:
        (unmerged_set, error_message): 成功时 error_message 为 None
    """
    stdout, stderr = run_command(
        ['git', 'branch', '--no-merged', target_branch], directory
    )
    if stderr:
        return set(), stderr

    unmerged = set()
    for line in stdout.strip().split('\n'):
        cleaned = _clean_branch_name(line)
        if cleaned:
            unmerged.add(cleaned)
    return unmerged, None


def get_remote_branch_details(directory):
    """一次性获取所有远程分支的详细信息（名称、提交时间、提交者、提交信息）。
    使用 git for-each-ref refs/remotes/origin/，排除 HEAD 引用。

    Returns:
        (branches_list, error_message): 成功时 error_message 为 None
        每个 branch dict 包含: name, last_commit_date, author, subject
    """
    fmt = '%(refname:short)|%(committerdate:iso8601)|%(authorname)|%(subject)'
    stdout, stderr = run_command(
        ['git', 'for-each-ref', f'--format={fmt}', '--sort=-committerdate', 'refs/remotes/origin/'],
        directory
    )
    if stderr:
        return [], f'获取远程分支详情失败: {stderr}'

    branches = []
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|', 3)
        if len(parts) < 4:
            continue
        full_name = parts[0]  # e.g. "origin/SZ_dev"
        if 'HEAD' in full_name:
            continue
        # 去掉 "origin/" 前缀，只保留分支名
        name = full_name
        if name.startswith('origin/'):
            name = name[len('origin/'):]
        branches.append({
            'name': name,
            'last_commit_date': parts[1],
            'author': parts[2],
            'subject': parts[3],
        })
    return branches, None


def get_remote_url(directory):
    """获取远程仓库 URL（用于远程删除时提取 GitLab 项目信息）。

    Returns:
        (url_string, error_message)
    """
    stdout, stderr = run_command(['git', 'remote', 'get-url', 'origin'], directory)
    if stderr:
        return None, f'获取远程 URL 失败: {stderr}'
    return stdout.strip(), None
