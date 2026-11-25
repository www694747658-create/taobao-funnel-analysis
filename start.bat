@echo off
setlocal
title Taobao Funnel Analytics Dashboard

REM ============================================================
REM One-click launcher
REM   1. Check Python
REM   2. Check / install dependencies
REM   3. Check MySQL reachability (warn only)
REM   4. Start Streamlit on port 8502
REM NOTE: keep this file pure ASCII (no Chinese), otherwise cmd
REM       mis-decodes the text and prints garbage commands.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   Taobao User Behavior Analytics ^& Conversion Funnel
echo ============================================================
echo.

REM ---- 1. Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo [1/3] %PYVER%

REM ---- 2. Dependencies ----
python -c "import streamlit, pandas, sqlalchemy, pymysql, plotly, sklearn" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Missing dependencies, installing requirements.txt ...
    python -m pip install -r requirements.txt -q
) else (
    echo [2/3] Dependencies ready
)

REM ---- 3. MySQL ----
python -c "import sys; sys.path.insert(0, 'src'); from db import get_engine, ping; print(ping(get_engine()))" >nul 2>nul
if errorlevel 1 (
    echo [3/3] WARNING: MySQL not reachable. Start MySQL and make sure the taobao_funnel database exists.
    echo       Connection settings live in src/db.py or environment variables. The app will still start.
) else (
    echo [3/3] MySQL connection OK
)

REM ---- 4. Reuse a running instance instead of starting a second one ----
set "PORTBUSY=0"
for /f "tokens=*" %%L in ('netstat -ano ^| findstr ":8502" ^| findstr "LISTENING"') do set "PORTBUSY=1"
if "%PORTBUSY%"=="1" (
    echo.
    echo Port 8502 is already serving the dashboard. Opening browser only.
    start "" http://localhost:8502
    exit /b 0
)

echo.
echo Starting dashboard on port 8502, the browser will open automatically...
echo Stop it with Ctrl+C in this window, or just close the window.
echo.

start "" http://localhost:8502
python -m streamlit run app.py --server.port 8502 --server.headless true

pause
