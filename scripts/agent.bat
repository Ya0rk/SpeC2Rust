@echo off
setlocal

if "%~1"=="" (
    echo Usage: %~nx0 ^<project-name^>
    exit /b 1
)

set "C_NAME=%~1"
shift
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent.ps1" "%C_NAME%" %*
exit /b %ERRORLEVEL%
