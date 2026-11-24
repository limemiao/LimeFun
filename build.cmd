@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm --distpath "%~dp0runtime\current" --workpath "%~dp0build\current-server" "%~dp0LimeFunServer.spec"
if errorlevel 1 exit /b %errorlevel%
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm --distpath "%~dp0runtime\current" --workpath "%~dp0build\current-webview" "%~dp0LimeFunWebView.spec"
