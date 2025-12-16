import gitlab
import re
from urllib.parse import urlparse
import subprocess

def run_command(command, directory):
    try:
        # Set shell=False for security and correctness when passing a list
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

def get_mr_defaults(project_path, source_branch, title_template, description_template):
    # Get last commit message
    stdout, stderr = run_command(['git', 'log', source_branch, '-1', '--pretty=%B'], project_path)
    if stderr:
        return None, f'Could not get last commit message: {stderr}'
    last_commit_message = stdout.strip()

    title = title_template.format(commit_message=last_commit_message)
    # The description from config parser might have \n as literal strings, so replace them.
    description = description_template.replace('\n', '\n').format(commit_message=last_commit_message)
    return {'title': title, 'description': description}, None

def parse_target_branch_from_source(source_branch):
    """Parses the target branch from the source branch name (e.g., 'target_feature' -> 'target')."""
    try:
        return source_branch.split('__from__')[1].replace('@', '/')
    except Exception:
        return None

def generate_mr(directory, gitlab_url, token, assignee_user, reviewer_user, source_branch, target_branch, title, description):
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=token)
        gl.auth()
    except Exception as e:
        return f'GitLab authentication failed: {e}'

    if not source_branch:
        return 'Please select a source branch.'
    if not target_branch:
        return 'Please select a target branch.'
    
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
