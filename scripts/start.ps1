param([string]$Config = "config.toml")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Chạy scripts/setup.ps1 trước để tạo môi trường Python.'
}
& $pythonPath -m shorts --config $Config
