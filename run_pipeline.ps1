# EKMI granite pipeline launcher (run manually via PowerShell).
# This is a normal Python program, NOT a Codex scheduled/automation task.
param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Command,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$Py = "C:/Users/HP/.gemini/antigravity/scratch/tender_scraper/venv/Scripts/python.exe"
$Root = "C:\Users\HP\Documents\Codex\2026-09-14\hi"

$env:PYTHONPATH = $Root
$SecretsFile = Join-Path $Root "secrets.env"
if (Test-Path $SecretsFile) {
    Get-Content $SecretsFile | ForEach-Object {
        if ($_ -match '^([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}

Write-Host "EKMI pipeline -> $Command" -ForegroundColor Cyan
if ($Command -eq "run-all") {
    & $Py -m pipeline.run_all @Rest
} else {
    & $Py -m pipeline.runner $Command @Rest
}
exit $LASTEXITCODE
