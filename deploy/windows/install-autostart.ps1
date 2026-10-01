# Registers the system's unattended start for the current Windows user:
#   - Task Scheduler task "Batcomputer": at this user's sign-in, runs
#     services\local-supervisor\autostart.py (supervisor + map window).
#   - Start Menu shortcut "Batcomputer mapa" with the hotkey Ctrl+Alt+B, which
#     opens the map window again. F11 toggles its full screen, Alt+F4 closes it.
#
# Usage, from the repository root:
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
#   ... -NoMapWindow    start the system without opening the map window
#   ... -Uninstall      remove the task and the shortcut
param(
    [switch]$NoMapWindow,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "Batcomputer"
$Repository = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Autostart = Join-Path $Repository "services\local-supervisor\autostart.py"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Batcomputer mapa.lnk"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Remove-Item -LiteralPath $Shortcut -ErrorAction SilentlyContinue
    Write-Output "autostart removed task=$TaskName"
    exit 0
}

if (-not (Test-Path $Autostart)) { throw "Missing $Autostart" }
# The supervisor's own services run on this interpreter (paho-mqtt installed).
$Python = (Get-Command python -ErrorAction Stop).Source
$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) { throw "Missing $PythonW next to $Python" }
$User = "$env:USERDOMAIN\$env:USERNAME"

$Arguments = "-u `"$Autostart`""
if ($NoMapWindow) { $Arguments += " --no-map-window" }
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Repository
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
# Interactive: Docker Desktop and the map window need the user's desktop session.
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
# Task Scheduler's default priority (7) is below normal, and every child
# process (services, Docker CLI) would inherit it.
$Settings.Priority = 4
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force `
    -Description "Starts the Batcomputer monitoring system at sign-in (repository: $Repository)." | Out-Null

$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $PythonW
$Link.Arguments = "`"$Autostart`" --open-map"
$Link.WorkingDirectory = $Repository
$Link.Hotkey = "CTRL+ALT+B"
$Link.Description = "Open the Batcomputer map window (F11 toggles full screen)"
$Link.Save()

$MapWindow = if ($NoMapWindow) { "off" } else { "on" }
Write-Output "autostart installed task=$TaskName user=$User map_window=$MapWindow"
Write-Output "shortcut=$Shortcut hotkey=Ctrl+Alt+B"
