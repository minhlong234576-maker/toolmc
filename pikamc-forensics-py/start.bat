@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title PikaMC Forensics Investigator - Real-Time Server

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ============================================================
echo   PIKAMC FORENSICS INVESTIGATOR - REAL-TIME DASHBOARD
echo ============================================================
echo.

REM --- 1. Kiem tra & Khoi tao .env ---
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] Tao file .env tu .env.example...
        copy /y ".env.example" ".env" >nul
        echo [INFO] Da tao file .env thanh cong.
    ) else (
        echo [WARN] Khong tim thay .env hoac .env.example.
    )
)

REM Doc PORT tu .env (mac dinh 8000)
set "PORT=8000"
if exist ".env" (
    for /f "tokens=2 delims==" %%a in ('findstr /b "APP_PORT=" .env') do set "PORT=%%a"
)
if "!PORT!"=="" set "PORT=8000"

REM --- 2. Tim Python tren he thong ---
set "PYTHON_CMD="

python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :find_ok
)

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :find_ok
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :find_ok
)

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :find_ok
)

if exist "%ProgramFiles%\Python312\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python312\python.exe"
    goto :find_ok
)

if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    goto :find_ok
)

echo [ERROR] Khong tim thay Python 3.11+ tren he thong.
echo         Vui long cai dat Python tu https://www.python.org/downloads/
echo         (Nho tich chon 'Add Python to PATH' khi cai dat).
pause
exit /b 1

:find_ok
echo [1/4] Python: !PYTHON_CMD!

REM --- 3. Virtual Environment ---
if not exist ".venv\Scripts\activate.bat" (
    echo [2/4] Dang tao moi virtual environment .venv ...
    !PYTHON_CMD! -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Tao virtual environment that bai.
        pause
        exit /b 1
    )
    echo [2/4] Da tao virtual environment thanh cong.
) else (
    echo [2/4] Virtual environment: .venv [San sang]
)

call .venv\Scripts\activate.bat

REM --- 4. Kiem tra & cai dat dependencies ---
set NEED_INSTALL=0
if not exist ".deps_installed" set NEED_INSTALL=1

python -c "import fastapi, uvicorn, httpx, nbtlib, sqlalchemy" >nul 2>&1
if errorlevel 1 set NEED_INSTALL=1

if "!NEED_INSTALL!"=="1" (
    echo [3/4] Dang kiem tra va cai dat dependencies tu requirements.txt...
    python -m pip install -q --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Cai dat dependencies that bai!
        pause
        exit /b 1
    )
    echo OK > .deps_installed
    echo [3/4] Cai dat dependencies hoan tat.
) else (
    echo [3/4] Dependencies: OK [Day du thu vien]
)

REM --- 5. Don dep port neu co tien trinh cu chiem dung ---
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":!PORT!" ^| findstr "LISTENING"') do (
    if not "%%p"=="" (
        echo [INFO] Dang giai phong port !PORT! [PID: %%p]
        taskkill /f /pid %%p >nul 2>&1
    )
)

REM --- 6. Mo Browser & Khoi dong Server ---
echo [4/4] Dang khoi dong PikaMC Forensics Dashboard tai:
echo       Truy cap: http://localhost:!PORT!
echo       Nhan Ctrl+C de dung server bat cu luc nao.
echo ============================================================
echo.

start /b "" cmd /c "ping 127.0.0.1 -n 3 >nul && start http://localhost:!PORT!"

python -m uvicorn app.main:app --host 0.0.0.0 --port !PORT! --reload

if errorlevel 1 (
    echo.
    echo [ERROR] Server gap loi va da dung lai.
    pause
)
