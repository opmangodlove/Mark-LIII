@echo off
rem ── JARVIS (Mark-LIII) launcher ──────────────────────────────────────────────
rem Starts the assistant using its own virtual-env Python, so no terminal
rem knowledge is needed. The window stays open only if the app crashes, so the
rem error can be read.
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" main.py
if %errorlevel% neq 0 (
  echo.
  echo JARVIS exited with an error. Details above.
  pause
)
