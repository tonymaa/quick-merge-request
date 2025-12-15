import subprocess

# 指定要执行命令的目录
directory = r'E:\lowcode\fe\tecq-lowcode-editor'
# 基于哪个分支创建
targetBranch = 'zhiming/advanced_responsive__from__SZ_dev' # 'qa/r5-s1'
# 新branch名
newBranch = 'zhiming/xx1'



# 定义要执行的 Git 命令
fetch_command = ['git', 'fetch', 'origin']
new_branch_name = newBranch + '__from__' + targetBranch.replace('/', '@')
branch_command = [
    'git',
    'branch', 
    new_branch_name, 
    'origin/' + targetBranch
]

set_branch_upstream_command = [
    'git',
    'branch',
    '--unset-upstream',
    new_branch_name
]

# 执行 git fetch
fetch_result = subprocess.run(fetch_command, cwd=directory, capture_output=True, text=True)

# 输出 fetch 命令的结果
print("Fetch STDOUT:")
print(fetch_result.stdout)
print("Fetch STDERR:")
print(fetch_result.stderr)

# 检查 fetch 命令是否成功
if fetch_result.returncode == 0:
    print("Fetch 命令执行成功！")
    
    # 执行 git branch
    branch_result = subprocess.run(branch_command, cwd=directory, capture_output=True, text=True)

    # 输出 branch 命令的结果
    print("Branch STDOUT:")
    print(branch_result.stdout)
    print("Branch STDERR:")
    print(branch_result.stderr)

    # 检查 branch 命令是否成功
    if branch_result.returncode == 0:
        print("Branch 命令执行成功！")
    else:
        print("Branch 命令执行失败！")

    # 设置upstream
    set_upstream_branch_result = subprocess.run(set_branch_upstream_command, cwd=directory, capture_output=True, text=True)

    # 输出 branch 命令的结果
    print("Branch STDOUT:")
    print(set_upstream_branch_result.stdout)
    print("Branch STDERR:")
    print(set_upstream_branch_result.stderr)

    # 检查 branch 命令是否成功
    if set_upstream_branch_result.returncode == 0:
        print("Branch 设置upstream成功!")
    else:
        print("Branch 设置upstream失败!")
else:
    print("Fetch 命令执行失败！")