"""
Менеджер конфигураций (в стиле читов) для AutoTiny v2.0.
Хранит пользовательские профили настроек сети и PXE в формате JSON в папке configs/.
Пользователь может свободно создавать, сохранять, загружать и удалять свои профили.

Авторы: Sddzxcua, Claude AI
"""

import os
import sys
import shutil
import json
from typing import List, Optional, Dict, Any


def _get_base_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_base_dir()
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")


def ensure_configs_dir():
    """Создаёт папку configs/, если её ещё нет, и распаковывает встроенные профили."""
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    if hasattr(sys, "_MEIPASS"):
        meipass_configs = os.path.join(sys._MEIPASS, "configs")
        if os.path.exists(meipass_configs):
            existing_jsons = [f for f in os.listdir(CONFIGS_DIR) if f.lower().endswith(".json")]
            if not existing_jsons:
                for f in os.listdir(meipass_configs):
                    if f.lower().endswith(".json"):
                        src = os.path.join(meipass_configs, f)
                        dst = os.path.join(CONFIGS_DIR, f)
                        try:
                            shutil.copy2(src, dst)
                        except Exception:
                            pass

    # Если после всех проверок папка configs всё ещё пуста — создаём Xiaomi_Recovery.json
    try:
        existing_jsons = [f for f in os.listdir(CONFIGS_DIR) if f.lower().endswith(".json")]
        if not existing_jsons:
            default_profile = {
                "name": "Xiaomi_Recovery",
                "dhcp_enabled": False,
                "dhcp_dns": False,
                "ip_address": "192.168.31.100",
                "subnet_mask": "255.255.255.0",
                "default_gateway": "192.168.31.1",
                "dns_primary": "192.168.31.1",
                "dns_secondary": "",
                "boot_filename": "test.bin",
                "proxy_dhcp": False,
                "pool_start": "192.168.31.2",
                "enable_http": False
            }
            default_path = os.path.join(CONFIGS_DIR, "Xiaomi_Recovery.json")
            with open(default_path, "w", encoding="utf-8") as f:
                json.dump(default_profile, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_config_names() -> List[str]:
    """Возвращает список доступных конфигураций (без расширения .json)."""
    if not os.path.exists(CONFIGS_DIR):
        return []
    names = []
    try:
        for f in os.listdir(CONFIGS_DIR):
            if f.lower().endswith(".json"):
                names.append(os.path.splitext(f)[0])
    except Exception:
        pass
    return sorted(names)


def load_config(name: str) -> Optional[Dict[str, Any]]:
    """Загружает конфиг по имени."""
    if not name:
        return None
    path = os.path.join(CONFIGS_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(name: str, data: Dict[str, Any]) -> bool:
    """Сохраняет / перезаписывает пользовательский конфиг."""
    if not name:
        return False
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    clean_name = os.path.basename(name).replace(".json", "")
    path = os.path.join(CONFIGS_DIR, f"{clean_name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def delete_config(name: str) -> bool:
    """Удаляет конфиг."""
    if not name:
        return False
    path = os.path.join(CONFIGS_DIR, f"{name}.json")
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False


def open_configs_folder():
    """Открывает папку configs/ в Проводнике."""
    ensure_configs_dir()
    try:
        os.startfile(CONFIGS_DIR)
    except Exception:
        pass
