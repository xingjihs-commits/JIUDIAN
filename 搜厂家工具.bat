@echo off
chcp 65001 >nul
title 搜索厂家授权工具 - 请稍等

echo ============================================
echo   搜索 CardLock-N 系列 EXE 文件
echo   如果找到了会自动复制到 U 盘
echo ============================================
echo.

:: 找 U 盘（就是脚本自己所在的盘）
set "USB=%~d0"
echo 脚本在: %USB%
echo.

:: 第一步：搜索 CardLock-N 系列 EXE
echo [1/4] 搜索 CardLock-N*.exe ...
echo.
dir /s /b C:\CardLock-N*.exe D:\CardLock-N*.exe 2>nul > "%TEMP%\found_exe.txt"
dir /s /b C:\CARDLOCK-N*.exe D:\CARDLOCK-N*.exe 2>nul >> "%TEMP%\found_exe.txt"
dir /s /b C:\*N8.9.1*.exe D:\*N8.9.1*.exe 2>nul >> "%TEMP%\found_exe.txt"
dir /s /b C:\*N02_d*.exe D:\*N02_d*.exe 2>nul >> "%TEMP%\found_exe.txt"

:: 第二步：搜桌面和下载目录
echo [2/4] 搜索桌面和下载目录 ...
echo.
if exist "%USERPROFILE%\Desktop\CardLock-N*.exe" (
    dir /b "%USERPROFILE%\Desktop\CardLock-N*.exe" >> "%TEMP%\found_exe.txt"
)
if exist "%USERPROFILE%\Desktop\*N8.9.1*.exe" (
    dir /b "%USERPROFILE%\Desktop\*N8.9.1*.exe" >> "%TEMP%\found_exe.txt"
)
if exist "%USERPROFILE%\Desktop\*N02_d*.exe" (
    dir /b "%USERPROFILE%\Desktop\*N02_d*.exe" >> "%TEMP%\found_exe.txt"
)
if exist "%USERPROFILE%\Downloads\CardLock-N*.exe" (
    dir /b "%USERPROFILE%\Downloads\CardLock-N*.exe" >> "%TEMP%\found_exe.txt"
)

:: 第三步：搜整个系统目录（智能门锁那个目录）
echo [3/4] 搜索门锁系统安装目录 ...
echo.
if exist "D:\智能门锁管理系统新2021网络版\" (
    dir /s /b "D:\智能门锁管理系统新2021网络版\*.exe" 2>nul >> "%TEMP%\found_exe.txt"
)
if exist "D:\AI\新02智能门锁系统\" (
    dir /s /b "D:\AI\新02智能门锁系统\*.exe" 2>nul >> "%TEMP%\found_exe.txt"
)
if exist "D:\新02智能门锁系统\" (
    dir /s /b "D:\新02智能门锁系统\*.exe" 2>nul >> "%TEMP%\found_exe.txt"
)

:: 第四步：找 S 开头的 7z 压缩包（厂家打包下载的）
echo [4/4] 搜索智能门锁系统.7z 压缩包 ...
echo.
dir /s /b D:\*智能门锁*.7z 2>nul >> "%TEMP%\found_7z.txt"

:: 显示结果
echo ============================================
echo   搜索结果
echo ============================================
echo.

if not exist "%TEMP%\found_exe.txt" (
    echo ⚠ 没找到 CardLock-N 系列 EXE 文件
    echo.
) else (
    for /f "tokens=*" %%i in (%TEMP%\found_exe.txt) do (
        if exist "%%i" (
            echo ✅ 找到: %%i
            copy "%%i" "%USB%\" /Y >nul
            echo   已复制到 U 盘！
        )
    )
    echo.
)

if not exist "%TEMP%\found_7z.txt" (
    echo ⚠ 没找到 智能门锁系统.7z 压缩包
) else (
    for /f "tokens=*" %%j in (%TEMP%\found_7z.txt) do (
        if exist "%%j" (
            echo ✅ 找到: %%j
            copy "%%j" "%USB%\" /Y >nul
            echo   已复制到 U 盘！
        )
    )
)

echo.
echo ============================================
echo   搜索完成！
echo.
echo   ✅ 如果文件找到了，已自动复制到 U 盘
echo   ⚠ 如果没找到，请试试：
echo     1. 打开桌面上的"回收站"看有没有
echo     2. 打开 D:\智能门锁管理系统新2021网络版\ 看看
echo     3. 看桌面有没有任何 CardLock 开头的文件
echo ============================================
pause
