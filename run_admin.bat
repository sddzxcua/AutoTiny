@echo off
:: AutoTiny — Запуск от имени администратора без консоли

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -WindowStyle Hidden -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
start /B pythonw main.py 2>nul || start /B python main.py
