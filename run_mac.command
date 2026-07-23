#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
if [ ! -f ".env" ]; then
  cp .env.example .env
fi
.venv/bin/python -m streamlit run app.py
