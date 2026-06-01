@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%SCRIPT_DIR%venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%SCRIPT_DIR%nt8_connection_notifier.py" start --config "%SCRIPT_DIR%notifier_config.json"
