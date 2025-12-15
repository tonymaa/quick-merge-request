import gitlab
import re
from urllib.parse import urlparse
import requests
import subprocess

# 指定要执行命令的目录
directory = r'E:\lowcode\fe\tecq-lowcode-editor'
merge_request_config = {
    "gitlab_url": "http://pd-gitlab.toppanecquaria.com:10080",
    "token": "glpat-1JeC8uvfQ9MoWsdzxyDy",
    "assignee": "zhangrongrong",
    "reviewer": "mengying",
    "title": "{lastCommitMessage}",
    "description_template": '''
## Description of Changes

{lastCommitMessage}

## Type of Change
(Please tick one)
- [x] Bug Fix
- [ ] Feature
- [ ] Refactoring
- [ ] Documentation
- [ ] Tests

## Taiga Number and link
TG-

## Checklist before review
- [x] I have performed a self-review of the code
- [x] No conflict with target branch
'''
}

# 1. 连接 GitLab
gl = gitlab.Gitlab(
    url=merge_request_config['gitlab_url'],   # 如果是私有 GitLab，换成你的地址
    private_token=merge_request_config['token']
)

def get_local_branches():
    # 定义要执行的 Git 命令
    branch_command = ['git', 'branch']

    # 执行 git branch
    branch_result = subprocess.run(branch_command, cwd=directory, capture_output=True, text=True)

    # 输出 branch 命令的结果
    if branch_result.returncode == 0:
        # branch_result.stdout 包含所有本地分支
        return branch_result.stdout.strip().split('\n')
    else:
        print("获取本地分支失败！")
        print("错误信息:", branch_result.stderr)
    return []

def filter_valid_branches(branches):
    return [item.strip() for item in branches if '__from__' in item]

def load_latest_commit_message(branch_name):
    # 定义要执行的 Git 命令
    command = ['git', 'log', branch_name, '-1', '--pretty=%B']
    try:
        # 执行 git log 命令
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)

        # 返回最新提交的消息
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"获取提交信息失败: {e.stderr.strip()}")
        return None


def get_project_namespace():
        # 定义要执行的 Git 命令
    command = ['git', 'remote', '-v']
    try:
        # 执行 git log 命令
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)

        # 返回最新提交的消息
        text = result.stdout.strip()
        url_pattern = r'https?://[^\s/$.?#].[^\s]*'
        urls = re.findall(url_pattern, text)
        project_id = urlparse(urls[0]).path.removesuffix('.git').strip('/')
        return project_id
    except subprocess.CalledProcessError as e:
        print(f"获取ProjectId: {e.stderr.strip()}")
        return None

def search_user_id(username):
    if username is None: return
    users = gl.users.list(username=username)  # 注意大小写敏感
    if users:
        user_id = users[0].id
        return user_id
        print(f"用户ID: {user_id}")
    else:
        print("用户未找到")


def create_mr(source_branch, target_branch, title, description):
    print('project_namespace: ' + get_project_namespace())
    project = gl.projects.get(get_project_namespace())
    mr_config = {
        'source_branch': source_branch,
        'target_branch': target_branch,
        'title': title,
        'description': description,
        'remove_source_branch': False,
        'squash': False
    }
    assignee_id = search_user_id(merge_request_config['assignee'])
    if assignee_id is not None: 
        mr_config['assignee_id'] = assignee_id
    reviewer_id = search_user_id(merge_request_config['reviewer'])
    if reviewer_id is not None: 
        mr_config['reviewer_ids'] = [reviewer_id]
    mr = project.mergerequests.create(mr_config)
    # 打印 MR 的完整响应对象
    print(mr)

    # 打印常用属性
    print("MR ID:", mr.id)
    print("MR IID（项目内编号）:", mr.iid)
    print("标题:", mr.title)
    print("状态:", mr.state)
    print("网页链接:", mr.web_url)
    print("源分支:", mr.source_branch)
    print("目标分支:", mr.target_branch)
    print("负责人 ID:", mr.assignee['id'] if mr.assignee else None)

def main():
    # 1. 查询符合规范的源分支
    valid_branches = filter_valid_branches(get_local_branches())
    # 源分支，目标分支自动根据源分支后缀解析
    user_selected_source_branch = valid_branches[1]
    target_branch = user_selected_source_branch.split('__from__')[1].replace('@', '/')
    print('用户选的源分支：' + '<' +user_selected_source_branch + '>')
    print('目标分支：' + '<' +target_branch + '>')
    latest_commit_message = load_latest_commit_message(user_selected_source_branch)
    print("用户分支最新提交信息：" + latest_commit_message)
    mr_title = merge_request_config['title'].format(lastCommitMessage=latest_commit_message)
    mr_desc = merge_request_config['description_template'].format(lastCommitMessage=latest_commit_message)
    # print("Merge Request 标题: " + mr_title)
    # print("Merge Request 描述: " + mr_desc)
    create_mr(user_selected_source_branch, target_branch, mr_title, mr_desc)

# main()
# print(get_gitlab_users())

# create_merge_request(get_project_id(), , , "", "test")
