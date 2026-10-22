@echo off
setlocal
cd /d "%~dp0"
set "NAILBOOK_PYTHON="
call :try_python "%~dp0.venv\Scripts\python.exe"
if defined NAILBOOK_PYTHON goto found
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*" "%USERPROFILE%\AppData\Local\Programs\Python\Python*") do (
    call :try_python "%%~fD\python.exe"
    if defined NAILBOOK_PYTHON goto found
)
for /d %%D in ("%ProgramFiles%\Python*") do (
    call :try_python "%%~fD\python.exe"
    if defined NAILBOOK_PYTHON goto found
)
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    call :try_python "%%P"
    if defined NAILBOOK_PYTHON goto found
)
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    call :try_python "%%P"
    if defined NAILBOOK_PYTHON goto found
)
echo Python 3.10-3.13 was not found. Install Python from python.org, then retry.
pause
exit /b 1

:found
if /i "%~1"=="--check-python" (
    "%NAILBOOK_PYTHON%" -c "import sys; print(sys.executable); print(sys.version)"
    exit /b 0
)
"%NAILBOOK_PYTHON%" "%~dp0start.py"
if errorlevel 1 pause
exit /b

:try_python
if not exist "%~1" exit /b
"%~1" -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14)))" >nul 2>&1
if not errorlevel 1 set "NAILBOOK_PYTHON=%~1"
exit /b
