@echo off
setlocal
pushd "%~dp0" || exit /b 1
if exist ".venv\Scripts\python.exe" goto dependencies
py -3.12 -V >nul 2>&1
if errorlevel 1 goto use_python
py -3.12 -m venv .venv || goto failed
goto dependencies

:use_python
python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1
if errorlevel 1 (
    echo Python 3.12 is required. See README.md.
    goto failed
)
python -m venv .venv || goto failed

:dependencies
".venv\Scripts\python.exe" -m pip --disable-pip-version-check install -r requirements.txt || goto failed
echo Open http://127.0.0.1:8000/ in Chrome. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
set "app_exit=%errorlevel%"
popd
exit /b %app_exit%

:failed
popd
exit /b 1
