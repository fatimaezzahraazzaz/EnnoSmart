@echo off
cd /d C:\EnnoSmart
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe fix_ai_passage_click.py C:\EnnoSmart
) else (
  python fix_ai_passage_click.py C:\EnnoSmart
)
pause
