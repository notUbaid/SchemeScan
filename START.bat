@echo off
title SchemeScan
color 0A
cls

echo.
echo   =========================================
echo     SchemeScan  -  Starting up...
echo   =========================================
echo.

:: ── Find Python ──────────────────────────────────────────────────────────────
set PY=
python --version >nul 2>&1 && set PY=python
if "%PY%"=="" py --version >nul 2>&1 && set PY=py
if "%PY%"=="" python3 --version >nul 2>&1 && set PY=python3

if "%PY%"=="" (
    echo  ERROR: Python not found.
    echo  Download: https://python.org/downloads
    echo  Tick "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)
echo  [1/5] Python: %PY%

:: ── Install packages ─────────────────────────────────────────────────────────
echo  [2/5] Installing packages...
%PY% -m pip install fastapi "uvicorn[standard]" python-multipart httpx Pillow rank-bm25 --quiet --disable-pip-version-check
%PY% -m pip install pytesseract --quiet --disable-pip-version-check >nul 2>&1
echo        Done.

:: ── Check backend imports BEFORE starting ─────────────────────────────────
echo  [3/5] Checking backend...
cd /d "%~dp0backend"
%PY% -c "import main; print('  main.py OK')" 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  !! Backend import failed - error shown above.
    echo     Fix the error then run START.bat again.
    echo.
    pause & exit /b 1
)

:: ── Build DB if missing ───────────────────────────────────────────────────────
echo  [4/5] Checking database...
if exist "schemes.db" (
    echo        schemes.db found.
) else (
    echo        Building database from scheme files ^(1-2 min^)...
    %PY% import_schemes.py
)

:: ── Start Ollama ─────────────────────────────────────────────────────────────
echo  [5/5] Starting services...
where ollama >nul 2>&1
if %errorlevel%==0 (
    start "" /min cmd /c "ollama run phi3:mini"
    timeout /t 4 /nobreak >nul
    echo        Ollama running in background.
) else (
    echo        Ollama not found - AI answers will be offline.
    echo        Fix: https://ollama.com/download  then: ollama pull phi3:mini
)

:: ── Start frontend HTTP server ────────────────────────────────────────────────
cd /d "%~dp0"
start "" /min cmd /c "%PY% -m http.server 3000 --directory frontend"
timeout /t 2 /nobreak >nul

:: ── Open browser ─────────────────────────────────────────────────────────────
start "" "http://localhost:3000"

:: ── Start backend ─────────────────────────────────────────────────────────────
echo.
echo   =========================================
echo    Frontend  :  http://localhost:3000
echo    Backend   :  http://localhost:8000/docs
echo    Ctrl+C    :  stop the server
echo   =========================================
echo.

cd /d "%~dp0backend"
%PY% -m uvicorn main:app --reload --port 8000 --host 0.0.0.0

echo.
echo  Server stopped.
pause
