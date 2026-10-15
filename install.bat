@echo off
chcp 65001 >nul 2>&1
title AutoTiny — Установка зависимостей
color 0B

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║           AutoTiny — Установка               ║
echo   ║          by Sddzxcua, Claude AI              ║
echo   ╚══════════════════════════════════════════════╝
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo   [X] Python не найден!
    echo   [?] Скачайте Python с https://python.org
    echo   [!] При установке отметьте "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo   [OK] Python найден:
python --version
echo.

echo   [*] Проверка и установка зависимостей...
pip install customtkinter Pillow pyinstaller pyserial
if %errorlevel% equ 0 (
    echo   [OK] Все зависимости установлены (customtkinter, Pillow, pyinstaller, pyserial)
) else (
    echo   [!] Предупреждение при установке через pip
)

echo.
echo   ══════════════════════════════════════════════
echo   [OK] Установка завершена!
echo.
echo   Запуск:          python main.py
echo   Сборка в .exe:   python build.py
echo   ══════════════════════════════════════════════
echo.
pause
