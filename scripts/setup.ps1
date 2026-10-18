param([string]$Python = "python", [switch]$WithSpeech, [switch]$WithCaptions)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Không tạo được venv. Cài Python 3.11+ từ python.org có pip.' }
$venvPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$extras = @('test')
if ($WithSpeech) { $extras += 'speech' }
if ($WithCaptions) { $extras += 'captions' }
$package = '.[' + ($extras -join ',') + ']'
& $venvPython -m pip install -e $package
if ($LASTEXITCODE -ne 0) { throw 'Cài thư viện thất bại.' }
if (-not (Test-Path -LiteralPath 'config.toml')) {
    Copy-Item -LiteralPath 'config.example.toml' -Destination 'config.toml'
}
Write-Host 'Sẵn sàng. Chạy scripts/start.ps1 rồi mở http://127.0.0.1:8765/docs'
