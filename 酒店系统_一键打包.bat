@echo off
chcp 936 >nul
title 酒店系统 - 一键打包（厂家标准构建流水线）
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================
echo   酒店系统 — 厂家标准构建流水线
echo   1 仅打包 Solid.exe（单文件，U盘携带去现场测试）
echo   2 完整构建（编译桥接 + 打包EXE + 生成安装包）
echo ============================================
echo.

set PYTHON_64="C:\Users\FF\AppData\Local\Programs\Python\Python312\python.exe"
set PYTHON_32="C:\Users\FF\AppData\Local\Programs\Python\Python312-32\python.exe"
set ISCC="C:\Users\FF\AppData\Local\Programs\Inno Setup 6\ISCC.exe"

if not exist %PYTHON_64% (
    echo [错误] 找不到 64 位 Python：%PYTHON_64%
    pause
    exit /b 1
)

echo 请选择：
echo  1 —— 仅打包 Solid.exe（去酒店测试用，约3分钟）
echo  2 —— 完整构建（编译桥接 + Solid.exe + 安装包，约8分钟）
echo.
set /p MODE="输入 1 或 2，然后回车："
if "%MODE%"=="" set MODE=1

cd /d "%~dp0\酒店系统"

echo.
echo ────────────────────────────────────────────
echo [1/8] 杀死残留的 Solid.exe 进程
echo         （防止 PermissionError，避免旧包残留）
echo ────────────────────────────────────────────
taskkill /f /im Solid.exe 2>nul
if !ERRORLEVEL! equ 0 (
    echo    ✓ 已杀死残留进程
) else (
    echo    - 无残留进程
)

echo.
echo ────────────────────────────────────────────
echo [2/8] 预检：验证核心模块能否被 Python 正常 import
echo         （抓语法错误，避免 PyInstaller 静默跳过模块）
echo ────────────────────────────────────────────
set PREFAIL=0
for %%m in (
    ui_helpers
    database
    i18n
    event_bus
    vendor_lockdown
    design_tokens
    theme_palette
    money_utils
    nav_manifest
    shop_icon_pack
    frontdesk_ui
    app_main
    main_window
    main_window_impl
    bridgecore.panic_recovery
    bridgecore.config
    bridgecore.fault_manager
    bridgecore.orchestrator
) do (
    %PYTHON_64% -c "import %%m" 2>&1 | findstr /i "error traceback exception" >nul
    if !ERRORLEVEL! equ 0 (
        echo    ✗ %%m —— import 失败！
        %PYTHON_64% -c "import %%m" 2>&1 | findstr /i "error"
        set PREFAIL=1
    ) else (
        echo    ✓ %%m
    )
)
if !PREFAIL! neq 0 (
    echo.
    echo [致命错误] 有模块无法被 Python 导入，请先修复语法错误或缺失依赖。
    echo 不要继续打包，否则编译出的 EXE 运行时会崩溃。
    pause
    exit /b 1
)
echo    → 全部通过，继续打包。

echo.
echo ────────────────────────────────────────────
echo [3/8] 安装/更新打包依赖
echo ────────────────────────────────────────────
%PYTHON_64% -m pip install -q pyinstaller pillow requests qrcode psutil screeninfo cryptography access-parser 2>nul

echo.
echo ────────────────────────────────────────────
echo [3.5/8] 刷新桌面图标 app_icon.ico + mark.png
echo         源图 assets\app_icon.png（磨砂透明，四主题通用）
echo         ICO  墨绿底+烫金边，小尺寸只留金色 emblem（tools\build_app_icon.py）
echo         打包 EXE / 安装包 / 桌面快捷方式 均用此 ICO
echo ────────────────────────────────────────────
%PYTHON_64% tools\build_app_icon.py
if !ERRORLEVEL! neq 0 (
    echo [错误] 图标生成失败！请确认 assets\app_icon.png 存在。
    pause
    exit /b 1
)
if not exist assets\app_icon.ico (
    echo [错误] 缺少 assets\app_icon.ico
    pause
    exit /b 1
)
echo    ✓ app_icon.ico / mark.png 已就绪

if "%MODE%"=="2" (
    echo.
    echo ────────────────────────────────────────────
    echo [4/8] 编译 32 位桥接发卡程序（rfl_bridge_32.exe）
    echo ────────────────────────────────────────────
    if not exist %PYTHON_32% (
        echo [错误] 找不到 32 位 Python：%PYTHON_32%
        echo 门锁桥接程序无法编译，安装包将不支持门锁发卡。
        echo 请安装 32 位 Python 后重试。
    ) else (
        %PYTHON_32% -m pip install -q pyinstaller 2>nul
        %PYTHON_32% -m PyInstaller --onefile --console ^
            --name rfl_bridge_32 lock_adapters/rfl_bridge_32.py ^
            --distpath lock_adapters --workpath build\bridge_build --specpath build >nul 2>&1
        if exist lock_adapters\rfl_bridge_32.exe (
            echo    ✓ 成功：lock_adapters\rfl_bridge_32.exe
        )
    )
)

echo.
echo ────────────────────────────────────────────
echo [5/8] 清理旧的打包产物
echo ────────────────────────────────────────────
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist Output rmdir /s /q Output

echo.
echo ────────────────────────────────────────────
echo [6/8] 打包 Solid.exe（单文件版）
echo ────────────────────────────────────────────
%PYTHON_64% -m PyInstaller Solid_onefile.spec --noconfirm
if !ERRORLEVEL! neq 0 (
    echo [错误] PyInstaller 打包失败！
    pause
    exit /b 1
)

for /f %%s in ('powershell -NoProfile -Command ^
    "(Get-Item dist\Solid.exe).Length / 1MB"') do set EXE_SIZE=%%s
echo    ✓ 成功：dist\Solid.exe（约 !EXE_SIZE:~0,5! MB）

echo.
echo ────────────────────────────────────────────
echo [6.5/8] 复制运行时资源到 dist/
echo         （redist/access/ — Access 驱动静默安装包）
echo ────────────────────────────────────────────
if exist redist\access (
    if not exist dist\redist mkdir dist\redist
    xcopy /e /i /q redist\access dist\redist\access >nul 2>&1
    if exist dist\redist\access\AccessDatabaseEngine_X64.exe (
        echo    ✓ redist\access 已复制
    ) else (
        echo    ⚠ redist\access 复制失败，但不影响主程序启动
    )
) else (
    echo    - redist\access 目录不存在，跳过
)

echo.
echo ────────────────────────────────────────────
echo [7/8] 检查 warning 文件中的 "invalid module"
echo         （PyInstaller 对语法错误模块静默标记为 invalid 并排除）
echo ────────────────────────────────────────────
set INVALID_MODULES=0
if exist build\Solid_onefile\warn-Solid_onefile.txt (
    findstr /i "invalid module" build\Solid_onefile\warn-Solid_onefile.txt >nul
    if !ERRORLEVEL! equ 0 (
        echo    ⚠ 发现 invalid module —— 以下模块有语法问题被打包排除：
        echo.
        findstr /i "invalid module" build\Solid_onefile\warn-Solid_onefile.txt
        echo.
        echo    [警告] 这些模块在运行时将报 No module named 'xxx'，请修复后重打。
        echo    注意：warning 中的 "missing module" 大部分是正常的（跨平台可选导入），
        echo    只有 "invalid module" 需要关注——模块本身语法错误。
        set INVALID_MODULES=1
    ) else (
        echo    ✓ 未发现 invalid module
    )
)
if !INVALID_MODULES! neq 0 (
    pause
    exit /b 1
)

echo.
echo ────────────────────────────────────────────
echo [8/8] 快速冒烟测试：启动 EXE 5 秒看是否崩溃
echo ────────────────────────────────────────────
start /b "" dist\Solid.exe
timeout /t 5 /nobreak >nul
tasklist 2>nul | findstr /i "Solid.exe" >nul
if !ERRORLEVEL! equ 0 (
    echo    ✓ EXE 启动后 5 秒仍在运行，冒烟通过
    echo    → 测试窗口将自动关闭
    taskkill /f /im Solid.exe >nul 2>&1
) else (
    echo    ⚠ EXE 在 5 秒内已退出，请检查 dist\logs\solid.log
    echo    运行：type dist\logs\solid.log ^| findstr CRITICAL
)

echo.
echo ────────────────────────────────────────────
echo [8.5/8] 复制桥接程序到 dist/
echo         （rfl_bridge_32.exe — 门锁发卡必备）
echo ────────────────────────────────────────────
if exist lock_adapters\rfl_bridge_32.exe (
    copy lock_adapters\rfl_bridge_32.exe dist\ >nul 2>&1
    echo    ✓ rfl_bridge_32.exe 已复制
) else if exist ..\采集器\bridge32.exe (
    copy ..\采集器\bridge32.exe dist\rfl_bridge_32.exe >nul 2>&1
    echo    ✓ 从 采集器/bridge32.exe 复制（重命名为 rfl_bridge_32.exe）
) else (
    echo    ⚠ rfl_bridge_32.exe 不存在，门锁发卡功能将不可用
)

if "%MODE%"=="2" (
    echo.
    echo ────────────────────────────────────────────
    echo [9/9] 编译安装包（Inno Setup）
    echo         SetupIconFile = assets\app_icon.ico（墨绿桌面图标）
    echo         桌面快捷方式图标 = Solid.exe 内嵌 ICO（同上）
    echo         卸载列表图标     = Solid.exe 内嵌 ICO
    echo         输出：Output\SolidPMS_Setup_1.0.0.exe
    echo ────────────────────────────────────────────
    copy lock_adapters\rfl_bridge_32.exe dist\ >nul 2>&1

    if exist build\bridge_build rmdir /s /q build\bridge_build

    if not exist %ISCC% (
        echo [警告] 找不到 Inno Setup：%ISCC%
        echo 请安装 Inno Setup 6 后重试。
    ) else (
        %ISCC% installer\SolidInstaller.iss
        if !ERRORLEVEL! equ 0 (
            for /f %%s in ('powershell -NoProfile -Command ^
                "(Get-ChildItem Output\SolidPMS_Setup_*.exe ^| Sort-Object LastWriteTime -Descending ^| Select-Object -First 1).Length / 1MB"') do set SETUP_SIZE=%%s
            echo    ✓ 成功：Output\SolidPMS_Setup_1.0.0.exe（约 !SETUP_SIZE:~0,5! MB）
            echo    ✓ 桌面图标已灌墨绿（与 app_icon.ico 一致）
        ) else (
            echo [警告] 安装包编译失败，但 Solid.exe 已可用。
        )
    )
)

echo.
echo ============================================
echo   构建完成！
echo.
echo   单文件版：%~dp0酒店系统\dist\Solid.exe
if "%MODE%"=="2" (
    echo   安装包：  %~dp0酒店系统\Output\SolidPMS_Setup_1.0.0.exe
)
echo.
echo   U盘测试：拷 dist\Solid.exe + rfl_bridge_32.exe
echo   到客户酒店双击 Solid.exe
echo.
echo   图标：assets\app_icon.png 源图 ^| app_icon.ico 桌面/EXE/安装包
echo   换图标后 tools\build_app_icon.py（打包 [3.5/8] 自动跑）
echo.
echo   最后确认：dist\logs\solid.log 无 CRITICAL 报错
echo ============================================
echo.
pause
