#Requires -Version 5.1
<#
  StoryForge x Odysseus — one-tap launcher.
  Starts Odysseus (native, no Docker) + the StoryForge Discord bot.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\launch_all.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\launch_all.ps1 -OdysseusDir "C:\Users\User\odysseus"
#>
param(
  [string]$OdysseusDir = "$env:USERPROFILE\odysseus",
  [int]$OdysseusPort = 7000
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

# 2) StoryForge Discord bot (needs DISCORD_BOT_TOKEN in .env).
Write-Host "Starting StoryForge Discord bot ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command","Set-Location '$SF'; python -m storyforge.discord_bot"

if (Test-Up "http://localhost:$OdysseusPort") { Start-Process "http://localhost:$OdysseusPort/" }
Write-Host "`nControl StoryForge from Discord: !help in your bot channel." -ForegroundColor Green
