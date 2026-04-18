@echo off
title Z-Paper

:: Frontend window
start "Z-Paper Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

:: Backend window
cd /d %~dp0backend
call venv\Scripts\activate.bat
uvicorn app.main:app --port 8000 --reload
