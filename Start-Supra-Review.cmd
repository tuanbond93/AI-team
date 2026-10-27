@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0start-supra-review.ps1"
set "launchExit=%errorlevel%"
if not "%launchExit%"=="0" pause
exit /b %launchExit%
