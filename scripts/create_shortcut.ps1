# Creates a "Faceless Studio" shortcut on the Desktop that launches the app.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root "Faceless Studio.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Faceless Studio.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $root
$sc.Description      = "Faceless Studio - local short-form video engine"
$sc.WindowStyle      = 7  # minimized console
# Use PowerShell's icon as a stand-in (no custom .ico shipped).
$sc.IconLocation     = "$env:SystemRoot\System32\SHELL32.dll,137"
$sc.Save()

Write-Output "Shortcut created: $lnk"
