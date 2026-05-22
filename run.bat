@echo off
REM Launch the Cargo Hunters save editor GUI using whichever Python is available.
REM This intentionally uses console Python instead of pythonw so startup errors
REM remain visible instead of silently disappearing.
setlocal

cd /d "%~dp0"

REM Prefer a local venv if someone creates one later.
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    goto :launch_path
)

REM Known local install on this machine.
if exist "D:\Python312\python.exe" (
    set "PYTHON_CMD=D:\Python312\python.exe"
    goto :launch_path
)
if exist "D:\Python310\python.exe" (
    set "PYTHON_CMD=D:\Python310\python.exe"
    goto :launch_path
)

REM Otherwise use the Windows py launcher (3.10+ preferred), then plain python.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "editor_gui.py" %*
    goto :check_result
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "editor_gui.py" %*
    goto :check_result
)

echo Could not find Python on PATH. Install Python 3.10+ and try again.
pause
exit /b 1

:launch_path
"%PYTHON_CMD%" "editor_gui.py" %*

:check_result
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo Cargo Hunters Save Editor exited with error code %ERRORLEVEL%.
    echo Review the error above, then press any key to close this window.
    pause >nul
)
exit /b %ERRORLEVEL%
