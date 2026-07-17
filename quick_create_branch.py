import subprocess
from datetime import datetime

def run_command(command, directory):
    try:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', check=True, shell=True)
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
    set_upstream_branch_result = subprocess.run(set_branch_upstream_command, cwd=directory, capture_output=True, text=True, encoding='utf-8', errors='replace')

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


def current_branch(directory: str) -> str | None:
    """返回当前分支名；detached HEAD 或失败返回 None。"""
    success, stdout, stderr = run_command(
        ['git', 'branch'], directory
    )
    if not success:
        return None
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('* '):
            return stripped[2:].strip()
    return None


def smart_checkout(directory: str, target_branch: str) -> tuple[str, str]:
    """智能切换分支：直接 checkout → 失败 stash → 仍失败回滚。

    返回 (status, message)。status:
      - 'skip':     已在目标分支
      - 'ok':       切换成功，改动保留
      - 'ok_stash': 切换成功，改动已 stash
      - 'fail':     切换失败（已回滚 stash 或 stash 本身失败）
    """
    cur = current_branch(directory)
    if cur == target_branch:
        return ('skip', f'已在目标分支 {target_branch}')

    # 第一次尝试：直接 checkout
    success, stdout, stderr = run_command(
        ['git', 'checkout', target_branch], directory
    )
    if success:
        return ('ok', f'已切换到 {target_branch}，工作区改动已保留')

    # 失败 → stash 后重试
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur_display = cur or '<detached>'
    stash_msg = f'auto-stash: {cur_display} → {target_branch} [{ts}]'
    s_ok, s_out, s_err = run_command(
        ['git', 'stash', 'push', '-m', stash_msg], directory
    )
    if not s_ok:
        return ('fail', f'stash 失败: {s_err}')

    c2_ok, c2_out, c2_err = run_command(
        ['git', 'checkout', target_branch], directory
    )
    if c2_ok:
        l_ok, l_out, l_err = run_command(
            ['git', 'stash', 'list'], directory
        )
        ref = 'stash@{0}'
        if l_ok:
            lines = [ln for ln in l_out.splitlines() if ln.strip()]
            if lines:
                ref = lines[0].split(':')[0]
        return ('ok_stash',
                f'已切换到 {target_branch}\n'
                f'原工作区有冲突已 stash: {ref}\n'
                f'stash 消息: {stash_msg}\n'
                f'恢复: git stash pop')

    # checkout 仍失败 → 回滚 stash
    run_command(['git', 'stash', 'pop'], directory)
    return ('fail', f'切换失败（改动已恢复）: {c2_err}')

if __name__ == '__main__':
    # Example usage:
    directory = r'E:\lowcode\fe\tecq-lowcode-editor'
    target_branch = 'zhiming/advanced_responsive__from__SZ_dev'
    new_branch = 'zhiming/xx2'
    output = create_branch(directory, target_branch, new_branch)
    print(output)