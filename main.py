"""
AutoTiny v2.0 — Современный инструмент для сетевой прошивки роутеров и PXE-загрузки.
Включает полный функционал Autosettings для управления сетевыми настройками TCP/IPv4,
собственный TFTP-сервер с живым прогрессом, строгое разделение движков запуска,
прямое копирование файлов без принудительного переименования,
кнопки обновления файлов и конфигов, UART-терминал для перехвата U-Boot
и безопасное восстановление сети из первоначального бэкапа в любой момент.

Авторы: Sddzxcua, Claude AI
"""

import sys
import os
import ctypes
import time
import threading
import re
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image
import customtkinter as ctk

from network_utils import (
    NetworkSettings,
    DNS_PRESETS,
    get_adapter_names,
    get_current_settings,
    apply_network_settings,
    save_backup,
    load_backup,
    has_backup,
    save_auto_backup,
    rollback_network_on_exit,
    check_crash_recovery,
    clear_crash_flag,
    run_all_preflight_checks,
    add_firewall_rule,
    remove_firewall_rule,
    scan_for_router,
    verify_firmware,
    generate_bug_report,
    is_admin,
    restart_as_admin,
    ping_host,
)
from pxe_service import (
    PXEConfig,
    PXEProcessManager,
    PythonTFTPServer,
    PythonBOOTPServer,
    UARTManager,
    read_config_ini,
    write_clean_config_ini,
    get_available_boot_files,
    check_file_status,
    copy_firmware_to_pxe,
    test_tftp_service,
    format_bytes,
    TINYPXE_DIR,
    PXESRV_EXE,
    CONFIG_INI,
)
import config_manager


def resource_path(relative_path: str) -> str:
    """Получает абсолютный путь к ресурсу."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


# ──────────────────────────── Цветовая палитра ────────────────────────────

COLORS = {
    "bg_dark":       "#0f0f14",
    "bg_card":       "#16161f",
    "bg_card_inner": "#1c1c28",
    "bg_input":      "#12121c",
    "accent":        "#6c5ce7",
    "accent_hover":  "#7c6ef7",
    "accent_light":  "#a29bfe",
    "accent_glow":   "#2a2545",
    "success":       "#00b894",
    "success_hover": "#00d6a0",
    "warning":       "#fdcb6e",
    "danger":        "#e17055",
    "danger_hover":  "#f07060",
    "text":          "#e0e0e8",
    "text_dim":      "#7f8c9b",
    "text_muted":    "#3a3f4f",
    "border_subtle": "#1e1e2e",
    "border_focus":  "#6c5ce7",
    "titlebar":      "#0a0a10",
    "switch_on":     "#6c5ce7",
    "switch_off":    "#2a2a3f",
    "online":        "#00b894",
    "offline":       "#e17055",
    "console_bg":    "#0a0a10",
    "console_fg":    "#a29bfe",
}

APP_VERSION = "2.0.0"
APP_AUTHORS = "Sddzxcua, Claude AI"


# ──────────────────── DWM тёмная рамка Windows ────────────────────

def _apply_dark_title_bar(hwnd: int):
    """Включает тёмный режим окна через Windows DWM API."""
    try:
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        border = ctypes.c_uint32(0x00100f0f)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        caption = ctypes.c_uint32(0x0014130f)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        text_color = ctypes.c_uint32(0x00e8e0e0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
    except Exception:
        pass


# ──────────────────────────── Виджеты ────────────────────────────

class Card(ctk.CTkFrame):
    """Карточка-секция с мягкой подсветкой."""

    def __init__(self, master, title: str = "", **kwargs):
        super().__init__(
            master,
            fg_color=COLORS["bg_card"],
            corner_radius=14,
            border_width=0,
            **kwargs,
        )

        if title:
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x", padx=18, pady=(14, 0))

            lbl = ctk.CTkLabel(
                header,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=COLORS["accent_light"],
                anchor="w",
            )
            lbl.pack(side="left")

            sep = ctk.CTkFrame(self, height=1, fg_color=COLORS["accent_glow"], corner_radius=1)
            sep.pack(fill="x", padx=18, pady=(8, 4))


class LabeledEntry(ctk.CTkFrame):
    """Подписанное поле ввода с безопасным обновлением значения при disabled."""

    def __init__(self, master, label: str, placeholder: str = "", hint: str = "", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        lbl_row = ctk.CTkFrame(self, fg_color="transparent")
        lbl_row.pack(fill="x", padx=4, pady=(0, 3))

        self.label = ctk.CTkLabel(
            lbl_row,
            text=label,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.label.pack(side="left")

        if hint:
            self.hint_lbl = ctk.CTkLabel(
                lbl_row,
                text=hint,
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_muted"],
                anchor="e",
            )
            self.hint_lbl.pack(side="right")

        self.entry = ctk.CTkEntry(
            self,
            placeholder_text=placeholder,
            font=ctk.CTkFont(family="Consolas", size=13),
            height=38,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            text_color=COLORS["text"],
            placeholder_text_color=COLORS["text_muted"],
        )
        self.entry.pack(fill="x", padx=4)

        self.entry.bind("<FocusIn>", lambda e: self.entry.configure(border_color=COLORS["border_focus"]))
        self.entry.bind("<FocusOut>", lambda e: self.entry.configure(border_color=COLORS["border_subtle"]))

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, value: str):
        prev_state = self.entry.cget("state")
        if prev_state == "disabled":
            self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.entry.insert(0, str(value if value is not None else ""))
        if prev_state == "disabled":
            self.entry.configure(state="disabled")

    def set_state(self, state: str):
        self.entry.configure(state=state)
        if state == "disabled":
            self.label.configure(text_color=COLORS["text_muted"])
        else:
            self.label.configure(text_color=COLORS["text_dim"])


class StatusBar(ctk.CTkFrame):
    """Полоса статуса внизу окна."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["titlebar"], height=32, corner_radius=0, **kwargs)
        self.pack_propagate(False)

        self.dot = ctk.CTkLabel(
            self, text="●", font=ctk.CTkFont(size=9),
            text_color=COLORS["text_muted"], width=16,
        )
        self.dot.pack(side="left", padx=(12, 0))

        self.label = ctk.CTkLabel(
            self,
            text="Готово",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.label.pack(side="left", padx=4)

        self.authors = ctk.CTkLabel(
            self,
            text=f"v{APP_VERSION}  •  {APP_AUTHORS}",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        self.authors.pack(side="right", padx=(0, 14))

        self.admin_badge = ctk.CTkLabel(
            self,
            text="👑 Админ" if is_admin() else "⚠ Нет прав",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["success"] if is_admin() else COLORS["warning"],
        )
        self.admin_badge.pack(side="right", padx=(0, 10))

    def set_status(self, text: str, color: str = None):
        self.label.configure(text=text)
        c = color or COLORS["text_dim"]
        self.label.configure(text_color=c)
        self.dot.configure(text_color=c)


# ──────────────────────────── Главное окно ────────────────────────────

class AutoTinyApp(ctk.CTk):
    """Главное окно AutoTiny v2.0."""

    WIDTH = 840
    HEIGHT = 860

    def __init__(self):
        super().__init__()

        # Только чистое имя AutoTiny в заголовке
        self.title("AutoTiny")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(740, 720)
        self.configure(fg_color=COLORS["bg_dark"])

        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        ctk.set_appearance_mode("dark")
        self.after(50, self._apply_window_style)

        # Сервисы
        config_manager.ensure_configs_dir()
        self.pxe_process = PXEProcessManager()
        self.tftp_server = PythonTFTPServer(root_dir=TINYPXE_DIR)
        self.bootp_server = PythonBOOTPServer()
        self.uart = UARTManager()

        # Привязка событий TFTP
        self.tftp_server.on_request = self._tftp_on_request
        self.tftp_server.on_progress = self._tftp_on_progress
        self.tftp_server.on_complete = self._tftp_on_complete
        self.tftp_server.on_error = self._tftp_on_error
        self.tftp_server.on_log = self.log_msg

        # Привязка событий BOOTP/DHCP
        self.bootp_server.on_log = self.log_msg

        # Привязка событий UART
        self.uart.on_data = self._uart_on_data

        self.auto_rollback_on_exit = tk.BooleanVar(value=True)
        self.log_history: list = []

        self._build_ui()
        if not is_admin():
            self.status_bar.admin_badge.configure(cursor="hand2")
            self.status_bar.admin_badge.bind("<Button-1>", lambda e: self._on_admin_badge_click())

        self._refresh_config_list()
        self._load_current_adapter()
        self._check_boot_file_status()

        # Создаём первоначальный бэкап при запуске, если его ещё нет
        self.after(200, self._ensure_initial_backup)

        # Проверка аварийного завершения
        self.after(600, self._check_crash_recovery_on_startup)

        # Закрытие
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_window_style(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            _apply_dark_title_bar(hwnd)
        except Exception:
            pass

    def _ensure_initial_backup(self):
        """Гарантирует сохранение первоначального бэкапа настроек при первом открытии."""
        if not has_backup():
            ad = self.adapter_combo.get()
            if ad:
                st = get_current_settings(ad)
                save_backup(st)
                self._update_backup_label()

    def _update_backup_label(self):
        """Обновляет индикатор наличия бэкапа."""
        b = load_backup()
        if b:
            mode = "DHCP (авто)" if b.dhcp_enabled else f"IP: {b.ip_address}"
            self.lbl_backup_status.configure(
                text=f"Сохранён бэкап «{b.adapter_name}» ({mode})",
                text_color=COLORS["success"],
            )
        else:
            self.lbl_backup_status.configure(
                text="Бэкап ещё не создан",
                text_color=COLORS["text_muted"],
            )

    # ────────────────────── Построение интерфейса ──────────────────────

    def _build_ui(self):
        """Строит интерфейс приложения."""

        # ── Шапка: Логотип + Чистое имя "AutoTiny" + Индикатор ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(14, 6))

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill="x")

        png_path = resource_path("icon.png")
        if os.path.exists(png_path):
            try:
                self.logo_img = ctk.CTkImage(Image.open(png_path), size=(32, 32))
                logo_lbl = ctk.CTkLabel(title_row, image=self.logo_img, text="")
                logo_lbl.pack(side="left", padx=(0, 10))
            except Exception:
                pass

        title = ctk.CTkLabel(
            title_row,
            text="AutoTiny",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text"],
        )
        title.pack(side="left")

        # Индикатор Онлайн/Офлайн
        self.status_badge = ctk.CTkLabel(
            title_row,
            text="● ОФЛАЙН",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["offline"],
        )
        self.status_badge.pack(side="right", padx=(0, 4))

        # ── Панель пользовательских конфигов (в стиле читов) ──
        cfg_panel = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        cfg_panel.pack(fill="x", padx=20, pady=(0, 8))

        cfg_inner = ctk.CTkFrame(cfg_panel, fg_color="transparent")
        cfg_inner.pack(fill="x", padx=14, pady=8)

        ctk.CTkLabel(
            cfg_inner,
            text="Конфиг:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent_light"],
        ).pack(side="left", padx=(0, 8))

        self.config_combo = ctk.CTkComboBox(
            cfg_inner,
            values=["(нет сохранённых конфигов)"],
            font=ctk.CTkFont(size=12),
            height=34,
            width=210,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._on_config_selected,
        )
        self.config_combo.pack(side="left", padx=(0, 6))

        # Кнопка обновления списка конфигов
        btn_cfg_refresh = ctk.CTkButton(
            cfg_inner,
            text="⟳",
            width=34,
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_card_inner"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=14),
            command=self._refresh_config_list,
        )
        btn_cfg_refresh.pack(side="left", padx=(0, 8))

        btn_cfg_load = ctk.CTkButton(
            cfg_inner,
            text="📥  Загрузить",
            width=84,
            height=34,
            corner_radius=8,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._load_selected_config,
        )
        btn_cfg_load.pack(side="left", padx=(0, 6))

        btn_cfg_save = ctk.CTkButton(
            cfg_inner,
            text="💾  Сохранить",
            width=84,
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_card_inner"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12),
            command=self._save_current_config,
        )
        btn_cfg_save.pack(side="left", padx=(0, 6))

        btn_cfg_new = ctk.CTkButton(
            cfg_inner,
            text="➕  Создать",
            width=80,
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_card_inner"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12),
            command=self._create_new_config,
        )
        btn_cfg_new.pack(side="left", padx=(0, 6))

        btn_cfg_del = ctk.CTkButton(
            cfg_inner,
            text="🗑",
            width=34,
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_card_inner"],
            hover_color=COLORS["danger_hover"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["danger"],
            font=ctk.CTkFont(size=13),
            command=self._delete_current_config,
        )
        btn_cfg_del.pack(side="left", padx=(0, 6))

        btn_cfg_folder = ctk.CTkButton(
            cfg_inner,
            text="📂",
            width=34,
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_card_inner"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=14),
            command=config_manager.open_configs_folder,
        )
        btn_cfg_folder.pack(side="right")

        # ── Вкладки (Tabs) ──
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="transparent",
            segmented_button_fg_color=COLORS["bg_card"],
            segmented_button_selected_color=COLORS["accent"],
            segmented_button_selected_hover_color=COLORS["accent_hover"],
            segmented_button_unselected_color=COLORS["bg_card"],
            segmented_button_unselected_hover_color=COLORS["accent_glow"],
            text_color=COLORS["text"],
        )
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(0, 4))

        self.tab_pxe = self.tabview.add("⚡  PXE / TFTP Сервер")
        self.tab_net = self.tabview.add("🌐  Сеть (Autosettings)")
        self.tab_wizard = self.tabview.add("🧙‍♂️  Мастер & UART")
        self.tab_help = self.tabview.add("📖  Инструкция и отчёт")

        self._build_tab_pxe()
        self._build_tab_net()
        self._build_tab_wizard()
        self._build_tab_help()

        # ── Статус-бар ──
        self.status_bar = StatusBar(self)
        self.status_bar.pack(fill="x", side="bottom")

    # ────────────────────── Вкладка 1: PXE / TFTP Сервер ──────────────────────

    def _build_tab_pxe(self):
        """Строит вкладку PXE/TFTP сервера."""
        scroll = ctk.CTkScrollableFrame(
            self.tab_pxe,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border_subtle"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Карточка: Загрузочный файл ──
        card_file = Card(scroll, title="📁  Файл прошивки / сетевой загрузки")
        card_file.pack(fill="x", pady=(0, 8))

        file_row = ctk.CTkFrame(card_file, fg_color="transparent")
        file_row.pack(fill="x", padx=16, pady=(4, 6))

        self.file_combo = ctk.CTkComboBox(
            file_row,
            values=["test.bin"],
            font=ctk.CTkFont(family="Consolas", size=13),
            dropdown_font=ctk.CTkFont(family="Consolas", size=12),
            height=38,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=lambda v: self._check_boot_file_status(),
        )
        self.file_combo.pack(side="left", fill="x", expand=True, padx=(0, 6))

        # Кнопка обновления списка файлов
        btn_file_refresh = ctk.CTkButton(
            file_row,
            text="⟳",
            width=38,
            height=38,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=18),
            command=lambda: self._refresh_boot_files_dropdown(),
        )
        btn_file_refresh.pack(side="left", padx=(0, 6))

        btn_browse_file = ctk.CTkButton(
            file_row,
            text="📂  Выбрать файл...",
            height=38,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._browse_and_copy_file,
        )
        btn_browse_file.pack(side="right")

        file_status_box = ctk.CTkFrame(card_file, fg_color="transparent")
        file_status_box.pack(fill="x", padx=18, pady=(0, 10))

        self.file_status_lbl = ctk.CTkLabel(
            file_status_box,
            text="Проверка файла...",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            anchor="w",
        )
        self.file_status_lbl.pack(side="left")

        btn_verify = ctk.CTkButton(
            file_status_box,
            text="🔍  Проверить SHA256",
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=11),
            command=self._verify_current_file,
        )
        btn_verify.pack(side="right", padx=(6, 0))

        btn_open_folder = ctk.CTkButton(
            file_status_box,
            text="📁  Папка TinyPXE",
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=11),
            command=lambda: os.startfile(TINYPXE_DIR) if os.path.exists(TINYPXE_DIR) else None,
        )
        btn_open_folder.pack(side="right")

        # ── Карточка: Выбор движка и параметры ──
        card_srv = Card(scroll, title="⚙️  Параметры и движок сервера")
        card_srv.pack(fill="x", pady=(0, 8))

        engine_row = ctk.CTkFrame(card_srv, fg_color="transparent")
        engine_row.pack(fill="x", padx=18, pady=(4, 6))

        ctk.CTkLabel(
            engine_row,
            text="Активный сервер:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"],
        ).pack(side="left", padx=(0, 10))

        self.tftp_engine_var = ctk.StringVar(value="builtin")
        self.combo_engine = ctk.CTkComboBox(
            engine_row,
            values=["Python TFTP (с живым прогрессом)", "TinyPXE Server (DHCP + PXE)"],
            width=240,
            height=32,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            font=ctk.CTkFont(size=11),
            command=self._on_engine_changed,
        )
        self.combo_engine.pack(side="left", padx=(0, 14))

        # Тестовый прогон (Dry-run)
        self.dry_run_var = ctk.BooleanVar(value=False)
        self.sw_dry_run = ctk.CTkSwitch(
            engine_row,
            text="Тестовый прогон (Dry-run)",
            variable=self.dry_run_var,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["warning"],
            progress_color=COLORS["warning"],
            fg_color=COLORS["switch_off"],
            button_color=COLORS["text"],
            command=self._on_dry_run_toggled,
        )
        self.sw_dry_run.pack(side="right")

        # ProxyDHCP
        proxy_row = ctk.CTkFrame(card_srv, fg_color="transparent")
        proxy_row.pack(fill="x", padx=18, pady=(2, 6))

        ctk.CTkLabel(
            proxy_row,
            text="Режим ProxyDHCP (если IP раздаёт роутер):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"],
        ).pack(side="left")

        self.proxy_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            proxy_row,
            text="",
            variable=self.proxy_var,
            width=46,
            height=24,
            switch_width=42,
            switch_height=22,
            progress_color=COLORS["switch_on"],
            fg_color=COLORS["switch_off"],
            button_color=COLORS["text"],
            command=self._on_proxy_toggled,
        ).pack(side="right")

        # Сетка параметров IP
        self.pxe_fields = ctk.CTkFrame(card_srv, fg_color="transparent")
        self.pxe_fields.pack(fill="x", padx=14, pady=(0, 6))
        self.pxe_fields.columnconfigure((0, 1), weight=1)

        self.entry_pxe_server_ip = LabeledEntry(
            self.pxe_fields, "IP-адрес сервера (ПК)", placeholder="192.168.1.2", hint="opt54"
        )
        self.entry_pxe_server_ip.grid(row=0, column=0, sticky="ew", padx=4, pady=4)

        self.entry_pxe_mask = LabeledEntry(
            self.pxe_fields, "Маска подсети", placeholder="255.255.255.0", hint="opt1"
        )
        self.entry_pxe_mask.grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        self.entry_pxe_gateway = LabeledEntry(
            self.pxe_fields, "Основной шлюз", placeholder="192.168.1.1", hint="opt3"
        )
        self.entry_pxe_gateway.grid(row=1, column=0, sticky="ew", padx=4, pady=4)

        self.entry_pxe_pool_start = LabeledEntry(
            self.pxe_fields, "Начало пула IP", placeholder="192.168.1.3", hint="poolstart"
        )
        self.entry_pxe_pool_start.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        # Опция HTTP
        srv_opts = ctk.CTkFrame(card_srv, fg_color="transparent")
        srv_opts.pack(fill="x", padx=18, pady=(4, 10))

        ctk.CTkLabel(
            srv_opts, text="Включить HTTP-сервер (порт 80):",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"]
        ).pack(side="left", padx=(0, 6))

        self.http_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            srv_opts, text="", variable=self.http_var,
            width=40, height=20, switch_width=38, switch_height=20,
            progress_color=COLORS["switch_on"], fg_color=COLORS["switch_off"],
            button_color=COLORS["text"],
        ).pack(side="left")

        # ── Кнопки запуска ──
        ctrl_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        ctrl_grid.pack(fill="x", pady=(2, 6))
        ctrl_grid.columnconfigure((0, 1), weight=1)

        self.btn_start = ctk.CTkButton(
            ctrl_grid,
            text="▶   Запустить сервер",
            height=46,
            corner_radius=12,
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_server_action,
        )
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=4, pady=2)

        self.btn_stop = ctk.CTkButton(
            ctrl_grid,
            text="⏹   Остановить сервер",
            height=46,
            corner_radius=12,
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._stop_server_action,
        )
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        # ── Карточка: Живой прогресс передачи TFTP ──
        card_progress = Card(scroll, title="📊  Передача файла (TFTP)")
        card_progress.pack(fill="x", pady=(0, 8))

        prog_box = ctk.CTkFrame(card_progress, fg_color="transparent")
        prog_box.pack(fill="x", padx=18, pady=(4, 12))

        self.progress_bar = ctk.CTkProgressBar(
            prog_box,
            height=14,
            corner_radius=7,
            progress_color=COLORS["accent"],
            fg_color=COLORS["bg_input"],
        )
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.set(0)

        stat_row = ctk.CTkFrame(prog_box, fg_color="transparent")
        stat_row.pack(fill="x")

        self.lbl_tftp_status = ctk.CTkLabel(
            stat_row,
            text="Ожидание запроса от роутера...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.lbl_tftp_status.pack(side="left")

        self.lbl_tftp_metrics = ctk.CTkLabel(
            stat_row,
            text="0 КБ / 0 КБ  •  0 КБ/с",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLORS["accent_light"],
            anchor="e",
        )
        self.lbl_tftp_metrics.pack(side="right")

        # ── Журнал событий и диагностика ──
        card_log = Card(scroll, title="📟  Журнал событий и диагностика")
        card_log.pack(fill="x", pady=(0, 8))

        diag_row = ctk.CTkFrame(card_log, fg_color="transparent")
        diag_row.pack(fill="x", padx=16, pady=(0, 6))

        btn_preflight = ctk.CTkButton(
            diag_row,
            text="🛡  Предполётная проверка",
            height=30,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["accent_light"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._run_preflight_dialog,
        )
        btn_preflight.pack(side="left", padx=(0, 6))

        btn_test_tftp = ctk.CTkButton(
            diag_row,
            text="🔍  Тест TFTP",
            height=30,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            command=self._test_tftp,
        )
        btn_test_tftp.pack(side="left", padx=(0, 6))

        btn_ping = ctk.CTkButton(
            diag_row,
            text="📡  Пинг шлюза",
            height=30,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            command=self._ping_gateway,
        )
        btn_ping.pack(side="left", padx=(0, 6))

        btn_clear = ctk.CTkButton(
            diag_row,
            text="🧹  Очистить",
            height=30,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        )
        btn_clear.pack(side="right")

        self.log_text = ctk.CTkTextbox(
            card_log,
            height=120,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["console_bg"],
            text_color=COLORS["console_fg"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border_subtle"],
        )
        self.log_text.pack(fill="x", padx=16, pady=(0, 14))

        self.log_msg("AutoTiny v2.0 инициализирован.")

    # ────────────────────── Вкладка 2: Сеть (Autosettings) ──────────────────────

    def _build_tab_net(self):
        """Строит вкладку Autosettings."""
        scroll = ctk.CTkScrollableFrame(
            self.tab_net,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border_subtle"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Сетевой адаптер ──
        card_adapter = Card(scroll, title="🔌  Сетевой адаптер")
        card_adapter.pack(fill="x", pady=(0, 8))

        ad_row = ctk.CTkFrame(card_adapter, fg_color="transparent")
        ad_row.pack(fill="x", padx=16, pady=(4, 10))

        self.adapter_combo = ctk.CTkComboBox(
            ad_row,
            values=["Ethernet"],
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=12),
            height=38,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["accent"],
            text_color=COLORS["text"],
            command=self._on_adapter_changed,
        )
        self.adapter_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_refresh = ctk.CTkButton(
            ad_row,
            text="⟳",
            width=40,
            height=38,
            corner_radius=10,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_glow"],
            border_width=0,
            text_color=COLORS["text_dim"],
            font=ctk.CTkFont(size=18),
            command=self._refresh_adapter_list,
        )
        btn_refresh.pack(side="left", padx=(0, 6))

        btn_find_router = ctk.CTkButton(
            ad_row,
            text="🔍  Найти роутер",
            height=38,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._scan_routers_action,
        )
        btn_find_router.pack(side="right")

        # ── IP-конфигурация ──
        card_ip = Card(scroll, title="🌐  IP-конфигурация (IPv4)")
        card_ip.pack(fill="x", pady=(0, 8))

        dhcp_row = ctk.CTkFrame(card_ip, fg_color="transparent")
        dhcp_row.pack(fill="x", padx=18, pady=(4, 8))

        ctk.CTkLabel(
            dhcp_row,
            text="Получать IP автоматически (DHCP)",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text"],
        ).pack(side="left")

        self.dhcp_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            dhcp_row,
            text="",
            variable=self.dhcp_var,
            width=46,
            height=24,
            switch_width=42,
            switch_height=22,
            progress_color=COLORS["switch_on"],
            fg_color=COLORS["switch_off"],
            button_color=COLORS["text"],
            command=self._toggle_dhcp_fields,
        ).pack(side="right")

        ip_grid = ctk.CTkFrame(card_ip, fg_color="transparent")
        ip_grid.pack(fill="x", padx=14, pady=(0, 12))
        ip_grid.columnconfigure((0, 1), weight=1)

        self.entry_net_ip = LabeledEntry(ip_grid, "IP-адрес", "192.168.1.2")
        self.entry_net_ip.grid(row=0, column=0, sticky="ew", padx=4, pady=4)

        self.entry_net_mask = LabeledEntry(ip_grid, "Маска подсети", "255.255.255.0")
        self.entry_net_mask.grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        self.entry_net_gw = LabeledEntry(ip_grid, "Основной шлюз", "192.168.1.1")
        self.entry_net_gw.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=4)

        # ── DNS-серверы ──
        card_dns = Card(scroll, title="📡  DNS-серверы")
        card_dns.pack(fill="x", pady=(0, 8))

        dns_dhcp_row = ctk.CTkFrame(card_dns, fg_color="transparent")
        dns_dhcp_row.pack(fill="x", padx=18, pady=(4, 8))

        ctk.CTkLabel(
            dns_dhcp_row,
            text="Получать DNS автоматически",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text"],
        ).pack(side="left")

        self.dhcp_dns_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            dns_dhcp_row,
            text="",
            variable=self.dhcp_dns_var,
            width=46,
            height=24,
            switch_width=42,
            switch_height=22,
            progress_color=COLORS["switch_on"],
            fg_color=COLORS["switch_off"],
            button_color=COLORS["text"],
            command=self._toggle_dns_fields,
        ).pack(side="right")

        dns_grid = ctk.CTkFrame(card_dns, fg_color="transparent")
        dns_grid.pack(fill="x", padx=14, pady=(0, 6))
        dns_grid.columnconfigure((0, 1), weight=1)

        self.entry_net_dns1 = LabeledEntry(dns_grid, "Первичный DNS", "8.8.8.8")
        self.entry_net_dns1.grid(row=0, column=0, sticky="ew", padx=4, pady=4)

        self.entry_net_dns2 = LabeledEntry(dns_grid, "Вторичный DNS", "8.8.4.4")
        self.entry_net_dns2.grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        chips_row = ctk.CTkFrame(card_dns, fg_color="transparent")
        chips_row.pack(fill="x", padx=18, pady=(2, 12))

        ctk.CTkLabel(
            chips_row, text="Быстрый DNS:",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"]
        ).pack(side="left", padx=(0, 6))

        for name, data in DNS_PRESETS.items():
            btn = ctk.CTkButton(
                chips_row,
                text=name,
                width=0,
                height=26,
                corner_radius=13,
                fg_color="transparent",
                hover_color=COLORS["accent_glow"],
                border_width=1,
                border_color=COLORS["border_subtle"],
                text_color=COLORS["text_dim"],
                font=ctk.CTkFont(size=10),
                command=lambda d=data: self._apply_dns_preset(d),
            )
            btn.pack(side="left", padx=2)

        # ── Кнопки сетевых действий и Бэкап ──
        act_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        act_grid.pack(fill="x", pady=(2, 6))
        act_grid.columnconfigure((0, 1), weight=1)

        btn_apply_net = ctk.CTkButton(
            act_grid,
            text="🚀   Применить к адаптеру",
            height=46,
            corner_radius=12,
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._apply_network_to_windows,
        )
        btn_apply_net.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 6))

        btn_read_net = ctk.CTkButton(
            act_grid,
            text="📖  Считать с адаптера",
            height=40,
            corner_radius=10,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._read_from_adapter,
        )
        btn_read_net.grid(row=1, column=0, sticky="ew", padx=4, pady=2)

        btn_save_backup = ctk.CTkButton(
            act_grid,
            text="💾  Сохранить бэкап",
            height=40,
            corner_radius=10,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["accent_glow"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_backup_action,
        )
        btn_save_backup.grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        btn_restore_backup = ctk.CTkButton(
            act_grid,
            text="🔙  Восстановить из бэкапа",
            height=42,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_width=0,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._restore_backup_action,
        )
        btn_restore_backup.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 2))

        # Индикатор бэкапа
        backup_info_row = ctk.CTkFrame(scroll, fg_color="transparent")
        backup_info_row.pack(fill="x", padx=18, pady=(4, 6))

        self.lbl_backup_status = ctk.CTkLabel(
            backup_info_row,
            text="Бэкап ещё не создан",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
            anchor="w",
        )
        self.lbl_backup_status.pack(side="left")

        # Опция авто-отката при выходе
        rollback_row = ctk.CTkFrame(scroll, fg_color="transparent")
        rollback_row.pack(fill="x", padx=14, pady=(2, 8))

        ctk.CTkSwitch(
            rollback_row,
            text="Автоматически восстанавливать исходную сеть при закрытии программы",
            variable=self.auto_rollback_on_exit,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"],
            progress_color=COLORS["accent"],
            fg_color=COLORS["switch_off"],
        ).pack(side="left")

    # ────────────────────── Вкладка 3: Мастер & UART ──────────────────────

    def _build_tab_wizard(self):
        """Строит пошаговый мастер и UART терминал."""
        scroll = ctk.CTkScrollableFrame(
            self.tab_wizard,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border_subtle"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Интерактивный мастер ──
        card_wiz = Card(scroll, title="🧙‍♂️  Мастер прошивки роутера")
        card_wiz.pack(fill="x", pady=(0, 8))

        wiz_box = ctk.CTkFrame(card_wiz, fg_color=COLORS["bg_card_inner"], corner_radius=10)
        wiz_box.pack(fill="x", padx=16, pady=(4, 14))

        self.wiz_step_labels = []
        default_steps = [
            ("1", "Подключите патч-корд от сетевой карты ПК в LAN1 порт роутера"),
            ("2", "Примените нужный IP во вкладке «Сеть» (кнопка 'Применить к адаптеру')"),
            ("3", "Во вкладке «PXE/TFTP» выберите файл прошивки и запустите сервер"),
            ("4", "Отключите провод питания роутера, зажмите кнопку RESET спичкой/скрепкой"),
            ("5", "Не отпуская RESET, воткните провод питания и удерживайте 8-10 секунд"),
            ("6", "Отпустите RESET. Роутер начнёт запрашивать прошивку по TFTP"),
            ("7", "Следите за прогресс-баром передачи и дождитесь перезагрузки роутера"),
        ]

        for num, text in default_steps:
            row = ctk.CTkFrame(wiz_box, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=4)

            badge = ctk.CTkLabel(
                row,
                text=num,
                width=24,
                height=24,
                corner_radius=12,
                fg_color=COLORS["accent"],
                text_color="white",
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            badge.pack(side="left", padx=(0, 10))

            lbl = ctk.CTkLabel(
                row,
                text=text,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text"],
                anchor="w",
                wraplength=660,
                justify="left",
            )
            lbl.pack(side="left", fill="x", expand=True)
            self.wiz_step_labels.append((badge, lbl))

        # ── UART Терминал ──
        card_uart = Card(scroll, title="🔌  Встроенный UART-терминал (COM-порт)")
        card_uart.pack(fill="x", pady=(0, 8))

        uart_ctrl = ctk.CTkFrame(card_uart, fg_color="transparent")
        uart_ctrl.pack(fill="x", padx=16, pady=(4, 8))

        ctk.CTkLabel(
            uart_ctrl, text="Порт:", font=ctk.CTkFont(size=12, weight="bold"), text_color=COLORS["text_dim"]
        ).pack(side="left", padx=(0, 6))

        self.uart_combo = ctk.CTkComboBox(
            uart_ctrl,
            values=UARTManager.get_available_ports(),
            width=130,
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(size=11),
        )
        self.uart_combo.pack(side="left", padx=(0, 6))

        btn_uart_refresh = ctk.CTkButton(
            uart_ctrl,
            text="⟳",
            width=32,
            height=32,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            font=ctk.CTkFont(size=14),
            command=self._refresh_uart_ports,
        )
        btn_uart_refresh.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            uart_ctrl, text="Бод:", font=ctk.CTkFont(size=12), text_color=COLORS["text_dim"]
        ).pack(side="left", padx=(0, 4))

        self.uart_baud_combo = ctk.CTkComboBox(
            uart_ctrl,
            values=["115200", "57600", "38400", "19200", "9600", "230400", "921600"],
            width=90,
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(size=11),
        )
        self.uart_baud_combo.set("115200")
        self.uart_baud_combo.pack(side="left", padx=(0, 8))

        self.btn_uart_toggle = ctk.CTkButton(
            uart_ctrl,
            text="🔌 Подключить",
            width=110,
            height=32,
            corner_radius=8,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._toggle_uart,
        )
        self.btn_uart_toggle.pack(side="left", padx=(0, 8))

        btn_uart_clear = ctk.CTkButton(
            uart_ctrl,
            text="🧹 Очистить",
            width=80,
            height=32,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
            font=ctk.CTkFont(size=11),
            command=lambda: self.uart_text.delete("1.0", "end"),
        )
        btn_uart_clear.pack(side="left", padx=(0, 8))

        self.btn_uboot = ctk.CTkButton(
            uart_ctrl,
            text="⚡ Перехват U-Boot",
            height=32,
            corner_radius=8,
            fg_color=COLORS["warning"],
            hover_color="#f39c12",
            text_color="#1a1a1a",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._interrupt_uboot,
        )
        self.btn_uboot.pack(side="right")

        self.uart_text = ctk.CTkTextbox(
            card_uart,
            height=140,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["console_bg"],
            text_color="#2ed573",
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border_subtle"],
        )
        self.uart_text.pack(fill="x", padx=16, pady=(0, 8))

        # Отправка команды в UART
        cmd_row = ctk.CTkFrame(card_uart, fg_color="transparent")
        cmd_row.pack(fill="x", padx=16, pady=(0, 14))

        self.entry_uart_cmd = ctk.CTkEntry(
            cmd_row,
            placeholder_text="Команда в UART (например: printenv, help, boot)...",
            font=ctk.CTkFont(family="Consolas", size=11),
            height=32,
            corner_radius=8,
            fg_color=COLORS["bg_input"],
        )
        self.entry_uart_cmd.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry_uart_cmd.bind("<Return>", lambda e: self._send_uart_command())

        btn_send_cmd = ctk.CTkButton(
            cmd_row,
            text="Отправить",
            width=90,
            height=32,
            corner_radius=8,
            fg_color=COLORS["accent"],
            command=self._send_uart_command,
        )
        btn_send_cmd.pack(side="right")

    # ────────────────────── Вкладка 4: Инструкция и отчёт ──────────────────────

    def _build_tab_help(self):
        """Строит вкладку справки и отчёта."""
        scroll = ctk.CTkScrollableFrame(
            self.tab_help,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border_subtle"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Баг-репорт ──
        card_report = Card(scroll, title="📋  Диагностический отчёт для баг-репорта")
        card_report.pack(fill="x", pady=(0, 8))

        rep_row = ctk.CTkFrame(card_report, fg_color="transparent")
        rep_row.pack(fill="x", padx=16, pady=(4, 12))

        ctk.CTkLabel(
            rep_row,
            text="Сформировать единый системный отчёт (версия ОС, настройки сети, результат предполётной проверки и лог):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
            wraplength=520,
            justify="left",
        ).pack(side="left")

        btn_copy_rep = ctk.CTkButton(
            rep_row,
            text="📋  Скопировать отчёт",
            height=36,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._copy_bug_report,
        )
        btn_copy_rep.pack(side="right")

        # ── Инструкция по прошивке ──
        card_guide = Card(scroll, title="📖  Инструкция по восстановлению")
        card_guide.pack(fill="x", pady=(0, 8))

        guide_box = ctk.CTkFrame(card_guide, fg_color=COLORS["bg_card_inner"], corner_radius=10)
        guide_box.pack(fill="x", padx=16, pady=(4, 14))

        steps = [
            ("1", "Подключение кабеля", "Соедините сетевую карту ПК и любой LAN-порт роутера прямым Ethernet патч-кордом."),
            ("2", "Настройка IP", "Во вкладке «Сеть» задайте нужный статический IP (например, 192.168.1.2) и нажмите «Применить к адаптеру»."),
            ("3", "Загрузочный файл", "Во вкладке «PXE/TFTP» нажмите «Выбрать файл» и укажите вашу прошивку. Файл автоматически скопируется под своим оригинальным именем."),
            ("4", "Запуск сервера", "Выберите нужный движок (Python TFTP или TinyPXE) и нажмите зелёную кнопку «▶ Запустить сервер»."),
            ("5", "Подготовка роутера", "Отключите провод питания от роутера. Зажмите и удерживайте скрепкой/спичкой кнопку RESET."),
            ("6", "Включение с зажатым RESET", "Не отпуская RESET, воткните провод питания. Удерживайте RESET 8–10 секунд, пока индикатор не замигает."),
            ("7", "Передача и прошивка", "Роутер отправит TFTP-запрос, скачает файл и начнёт установку. Ход передачи виден на прогресс-баре. Дождитесь автоматической перезагрузки."),
        ]

        for num, header_txt, detail_txt in steps:
            s_row = ctk.CTkFrame(guide_box, fg_color="transparent")
            s_row.pack(fill="x", padx=12, pady=6)

            n_lbl = ctk.CTkLabel(
                s_row,
                text=num,
                width=24,
                height=24,
                corner_radius=12,
                fg_color=COLORS["accent"],
                text_color="white",
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            n_lbl.pack(side="left", padx=(0, 10), anchor="n")

            content_box = ctk.CTkFrame(s_row, fg_color="transparent")
            content_box.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                content_box,
                text=header_txt,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLORS["accent_light"],
                anchor="w",
            ).pack(fill="x")

            ctk.CTkLabel(
                content_box,
                text=detail_txt,
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text"],
                justify="left",
                wraplength=640,
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

    # ────────────────────── Журналирование ──────────────────────

    def log_msg(self, text: str, level: str = "ИНФО"):
        now = datetime.now().strftime("%H:%M:%S")
        line = f"[{now}] [{level}] {text}\n"
        self.log_history.append(line)
        self.after(0, lambda: self._append_to_log_widget(line))

    def _append_to_log_widget(self, line: str):
        self.log_text.insert("end", line)
        self.log_text.see("end")

    def _clear_log(self):
        self.log_text.delete("1.0", "end")
        self.log_history.clear()

    # ────────────────────── Конфиг-менеджер ──────────────────────

    def _refresh_config_list(self):
        """Обновляет список конфигов из папки configs/."""
        names = config_manager.get_config_names()
        if names:
            self.config_combo.configure(values=names)
            cur = self.config_combo.get()
            if cur not in names:
                self.config_combo.set(names[0])
                self._load_selected_config()
        else:
            self.config_combo.configure(values=["(нет сохранённых конфигов)"])
            self.config_combo.set("(нет сохранённых конфигов)")

    def _on_config_selected(self, name: str):
        if name != "(нет сохранённых конфигов)":
            self._load_selected_config()

    def _load_selected_config(self):
        """Загружает выбранный пользовательский конфиг."""
        name = self.config_combo.get()
        if not name or name == "(нет сохранённых конфигов)":
            return

        data = config_manager.load_config(name)
        if not data:
            return

        # Сеть
        self.dhcp_var.set(data.get("dhcp_enabled", False))
        self.dhcp_dns_var.set(data.get("dhcp_dns", False))
        self.entry_net_ip.set(data.get("ip_address", "192.168.1.2"))
        self.entry_net_mask.set(data.get("subnet_mask", "255.255.255.0"))
        self.entry_net_gw.set(data.get("default_gateway", "192.168.1.1"))
        self.entry_net_dns1.set(data.get("dns_primary", "192.168.1.1"))
        self.entry_net_dns2.set(data.get("dns_secondary", ""))

        # PXE
        self.proxy_var.set(data.get("proxy_dhcp", False))
        self.entry_pxe_server_ip.set(data.get("ip_address", "192.168.1.2"))
        self.entry_pxe_mask.set(data.get("subnet_mask", "255.255.255.0"))
        self.entry_pxe_gateway.set(data.get("default_gateway", "192.168.1.1"))
        self.entry_pxe_pool_start.set(data.get("pool_start", "192.168.1.3"))
        self.http_var.set(data.get("enable_http", False))

        fn = data.get("boot_filename", "test.bin")
        self._refresh_boot_files_dropdown(select_name=fn)

        self._toggle_dhcp_fields()
        self._toggle_dns_fields()
        self._on_proxy_toggled()

        self.status_bar.set_status(f"✓ Загружен конфиг: {name}", COLORS["accent_light"])
        self.log_msg(f"Загружен конфиг '{name}': IP={data.get('ip_address')}, Файл={fn}")

    def _collect_current_data(self) -> dict:
        """Собирает параметры в словарь."""
        return {
            "name": self.config_combo.get(),
            "dhcp_enabled": self.dhcp_var.get(),
            "dhcp_dns": self.dhcp_dns_var.get(),
            "ip_address": self.entry_net_ip.get(),
            "subnet_mask": self.entry_net_mask.get(),
            "default_gateway": self.entry_net_gw.get(),
            "dns_primary": self.entry_net_dns1.get(),
            "dns_secondary": self.entry_net_dns2.get(),
            "boot_filename": self._get_selected_filename(),
            "proxy_dhcp": self.proxy_var.get(),
            "pool_start": self.entry_pxe_pool_start.get(),
            "enable_http": self.http_var.get(),
        }

    def _save_current_config(self):
        name = self.config_combo.get()
        if not name or name == "(нет сохранённых конфигов)":
            self._create_new_config()
            return

        data = self._collect_current_data()
        if config_manager.save_config(name, data):
            self.status_bar.set_status(f"✓ Конфиг '{name}' сохранён", COLORS["success"])
            self.log_msg(f"Конфиг '{name}' сохранён.", "УСПЕХ")
            messagebox.showinfo("Конфиг сохранён", f"Конфигурация «{name}» успешно сохранена!")
        else:
            messagebox.showerror("Ошибка", f"Не удалось сохранить {name}")

    def _create_new_config(self):
        dialog = ctk.CTkInputDialog(text="Введите имя нового конфига:", title="Создать конфиг")
        new_name = dialog.get_input()
        if not new_name:
            return

        clean = "".join(c for c in new_name if c.isalnum() or c in ("_", "-")).strip()
        if not clean:
            messagebox.showerror("Ошибка", "Некорректное имя конфига!")
            return

        data = self._collect_current_data()
        data["name"] = clean
        if config_manager.save_config(clean, data):
            self._refresh_config_list()
            self.config_combo.set(clean)
            self.status_bar.set_status(f"✓ Создан конфиг: {clean}", COLORS["success"])
            self.log_msg(f"Создан новый конфиг: {clean}")
        else:
            messagebox.showerror("Ошибка", f"Не удалось создать {clean}")

    def _delete_current_config(self):
        name = self.config_combo.get()
        if not name or name == "(нет сохранённых конфигов)":
            return

        if not messagebox.askyesno("Удаление конфига", f"Вы уверены, что хотите удалить конфиг «{name}»?"):
            return

        if config_manager.delete_config(name):
            self.log_msg(f"Конфиг '{name}' удалён.")
            self._refresh_config_list()
        else:
            messagebox.showerror("Ошибка", f"Не удалось удалить {name}")

    # ────────────────────── Управление загрузочным файлом ──────────────────────

    def _get_selected_filename(self) -> str:
        """Безопасно извлекает чистое имя файла из строки комбобокса."""
        raw = self.file_combo.get().strip()
        if not raw:
            return "test.bin"
        m = re.match(r"^(.*?)(?:\s+\([^\)]+\))?$", raw)
        if m:
            return m.group(1).strip()
        return raw

    def _refresh_boot_files_dropdown(self, select_name: str = ""):
        """Пересканирует папку TinyPXE и обновляет комбобокс файлов."""
        files = get_available_boot_files()
        values = []
        target = ""

        found_names = [f[0].lower() for f in files]
        for fn, sz in files:
            val = f"{fn}  ({sz})"
            values.append(val)
            if select_name and fn.lower() == select_name.lower():
                target = val

        if select_name and select_name.lower() not in found_names:
            missing_val = f"{select_name}  (файл отсутствует)"
            values.insert(0, missing_val)
            target = missing_val

        if not values:
            values = ["test.bin  (файл не найден)"]

        self.file_combo.configure(values=values)
        if target:
            self.file_combo.set(target)
        elif values:
            self.file_combo.set(values[0])

        self._check_boot_file_status()

    def _check_boot_file_status(self):
        fn = self._get_selected_filename()
        exists, desc, size = check_file_status(fn)

        if exists:
            self.file_status_lbl.configure(
                text=f"✓ {fn} найден в папке TinyPXE ({format_bytes(size)})",
                text_color=COLORS["success"],
            )
        else:
            self.file_status_lbl.configure(
                text=f"⚠ Файл {fn} отсутствует в папке TinyPXE! Нажмите 'Выбрать файл'",
                text_color=COLORS["warning"],
            )

    def _browse_and_copy_file(self):
        """Выбор файла: копирует напрямую с его оригинальным именем, без переименований!"""
        chosen = filedialog.askopenfilename(
            title="Выберите файл прошивки для загрузки по сети",
            filetypes=[
                ("Файлы прошивок и образов (*.bin;*.img;*.iso;*.wim;*.pxe)", "*.bin;*.img;*.iso;*.wim;*.pxe"),
                ("Все файлы (*.*)", "*.*"),
            ],
        )
        if not chosen:
            return

        base = os.path.basename(chosen)
        ok, msg = copy_firmware_to_pxe(chosen, base)
        if ok:
            self.log_msg(f"Файл {base} скопирован в папку TinyPXE с оригинальным именем.", "УСПЕХ")
            self._refresh_boot_files_dropdown(select_name=base)
            self.status_bar.set_status(f"✓ Файл {base} скопирован", COLORS["success"])
            messagebox.showinfo("Готово", f"Файл «{base}» успешно скопирован в папку TinyPXE!")
        else:
            messagebox.showerror("Ошибка копирования", msg)

    def _verify_current_file(self):
        """Проверяет контрольную сумму и размер текущего файла."""
        fn = self._get_selected_filename()
        full_path = os.path.join(TINYPXE_DIR, fn)
        results = verify_firmware(full_path)
        msg_lines = []
        for r in results:
            status = "✓" if r.passed else "⚠"
            msg_lines.append(f"{status} {r.name}: {r.message}")

        full_msg = "\n\n".join(msg_lines)
        self.log_msg(f"Проверка прошивки {fn}:\n{full_msg}", "ПРОВЕРКА")
        messagebox.showinfo(f"Проверка файла {fn}", full_msg)

    # ────────────────────── Логика Сервера ──────────────────────

    def _on_engine_changed(self, choice: str):
        if "Python" in choice:
            self.tftp_engine_var.set("builtin")
        else:
            self.tftp_engine_var.set("tinypxe")

    def _on_dry_run_toggled(self):
        is_dry = self.dry_run_var.get()
        self.tftp_server.dry_run = is_dry
        if is_dry:
            self.status_bar.set_status("⚠ Включён режим Dry-Run (тестовый прогон)", COLORS["warning"])
            self.log_msg("Включён режим Dry-Run: реальная запись в устройство выполняться не будет.", "ВНИМАНИЕ")

    def _start_server_action(self):
        """Запускает ТОЛЬКО тот сервер, который выбран пользователем!"""
        cfg = PXEConfig()
        cfg.tftp_engine = self.tftp_engine_var.get()
        cfg.dry_run = self.dry_run_var.get()
        cfg.proxy_dhcp = self.proxy_var.get()
        cfg.server_ip = self.entry_pxe_server_ip.get() or "192.168.1.2"
        cfg.subnet_mask = self.entry_pxe_mask.get() or "255.255.255.0"
        cfg.gateway = self.entry_pxe_gateway.get() or "192.168.1.1"
        cfg.pool_start = self.entry_pxe_pool_start.get() or "192.168.1.3"
        cfg.boot_filename = self._get_selected_filename()
        cfg.enable_http = self.http_var.get()

        exists, _, _ = check_file_status(cfg.boot_filename)
        if not exists and not cfg.dry_run:
            if not messagebox.askyesno(
                "Файл отсутствует",
                f"Файл '{cfg.boot_filename}' отсутствует в папке TinyPXE!\n"
                "Сервер всё равно будет запущен, но файл не будет отдан.\n\n"
                "Продолжить запуск?",
            ):
                return

        # Добавляем временное правило фаервола
        add_firewall_rule()

        self.status_bar.set_status("Запуск сервера...", COLORS["warning"])

        # СТРОГАЯ ИЗОЛЯЦИЯ: запускаем ТОЛЬКО выбранный движок!
        if cfg.tftp_engine == "builtin":
            # Останавливаем TinyPXE, если вдруг был запущен
            if self.pxe_process.is_running:
                self.pxe_process.stop()

            # Настраиваем fallback-файл в TFTP
            self.tftp_server.default_boot_file = cfg.boot_filename
            self.tftp_server.dry_run = cfg.dry_run

            # Настраиваем и запускаем встроенный BOOTP/DHCP сервер (UDP:67)
            self.bootp_server.server_ip = cfg.server_ip
            self.bootp_server.subnet_mask = cfg.subnet_mask
            self.bootp_server.gateway = cfg.gateway
            self.bootp_server.client_ip = cfg.pool_start or "192.168.31.2"
            self.bootp_server.boot_filename = cfg.boot_filename

            bootp_ok, bootp_msg = self.bootp_server.start()
            if bootp_ok:
                self.log_msg(f"Python BOOTP/DHCP сервер запущен на UDP 67 (выдача IP: {self.bootp_server.client_ip}, файл: {cfg.boot_filename})", "УСПЕХ")
            else:
                self.log_msg(f"BOOTP/DHCP сервер: {bootp_msg}", "ПРЕДУПРЕЖДЕНИЕ")

            # Запускаем Python TFTP сервер
            tftp_ok, tftp_msg = self.tftp_server.start()

            if tftp_ok:
                self.status_badge.configure(text="● ОНЛАЙН (TFTP/DHCP)", text_color=COLORS["online"])
                self.status_bar.set_status("✓ Python TFTP/DHCP сервер активен (UDP:67/69)", COLORS["success"])
                self.log_msg(f"Python TFTP/DHCP запущен на UDP 67/69 (без TinyPXE). Файл: {cfg.boot_filename}", "УСПЕХ")
                self.lbl_tftp_status.configure(text="Ожидание запроса от роутера...", text_color=COLORS["accent_light"])
            else:
                self.status_bar.set_status(f"Ошибка TFTP: {tftp_msg}", COLORS["danger"])
                self.log_msg(f"Ошибка TFTP: {tftp_msg}", "ОШИБКА")
                messagebox.showerror("Ошибка запуска", tftp_msg)
        else:
            # Останавливаем Python TFTP и BOOTP, если вдруг были запущены
            if self.bootp_server.is_running:
                self.bootp_server.stop()
            if self.tftp_server.is_running:
                self.tftp_server.stop()

            # Запускаем ТОЛЬКО TinyPXE
            ok, msg = self.pxe_process.start(cfg, disable_tftp=False)
            if ok:
                self.status_badge.configure(text="● ОНЛАЙН (TinyPXE)", text_color=COLORS["online"])
                self.status_bar.set_status("✓ TinyPXE сервер активен (ONLINE)", COLORS["success"])
                self.log_msg("TinyPXE сервер успешно запущен в режиме ONLINE (без Python TFTP).", "УСПЕХ")
            else:
                self.status_bar.set_status(f"Ошибка TinyPXE: {msg}", COLORS["danger"])
                self.log_msg(f"Ошибка запуска TinyPXE: {msg}", "ОШИБКА")
                messagebox.showerror("Ошибка запуска", msg)

    def _stop_server_action(self):
        """Останавливает запущенные серверы."""
        if self.bootp_server.is_running:
            self.bootp_server.stop()
        if self.tftp_server.is_running:
            self.tftp_server.stop()
        if self.pxe_process.is_running:
            self.pxe_process.stop()

        remove_firewall_rule()

        self.status_badge.configure(text="● ОФЛАЙН", text_color=COLORS["offline"])
        self.status_bar.set_status("Сервер остановлен", COLORS["text_dim"])
        self.lbl_tftp_status.configure(text="Сервер остановлен", text_color=COLORS["text_dim"])
        self.progress_bar.set(0)
        self.log_msg("Сервер остановлен.", "ИНФО")

    # ────────────────────── Коллбэки передачи TFTP ──────────────────────

    def _tftp_on_request(self, client_ip: str, client_port: int, filename: str):
        self.after(0, lambda: self._ui_on_tftp_request(client_ip, filename))

    def _ui_on_tftp_request(self, client_ip: str, filename: str):
        self.lbl_tftp_status.configure(
            text=f"Роутер {client_ip} запросил файл '{filename}'!",
            text_color=COLORS["warning"],
        )
        self.status_bar.set_status(f"Передача {filename} к {client_ip}...", COLORS["warning"])
        if len(self.wiz_step_labels) >= 6:
            badge, lbl = self.wiz_step_labels[5]
            badge.configure(fg_color=COLORS["success"])
            lbl.configure(text=f"✓ Запрос получен от {client_ip}! Идёт передача {filename}...")

    def _tftp_on_progress(self, sent: int, total: int, speed: str, retries: int, pct: float):
        self.after(0, lambda: self._ui_on_tftp_progress(sent, total, speed, retries, pct))

    def _ui_on_tftp_progress(self, sent: int, total: int, speed: str, retries: int, pct: float):
        self.progress_bar.set(pct / 100.0)
        retries_str = f" • Повторов: {retries}" if retries > 0 else ""
        self.lbl_tftp_metrics.configure(
            text=f"{format_bytes(sent)} / {format_bytes(total)} ({pct:.1f}%)  •  {speed}{retries_str}"
        )

    def _tftp_on_complete(self, client_ip: str, filename: str, elapsed: float, total: int):
        self.after(0, lambda: self._ui_on_tftp_complete(client_ip, filename, elapsed, total))

    def _ui_on_tftp_complete(self, client_ip: str, filename: str, elapsed: float, total: int):
        self.progress_bar.set(1.0)
        self.lbl_tftp_status.configure(
            text=f"✓ Файл '{filename}' успешно передан {client_ip}!",
            text_color=COLORS["success"],
        )
        self.status_bar.set_status(f"✓ Передача {filename} завершена за {elapsed:.1f}с", COLORS["success"])
        if len(self.wiz_step_labels) >= 7:
            badge, lbl = self.wiz_step_labels[6]
            badge.configure(fg_color=COLORS["success"])
            lbl.configure(text=f"✓ Передача завершена! Роутер перезагрузится через 1–2 мин.")

    def _tftp_on_error(self, err_msg: str):
        self.after(0, lambda: self._ui_on_tftp_error(err_msg))

    def _ui_on_tftp_error(self, err_msg: str):
        self.lbl_tftp_status.configure(text=f"✗ Ошибка передачи: {err_msg}", text_color=COLORS["danger"])
        self.status_bar.set_status(f"Ошибка TFTP: {err_msg}", COLORS["danger"])

    # ────────────────────── Диагностика и предполётная проверка ──────────────────────

    def _run_preflight_dialog(self):
        """Запускает комплексную предполётную проверку."""
        checks = run_all_preflight_checks()
        lines = []
        all_passed = True
        for c in checks:
            if not c.passed and c.severity == "error":
                all_passed = False
            status = "✓" if c.passed else ("⚠" if c.severity == "warning" else "✗")
            lines.append(f"{status}  {c.name}: {c.message}")

        text = "\n\n".join(lines)
        self.log_msg("Результаты предполётной проверки:\n" + text, "ПРОВЕРКА")

        if all_passed:
            messagebox.showinfo("Предполётная проверка", f"Система готова к прошивке!\n\n{text}")
        else:
            messagebox.showwarning("Предполётная проверка", f"Обнаружены предупреждения:\n\n{text}")

    def _test_tftp(self):
        ip = self.entry_pxe_server_ip.get() or "192.168.1.2"
        fn = self._get_selected_filename()
        self.log_msg(f"Тест TFTP: отправка запроса на {ip}:69...")
        self.status_bar.set_status("Проверка TFTP...", COLORS["warning"])

        def test_worker():
            ok, msg = test_tftp_service(ip, fn, timeout_sec=1.5)
            c = COLORS["success"] if ok else COLORS["danger"]
            self.after(0, lambda: self.status_bar.set_status(msg, c))
            self.after(0, lambda: self.log_msg(msg, "TFTP" if ok else "ОШИБКА"))

        threading.Thread(target=test_worker, daemon=True).start()

    def _ping_gateway(self):
        gw = self.entry_pxe_gateway.get() or "192.168.1.1"
        self.log_msg(f"Пинг шлюза {gw}...")
        self.status_bar.set_status(f"Пинг {gw}...", COLORS["warning"])

        def ping_worker():
            ok = ping_host(gw, timeout_ms=800)
            if ok:
                msg = f"✓ Устройство {gw} отвечает на пинг! Соединение активно."
                self.after(0, lambda: self.status_bar.set_status(msg, COLORS["success"]))
                self.after(0, lambda: self.log_msg(msg, "СВЯЗЬ"))
            else:
                msg = f"✗ {gw} не отвечает на пинг (устройство выключено или в загрузчике)."
                self.after(0, lambda: self.status_bar.set_status(msg, COLORS["warning"]))
                self.after(0, lambda: self.log_msg(msg, "ПИНГ"))

        threading.Thread(target=ping_worker, daemon=True).start()

    def _on_proxy_toggled(self):
        is_proxy = self.proxy_var.get()
        state = "disabled" if is_proxy else "normal"
        self.entry_pxe_pool_start.set_state(state)
        self.entry_pxe_gateway.set_state(state)

    # ────────────────────── Логика Сети ──────────────────────

    def _refresh_adapter_list(self):
        names = get_adapter_names()
        if names:
            self.adapter_combo.configure(values=names)
            cur = self.adapter_combo.get()
            if cur not in names:
                self.adapter_combo.set(names[0])
            self._read_from_adapter()
        else:
            self.adapter_combo.configure(values=["Ethernet"])

    def _load_current_adapter(self):
        self._refresh_adapter_list()

    def _on_adapter_changed(self, adapter_name: str):
        self._read_from_adapter()

    def _read_from_adapter(self):
        ad = self.adapter_combo.get()
        if not ad:
            return

        self.status_bar.set_status(f"Чтение настроек «{ad}»...", COLORS["accent_light"])
        st = get_current_settings(ad)

        self.dhcp_var.set(st.dhcp_enabled)
        self.dhcp_dns_var.set(st.dhcp_dns)
        self.entry_net_ip.set(st.ip_address)
        self.entry_net_mask.set(st.subnet_mask or "255.255.255.0")
        self.entry_net_gw.set(st.default_gateway)
        self.entry_net_dns1.set(st.dns_primary)
        self.entry_net_dns2.set(st.dns_secondary)

        self._toggle_dhcp_fields()
        self._toggle_dns_fields()

        if st.ip_address:
            self.entry_pxe_server_ip.set(st.ip_address)
            self.entry_pxe_mask.set(st.subnet_mask or "255.255.255.0")
            if st.default_gateway:
                self.entry_pxe_gateway.set(st.default_gateway)

        self.status_bar.set_status(f"Настройки «{ad}» считаны", COLORS["success"])
        self.log_msg(f"Считан адаптер '{ad}': IP={st.ip_address}, DHCP={'Да' if st.dhcp_enabled else 'Нет'}")
        self._update_backup_label()

    def _toggle_dhcp_fields(self):
        st = "disabled" if self.dhcp_var.get() else "normal"
        self.entry_net_ip.set_state(st)
        self.entry_net_mask.set_state(st)
        self.entry_net_gw.set_state(st)

    def _toggle_dns_fields(self):
        st = "disabled" if self.dhcp_dns_var.get() else "normal"
        self.entry_net_dns1.set_state(st)
        self.entry_net_dns2.set_state(st)

    def _apply_dns_preset(self, preset_data: dict):
        self.dhcp_dns_var.set(False)
        self._toggle_dns_fields()
        self.entry_net_dns1.set(preset_data["dns_primary"])
        self.entry_net_dns2.set(preset_data["dns_secondary"])
        self.status_bar.set_status(f"Выбран DNS: {preset_data['desc']}", COLORS["accent_light"])

    def _restart_elevated(self):
        """Перезапускает приложение с правами администратора и закрывает текущую не-админ копию."""
        if restart_as_admin():
            self.auto_rollback_on_exit.set(False)
            try:
                self._on_close()
            except Exception:
                pass
            try:
                self.quit()
                self.destroy()
            except Exception:
                pass
            os._exit(0)

    def _on_admin_badge_click(self):
        """Обработчик клика по бейджу статуса 'Нет прав'."""
        if is_admin():
            return
        if messagebox.askyesno(
            "Права администратора",
            "AutoTiny сейчас запущен без прав администратора.\n\n"
            "Перезапустить программу от имени администратора?",
        ):
            self._restart_elevated()

    def _apply_network_to_windows(self):
        """Применяет параметры к адаптеру с фиксацией исходного бэкапа."""
        if not is_admin():
            if messagebox.askyesno(
                "Требуются права администратора",
                "Для изменения сетевых настроек требуются права Администратора.\n\n"
                "Перезапустить программу с правами администратора?",
            ):
                self._restart_elevated()
            return

        ad = self.adapter_combo.get()

        # Создаём авто-бэкап ПЕРЕД изменением сети (сохраняет исходный baseline)
        save_auto_backup(ad)
        self._update_backup_label()

        st = NetworkSettings(adapter_name=ad)
        st.dhcp_enabled = self.dhcp_var.get()
        st.dhcp_dns = self.dhcp_dns_var.get()
        st.ip_address = self.entry_net_ip.get()
        st.subnet_mask = self.entry_net_mask.get() or "255.255.255.0"
        st.default_gateway = self.entry_net_gw.get()
        st.dns_primary = self.entry_net_dns1.get()
        st.dns_secondary = self.entry_net_dns2.get()

        self.status_bar.set_status("Применение сетевых настроек...", COLORS["warning"])
        self.log_msg(f"Применение настроек к адаптеру '{ad}'...")

        def worker():
            ok, msg = apply_network_settings(st)
            if ok:
                self.after(0, lambda: self.status_bar.set_status("✓ Сетевые настройки успешно применены!", COLORS["success"]))
                self.after(0, lambda: self.log_msg(f"Настройки применены к '{ad}'!", "УСПЕХ"))
                if st.ip_address:
                    self.after(0, lambda: self.entry_pxe_server_ip.set(st.ip_address))
                self.after(0, lambda: messagebox.showinfo("Успех", "Сетевые настройки успешно применены к адаптеру!"))
            else:
                self.after(0, lambda: self.status_bar.set_status("Ошибка применения", COLORS["danger"]))
                self.after(0, lambda: self.log_msg(f"Ошибка: {msg}", "ОШИБКА"))
                self.after(0, lambda: messagebox.showerror("Ошибка", msg))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_routers_action(self):
        """Сканирует типовые IP-адреса роутеров."""
        self.status_bar.set_status("Поиск роутера в сети (ARP / ping)...", COLORS["warning"])
        self.log_msg("Запуск сканирования сети на типовых адресах роутеров...")

        def scan_worker():
            routers = scan_for_router()
            if routers:
                r = routers[0]
                msg = f"Найдено устройство! IP: {r.ip}, MAC: {r.mac or 'н/д'}, Вендор: {r.vendor}"
                self.after(0, lambda: self.status_bar.set_status(f"✓ Роутер найден: {r.ip} ({r.vendor})", COLORS["success"]))
                self.after(0, lambda: self.log_msg(msg, "УСПЕХ"))
                self.after(0, lambda: messagebox.showinfo(
                    "Роутер обнаружен",
                    f"Найдено устройство в сети:\n\n"
                    f"IP-адрес: {r.ip}\n"
                    f"MAC-адрес: {r.mac or 'Не определён'}\n"
                    f"Производитель: {r.vendor}\n\n"
                    f"Подходит для прошивки!"
                ))
            else:
                msg = "Роутер не отвечает на типовых адресах (192.168.1.1, 192.168.31.1, 192.168.0.1)."
                self.after(0, lambda: self.status_bar.set_status("Роутер не найден", COLORS["text_dim"]))
                self.after(0, lambda: self.log_msg(msg, "СКАНИРОВАНИЕ"))
                self.after(0, lambda: messagebox.showinfo("Поиск", msg + "\n\nУбедитесь, что Ethernet-кабель подключён."))

        threading.Thread(target=scan_worker, daemon=True).start()

    def _save_backup_action(self):
        """Сохраняет текущие параметры адаптера в постоянный бэкап."""
        ad = self.adapter_combo.get()
        st = get_current_settings(ad)
        if save_backup(st):
            self.status_bar.set_status("✓ Бэкап успешно сохранён", COLORS["success"])
            self._update_backup_label()
            messagebox.showinfo("Бэкап сохранён", f"Параметры адаптера «{ad}» сохранены в бэкап!")
        else:
            messagebox.showerror("Ошибка", "Не удалось сохранить бэкап!")

    def _restore_backup_action(self):
        """Восстанавливает настройки сети из сохранённого бэкапа в любой момент!"""
        if not is_admin():
            if messagebox.askyesno(
                "Требуются права администратора",
                "Для восстановления настроек требуются права Администратора.\n\n"
                "Перезапустить программу с правами администратора?",
            ):
                self._restart_elevated()
            return

        b = load_backup()
        if not b:
            messagebox.showwarning("Бэкап не найден", "Резервная копия сетевых настроек не найдена!")
            return

        msg = f"Восстановить исходные параметры адаптера «{b.adapter_name}»?\n\n"
        msg += f"Режим IP: {'DHCP (автоматически)' if b.dhcp_enabled else b.ip_address}\n"
        if not b.dhcp_enabled:
            msg += f"Маска: {b.subnet_mask}\nШлюз: {b.default_gateway or '(нет)'}\n"
        msg += f"Режим DNS: {'DHCP (автоматически)' if b.dhcp_dns else b.dns_primary}\n"

        if not messagebox.askyesno("Восстановление из бэкапа", msg):
            return

        self.status_bar.set_status("Восстановление настроек из бэкапа...", COLORS["warning"])
        ok, res_msg = apply_network_settings(b)
        if ok:
            self.status_bar.set_status("✓ Настройки восстановлены из бэкапа!", COLORS["success"])
            self.log_msg(f"Сеть успешно восстановлена из бэкапа для '{b.adapter_name}'!", "УСПЕХ")
            self._read_from_adapter()
            messagebox.showinfo("Успех", "Настройки сети успешно восстановлены из бэкапа!")
        else:
            self.status_bar.set_status("Ошибка восстановления", COLORS["danger"])
            messagebox.showerror("Ошибка", res_msg)

    # ────────────────────── UART Терминал ──────────────────────

    def _refresh_uart_ports(self):
        ports = UARTManager.get_available_ports()
        self.uart_combo.configure(values=ports)
        if ports:
            self.uart_combo.set(ports[0])

    def _toggle_uart(self):
        if self.uart.is_connected:
            self.uart.disconnect()
            self.btn_uart_toggle.configure(text="🔌 Подключить", fg_color=COLORS["accent"])
            self.status_bar.set_status("UART отключен", COLORS["text_dim"])
            self.log_msg("UART терминал отключён.")
        else:
            port = self.uart_combo.get()
            try:
                baud = int(self.uart_baud_combo.get())
            except ValueError:
                baud = 115200

            ok, msg = self.uart.connect(port, baud)
            if ok:
                self.btn_uart_toggle.configure(text="❌ Отключить", fg_color=COLORS["danger"])
                self.status_bar.set_status(f"✓ UART: {port} ({baud})", COLORS["success"])
                self.log_msg(f"UART подключен к {port} на скорости {baud} бод", "UART")
            else:
                self.status_bar.set_status(f"Ошибка UART: {msg}", COLORS["danger"])
                messagebox.showerror("Ошибка UART", msg)

    def _uart_on_data(self, text: str):
        self.after(0, lambda: self._append_uart_text(text))

    def _append_uart_text(self, text: str):
        self.uart_text.insert("end", text)
        self.uart_text.see("end")

    def _send_uart_command(self):
        cmd = self.entry_uart_cmd.get().strip()
        if not cmd:
            return
        if not self.uart.is_connected:
            messagebox.showwarning("UART", "Сначала подключитесь к COM-порту!")
            return
        self.uart.send(cmd + "\r\n")
        self.entry_uart_cmd.delete(0, "end")

    def _interrupt_uboot(self):
        if not self.uart.is_connected:
            messagebox.showwarning("UART", "Сначала подключитесь к COM-порту роутера!")
            return
        self.log_msg("Отправка сигнала прерывания автозагрузки U-Boot...", "UART")
        self.status_bar.set_status("Отправка U-Boot прерывания...", COLORS["warning"])
        threading.Thread(target=self.uart.interrupt_uboot, daemon=True).start()

    # ────────────────────── Баг-репорт ──────────────────────

    def _copy_bug_report(self):
        report = generate_bug_report(
            adapter_name=self.adapter_combo.get(),
            config_name=self.config_combo.get(),
            log_lines=self.log_history,
            extra_info={
                "TFTP Engine": self.tftp_engine_var.get(),
                "Dry Run": self.dry_run_var.get(),
                "Boot File": self._get_selected_filename(),
            }
        )
        self.clipboard_clear()
        self.clipboard_append(report)
        self.status_bar.set_status("✓ Отчёт скопирован в буфер обмена", COLORS["success"])
        messagebox.showinfo("Готово", "Диагностический отчёт скопирован в буфер обмена!\nВы можете отправить его разработчику.")

    # ────────────────────── Аварийное восстановление и выход ──────────────────────

    def _check_crash_recovery_on_startup(self):
        crash_settings = check_crash_recovery()
        if crash_settings:
            if messagebox.askyesno(
                "Восстановление сети после сбоя",
                "Обнаружено, что в прошлой сессии AutoTiny программа была закрыта аварийно,\n"
                f"когда на адаптере «{crash_settings.adapter_name}» был изменён IP.\n\n"
                "Восстановить исходные настройки сети сейчас?",
            ):
                ok, msg = apply_network_settings(crash_settings)
                if ok:
                    clear_crash_flag()
                    self._read_from_adapter()
                    messagebox.showinfo("Успех", "Исходные настройки сети восстановлены!")
                else:
                    messagebox.showerror("Ошибка", f"Не удалось восстановить сеть: {msg}")
            else:
                clear_crash_flag()

    def _on_close(self):
        # Остановка серверов
        if self.bootp_server.is_running:
            self.bootp_server.stop()
        if self.tftp_server.is_running:
            self.tftp_server.stop()
        if self.pxe_process.is_running:
            self.pxe_process.stop()
        if self.uart.is_connected:
            self.uart.disconnect()

        remove_firewall_rule()

        # Авто-откат сети при выходе
        if self.auto_rollback_on_exit.get():
            ad = self.adapter_combo.get()
            rollback_network_on_exit(ad)
        else:
            clear_crash_flag()

        self.destroy()


# ──────────────────────────── Точка входа ────────────────────────────

def main():
    app = AutoTinyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
