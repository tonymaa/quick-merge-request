import gitlab
import re
from urllib.parse import urlparse
import subprocess

def run_command(command, directory):
    try:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True, shell=False)
        return result.stdout, None
    except subprocess.CalledProcessError as e:
        return None, e.stderr

def get_local_branches(directory):
    stdout, stderr = run_command(['git', 'branch'], directory)
    if stderr:
        return [], f"Error loading branches:\n{stderr}"
    branches = stdout.strip().split('\n')
    valid_branches = [b.strip().replace('* ', '') for b in branches if '__from__' in b]
    return valid_branches, "Branches loaded."

def get_all_local_branches(directory):
    stdout, stderr = run_command(['git', 'branch'], directory)
    if stderr:
        return [], f"Error loading branches:\n{stderr}"
    branches = stdout.strip().split('\n')
    all_branches = [b.strip().replace('* ', '') for b in branches if b.strip()]
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

def generate_mr(directory, gitlab_url, token, assignee_user, reviewer_user, source_branch, title, description, target_branch):
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=token)
        gl.auth()
    except Exception as e:
        return f'GitLab authentication failed: {e}'

    if not source_branch:
        return 'Please select a source branch.'

    
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
    fetch_cmd = ['git', 'fetch', 'origin']
    run_command(fetch_cmd, directory)

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
