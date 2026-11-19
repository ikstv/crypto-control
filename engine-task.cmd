@echo off
cd /d "%~dp0"
if exist "data\engine-stopped" exit /b 0
".venv\Scripts\python.exe" engine.py
if exist "data\engine-stopped" exit /b 0
exit /b %errorlevel%
