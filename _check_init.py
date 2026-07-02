import os, glob

root = '酒店系统'
# 找所有包含 .py 的目录，检查是否有 __init__.py
dirs_with_py = set()
for f in glob.glob(f'{root}/**/*.py', recursive=True):
    d = os.path.dirname(f)
    dirs_with_py.add(d)

print('=== 缺少 __init__.py 的包目录 ===')
for d in sorted(dirs_with_py):
    init = os.path.join(d, '__init__.py')
    if not os.path.exists(init):
        rel = os.path.relpath(d, root)
        py_count = len([f for f in os.listdir(d) if f.endswith('.py')])
        print(f'  {rel}/ ({py_count} 个 .py 文件)')

print()

# 检查 fcntl 在 Windows 下的处理
print('=== fcntl 使用场景 ===')
for f in glob.glob(f'{root}/**/*.py', recursive=True):
    with open(f, encoding='utf-8') as fh:
        for i, line in enumerate(fh.readlines(), 1):
            if 'fcntl' in line and 'import' in line:
                rel = os.path.relpath(f, root)
                print(f'  {rel}:{i}: {line.strip()[:100]}')

print()

# 检查 nfc 模块使用
print('=== nfc 模块使用场景 ===')
for f in glob.glob(f'{root}/**/*.py', recursive=True):
    with open(f, encoding='utf-8') as fh:
        for i, line in enumerate(fh.readlines(), 1):
            if 'import nfc' in line or 'from nfc' in line:
                rel = os.path.relpath(f, root)
                print(f'  {rel}:{i}: {line.strip()[:100]}')
