#Requires -Version 5.1
<#
  StoryForge x Odysseus — one-tap launcher / "opening widget".
  Starts Odysseus (native, no Docker) + the StoryForge control app, waits for both,
  then opens the StoryForge Command Center and Odysseus in your browser.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\launch_all.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\launch_all.ps1 -OdysseusDir "C:\Users\User\odysseus"
#>
param(
  [string]$OdysseusDir = "$env:USERPROFILE\odysseus",
  [int]$OdysseusPort = 7000,
  [int]$AppPort = 8787
)
$ErrorActionPreference = "Continue"
$SF = Split-Path -Parent $PSScriptRoot   # StoryForge repo root

function Test-Up($url) { try { Invoke-WebRequest $url -TimeoutSec 3 -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop | Out-Null; return $true } catch { return ($null -ne $_.Exception.Response) } }

# 1) Odysseus (native). First run prints an admin password — shown in its window.
if (Test-Up "http://localhost:$OdysseusPort") {
  Write-Host "Odysseus already up on :$OdysseusPort" -ForegroundColor Green
} elseif (Test-Path (Join-Path $OdysseusDir "launch-windows.ps1")) {
  Write-Host "Starting Odysseus from $OdysseusDir ..." -ForegroundColor Cyan
  Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File",(Join-Path $OdysseusDir "launch-windows.ps1"),"-Port","$OdysseusPort"
} else {
  Write-Host "Odysseus not found at $OdysseusDir (skipping). Clone it or pass -OdysseusDir." -ForegroundColor Yellow
}

# 2) StoryForge control app (prints its APP_TOKEN in its own window).
if (Test-Up "http://localhost:$AppPort") {
  Write-Host "StoryForge app already up on :$AppPort" -ForegroundColor Green
} else {
  Write-Host "Starting StoryForge control app ..." -ForegroundColor Cyan
  $env:APP_PORT = "$AppPort"
  Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command","`$env:APP_PORT='$AppPort'; Set-Location '$SF'; python -m app.main"
}

# 3) Wait, then open the Command Center + Odysseus.
Write-Host "Waiting for services..." -ForegroundColor Cyan
1..40 | ForEach-Object { if ((Test-Up "http://localhost:$AppPort")) { break }; Start-Sleep 1 }
Start-Process "http://localhost:$AppPort/"
if (Test-Up "http://localhost:$OdysseusPort") { Start-Process "http://localhost:$OdysseusPort/" }
Write-Host "`nCommand Center: http://localhost:$AppPort/   (paste the APP_TOKEN shown in the StoryForge window)" -ForegroundColor Green
