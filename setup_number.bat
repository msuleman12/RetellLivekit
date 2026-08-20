@echo off
REM Creates the LiveKit inbound trunk + dispatch rule for the intake number.
REM Does NOT touch Twilio and does NOT move the live number.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat
python scripts\setup_sip.py %*
pause
