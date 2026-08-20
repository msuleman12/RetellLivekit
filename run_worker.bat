@echo off
REM Answers calls. Use "start" instead of "dev" in production.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat
python -m src.worker %*
