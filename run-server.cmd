@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0player_server.py" %*
) else (
    python "%~dp0player_server.py" %*
)
