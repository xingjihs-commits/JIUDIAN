@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 设置环境变量（请确保 .env.claude 中的 API Key 已正确配置）
REM 从 .env.claude 读取配置
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env.claude") do (
    set "%%a=%%b"
)

REM 检查 API Key
if "%ANTHROPIC_API_KEY%"=="YOUR_API_KEY_HERE" (
    echo [错误] 请先编辑 .env.claude 文件，将 YOUR_API_KEY_HERE 替换为实际 API Key！
    pause
    exit /b 1
)

echo ========================================
echo   Claude Code - 第三方 API 模式
echo   工作区域: %cd%
echo   模型: glm-5.2
echo   Base URL: %ANTHROPIC_BASE_URL%
echo ========================================

claude --model glm-5.2 %*