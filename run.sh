#!/bin/bash
# One-command launcher for Linux / macOS
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Starting server on 0.0.0.0:8000..."
python app.py
