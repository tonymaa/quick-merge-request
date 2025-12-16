import subprocess

def run_command(command, directory):
    try:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True, shell=True)
        return True, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr

def create_branch(directory, target_branch, new_branch):
    outputs = []

    # 1. Fetch
    outputs.append('Running git fetch...')
    success, stdout, stderr = run_command(['git', 'fetch', 'origin'], directory)
    outputs.append(f'STDOUT:\n{stdout}')
    outputs.append(f'STDERR:\n{stderr}')
    if not success:
        outputs.append('Fetch failed!')
        return '\n'.join(outputs)
    outputs.append('Fetch successful!')

    # 2. Create branch
    new_branch_name = new_branch + '__from__' + target_branch.replace('/', '@')
    outputs.append(f'Creating branch {new_branch_name}...')
    branch_command = ['git', 'branch', new_branch_name, f'origin/{target_branch}']
    success, stdout, stderr = run_command(branch_command, directory)
    outputs.append(f'STDOUT:\n{stdout}')
    outputs.append(f'STDERR:\n{stderr}')
    if not success:
        outputs.append('Branch creation failed!')
        return '\n'.join(outputs)
    outputs.append('Branch created successfully!')

    set_branch_upstream_command = [
        'git',
        'branch',
        '--unset-upstream',
        new_branch_name
    ]
        # 设置upstream
    set_upstream_branch_result = subprocess.run(set_branch_upstream_command, cwd=directory, capture_output=True, text=True)

    # 输出 branch 命令的结果
    outputs.append("Branch STDOUT:")
    outputs.append(set_upstream_branch_result.stdout)
    outputs.append("Branch STDERR:")
    outputs.append(set_upstream_branch_result.stderr)

    # 检查 branch 命令是否成功
    if set_upstream_branch_result.returncode == 0:
        outputs.append("Branch 设置upstream成功!")
    else:
        outputs.append("Branch 设置upstream失败!")
    
    return '\n'.join(outputs)

def get_remote_branches(directory):
    success, stdout, stderr = run_command(['git', 'branch', '-r'], directory)
    if not success:
        return [], f"Error loading remote branches:\n{stderr}"
    
    branches = stdout.strip().split('\n')
    # Clean up branch names (e.g., "  origin/master" -> "master")
    remote_branches = [b.strip().replace('origin/', '') for b in branches if 'HEAD' not in b]
    return remote_branches, "Remote branches loaded."

if __name__ == '__main__':
    # Example usage:
    directory = r'E:\lowcode\fe\tecq-lowcode-editor'
    target_branch = 'zhiming/advanced_responsive__from__SZ_dev'
    new_branch = 'zhiming/xx2'
    output = create_branch(directory, target_branch, new_branch)
    print(output)