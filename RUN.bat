@echo off
setlocal enabledelayedexpansion
title SchemeScan — Auto Setup + Run
color 0A
cls

echo.
echo  ================================================
echo    SchemeScan — Auto Setup + Run
echo  ================================================
echo.

:: ─────────────────────────────────────────────────────────────────
:: STEP 1: Find Python
:: ─────────────────────────────────────────────────────────────────
echo [1/5] Looking for Python...

set PYTHON=

python --version >nul 2>&1
if %errorlevel%==0 ( set PYTHON=python & goto :found_python )

python3 --version >nul 2>&1
if %errorlevel%==0 ( set PYTHON=python3 & goto :found_python )

for %%V in (313 312 311 310 39) do (
    for %%D in (
        "C:\Python%%V\python.exe"
        "C:\Program Files\Python%%V\python.exe"
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python%%V\python.exe"
    ) do (
        if exist %%D ( set PYTHON=%%D & goto :found_python )
    )
)

py --version >nul 2>&1
if %errorlevel%==0 ( set PYTHON=py & goto :found_python )

echo.
echo  ERROR: Python not found.
echo  Download from: https://www.python.org/downloads/
echo  IMPORTANT: Check "Add Python to PATH" during install.
pause
exit /b 1

:found_python
for /f "tokens=*" %%i in ('!PYTHON! --version 2^>^&1') do echo  Found: %%i
echo.

:: ─────────────────────────────────────────────────────────────────
:: STEP 2: Install packages
:: ─────────────────────────────────────────────────────────────────
echo [2/5] Installing packages...
!PYTHON! -m pip install --upgrade pip --quiet --disable-pip-version-check
!PYTHON! -m pip install fastapi "uvicorn[standard]" python-multipart httpx Pillow rank-bm25 --quiet --disable-pip-version-check
!PYTHON! -m pip install pytesseract --quiet --disable-pip-version-check >nul 2>&1
echo  Done.
echo.

:: ─────────────────────────────────────────────────────────────────
:: STEP 3: Build database
:: ─────────────────────────────────────────────────────────────────
echo [3/5] Checking scheme database...
cd /d "%~dp0backend"
if exist "schemes.db" (
    echo  schemes.db exists — skipping import.
) else (
    echo  Building database from scheme files ^(1-2 minutes^)...
    !PYTHON! import_schemes.py
)
echo.

:: ─────────────────────────────────────────────────────────────────
:: STEP 4: Start FastAPI backend in background window
:: ─────────────────────────────────────────────────────────────────
echo [4/5] Starting API backend on http://localhost:8000 ...
start "SchemeScan-API" /min cmd /c "cd /d "%~dp0backend" && !PYTHON! -m uvicorn main:app --reload --port 8000 --host 0.0.0.0 2>&1"
timeout /t 2 /nobreak >nul
echo  Backend started in background window.
echo.

:: ─────────────────────────────────────────────────────────────────
:: STEP 5: Serve frontend over HTTP on port 3000
::         (required for microphone / Web Speech API)
:: ─────────────────────────────────────────────────────────────────
echo [5/5] Starting frontend server on http://localhost:3000 ...
echo.
echo  ================================================
echo   Frontend : http://localhost:3000
echo   API      : http://localhost:8000
echo   API Docs : http://localhost:8000/docs
echo  ================================================
echo.
echo  Opening browser...
timeout /t 1 /nobreak >nul
start "" "http://localhost:3000"
echo.
echo  Press Ctrl+C to stop the frontend server.
echo  (Close the other window to stop the API too)
echo.

cd /d "%~dp0frontend"
!PYTHON! -m http.server 3000

echo.
pause
