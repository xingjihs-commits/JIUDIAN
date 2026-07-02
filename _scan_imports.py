import glob, os, ast
from collections import defaultdict

root = '酒店系统'

# 收集所有 .py 文件路径（相对路径，无后缀）
all_py = {}
for f in glob.glob(f'{root}/**/*.py', recursive=True):
    rel = os.path.relpath(f, root).replace(os.sep, '/')
    rel_no_ext = rel.replace('.py', '')
    all_py[rel_no_ext] = f

# 外部库白名单（标准库 + 已安装的第三方）
EXTERNAL = {
    'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 
    'PySide6.QtPrintSupport', 'PySide6.QtNetwork',
    'requests', 'urllib3', 'certifi', 'charset_normalizer', 'idna',
    'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
    'cryptography.hazmat.backends', 'cryptography.hazmat.primitives.ciphers',
    'sqlite3', 'sqlcipher3',
    'qrcode', 'qrcode.image', 'qrcode.image.pil', 'qrcode.image.svg',
    'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
    'reportlab', 'reportlab.lib', 'reportlab.lib.pagesizes', 'reportlab.lib.units',
    'reportlab.platypus', 'reportlab.pdfbase', 'reportlab.pdfbase.ttfonts',
    'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
    'psutil', 'screeninfo', 'ntplib',
    'pywinauto', 'pywinauto.timings', 'pywinauto.keyboard',
    'pyscard', 'construct', 'tabulate', 'access_parser', 'dbfread',
    'pyodbc', 'pyserial', 'comtypes', 'pywin32', 'six', 'cffi', 
    'et_xmlfile', 'pycparser',
    'shiboken6', 'shiboken6.Shiboken',
    'numpy', 'telegram', 'matplotlib',
    # 标准库
    'os', 'sys', 'json', 'logging', 'threading', 'time', 'datetime',
    'hashlib', 're', 'uuid', 'copy', 'math', 'functools', 'pathlib',
    'typing', 'abc', 'enum', 'io', 'csv', 'glob', 'shutil', 'struct',
    'zlib', 'codecs', '__future__',
    'pytest', 'unittest', 'unittest.mock', 'tempfile', 'contextlib',
    'inspect', 'random', 'traceback', 'pickle', 'base64', 'binascii',
    'collections', 'itertools', 'operator', 'asyncio', 'urllib.parse',
    'subprocess', 'importlib', 'textwrap', 'webbrowser', 'platform',
    'ctypes', 'configparser', 'xml.etree', 'xml.etree.ElementTree',
    'argparse', 'getpass', 'numbers', 'decimal', 'fractions',
    'statistics', 'string', 'pkgutil', 'dataclasses', 'weakref',
    'pprint', 'atexit', 'signal', 'mmap',
    'socket', 'ssl', 'email', 'http',
    'ast', 'dis',
}

# 解析 import
missing = defaultdict(list)

for f in glob.glob(f'{root}/**/*.py', recursive=True):
    rel = os.path.relpath(f, root).replace(os.sep, '/')
    try:
        with open(f, encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), filename=f)
    except SyntaxError as e:
        print(f'[SYNTAX ERROR] {rel}: {e}')
        continue
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if mod in EXTERNAL:
                    continue
                # 检查是否存在于项目中
                parts = mod.split('.')
                found = False
                for i in range(1, len(parts)+1):
                    key = '/'.join(parts[:i])
                    if key in all_py:
                        found = True
                        break
                if not found:
                    missing[mod].append(rel)
                    
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level > 0:
                continue
            mod = node.module
            if mod in EXTERNAL:
                continue
            parts = mod.split('.')
            found = False
            for i in range(1, len(parts)+1):
                key = '/'.join(parts[:i])
                if key in all_py:
                    found = True
                    break
            if not found:
                missing[mod].append(rel)

print('=== 项目中不存在的被引用模块 ===')
for mod in sorted(missing):
    files = missing[mod]
    print(f'\n{mod}')
    for f in files:
        print(f'  -> {f}')
