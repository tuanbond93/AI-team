@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-supra-review.ps1" -Port 8506
if errorlevel 1 pause
