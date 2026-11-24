@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m limefun.player_server %*
) else (
    python -m limefun.player_server %*
)
