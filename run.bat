@echo off
REM Launcher: always runs from THIS folder using the project venv, no matter
REM where you call it from. Usage:  run generate --topic "..." --language id
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
