"""
Модуль сетевого сервиса для AutoTiny v2.0:
- Управление TCP/IPv4 настройками Windows (netsh)
- Выбор адаптеров, чтение текущих параметров
- Установка статического IP / шлюза / маски / DHCP
- DNS-пресеты (Google, Cloudflare, Yandex, AdGuard, Quad9)
- Бэкапы сетевых настроек с авто-откатом при крашах
- Предполётные проверки (порт 69, VPN, фаервол, админ)
- Временные правила фаервола
- Автоопределение роутера (ARP/ping-сканер)
- Генерация баг-репорта

Авторы: Sddzxcua, Claude AI
"""

import os
import re
import json
import socket
import subprocess
import ctypes
import platform
import hashlib
import time
import threading
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Tuple, Dict, Callable
from datetime import datetime


def _get_base_dir() -> str:
    if hasattr(os.sys, "_MEIPASS"):
        return os.path.dirname(os.sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_base_dir()
BACKUP_FILE = os.path.join(BASE_DIR, "network_backup.json")
AUTO_BACKUP_FILE = os.path.join(BASE_DIR, "network_auto_backup.json")
CRASH_FLAG_FILE = os.path.join(BASE_DIR, ".network_changed_flag")
EXTERNAL_AUTOSETTINGS_BACKUP = os.path.join(
    os.path.dirname(BASE_DIR), "Autosetting", "network_backup.json"
)

APP_VERSION = "2.0.0"
FIREWALL_RULE_NAME = "AutoTiny_PXE_Allow"


# ────────────────────── Модель сетевых настроек ──────────────────────

@dataclass
class NetworkSettings:
    """Модель сетевых настроек адаптера."""
    adapter_name: str = ""
    dhcp_enabled: bool = True
    ip_address: str = ""
    subnet_mask: str = "255.255.255.0"
    default_gateway: str = ""
    dns_primary: str = ""
    dns_secondary: str = ""
    dhcp_dns: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NetworkSettings":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})


# ────────────────────── Пресеты DNS ──────────────────────

DNS_PRESETS = {
    "Google": {
        "dns_primary": "8.8.8.8",
        "dns_secondary": "8.8.4.4",
        "desc": "Google Public DNS",
    },
    "Cloudflare": {
        "dns_primary": "1.1.1.1",
        "dns_secondary": "1.0.0.1",
        "desc": "Cloudflare DNS (быстрый)",
    },
    "Yandex": {
        "dns_primary": "77.88.8.8",
        "dns_secondary": "77.88.8.1",
        "desc": "Яндекс DNS",
    },
    "AdGuard": {
        "dns_primary": "94.140.14.14",
        "dns_secondary": "94.140.15.15",
        "desc": "AdGuard DNS (блокировка рекламы)",
    },
    "Quad9": {
        "dns_primary": "9.9.9.9",
        "dns_secondary": "149.112.112.112",
        "desc": "Quad9 (защита от угроз)",
    },
}


# ────────────────────── Системные функции ──────────────────────

def is_admin() -> bool:
    """Проверяет права администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def restart_as_admin() -> bool:
    """Перезапускает приложение с правами администратора. Возвращает True при успехе."""
    import sys
    try:
        if hasattr(sys, "_MEIPASS"):
            exe = sys.executable
            args = ""
        else:
            exe = sys.executable
            args = f'"{os.path.abspath(sys.argv[0])}"'
        res = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        return int(res) > 32
    except Exception:
        return False


def run_cmd(command: str) -> Tuple[int, str, str]:
    """Запускает команду оболочки Windows."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            encoding="cp866",
            errors="replace",
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


# ────────────────────── Чтение сетевых настроек ──────────────────────

def get_adapter_names() -> List[str]:
    """Возвращает список имён активных адаптеров."""
    code, out, _ = run_cmd('netsh interface ipv4 show interfaces')
    adapters = []
    for line in out.splitlines():
        match = re.match(r'\s*\d+\s+\d+\s+\d+\s+(?:connected|подключено|подключен)\s+(.+)', line, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if "loopback" not in name.lower():
                adapters.append(name)
    if not adapters:
        excluded_headers = ("имя", "name", "---", "состояние", "state", "интерфейс", "interface", "тип", "type")
        for line in out.splitlines():
            m = re.match(r'\s*\d+\s+\d+\s+\d+\s+\S+\s+(.+)', line)
            if m:
                n = m.group(1).strip()
                n_lower = n.lower()
                if "loopback" not in n_lower and not any(h in n_lower for h in excluded_headers):
                    adapters.append(n)
    return adapters or ["Ethernet"]


def get_current_settings(adapter_name: str) -> NetworkSettings:
    """Считывает текущие настройки выбранного адаптера."""
    settings = NetworkSettings(adapter_name=adapter_name)
    if not adapter_name:
        return settings

    # IP конфигурация
    code, out, _ = run_cmd(f'netsh interface ipv4 show addresses "{adapter_name}"')

    # DHCP
    dhcp_m = re.search(r'DHCP\s+enabled[:\s]+(yes|да)', out, re.IGNORECASE)
    settings.dhcp_enabled = dhcp_m is not None

    # IP
    ip_m = re.search(r'IP\s*[Aa]ddress[:\s]+([\d.]+)', out)
    if not ip_m:
        ip_m = re.search(r'IP-[аА]дрес[:\s]+([\d.]+)', out)
    if ip_m:
        settings.ip_address = ip_m.group(1)

    # Маска
    mask_m = re.search(r'mask\s+([\d.]+)', out, re.IGNORECASE)
    if not mask_m:
        mask_m = re.search(r'[Мм]аска\s*подсети[:\s]+([\d.]+)', out)
    if mask_m:
        settings.subnet_mask = mask_m.group(1)

    # Шлюз
    gw_m = re.search(r'[Dd]efault\s*[Gg]ateway[:\s]+([\d.]+)', out)
    if not gw_m:
        gw_m = re.search(r'[Шш]люз[:\s]+([\d.]+)', out)
    if gw_m:
        settings.default_gateway = gw_m.group(1)

    # DNS
    code, dns_out, _ = run_cmd(f'netsh interface ipv4 show dnsservers "{adapter_name}"')
    if "DHCP" in dns_out.upper() or "dhcp" in dns_out.lower():
        dhcp_dns_m = re.search(r'(configured through DHCP|настроен через DHCP|DHCP)', dns_out, re.IGNORECASE)
        settings.dhcp_dns = dhcp_dns_m is not None
    else:
        settings.dhcp_dns = False

    dns_servers = re.findall(r'((?:\d{1,3}\.){3}\d{1,3})', dns_out)
    if dns_servers:
        settings.dns_primary = dns_servers[0]
        if len(dns_servers) > 1:
            settings.dns_secondary = dns_servers[1]

    return settings


# ────────────────────── Применение настроек ──────────────────────

def apply_network_settings(settings: NetworkSettings) -> Tuple[bool, str]:
    """Применяет сетевые настройки через netsh."""
    if not is_admin():
        return False, "Для изменения параметров сети требуются права Администратора!"

    adapter = settings.adapter_name
    errors = []

    # 1. IP конфигурация
    if settings.dhcp_enabled:
        code, _, err = run_cmd(f'netsh interface ipv4 set address "{adapter}" source=dhcp')
        if code != 0:
            errors.append(f"DHCP IP: {err.strip()}")
    else:
        gw_part = f' gateway={settings.default_gateway}' if settings.default_gateway else ""
        code, _, err = run_cmd(
            f'netsh interface ipv4 set address "{adapter}" static '
            f'{settings.ip_address} {settings.subnet_mask}{gw_part}'
        )
        if code != 0:
            errors.append(f"Статический IP: {err.strip()}")

    # 2. DNS конфигурация
    if settings.dhcp_dns:
        code, _, err = run_cmd(f'netsh interface ipv4 set dnsservers "{adapter}" source=dhcp')
        if code != 0:
            errors.append(f"DHCP DNS: {err.strip()}")
    else:
        if settings.dns_primary:
            code, _, err = run_cmd(
                f'netsh interface ipv4 set dnsservers "{adapter}" static {settings.dns_primary} primary'
            )
            if code != 0:
                errors.append(f"Первичный DNS: {err.strip()}")

            if settings.dns_secondary:
                code, _, err = run_cmd(
                    f'netsh interface ipv4 add dnsservers "{adapter}" {settings.dns_secondary} index=2'
                )
                if code != 0:
                    errors.append(f"Вторичный DNS: {err.strip()}")

    if errors:
        return False, "\n".join(errors)
    return True, "Настройки успешно применены к адаптеру!"


# ────────────────────── Бэкап и восстановление ──────────────────────

def save_backup(settings: NetworkSettings) -> bool:
    """Сохраняет настройки в файл бэкапа."""
    try:
        with open(BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_backup() -> Optional[NetworkSettings]:
    """Загружает настройки из бэкапа (основного, авто-бэкапа или Autosettings)."""
    targets = [BACKUP_FILE, AUTO_BACKUP_FILE, EXTERNAL_AUTOSETTINGS_BACKUP]
    for target in targets:
        if os.path.exists(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "settings" in data and isinstance(data["settings"], dict):
                        return NetworkSettings.from_dict(data["settings"])
                    return NetworkSettings.from_dict(data)
            except Exception:
                continue
    return None


def has_backup() -> bool:
    """Проверяет наличие файла бэкапа."""
    return (
        os.path.exists(BACKUP_FILE)
        or os.path.exists(AUTO_BACKUP_FILE)
        or os.path.exists(EXTERNAL_AUTOSETTINGS_BACKUP)
    )


# ────────────────────── Авто-бэкап (безопасное восстановление) ──────────────────────

def save_auto_backup(adapter_name: str) -> bool:
    """
    Сохраняет текущие настройки адаптера в авто-бэкап ПЕРЕД изменением IP.
    Если основной бэкап ещё не создан, сохраняет исходное состояние и туда.
    Ставит crash-флаг, который снимается при нормальном завершении.
    """
    try:
        current = get_current_settings(adapter_name)
        # Если основного бэкапа ещё нет, сохраняем исходное состояние в основной network_backup.json
        if not os.path.exists(BACKUP_FILE):
            save_backup(current)

        data = {
            "settings": current.to_dict(),
            "timestamp": datetime.now().isoformat(),
            "reason": "auto_backup_before_change",
        }
        with open(AUTO_BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Ставим crash-флаг
        with open(CRASH_FLAG_FILE, "w") as f:
            f.write(datetime.now().isoformat())
        return True
    except Exception:
        return False


def clear_crash_flag():
    """Снимает crash-флаг — значит программа завершилась нормально."""
    try:
        if os.path.exists(CRASH_FLAG_FILE):
            os.remove(CRASH_FLAG_FILE)
    except Exception:
        pass


def check_crash_recovery() -> Optional[NetworkSettings]:
    """
    Проверяет, нужен ли откат после аварийного завершения.
    Если crash-флаг остался — значит программа крашнулась после смены IP.
    Возвращает NetworkSettings для отката или None.
    """
    if not os.path.exists(CRASH_FLAG_FILE):
        return None
    if not os.path.exists(AUTO_BACKUP_FILE):
        clear_crash_flag()
        return None
    try:
        with open(AUTO_BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return NetworkSettings.from_dict(data["settings"])
    except Exception:
        clear_crash_flag()
        return None


def rollback_network_on_exit(adapter_name: str) -> Tuple[bool, str]:
    """
    Восстанавливает сетевые настройки из авто-бэкапа при нормальном закрытии.
    Вызывается из on_close если пользователь менял IP во время работы.
    """
    if not os.path.exists(AUTO_BACKUP_FILE):
        clear_crash_flag()
        return False, "Авто-бэкап не найден"
    try:
        with open(AUTO_BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = NetworkSettings.from_dict(data["settings"])
        ok, msg = apply_network_settings(settings)
        clear_crash_flag()
        return ok, msg
    except Exception as e:
        clear_crash_flag()
        return False, str(e)


# ────────────────────── Предполётные проверки ──────────────────────

@dataclass
class PreflightResult:
    """Результат одной проверки."""
    name: str
    passed: bool
    message: str
    severity: str = "info"  # info, warning, error


def check_udp_port_busy(port: int = 69) -> PreflightResult:
    """Проверяет, занят ли UDP-порт другой программой."""
    name = f"UDP порт {port}"
    try:
        code, out, _ = run_cmd(f'netstat -anu | findstr ":{port} "')
        if f":{port} " in out or f":{port}\t" in out:
            # Пробуем найти процесс
            code2, out2, _ = run_cmd(f'netstat -anob | findstr /C:":{port}"')
            return PreflightResult(
                name=name,
                passed=False,
                message=f"Порт UDP {port} уже занят другой программой!\n{out2.strip()[:200]}",
                severity="error"
            )
        return PreflightResult(
            name=name,
            passed=True,
            message=f"Порт UDP {port} свободен ✓"
        )
    except Exception:
        # Альтернативный способ — попробовать привязаться к порту
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", port))
            sock.close()
            return PreflightResult(
                name=name,
                passed=True,
                message=f"Порт UDP {port} свободен ✓"
            )
        except OSError:
            return PreflightResult(
                name=name,
                passed=False,
                message=f"Порт UDP {port} занят!",
                severity="error"
            )


def check_vpn_active() -> PreflightResult:
    """Проверяет наличие активных VPN-соединений которые могут мешать."""
    vpn_keywords = ["tap", "tun", "vpn", "wireguard", "wintun", "openvpn", "nordvpn", "proton"]
    code, out, _ = run_cmd('netsh interface show interface')
    active_vpns = []
    for line in out.splitlines():
        lower = line.lower()
        if "connected" in lower or "подключен" in lower:
            for kw in vpn_keywords:
                if kw in lower:
                    name = line.split()[-1] if line.split() else line.strip()
                    active_vpns.append(name)
                    break

    if active_vpns:
        return PreflightResult(
            name="VPN",
            passed=False,
            message=f"Обнаружены активные VPN: {', '.join(active_vpns)}. Могут мешать PXE!",
            severity="warning"
        )
    return PreflightResult(
        name="VPN",
        passed=True,
        message="Активных VPN не обнаружено ✓"
    )


def check_wifi_active() -> PreflightResult:
    """Проверяет, включён ли Wi-Fi (может мешать при прошивке по кабелю)."""
    code, out, _ = run_cmd('netsh interface show interface')
    for line in out.splitlines():
        lower = line.lower()
        if ("wi-fi" in lower or "wifi" in lower or "wlan" in lower or "беспроводн" in lower):
            if "connected" in lower or "подключен" in lower:
                return PreflightResult(
                    name="Wi-Fi",
                    passed=False,
                    message="Wi-Fi включён! Рекомендуется отключить Wi-Fi при прошивке по кабелю.",
                    severity="warning"
                )
    return PreflightResult(
        name="Wi-Fi",
        passed=True,
        message="Wi-Fi не активен (хорошо для кабельной прошивки) ✓"
    )


def check_admin_rights() -> PreflightResult:
    """Проверяет права администратора."""
    if is_admin():
        return PreflightResult(
            name="Права администратора",
            passed=True,
            message="Запущено с правами администратора ✓"
        )
    return PreflightResult(
        name="Права администратора",
        passed=False,
        message="Нет прав администратора! Не удастся менять настройки сети и запускать DHCP.",
        severity="error"
    )


def check_firewall_status() -> PreflightResult:
    """Проверяет состояние Windows Firewall."""
    code, out, _ = run_cmd('netsh advfirewall show allprofiles state')
    blocked_profiles = []
    current_profile = ""
    for line in out.splitlines():
        line_s = line.strip()
        if "Profile" in line_s or "профил" in line_s.lower():
            current_profile = line_s
        if "ON" in line_s.upper() or "Вкл" in line_s:
            blocked_profiles.append(current_profile or "Unknown")

    if blocked_profiles:
        return PreflightResult(
            name="Фаервол",
            passed=False,
            message=f"Фаервол Windows включён ({len(blocked_profiles)} проф.). Может блокировать UDP 67/69.",
            severity="warning"
        )
    return PreflightResult(
        name="Фаервол",
        passed=True,
        message="Фаервол Windows выключен ✓"
    )


def check_pxesrv_exists() -> PreflightResult:
    """Проверяет наличие pxesrv.exe."""
    from pxe_service import PXESRV_EXE
    if os.path.exists(PXESRV_EXE):
        return PreflightResult(
            name="pxesrv.exe",
            passed=True,
            message="pxesrv.exe найден ✓"
        )
    return PreflightResult(
        name="pxesrv.exe",
        passed=False,
        message=f"pxesrv.exe не найден по пути: {PXESRV_EXE}",
        severity="error"
    )


def run_all_preflight_checks() -> List[PreflightResult]:
    """Запускает все предполётные проверки и возвращает список результатов."""
    checks = [
        check_admin_rights(),
        check_pxesrv_exists(),
        check_udp_port_busy(67),
        check_udp_port_busy(69),
        check_vpn_active(),
        check_wifi_active(),
        check_firewall_status(),
    ]
    return checks


# ────────────────────── Временные правила фаервола ──────────────────────

def add_firewall_rule() -> Tuple[bool, str]:
    """Добавляет временное правило фаервола для PXE (UDP 67, 69)."""
    if not is_admin():
        return False, "Нужны права администратора"
    try:
        cmds = [
            f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}_DHCP" dir=in action=allow protocol=UDP localport=67 enable=yes',
            f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}_TFTP" dir=in action=allow protocol=UDP localport=69 enable=yes',
            f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}_HTTP" dir=in action=allow protocol=TCP localport=80 enable=yes',
        ]
        for cmd in cmds:
            run_cmd(cmd)
        return True, "Правила фаервола добавлены для UDP 67, 69 и TCP 80"
    except Exception as e:
        return False, f"Ошибка: {e}"


def remove_firewall_rule() -> Tuple[bool, str]:
    """Удаляет временные правила фаервола."""
    try:
        cmds = [
            f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}_DHCP"',
            f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}_TFTP"',
            f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}_HTTP"',
        ]
        for cmd in cmds:
            run_cmd(cmd)
        return True, "Правила фаервола удалены"
    except Exception as e:
        return False, f"Ошибка: {e}"


def has_firewall_rule() -> bool:
    """Проверяет наличие нашего правила фаервола."""
    code, out, _ = run_cmd(f'netsh advfirewall firewall show rule name="{FIREWALL_RULE_NAME}_TFTP"')
    return FIREWALL_RULE_NAME in out


# ────────────────────── Автоопределение роутера ──────────────────────

@dataclass
class RouterInfo:
    """Информация о найденном устройстве в сети."""
    ip: str = ""
    mac: str = ""
    vendor: str = ""
    responding: bool = False


# Известные OUI-префиксы (первые 6 символов MAC без разделителей)
VENDOR_OUI = {
    "28:6C:07": "Xiaomi",
    "C4:6E:1F": "TP-Link",
    "08:CC:68": "Xiaomi",
    "34:CE:00": "Xiaomi",
    "64:64:4A": "Xiaomi",
    "78:11:DC": "Xiaomi",
    "A4:56:02": "MediaTek",
    "00:0C:43": "Ralink (MediaTek)",
    "7C:8B:CA": "TP-Link",
    "EC:41:18": "TP-Link",
    "B0:95:75": "TP-Link",
    "F4:EC:38": "TP-Link",
    "50:D2:F5": "TP-Link",
    "A8:5E:45": "ASUSTek",
    "04:D9:F5": "ASUSTek",
    "B0:6E:BF": "ASUSTek",
    "60:45:CB": "ASUSTek",
    "AC:9E:17": "ASUSTek",
    "0C:9D:92": "ASUSTek",
    "C8:60:00": "ASUSTek",
    "60:A4:4C": "ASUSTek",
    "70:8B:CD": "ASUSTek",
    "FC:34:97": "ASUSTek",
    "00:1A:2B": "Keenetic/Zyxel",
    "F0:9F:C2": "Keenetic",
    "FC:E8:06": "Keenetic",
    "BC:CF:4F": "Keenetic",
    "CC:50:E3": "Edimax",
    "08:86:3B": "Belkin",
    "D8:0D:17": "TP-Link",
    "00:50:56": "VMware",
    "00:0C:29": "VMware",
    "08:00:27": "VirtualBox",
}


def lookup_vendor(mac: str) -> str:
    """Ищет вендора по MAC-адресу."""
    if not mac:
        return "Неизвестно"
    # Нормализуем MAC
    clean = mac.upper().replace("-", ":").replace(".", ":")
    prefix = clean[:8]  # XX:XX:XX
    return VENDOR_OUI.get(prefix, "Неизвестно")


def get_mac_from_arp(ip: str) -> str:
    """Получает MAC-адрес через ARP-таблицу."""
    code, out, _ = run_cmd(f'arp -a {ip}')
    mac_m = re.search(r'([\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2})', out)
    if mac_m:
        return mac_m.group(1).upper().replace("-", ":")
    return ""


def scan_for_router(
    target_ips: List[str] = None,
    timeout_ms: int = 500,
    callback: Callable[[RouterInfo], None] = None
) -> List[RouterInfo]:
    """
    Сканирует типовые адреса роутеров.
    Для каждого найденного вызывает callback с RouterInfo.
    """
    if target_ips is None:
        target_ips = [
            "192.168.1.1",
            "192.168.0.1",
            "192.168.31.1",
            "192.168.88.1",   # MikroTik
            "10.0.0.1",
            "192.168.10.1",
            "192.168.8.1",    # Huawei
            "192.168.2.1",
        ]

    found = []
    for ip in target_ips:
        responding = ping_host(ip, timeout_ms=timeout_ms)
        mac = ""
        vendor = ""
        if responding:
            mac = get_mac_from_arp(ip)
            vendor = lookup_vendor(mac)

        info = RouterInfo(
            ip=ip,
            mac=mac,
            vendor=vendor,
            responding=responding,
        )
        if responding:
            found.append(info)
            if callback:
                callback(info)

    return found


# ────────────────────── Проверка прошивки ──────────────────────

def calculate_file_hash(filepath: str, algorithm: str = "sha256") -> str:
    """Вычисляет хеш файла."""
    if not os.path.exists(filepath):
        return ""
    hasher = hashlib.new(algorithm)
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""


def verify_firmware(filepath: str, expected_hash: str = "", expected_size: int = 0) -> List[PreflightResult]:
    """
    Проверяет файл прошивки: размер, хеш, подозрительное имя.
    """
    results = []

    if not os.path.exists(filepath):
        results.append(PreflightResult(
            name="Файл прошивки",
            passed=False,
            message=f"Файл не найден: {filepath}",
            severity="error"
        ))
        return results

    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)

    # Проверка размера
    if file_size < 1024:
        results.append(PreflightResult(
            name="Размер файла",
            passed=False,
            message=f"Файл слишком маленький ({file_size} байт) — вероятно, заглушка.",
            severity="warning"
        ))
    elif expected_size > 0:
        if abs(file_size - expected_size) > 1024:
            results.append(PreflightResult(
                name="Размер файла",
                passed=False,
                message=f"Размер файла ({file_size}) не совпадает с ожидаемым ({expected_size})",
                severity="warning"
            ))
        else:
            results.append(PreflightResult(
                name="Размер файла",
                passed=True,
                message=f"Размер файла совпадает с ожидаемым ✓ ({file_size} байт)"
            ))
    else:
        from pxe_service import format_bytes
        results.append(PreflightResult(
            name="Размер файла",
            passed=True,
            message=f"Размер файла: {format_bytes(file_size)}"
        ))

    # Проверка хеша
    if expected_hash:
        algo = "md5" if len(expected_hash) == 32 else "sha256"
        actual_hash = calculate_file_hash(filepath, algo)
        if actual_hash.lower() == expected_hash.lower():
            results.append(PreflightResult(
                name=f"Контрольная сумма ({algo.upper()})",
                passed=True,
                message=f"Хеш совпадает ✓"
            ))
        else:
            results.append(PreflightResult(
                name=f"Контрольная сумма ({algo.upper()})",
                passed=False,
                message=f"Хеш НЕ совпадает!\nОжидаемый: {expected_hash[:16]}...\nПолученный: {actual_hash[:16]}...",
                severity="warning"
            ))
    else:
        sha = calculate_file_hash(filepath, "sha256")
        results.append(PreflightResult(
            name="SHA256",
            passed=True,
            message=f"SHA256: {sha[:32]}..."
        ))

    return results


# ────────────────────── Пинг ──────────────────────

def ping_host(host: str, timeout_ms: int = 800) -> bool:
    """Пингует указанный IP-адрес."""
    if not host:
        return False
    code, out, _ = run_cmd(f"ping -n 1 -w {timeout_ms} {host}")
    return code == 0 and ("TTL=" in out or "ttl=" in out)


# ────────────────────── Баг-репорт ──────────────────────

def generate_bug_report(
    adapter_name: str = "",
    config_name: str = "",
    log_lines: List[str] = None,
    extra_info: Dict = None
) -> str:
    """Генерирует текстовый отчёт для баг-репорта."""
    lines = [
        "=" * 60,
        "  AutoTiny — Отчёт для баг-репорта",
        "=" * 60,
        "",
        f"Версия: {APP_VERSION}",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"ОС: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"Архитектура: {platform.architecture()[0]}",
        f"Админ: {'Да' if is_admin() else 'Нет'}",
        "",
    ]

    if adapter_name:
        lines.append(f"Адаптер: {adapter_name}")
        settings = get_current_settings(adapter_name)
        lines.append(f"  DHCP: {'Да' if settings.dhcp_enabled else 'Нет'}")
        lines.append(f"  IP: {settings.ip_address}")
        lines.append(f"  Маска: {settings.subnet_mask}")
        lines.append(f"  Шлюз: {settings.default_gateway}")
        lines.append(f"  DNS: {settings.dns_primary} / {settings.dns_secondary}")
        lines.append("")

    if config_name:
        lines.append(f"Конфиг: {config_name}")
        lines.append("")

    # Предполётные проверки
    lines.append("--- Предполётные проверки ---")
    for check in run_all_preflight_checks():
        status = "✓" if check.passed else "✗"
        lines.append(f"  [{status}] {check.name}: {check.message}")
    lines.append("")

    if log_lines:
        lines.append("--- Последние строки лога ---")
        for line in log_lines[-30:]:
            lines.append(f"  {line.rstrip()}")
        lines.append("")

    if extra_info:
        lines.append("--- Дополнительная информация ---")
        for k, v in extra_info.items():
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
