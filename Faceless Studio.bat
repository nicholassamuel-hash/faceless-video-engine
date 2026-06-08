@echo off
title Faceless Studio
cd /d "%~dp0"
echo Starting Faceless Studio... a browser tab will open shortly.
".venv\Scripts\python.exe" -m webapp.launch
pause
