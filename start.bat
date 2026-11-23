@echo off
title Z-Paper

:: Frontend window
start "Z-Paper Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

:: Backend window（用 python-embed312 运行时，无需 venv）
cd /d %~dp0backend
"%~dp0python-embed312\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
