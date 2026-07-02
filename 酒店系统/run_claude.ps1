# Claude Code 启动脚本 (PowerShell)
param(
    [string[]]$ArgsList
)

Set-Location $PSScriptRoot

# 读取 .env.claude
$envContent = Get-Content "$PSScriptRoot\.env.claude" -Encoding UTF8
foreach ($line in $envContent) {
    if ($line -match '^\s*([^#][^=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

# 检查 API Key
if ($env:ANTHROPIC_API_KEY -eq 'YOUR_API_KEY_HERE' -or [string]::IsNullOrEmpty($env:ANTHROPIC_API_KEY)) {
    Write-Host "[错误] 请先编辑 .env.claude 文件，将 YOUR_API_KEY_HERE 替换为实际 API Key！" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Claude Code - 第三方 API 模式" -ForegroundColor Cyan
Write-Host "  工作区域: $(Get-Location)" -ForegroundColor Cyan
Write-Host "  模型: glm-5.2" -ForegroundColor Cyan
Write-Host "  Base URL: $env:ANTHROPIC_BASE_URL" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 启动 Claude Code
& claude --model glm-5.2 @ArgsList