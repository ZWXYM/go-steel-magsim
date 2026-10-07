@echo off
setlocal
cd /d "%~dp0"
if exist "..\.runtime\magsim\Scripts\python.exe" (
  "..\.runtime\magsim\Scripts\python.exe" -X utf8 tools\start_workbench.py --port 5001 --page system --open-browser %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 tools\start_workbench.py --port 5001 --page system --open-browser %*
) else (
  python -X utf8 tools\start_workbench.py --port 5001 --page system --open-browser %*
)
if errorlevel 1 pause
