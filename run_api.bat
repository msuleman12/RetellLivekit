@echo off
REM Control API on http://localhost:8000 (docs at /docs)
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat
python -m uvicorn src.api:app --host 0.0.0.0 --port 8000
