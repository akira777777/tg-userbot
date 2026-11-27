# Запуск веб-панели tg-userbot (web.py) — http://127.0.0.1:8642.
# Повторный запуск безопасен: если порт уже слушается, скрипт печатает статус и не создаёт дубликат.
param(
    [int]$Port = 0,
    [string[]]$PythonCmd = @("py", "-3.15")
)

$ErrorActionPreference = "Stop"

if ($Port -le 0) {
    $envPort = $env:WEBBOT_PORT
    $Port = if ($envPort) { [int]$envPort } else { 8642 }
}

# Корень проекта — родитель каталога scripts
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    Write-Output ("Панель уже запущена: http://127.0.0.1:{0} (PID {1})" -f $Port, $listen.OwningProcess)
    exit 0
}

Write-Output ("Запуск панели: http://127.0.0.1:{0}  (Ctrl+C — остановить)" -f $Port)
$exe = $PythonCmd[0]
$pyArgs = @()
if ($PythonCmd.Count -gt 1) {
    $pyArgs = $PythonCmd[1..($PythonCmd.Count - 1)]
}
& $exe @pyArgs web.py
exit $LASTEXITCODE
