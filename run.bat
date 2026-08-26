@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing dependencies...
pip install -r requirements.txt
echo Starting server on 0.0.0.0:8000...
python app.py