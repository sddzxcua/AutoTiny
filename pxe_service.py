"""
Сервис управления PXE, TFTP и UART для AutoTiny v2.0:
- Встроенный Python TFTP сервер (RFC 1350 + RFC 2347/2348 blksize/tsize)
  с живым прогресс-баром, измерением скорости, подсчётом повторов и dry-run режимом!
- Управление процессом TinyPXE (pxesrv.exe) для DHCP / ProxyDHCP / HTTP
- Встроенный UART-терминал для логов загрузки и перехвата U-Boot
- Проверка и импорт файлов прошивок (test.bin, recovery.bin, etc.)
- Тестирование TFTP сервиса

Авторы: Sddzxcua, Claude AI
"""

import os
import sys
import shutil
import socket
import subprocess
import time
import threading
import configparser
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple, Callable, Dict, Any


def _get_app_dir() -> str:
    """Возвращает рабочую директорию приложения."""
    if hasattr(sys, "_MEIPASS"):
        exe_dir = os.path.dirname(sys.executable)
        target_tinypxe = os.path.join(exe_dir, "TinyPXE")
        meipass_tinypxe = os.path.join(sys._MEIPASS, "TinyPXE")
        if not os.path.exists(target_tinypxe) and os.path.exists(meipass_tinypxe):
            try:
                shutil.copytree(meipass_tinypxe, target_tinypxe)
            except Exception:
                pass
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _get_app_dir()
TINYPXE_DIR = os.path.join(APP_DIR, "TinyPXE")
PXESRV_EXE = os.path.join(TINYPXE_DIR, "pxesrv.exe")
CONFIG_INI = os.path.join(TINYPXE_DIR, "config.ini")
AUTOTINY_CONFIG = os.path.join(APP_DIR, "autotiny_config.json")


# ────────────────────── Форматирование байт ──────────────────────

def format_bytes(size_bytes: int) -> str:
    """Форматирует байты в КБ / МБ."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} МБ"


# ────────────────────── Модель настроек PXE ──────────────────────

@dataclass
class PXEConfig:
    """Конфигурация AutoTiny PXE."""
    # Режим работы
    tftp_engine: str = "builtin"       # "builtin" (Python TFTP с прогрессом) или "tinypxe"
    dry_run: bool = False              # Тестовый прогон без реальной прошивки
    proxy_dhcp: bool = False
    show_raw_window: bool = False
    auto_start_server: bool = True

    # Сеть
    server_ip: str = "192.168.1.2"
    subnet_mask: str = "255.255.255.0"
    gateway: str = "192.168.1.1"
    dns_server: str = "192.168.1.1"
    pool_start: str = "192.168.1.3"
    pool_size: int = 10

    # Загрузочные файлы
    boot_filename: str = "test.bin"
    alt_filename: str = ""
    root_dir: str = ""
    uefi_x64_file: str = ""

    # Доп. службы
    enable_http: bool = False
    http_port: int = 80
    enable_log: bool = True

    # UART
    uart_port: str = ""
    uart_baud: int = 115200

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PXEConfig":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})


# ────────────────────── Встроенный Python TFTP Сервер ──────────────────────

class PythonTFTPServer:
    """
    Полнофункциональный TFTP-сервер (RFC 1350 + RFC 2347/2348 blksize/tsize)
    с живым отображением прогресса, подсчётом скорости, повторов и поддержкой dry-run.
    """

    def __init__(
        self,
        bind_ip: str = "0.0.0.0",
        port: int = 69,
        root_dir: str = TINYPXE_DIR,
        dry_run: bool = False,
    ):
        self.bind_ip = bind_ip
        self.port = port
        self.root_dir = root_dir
        self.dry_run = dry_run
        self.default_boot_file: str = "test.bin"
        self._sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Коллбэки для UI
        self.on_request: Optional[Callable[[str, int, str], None]] = None
        self.on_progress: Optional[Callable[[int, int, str, int, float], None]] = None
        self.on_complete: Optional[Callable[[str, str, float, int], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_log: Optional[Callable[[str, str], None]] = None

    @property
    def is_running(self) -> bool:
        return self._sock is not None and not self._stop_event.is_set()

    def start(self) -> Tuple[bool, str]:
        """Запускает TFTP-сервер в фоновом потоке."""
        if self.is_running:
            return True, "TFTP сервер уже работает"

        self._stop_event.clear()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Привязываемся к порту 69
            self._sock.bind((self.bind_ip, self.port))
            self._sock.settimeout(1.0)
        except Exception as e:
            self._sock = None
            return False, f"Не удалось занять UDP порт {self.port}: {e}"

        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()
        return True, f"TFTP сервер запущен на {self.bind_ip}:{self.port}"

    def stop(self):
        """Останавливает сервер."""
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _log(self, msg: str, level: str = "TFTP"):
        if self.on_log:
            try:
                self.on_log(msg, level)
            except Exception:
                pass

    def _server_loop(self):
        """Главный цикл приёма новых запросов."""
        self._log(f"Встроенный TFTP-сервер слушает UDP порт {self.port}")
        while not self._stop_event.is_set():
            try:
                data, client_addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if not self._stop_event.is_set():
                    self._log(f"Ошибка сокета: {e}", "ОШИБКА")
                break

            if len(data) < 4:
                continue

            opcode = int.from_bytes(data[:2], "big")
            if opcode == 1:  # RRQ
                threading.Thread(
                    target=self._handle_rrq,
                    args=(data, client_addr),
                    daemon=True
                ).start()
            elif opcode == 2:  # WRQ
                # Запись не разрешена
                self._send_error(client_addr, 4, "Запись на сервер запрещена")
            else:
                self._send_error(client_addr, 4, "Неподдерживаемая операция")

    def _send_error(self, addr: Tuple[str, int], err_code: int, err_msg: str, sock: socket.socket = None):
        """Отправляет TFTP ERROR пакет."""
        s = sock or self._sock
        if not s:
            return
        payload = b"\x00\x05" + err_code.to_bytes(2, "big") + err_msg.encode("latin1", errors="replace") + b"\x00"
        try:
            s.sendto(payload, addr)
        except Exception:
            pass

    def _handle_rrq(self, packet: bytes, client_addr: Tuple[str, int]):
        """Обрабатывает Read Request от клиента."""
        client_ip, client_port = client_addr

        # Разбираем пакет RRQ: [01][filename][00][mode][00][opt1][00][val1][00]...
        parts = packet[2:].split(b"\x00")
        if len(parts) < 2:
            return

        raw_filename = parts[0].decode("latin1", errors="replace").strip()
        mode = parts[1].decode("latin1", errors="replace").lower()

        # Разбор опций RFC 2347 (blksize, tsize)
        options: Dict[str, str] = {}
        i = 2
        while i + 1 < len(parts) and parts[i]:
            opt_name = parts[i].decode("latin1", errors="replace").lower()
            opt_val = parts[i + 1].decode("latin1", errors="replace")
            options[opt_name] = opt_val
            i += 2

        filename = os.path.basename(raw_filename)
        self._log(f"Запрос от {client_ip}:{client_port} -> файл '{filename}' (режим: {mode})", "TFTP-ЗАПРОС")

        if self.on_request:
            try:
                self.on_request(client_ip, client_port, filename)
            except Exception:
                pass

        # Ищем файл в папке
        filepath = os.path.join(self.root_dir, filename)
        if not os.path.exists(filepath):
            target_default = os.path.basename(self.default_boot_file) if self.default_boot_file else "test.bin"
            default_path = os.path.join(self.root_dir, target_default)
            test_bin = os.path.join(self.root_dir, "test.bin")

            if os.path.exists(default_path):
                self._log(f"Роутер запросил '{filename}', отдаём выбранный образ '{self.default_boot_file}'", "TFTP")
                filepath = default_path
            elif os.path.exists(test_bin):
                self._log(f"Файл '{filename}' не найден, используется test.bin", "TFTP")
                filepath = test_bin
            else:
                self._log(f"Файл '{filename}' не найден в папке!", "ОШИБКА")
                self._send_error(client_addr, 1, f"Файл {filename} не найден")
                return

        file_size = os.path.getsize(filepath)
        blksize = 512
        if "blksize" in options:
            try:
                req_blk = int(options["blksize"])
                if 512 <= req_blk <= 65464:
                    blksize = req_blk
            except ValueError:
                pass

        # Сокет передачи для этого клиента (динамический порт по RFC 1350)
        data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        data_sock.settimeout(2.0)

        # Режим Dry-Run
        if self.dry_run:
            self._log(f"[DRY-RUN] Имитация передачи {filename} ({format_bytes(file_size)}) клиенту {client_ip}", "DRY-RUN")
            # Симулируем передачу для UI
            steps = 10
            for step in range(1, steps + 1):
                time.sleep(0.15)
                sent = int(file_size * (step / steps))
                pct = (step / steps) * 100.0
                if self.on_progress:
                    self.on_progress(sent, file_size, "1.5 МБ/с [SIM]", 0, pct)
            self._log(f"[DRY-RUN] Тестовый прогон завершён успешно (без записи в устройство)!", "DRY-RUN")
            if self.on_complete:
                self.on_complete(client_ip, filename, 1.5, file_size)
            data_sock.close()
            return

        # Если запрошены опции (OACK)
        has_oack = False
        oack_bytes = bytearray(b"\x00\x06")
        if "blksize" in options:
            oack_bytes.extend(b"blksize\x00" + str(blksize).encode("latin1") + b"\x00")
            has_oack = True
        if "tsize" in options:
            oack_bytes.extend(b"tsize\x00" + str(file_size).encode("latin1") + b"\x00")
            has_oack = True

        try:
            current_target = client_addr
            start_block = 1

            if has_oack:
                # Отправляем OACK и ждём ACK с блоком 0
                data_sock.sendto(bytes(oack_bytes), current_target)
                try:
                    ack_pkt, current_target = data_sock.recvfrom(516)
                    ack_op = int.from_bytes(ack_pkt[:2], "big")
                    ack_blk = int.from_bytes(ack_pkt[2:4], "big")
                    if ack_op != 4 or ack_blk != 0:
                        self._log(f"Неверный ACK на OACK: op={ack_op}, blk={ack_blk}", "ОШИБКА")
                        return
                except socket.timeout:
                    self._log(f"Клиент {client_ip} не ответил на OACK", "ОШИБКА")
                    return

            # Читаем файл и передаём блоками
            t_start = time.time()
            total_sent = 0
            retries = 0
            block_num = start_block

            with open(filepath, "rb") as f:
                while not self._stop_event.is_set():
                    chunk = f.read(blksize)
                    pkt = b"\x00\x03" + (block_num & 0xFFFF).to_bytes(2, "big") + chunk

                    # Отправляем блок с поддержкой повторов при потере
                    ack_received = False
                    for attempt in range(5):
                        data_sock.sendto(pkt, current_target)
                        try:
                            ack_data, current_target = data_sock.recvfrom(516)
                            if len(ack_data) >= 4:
                                op = int.from_bytes(ack_data[:2], "big")
                                blk = int.from_bytes(ack_data[2:4], "big")
                                if op == 4 and blk == (block_num & 0xFFFF):
                                    ack_received = True
                                    break
                                elif op == 5:
                                    # Ошибка от клиента
                                    err_m = ack_data[4:].decode("latin1", errors="replace").strip("\x00")
                                    self._log(f"Клиент прислал ошибку: {err_m}", "ОШИБКА")
                                    return
                        except socket.timeout:
                            retries += 1
                            continue

                    if not ack_received:
                        self._log(f"Таймаут передачи блока #{block_num} клиенту {client_ip}", "ОШИБКА")
                        if self.on_error:
                            self.on_error(f"Потеря связи с {client_ip} на блоке {block_num}")
                        return

                    total_sent += len(chunk)
                    elapsed = max(0.001, time.time() - t_start)
                    speed_bps = total_sent / elapsed
                    speed_str = f"{format_bytes(int(speed_bps))}/с"
                    pct = (total_sent / file_size) * 100.0 if file_size > 0 else 100.0

                    if self.on_progress:
                        try:
                            self.on_progress(total_sent, file_size, speed_str, retries, pct)
                        except Exception:
                            pass

                    # Последний блок (меньше размера блока)
                    if len(chunk) < blksize:
                        break

                    block_num = (block_num + 1) & 0xFFFF

            elapsed = max(0.001, time.time() - t_start)
            avg_speed = format_bytes(int(total_sent / elapsed)) + "/с"
            self._log(
                f"Успешно передан файл '{filename}' ({format_bytes(total_sent)}) клиенту {client_ip} за {elapsed:.1f}с ({avg_speed}, повторов: {retries})",
                "УСПЕХ"
            )

            if self.on_complete:
                try:
                    self.on_complete(client_ip, filename, elapsed, total_sent)
                except Exception:
                    pass

        except Exception as e:
            self._log(f"Исключение при передаче: {e}", "ОШИБКА")
            if self.on_error:
                self.on_error(str(e))
        finally:
            data_sock.close()


# ────────────────────── Встроенный Python BOOTP/DHCP Сервер ──────────────────────

class PythonBOOTPServer:
    """
    Автономный BOOTP / DHCP сервер на чистом Python (RFC 951, RFC 2131)
    для роутеров в режиме восстановления (Xiaomi / Redmi / OpenWrt Recovery).
    Слушает запросы на UDP порт 67 и выдаёт IP из pool_start, маску subnet_mask,
    IP TFTP-сервера (siaddr / opt54) и имя загрузочного файла (file / opt67).
    """

    def __init__(
        self,
        bind_ip: str = "0.0.0.0",
        port: int = 67,
        server_ip: str = "192.168.31.100",
        client_ip: str = "192.168.31.2",
        subnet_mask: str = "255.255.255.0",
        gateway: str = "192.168.31.1",
        boot_filename: str = "test.bin",
    ):
        self.bind_ip = bind_ip
        self.port = port
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.subnet_mask = subnet_mask
        self.gateway = gateway
        self.boot_filename = boot_filename

        self._sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.on_log: Optional[Callable[[str, str], None]] = None

    @property
    def is_running(self) -> bool:
        return self._sock is not None and not self._stop_event.is_set()

    def start(self) -> Tuple[bool, str]:
        """Запускает BOOTP/DHCP-сервер на UDP порту 67."""
        if self.is_running:
            return True, "BOOTP/DHCP сервер уже работает"

        self._stop_event.clear()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.bind((self.bind_ip, self.port))
            self._sock.settimeout(1.0)
        except Exception as e:
            self._sock = None
            return False, f"Не удалось занять UDP порт {self.port}: {e}"

        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()
        return True, f"BOOTP/DHCP сервер запущен на {self.bind_ip}:{self.port}"

    def stop(self):
        """Останавливает BOOTP/DHCP-сервер."""
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _log(self, msg: str, level: str = "DHCP"):
        if self.on_log:
            try:
                self.on_log(msg, level)
            except Exception:
                pass

    def _server_loop(self):
        self._log(f"Встроенный BOOTP/DHCP-сервер слушает UDP порт {self.port}", "DHCP")
        while not self._stop_event.is_set():
            try:
                data, client_addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if not self._stop_event.is_set():
                    self._log(f"Ошибка сокета DHCP: {e}", "ОШИБКА")
                break

            if len(data) < 236:
                continue

            threading.Thread(
                target=self._handle_packet,
                args=(data, client_addr),
                daemon=True,
            ).start()

    def _handle_packet(self, data: bytes, client_addr: Tuple[str, int]):
        op = data[0]
        if op != 1:  # 1 = BOOTREQUEST
            return

        chaddr = data[28:44]
        mac_str = ":".join(f"{b:02X}" for b in chaddr[:6])

        # Парсим опции DHCP
        dhcp_msg_type = None
        magic_cookie = data[236:240] if len(data) >= 240 else b""
        if magic_cookie == b"\x63\x82\x53\x63":
            idx = 240
            while idx < len(data):
                opt = data[idx]
                if opt == 255:  # End
                    break
                if opt == 0:  # Pad
                    idx += 1
                    continue
                if idx + 1 >= len(data):
                    break
                opt_len = data[idx + 1]
                val = data[idx + 2 : idx + 2 + opt_len]
                if opt == 53 and opt_len >= 1:
                    dhcp_msg_type = val[0]
                idx += 2 + opt_len

        reply_type = None
        if dhcp_msg_type == 1:  # DHCPDISCOVER
            reply_type = 2  # DHCPOFFER
            self._log(
                f"DHCP DISCOVER от {mac_str} ({client_addr[0]}:{client_addr[1]}) -> "
                f"отправляем OFFER (IP: {self.client_ip}, файл: {self.boot_filename})",
                "DHCP",
            )
        elif dhcp_msg_type == 3:  # DHCPREQUEST
            reply_type = 5  # DHCPACK
            self._log(
                f"DHCP REQUEST от {mac_str} -> отправляем ACK (IP: {self.client_ip}, TFTP: {self.server_ip})",
                "DHCP",
            )
        elif dhcp_msg_type is None:
            # Чистый BOOTP запрос
            self._log(
                f"BOOTP запрос от {mac_str} -> отправляем BOOTP REPLY (IP: {self.client_ip}, файл: {self.boot_filename})",
                "BOOTP",
            )
        else:
            return

        reply_pkt = self._build_reply(data, reply_type)
        if not reply_pkt:
            return

        # Отправляем ответ: привязываемся к self.server_ip, чтобы Windows
        # гарантированно отправила broadcast в нужный сетевой адаптер (кабель роутера)
        send_sock = None
        try:
            send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            send_sock.bind((self.server_ip, 0))
            for target in ("255.255.255.255", self.client_ip):
                try:
                    send_sock.sendto(reply_pkt, (target, 68))
                except Exception:
                    pass
        except OSError:
            # Fallback: отправка через основной слушающий сокет
            if self._sock:
                for target in ("255.255.255.255", self.client_ip):
                    try:
                        self._sock.sendto(reply_pkt, (target, 68))
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            if send_sock:
                try:
                    send_sock.close()
                except Exception:
                    pass

    def _build_reply(self, req: bytes, reply_type: Optional[int]) -> bytes:
        xid = req[4:8]
        flags = req[10:12]
        chaddr = req[28:44]

        try:
            yiaddr = socket.inet_aton(self.client_ip)
        except Exception:
            yiaddr = socket.inet_aton("192.168.31.2")

        try:
            siaddr = socket.inet_aton(self.server_ip)
        except Exception:
            siaddr = socket.inet_aton("192.168.31.100")

        # Имя загрузочного файла в заголовке BOOTP (128 байт, ASCII null-padded)
        boot_fn = (self.boot_filename or "test.bin").encode("latin1", errors="replace")[:127]
        file_bytes = boot_fn.ljust(128, b"\x00")
        sname = b"\x00" * 64

        reply = bytearray()
        reply.append(2)  # op: BOOTREPLY
        reply.append(1)  # htype: Ethernet
        reply.append(6)  # hlen: 6
        reply.append(0)  # hops: 0
        reply.extend(xid)
        reply.extend(b"\x00\x00")  # secs
        reply.extend(flags)
        reply.extend(b"\x00\x00\x00\x00")  # ciaddr
        reply.extend(yiaddr)  # yiaddr (Your IP)
        reply.extend(siaddr)  # siaddr (Next server IP / TFTP Server)
        reply.extend(b"\x00\x00\x00\x00")  # giaddr
        reply.extend(chaddr)  # chaddr
        reply.extend(sname)
        reply.extend(file_bytes)
        reply.extend(b"\x63\x82\x53\x63")  # Magic cookie

        # DHCP Options (RFC 2131)
        if reply_type is not None:
            # Opt 53: DHCP Message Type
            reply.extend(bytes([53, 1, reply_type]))

        # Opt 54: Server Identifier
        reply.extend(bytes([54, 4]))
        reply.extend(siaddr)

        # Opt 1: Subnet Mask
        try:
            mask_bytes = socket.inet_aton(self.subnet_mask)
            reply.extend(bytes([1, 4]))
            reply.extend(mask_bytes)
        except Exception:
            pass

        # Opt 3: Router / Gateway
        if self.gateway:
            try:
                gw_bytes = socket.inet_aton(self.gateway)
                reply.extend(bytes([3, 4]))
                reply.extend(gw_bytes)
            except Exception:
                pass

        # Opt 51: Lease Time (86400 s)
        reply.extend(bytes([51, 4]))
        reply.extend((86400).to_bytes(4, "big"))

        # Opt 67: Bootfile-Name
        reply.extend(bytes([67, len(boot_fn)]))
        reply.extend(boot_fn)

        # Opt 255: End
        reply.append(255)

        # Дополняем до минимум 300 байт по стандарту DHCP
        if len(reply) < 300:
            reply.extend(b"\x00" * (300 - len(reply)))

        return bytes(reply)


# ────────────────────── Чтение и запись config.ini ──────────────────────

def read_config_ini() -> PXEConfig:
    """Читает существующий config.ini TinyPXE."""
    cfg = PXEConfig()
    if not os.path.exists(CONFIG_INI):
        return cfg

    config = configparser.ConfigParser(
        allow_no_value=True,
        comment_prefixes=(";",),
        inline_comment_prefixes=(";",),
    )
    config.optionxform = str
    try:
        config.read(CONFIG_INI, encoding="utf-8")
    except Exception:
        return cfg

    if config.has_section("dhcp"):
        d = dict(config.items("dhcp"))
        cfg.proxy_dhcp = d.get("proxydhcp", "0") == "1"
        cfg.boot_filename = d.get("filename", "test.bin")
        cfg.alt_filename = d.get("altfilename", "")
        cfg.root_dir = d.get("root", "")
        cfg.enable_http = d.get("httpd", "0") == "1"
        cfg.enable_log = d.get("log", "1") == "1"

        if "opt54" in d and d["opt54"]:
            cfg.server_ip = d["opt54"]
        if "opt1" in d and d["opt1"]:
            cfg.subnet_mask = d["opt1"]
        if "opt3" in d and d["opt3"]:
            cfg.gateway = d["opt3"]
        if "opt6" in d and d["opt6"]:
            cfg.dns_server = d["opt6"]
        if "poolstart" in d and d["poolstart"]:
            cfg.pool_start = d["poolstart"]
        if "poolsize" in d and d["poolsize"]:
            try:
                cfg.pool_size = int(d["poolsize"])
            except ValueError:
                pass

    return cfg


def write_clean_config_ini(cfg: PXEConfig, set_online: bool = True, disable_tinypxe_tftp: bool = False):
    """
    Записывает абсолютно чистый config.ini.
    Если disable_tinypxe_tftp=True (активен Python TFTP), выключает встроенный TFTP в TinyPXE (tftpd=0).
    """
    lines = [
        "; ==================================================",
        "; AutoTiny v2.0 — Чистый конфиг TinyPXE Server",
        "; ==================================================",
        "",
        "[arch]",
    ]

    if cfg.uefi_x64_file:
        lines.append(f"00007={cfg.uefi_x64_file}")
        lines.append(f"00009={cfg.uefi_x64_file}")
    else:
        lines.append(";00007=bootx64.efi")

    lines.extend([
        "",
        "[dhcp]",
        "rfc951=1",
        f"start={'1' if set_online else '0'}",
        f"proxydhcp={'1' if cfg.proxy_dhcp else '0'}",
        "bind=1",
        f"tftpd={'0' if disable_tinypxe_tftp else '1'}",
        f"filename={cfg.boot_filename or 'test.bin'}",
    ])

    if cfg.alt_filename:
        lines.append(f"altfilename={cfg.alt_filename}")

    if cfg.root_dir:
        lines.append(f"root={cfg.root_dir}")

    lines.append(f"httpd={'1' if cfg.enable_http else '0'}")
    lines.append("binl=0")
    lines.append("dnsd=0")
    lines.append("smb=0")
    lines.append(f"log={'1' if cfg.enable_log else '0'}")

    if not cfg.proxy_dhcp:
        if cfg.subnet_mask:
            lines.append(f"opt1={cfg.subnet_mask}")
        if cfg.gateway:
            lines.append(f"opt3={cfg.gateway}")
        if cfg.dns_server:
            lines.append(f"opt6={cfg.dns_server}")
        if cfg.pool_start:
            lines.append(f"poolstart={cfg.pool_start}")
        lines.append(f"poolsize={cfg.pool_size}")

    if cfg.server_ip:
        lines.append(f"opt54={cfg.server_ip}")

    lines.append("optextra=175.6.1.1.1.8.1.1")

    lines.extend([
        "",
        "[web]",
        f"port={cfg.http_port}",
        "",
        "[frmDHCPServer]",
        "top=100",
        "left=100",
        "",
    ])

    content = "\r\n".join(lines)
    os.makedirs(os.path.dirname(CONFIG_INI), exist_ok=True)
    with open(CONFIG_INI, "w", encoding="utf-8") as f:
        f.write(content)


# ────────────────────── Управление файлами прошивки ──────────────────────

def get_available_boot_files(root_dir: str = "") -> List[Tuple[str, str]]:
    """Возвращает список доступных файлов в папке TinyPXE: [(имя_файла, размер_строкой)]."""
    search_dir = root_dir if root_dir else TINYPXE_DIR
    if not os.path.isabs(search_dir):
        search_dir = os.path.join(TINYPXE_DIR, search_dir)

    if not os.path.exists(search_dir):
        return []

    valid_exts = {".bin", ".img", ".pxe", ".kpxe", ".efi", ".0", ".iso", ".wim"}
    result = []
    try:
        for f in os.listdir(search_dir):
            full = os.path.join(search_dir, f)
            if os.path.isfile(full):
                _, ext = os.path.splitext(f.lower())
                if ext in valid_exts or f.lower() in ("test.bin", "recovery.bin", "miwifi.bin", "ipxe.pxe"):
                    size_b = os.path.getsize(full)
                    size_str = format_bytes(size_b)
                    result.append((f, size_str))
    except Exception:
        pass

    def sort_key(item):
        name = item[0].lower()
        if name == "test.bin":
            return (0, name)
        if name == "recovery.bin":
            return (1, name)
        if name.endswith(".bin"):
            return (2, name)
        return (3, name)

    result.sort(key=sort_key)
    return result


def check_file_status(filename: str, root_dir: str = "") -> Tuple[bool, str, int]:
    """Проверяет существование файла. Возвращает: (найден_ли, описание, размер_в_байтах)."""
    if not filename:
        return False, "Имя файла не указано", 0

    base = root_dir if root_dir else TINYPXE_DIR
    if not os.path.isabs(base):
        base = os.path.join(TINYPXE_DIR, base)

    full_path = os.path.join(base, filename)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        size = os.path.getsize(full_path)
        return True, f"Найден ({format_bytes(size)})", size
    return False, "Файл не найден в папке", 0


def copy_firmware_to_pxe(source_file: str, target_name: str = "") -> Tuple[bool, str]:
    """Копирует выбранный файл прошивки в папку TinyPXE."""
    if not os.path.exists(source_file):
        return False, f"Исходный файл не существует: {source_file}"

    base = os.path.basename(source_file)
    final_target = target_name if target_name else base
    target_path = os.path.join(TINYPXE_DIR, final_target)

    try:
        shutil.copy2(source_file, target_path)
        size = os.path.getsize(target_path)
        return True, f"Файл скопирован как {final_target} ({format_bytes(size)})"
    except Exception as e:
        return False, f"Ошибка копирования: {e}"


# ────────────────────── Управление процессом TinyPXE ──────────────────────

class PXEProcessManager:
    """Управляет жизненным циклом pxesrv.exe."""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    @property
    def is_running(self) -> bool:
        if self._proc is None:
            return self._is_pxesrv_running_globally()
        return self._proc.poll() is None

    def _is_pxesrv_running_globally(self) -> bool:
        try:
            r = subprocess.run(
                'tasklist /FI "IMAGENAME eq pxesrv.exe" /NH',
                capture_output=True, text=True, shell=True, errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            return "pxesrv.exe" in r.stdout.lower()
        except Exception:
            return False

    def start(self, cfg: PXEConfig, disable_tftp: bool = False) -> Tuple[bool, str]:
        """Запускает pxesrv.exe."""
        if self.is_running:
            return True, "DHCP сервер уже запущен"

        if not os.path.exists(PXESRV_EXE):
            return False, f"Не найден исполняемый файл TinyPXE:\n{PXESRV_EXE}"

        try:
            write_clean_config_ini(cfg, set_online=True, disable_tinypxe_tftp=disable_tftp)
        except Exception as e:
            return False, f"Не удалось обновить config.ini: {e}"

        startupinfo = None
        if not cfg.show_raw_window and os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        try:
            self._proc = subprocess.Popen(
                [PXESRV_EXE],
                cwd=TINYPXE_DIR,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            time.sleep(0.5)
            if self._proc.poll() is not None:
                return False, f"Процесс pxesrv.exe сразу завершился (код: {self._proc.poll()})"
            return True, "TinyPXE запущен"
        except Exception as e:
            return False, f"Ошибка запуска: {e}"

    def stop(self) -> Tuple[bool, str]:
        """Останавливает сервер."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)
                self._proc = None
            except Exception:
                pass

        try:
            subprocess.run(
                "taskkill /F /IM pxesrv.exe /T",
                capture_output=True, shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        except Exception:
            pass

        return True, "TinyPXE остановлен"


# ────────────────────── UART Терминал ──────────────────────

class UARTManager:
    """Управление COM-портом для роутеров (pyserial)."""

    def __init__(self):
        self._serial = None
        self._thread = None
        self._stop_event = threading.Event()
        self.on_data: Optional[Callable[[str], None]] = None

    @staticmethod
    def get_available_ports() -> List[str]:
        """Возвращает список доступных COM-портов."""
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()]
            return ports or ["(COM-порты не найдены)"]
        except ImportError:
            return ["(установите pyserial)"]
        except Exception:
            return ["(ошибка чтения портов)"]

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, port: str, baudrate: int = 115200) -> Tuple[bool, str]:
        """Подключается к COM-порту."""
        try:
            import serial
        except ImportError:
            return False, "Библиотека pyserial не установлена"

        if self.is_connected:
            self.disconnect()

        try:
            self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=0.2)
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            return True, f"Подключено к {port} ({baudrate} бод)"
        except Exception as e:
            self._serial = None
            return False, f"Ошибка открытия {port}: {e}"

    def disconnect(self):
        """Отключается от COM-порта."""
        self._stop_event.set()
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def send(self, data: str or bytes):
        """Отправляет данные в COM-порт."""
        if not self.is_connected:
            return
        try:
            if isinstance(data, str):
                data = data.encode("utf-8", errors="replace")
            self._serial.write(data)
        except Exception:
            pass

    def interrupt_uboot(self):
        """Отправляет последовательность прерывания загрузки U-Boot."""
        if not self.is_connected:
            return
        # U-Boot прерывается нажатием Ctrl+C, пробела, Enter или '4'
        for _ in range(15):
            self.send(b"\x03")  # Ctrl+C
            self.send(b" ")
            self.send(b"\r\n")
            self.send(b"tpl\r\n")
            time.sleep(0.08)

    def _read_loop(self):
        while not self._stop_event.is_set() and self._serial and self._serial.is_open:
            try:
                chunk = self._serial.read(self._serial.in_waiting or 1)
                if chunk and self.on_data:
                    decoded = chunk.decode("utf-8", errors="replace")
                    self.on_data(decoded)
            except Exception:
                break


# ────────────────────── Тестирование TFTP ──────────────────────

def test_tftp_service(server_ip: str, filename: str, timeout_sec: float = 1.5) -> Tuple[bool, str]:
    """Отправляет быстрый TFTP RRQ на UDP порт 69."""
    if not server_ip:
        return False, "IP-адрес сервера не указан"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)

    fn_bytes = (filename or "test.bin").encode("latin1", errors="replace")
    rrq_packet = b"\x00\x01" + fn_bytes + b"\x00octet\x00"

    t0 = time.time()
    try:
        sock.sendto(rrq_packet, (server_ip, 69))
        resp, sender = sock.recvfrom(516)
        elapsed_ms = (time.time() - t0) * 1000

        opcode = int.from_bytes(resp[:2], "big")
        if opcode == 3:  # DATA
            block_num = int.from_bytes(resp[2:4], "big")
            data_len = len(resp) - 4
            return True, f"TFTP отвечает! Блок #{block_num} ({data_len} байт) за {elapsed_ms:.1f} мс"
        elif opcode == 5:  # ERROR
            err_code = int.from_bytes(resp[2:4], "big")
            err_msg = resp[4:].decode("latin1", errors="replace").strip("\x00")
            return True, f"TFTP активен, ответ сервера: [{err_code}] {err_msg} ({elapsed_ms:.1f} мс)"
        elif opcode == 6:  # OACK
            return True, f"TFTP ответил OACK за {elapsed_ms:.1f} мс"
        else:
            return True, f"TFTP ответил кодом {opcode} за {elapsed_ms:.1f} мс"
    except socket.timeout:
        return False, f"Таймаут ({timeout_sec}с): TFTP-сервер не ответил на {server_ip}:69"
    except Exception as e:
        return False, f"Ошибка проверки TFTP: {e}"
    finally:
        sock.close()
