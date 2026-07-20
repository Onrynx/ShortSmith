#Requires -Version 5.1
<#
  Register a daily Windows Task Scheduler job that runs StoryForge's scheduler
  (both lanes for today's assignment). This is the NON-Odysseus fallback autopilot;
  the preferred path is an Odysseus scheduled task hitting the /api/trigger webhook
  (see README "Automation"). Safe to re-run (recreates the task).

  Usage (run as your user):
    powershell -ExecutionPolicy Bypass -File .\scripts\setup_autopilot.ps1 -At 06:30
    powershell -ExecutionPolicy Bypass -File .\scripts\setup_autopilot.ps1 -At 06:30 -Mock
#>
param(
  [string]$At = "06:30",
  [switch]$Mock
)
$SF = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "python not on PATH" -ForegroundColor Red; exit 1 }

$args = "-m storyforge.scheduler daily --no-wait"
if ($Mock) { $args += " --mock" }
$action  = New-ScheduledTaskAction -Execute $py -Argument $args -WorkingDirectory $SF
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "StoryForge Autopilot" -Action $action -Trigger $trigger `
  -Settings $settings -Description "Daily StoryForge Shorts (both lanes)" -Force | Out-Null

Write-Host "Registered 'StoryForge Autopilot' to run daily at $At." -ForegroundColor Green
Write-Host "  Command: $py $args  (cwd: $SF)"
Write-Host "  Manage/disable it in Task Scheduler, or:  Unregister-ScheduledTask -TaskName 'StoryForge Autopilot'"
