# Launcher for PowerShell: runs from this folder using the project venv,
# regardless of your current directory.
# Usage:  .\run.ps1 generate --topic "..." --language id
Set-Location -LiteralPath $PSScriptRoot
& ".\.venv\Scripts\python.exe" main.py @args
