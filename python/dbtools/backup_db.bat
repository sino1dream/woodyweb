@echo off
rem ===================================================================
rem  One-click backup: double-click this file.
rem  All arguments are forwarded to backup_db.py, e.g.
rem      backup_db.bat --keep 30
rem
rem  NOTE: keep this file pure ASCII. cmd.exe decodes a .bat with the
rem  active code page (GBK on Chinese Windows), so non-ASCII lines get
rem  mangled before "chcp 65001" takes effect. All Chinese output comes
rem  from the Python script instead.
rem ===================================================================
chcp 65001 >nul
setlocal

set "PY=D:\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -B "%~dp0backup_db.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC% -- see the messages above.
) else (
    echo [OK] backup finished.
)
echo.
pause
exit /b %RC%
