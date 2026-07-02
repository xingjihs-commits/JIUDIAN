@echo off
chcp 65001 >nul
title 采集器 - 一键打包（SolidCollector 标准构建流水线）
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================
echo   采集器 — 标准构建流水线
echo   输出：dist/SolidCollector.exe（U盘采集工具）
echo ============================================
echo.

set PYTHON_64="C:\Users\FF.FC\AppData\Local\Programs\Python\Python311\python.exe"

if not exist %PYTHON_64% (
    echo [错误] 找不到 64 位 Python：%PYTHON_64%
    pause
    exit /b 1
)

cd /d "%~dp0\采集器"

echo.
echo ────────────────────────────────────────────
echo [1/6] 清理旧产物
echo ────────────────────────────────────────────
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo.
echo ────────────────────────────────────────────
echo [2/6] 预检：验证核心模块 import
echo ────────────────────────────────────────────
set PREFAIL=0
for %%m in (
    collector_main
    collector_bridge
    bridgecore.handover_packager
    bridgecore.path_prober
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
echo    → 检查包级导入（collector_ui 新包结构）...
%PYTHON_64% -c "import sys,os,types;pkg_root=os.path.dirname(os.path.abspath('collector_main.py'));parent=os.path.dirname(pkg_root);sys.path.insert(0,parent);pkg=types.ModuleType('collector');pkg.__path__=[pkg_root];sys.modules['collector']=pkg;from collector.collector_ui import CollectorWizard;from collector.collector_ui.constants import BUILD_TAG;from collector.collector_ui.models import SampleCapture;print(f'✓ collector_ui 包 (BUILD_TAG={BUILD_TAG})')" 2>&1 | findstr /i "✓"
if !ERRORLEVEL! neq 0 (
    echo    ✗ collector_ui 包导入失败！
    set PREFAIL=1
)
if !PREFAIL! neq 0 (
    echo.
    echo [致命错误] 有模块无法被 Python 导入，请先修复语法错误。
    pause
    exit /b 1
)
echo    → 全部通过，继续打包。

echo.
echo ────────────────────────────────────────────
echo [3/6] 确认桥接程序存在
echo ────────────────────────────────────────────
if exist bridge32.exe (
    echo    ✓ bridge32.exe 已就绪
) else (
    echo    ⚠ bridge32.exe 不存在！需先编译 32 位桥接。
    echo    尝试从 dist/ 复制...
    if exist dist\bridge32.exe (
        copy dist\bridge32.exe bridge32.exe >nul
        echo    ✓ 从 dist/ 恢复
    ) else (
        echo    [错误] 无法找到 bridge32.exe，请先编译。
        pause
        exit /b 1
    )
)

echo.
echo ────────────────────────────────────────────
echo [4/6] 安装打包依赖
echo ────────────────────────────────────────────
%PYTHON_64% -m pip install -q pyinstaller 2>nul

echo.
echo ────────────────────────────────────────────
echo [5/6] 打包 SolidCollector.exe
echo ────────────────────────────────────────────
%PYTHON_64% -m PyInstaller SolidCollector.spec --noconfirm
if !ERRORLEVEL! neq 0 (
    echo [错误] PyInstaller 打包失败！
    pause
    exit /b 1
)

for /f %%s in ('powershell -NoProfile -Command "(Get-Item dist\SolidCollector.exe).Length / 1MB"') do set EXE_SIZE=%%s
echo    ✓ 成功：dist\SolidCollector.exe（约 !EXE_SIZE:~0,5! MB）

echo.
echo ────────────────────────────────────────────
echo [6/6] 确认桥接程序在 dist/ 内
echo ────────────────────────────────────────────
if exist dist\bridge32.exe (
    echo    ✓ bridge32.exe 已打包
) else (
    copy bridge32.exe dist\ >nul 2>&1
    if exist dist\bridge32.exe (
        echo    ✓ bridge32.exe 已复制到 dist/
    ) else (
        echo    ⚠ 无法复制 bridge32.exe 到 dist/，U盘使用需手动放入
    )
)

echo.
echo ============================================
echo   构建完成！
echo.
echo   SolidCollector.exe：%~dp0采集器\dist\SolidCollector.exe
echo   bridge32.exe：      %~dp0采集器\dist\bridge32.exe
echo.
echo   U盘使用：拷贝 dist/ 所有文件到 U 盘即可
echo ============================================

REM ── 自动同步到工具文件夹 ──
set TOOLS_DIR="E:\Camera Roll\工具"
if exist %TOOLS_DIR% (
    echo.
    echo ────────────────────────────────────────────
    echo [7/7] 同步到工具文件夹
    echo ────────────────────────────────────────────
    echo   目标: %TOOLS_DIR%

    REM 备份旧版本
    if exist "%TOOLS_DIR%\SolidCollector.exe" (
        echo   备份旧版 SolidCollector.exe...
        move /Y "%TOOLS_DIR%\SolidCollector.exe" "%TOOLS_DIR%\SolidCollector.exe.bak" >nul 2>&1
        echo   ✓ 旧版已备份为 SolidCollector.exe.bak
    )
    if exist "%TOOLS_DIR%\bridge32.exe" (
        move /Y "%TOOLS_DIR%\bridge32.exe" "%TOOLS_DIR%\bridge32.exe.bak" >nul 2>&1
    )

    REM 拷贝新版本
    copy /Y "dist\SolidCollector.exe" %TOOLS_DIR%\ >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo   ✓ SolidCollector.exe → 工具文件夹
    ) else (
        echo   ✗ 复制 SolidCollector.exe 失败！
    )

    copy /Y "dist\bridge32.exe" %TOOLS_DIR%\ >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo   ✓ bridge32.exe → 工具文件夹
    ) else (
        echo   ✗ 复制 bridge32.exe 失败！
    )

    REM 同步核心资源文件
    if exist "known_signatures.json" (
        copy /Y "known_signatures.json" %TOOLS_DIR%\ >nul 2>&1
        echo   ✓ known_signatures.json → 工具文件夹
    )

    REM 更新 bat 启动脚本（如果源码中的更新）
    if exist "..\启动采集器.bat" (
        copy /Y "..\启动采集器.bat" %TOOLS_DIR%\启动采集器.bat >nul 2>&1
        echo   ✓ 启动采集器.bat → 工具文件夹
    )

    REM 同步 toolbox 工具
    if exist "toolbox" (
        robocopy "toolbox" "%TOOLS_DIR%\toolbox" /E /NFL /NDL /NJH /NJS >nul 2>&1
        if !ERRORLEVEL! lss 8 echo   ✓ toolbox → 工具文件夹
    )

    REM 同步 Ghidra 脚本（如果存在）
    if exist "ghidra_toolkit" (
        robocopy "ghidra_toolkit" "%TOOLS_DIR%\ghidra_toolkit" /E /NFL /NDL /NJH /NJS >nul 2>&1
        if !ERRORLEVEL! lss 8 echo   ✓ ghidra_toolkit → 工具文件夹
    )

    echo.
    echo   ✅ 工具文件夹同步完成
    echo   位置: %TOOLS_DIR%
    echo   配套: Ghidra + Java + toolbox(strings.exe) + bridge32.exe
)

echo.
pause
