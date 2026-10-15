"""
Скрипт сборки AutoTiny v2.0 в исполняемый файл .exe через PyInstaller.
Запуск: python build.py
"""

import subprocess
import sys
import os
import io
import shutil

# Вывод в UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

APP_NAME = "AutoTiny"
MAIN_SCRIPT = "main.py"
ICON_FILE = "icon.ico"


def build():
    print("")
    print("  ============================================")
    print(f"   Сборка {APP_NAME} v2.0 .exe")
    print("   by Sddzxcua, Claude AI")
    print("  ============================================")
    print("")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(base_dir, "dist")
    target_exe = os.path.join(dist_dir, f"{APP_NAME}.exe")

    # Если целевой EXE запущен или заблокирован, переименовываем его в .old
    if os.path.exists(target_exe):
        try:
            os.remove(target_exe)
        except Exception:
            backup_old = os.path.join(dist_dir, f"{APP_NAME}.exe.old")
            try:
                if os.path.exists(backup_old):
                    try:
                        os.remove(backup_old)
                    except Exception:
                        pass
                os.rename(target_exe, backup_old)
                print(f"  [i] Предыдущий {APP_NAME}.exe был заблокирован и переименован в .old")
            except Exception as e:
                print(f"  [!] Не удалось освободить {target_exe}: {e}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        f"--name={APP_NAME}",
        "--clean",
    ]

    if os.path.exists(ICON_FILE):
        cmd.append(f"--icon={ICON_FILE}")
        print(f"  [+] Иконка: {ICON_FILE}")

    # Добавляем изображения
    for img in ("icon.ico", "icon.png"):
        if os.path.exists(img):
            cmd.append(f"--add-data={img};.")
            print(f"  [+] Графика: {img}")

    # Добавляем папку configs
    configs_path = os.path.join(base_dir, "configs")
    if os.path.exists(configs_path):
        cmd.append(f"--add-data={configs_path};configs")
        print(f"  [+] Папка configs включена в сборку")

    # Добавляем папку TinyPXE
    tinypxe_path = os.path.join(base_dir, "TinyPXE")
    if os.path.exists(tinypxe_path):
        cmd.append(f"--add-data={tinypxe_path};TinyPXE")
        print(f"  [+] Папка TinyPXE включена в сборку")

    cmd.extend([
        "--hidden-import=customtkinter",
        "--hidden-import=darkdetect",
        "--hidden-import=PIL",
        "--hidden-import=serial",
        "--hidden-import=serial.tools.list_ports",
        "--collect-all=customtkinter",
    ])

    cmd.append(MAIN_SCRIPT)

    print(f"\n  [*] Запуск PyInstaller...\n")
    print("  " + "-" * 50)

    result = subprocess.run(cmd, cwd=base_dir)

    if result.returncode == 0:
        dist_dir = os.path.join(base_dir, "dist")
        exe_path = os.path.join(dist_dir, f"{APP_NAME}.exe")
        size_mb = os.path.getsize(exe_path) / (1024 * 1024) if os.path.exists(exe_path) else 0

        # Актуализируем папку TinyPXE в dist рядом с exe
        dist_tinypxe = os.path.join(dist_dir, "TinyPXE")
        if os.path.exists(dist_tinypxe):
            shutil.rmtree(dist_tinypxe, ignore_errors=True)
        shutil.copytree(tinypxe_path, dist_tinypxe)
        print(f"  [+] Актуализирована папка TinyPXE в dist/")

        # Актуализируем папку configs в dist рядом с exe
        dist_configs = os.path.join(dist_dir, "configs")
        if os.path.exists(dist_configs):
            shutil.rmtree(dist_configs, ignore_errors=True)
        shutil.copytree(configs_path, dist_configs)
        print(f"  [+] Актуализирована папка configs в dist/")

        print(f"\n  {'-' * 50}")
        print(f"  [OK] Сборка успешно завершена!")
        print(f"  [OK] Файл: {os.path.abspath(exe_path)}")
        print(f"  [OK] Размер: {size_mb:.1f} МБ")
        print(f"\n  Запуск: дважды кликните {APP_NAME}.exe")
        print(f"  (Рекомендуется: Запуск от имени администратора)\n")
    else:
        print(f"\n  [FAIL] Сборка завершилась с ошибкой! Код: {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(build())
