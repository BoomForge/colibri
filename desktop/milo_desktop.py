"""Milo Desktop MVP: a native Windows conversation window over local inference.

No browser, cloud account or model download is required by this application.
Colibri's already-running OpenAI-compatible local API provides inference.
Milo's memory, profiles and settings remain separate from the model weights.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime
from urllib.request import urlopen
import uuid
import webbrowser

import tkinter as tk
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    TkinterDnD = None
    DND_FILES = "DND_Files"
from tkinter import filedialog, messagebox, simpledialog, ttk
from desktop.chat_view import ChatView, ConversationList, COLORS, avatar_widget, AvatarBank

from colibri.milo_core.vision import (
    OllamaLocalVision, VisionError, vision_context,
)
from colibri.milo_core.windows_actions import (
    WindowsAction, available_actions, parse_action_request, execute_action,
)
from colibri.milo_core.global_hotkey import GlobalQuickChatHotkey
from colibri.milo_core.tool_activity import (
    init_activity, record_activity, list_activity, clear_activity,
)
from desktop.screen_capture import capture_primary_screen, ScreenCaptureError
from desktop.tray_service import MiloTray
from colibri.milo_core.consultation import prepare_consultation
from colibri.milo_core.conversation import MiloConversation, MiloProfile
from colibri.milo_core.filesystem import find_files_by_prefix
from colibri.milo_core.file_operations import (
    FileMoveError, plan_file_moves, execute_file_moves,
)
from colibri.milo_core.chat_files import parse_file_query, answer_file_query
from colibri.milo_core.desktop_actions import (
    parse_open_request, locate_file, validate_preview_path,
)
from desktop.file_viewer import open_preview
from colibri.milo_core.attachments import (
    AttachmentError, inspect_attachment, parse_dropped_paths,
    stage_attachments, attachment_context, MAX_ATTACHMENTS,
)
from colibri.milo_core.model_catalog import (
    MODEL_PAGE, ModelDownloadError, inspect_abliterated_release,
    download_abliterated,
)
from colibri.milo_core.research_model import (
    MODEL_PAGE as RESEARCH_MODEL_PAGE,
    inspect_research_asset, download_research_asset,
)
from colibri.milo_core.local_engine import (
    LocalEngine, EngineStartupError, chosen_model, discover_models,
    install_root, managed_models_root, model_valid,
    discover_loose_gguf, gguf_target,
)
from colibri.milo_core.legacy_archive import (
    LegacyArchiveError, import_legacy_archive, inspect_legacy_archive,
    loaded_persona,
)
from colibri.milo_core.portable_backup import BackupError, export_bundle, import_bundle
from colibri.milo_core.provider import LocalOpenAIProvider, ProviderError
from colibri.milo_core.store import LocalMemoryStore
from colibri.milo_core.documents import (
    DocumentError, import_document, init_documents,
    list_documents, remove_document,
)
from colibri.milo_core.web_research import (
    ResearchError, init_web_prefs, web_enabled,
    set_web_enabled, wants_research, query_for_research,
    search_public_web, source_context, source_footer,
    search_results_reply, harvard_references,
)
from colibri.milo_core.bots import (
    Bot, ChatSettings, attributed_messages, chat_settings,
    get_bot, init_bot_schema, list_bots, model_for_turn,
    resolve_target, save_bot, set_bot_active, set_chat_settings,
    delete_conversation, public_handle, SPECIALIST_HANDLES,
)
from colibri.milo_core.specialist_presets import ensure_specialists
from colibri.milo_core.updates import (
    CURRENT_VERSION, UpdateError, latest_milo_release,
    release_version, installer_asset, newer, download_verified,
    inspect_module_catalog, get_release_asset, MAX_MODULE,
)
from colibri.milo_core.modules import (
    BUILTINS, enabled as module_enabled, set_enabled,
    load_state as load_module_state, load_module_actions,
    install_verified_module,
)

VERSION = CURRENT_VERSION
BG = COLORS["background"]
SIDEBAR = COLORS["sidebar"]
PANEL = COLORS["header"]
PANEL_BRIGHT = "#2c2d38"
FG = COLORS["text"]
MUTED = COLORS["muted"]
VIOLET = COLORS["milo"]
ORANGE = COLORS["user"]
ACCENT = "#6753b4"
EDGE = COLORS["border"]
FONT = "Segoe UI"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "Milo"
DB_PATH = DATA_DIR / "memory.sqlite3"
CONFIG_PATH = DATA_DIR / "settings" / "preferences.json"
AVATARS = DATA_DIR / "avatars"
DOCUMENTS = DATA_DIR / "documents"
ORIGINAL_ARCHIVES = DATA_DIR / "original_archives"
BRAND_AVATAR = install_root() / "Assets" / "milo_profile.png"
BRAND_ICON = install_root() / "Assets" / "Milo.ico"
DEFAULTS = {
    "model": "",
    "base_url": "http://127.0.0.1:8000/v1",
    "user_name": "You",
    "milo_name": "Milo",
    "avatar_user": "",
    "avatar_milo": "",
    "current_chat": "main",
    "model_directory": "",
    "model_location": "",
    "backend": "vulkan",
    "global_shortcut": "on",
    "vision_enabled": "off",
    "vision_model": "qwen3-vl:2b",
    "vision_endpoint": "http://127.0.0.1:11434",
}


def load_config() -> dict:
    config = DEFAULTS.copy()
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            for key in config:
                if isinstance(saved.get(key), str):
                    config[key] = saved[key][:4096]
    except (OSError, ValueError):
        pass
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    contents = json.dumps({key: config[key] for key in DEFAULTS},
                          indent=2, ensure_ascii=False)
    temp = CONFIG_PATH.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(CONFIG_PATH)


def new_button(parent, label: str, command, *, primary: bool = False,
               width: int | None = None):
    button = tk.Button(parent, text=label, command=command, cursor="hand2",
                       bg=ACCENT if primary else PANEL_BRIGHT,
                       fg=FG, activebackground=EDGE, activeforeground=FG,
                       relief="flat", bd=0, font=(FONT, 10),
                       padx=13, pady=8, highlightthickness=0)
    if width:
        button.configure(width=width)
    return button


def style_text(widget: tk.Text, *, small: bool = False) -> None:
    widget.configure(bg=PANEL, fg=FG, insertbackground=VIOLET,
                     selectbackground=ACCENT, selectforeground=FG,
                     font=(FONT, 10 if small else 11), relief="flat",
                     borderwidth=0, highlightthickness=1,
                     highlightbackground=EDGE, highlightcolor=VIOLET,
                     padx=12, pady=10)


def status_endpoint(base_url: str) -> str:
    """Use the same loopback-only policy as the inference provider."""
    LocalOpenAIProvider(model="probe", base_url=base_url)
    return base_url.rstrip("/") + "/models"


def list_models(base_url: str) -> list[str]:
    with urlopen(status_endpoint(base_url), timeout=4) as response:
        payload = json.loads(response.read(512 * 1024))
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("the model endpoint did not return an OpenAI-style model list")
    # Provider types are independent: never auto-route a conversation to
    # an image-generation or decision-only model.
    return [x["id"] for x in data if isinstance(x, dict)
            and isinstance(x.get("id"), str) and x["id"]
            and (not isinstance(x.get("capabilities"), list)
                 or "chat" in x["capabilities"])][:30]


class MiloDesktop:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Milo — Personal Assistant")
        try:
            if BRAND_ICON.is_file() and sys.platform == "win32":
                self.root.iconbitmap(str(BRAND_ICON))
            elif BRAND_AVATAR.is_file():
                self.root.iconphoto(True, tk.PhotoImage(file=str(BRAND_AVATAR)))
        except tk.TclError:
            pass
        self.root.geometry("1280x810")
        self.root.minsize(1040, 620)
        self.root.configure(bg=BG)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        AVATARS.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.config = load_config()
        self.install_root = install_root()
        self.models_root = managed_models_root(self.install_root)
        self.engine = LocalEngine(self.install_root, log_folder=DATA_DIR / "logs")
        self.active_base_url = self.config["base_url"]
        self.engine_starting = False
        self.model_ready = False
        # All managed model weights live under the named Models directory.
        self.config["model_directory"] = str(self.models_root)
        self.db = LocalMemoryStore(DB_PATH)
        init_bot_schema(self.db.db)
        ensure_specialists(self.db.db, self.models_root)
        init_documents(self.db.db)
        init_web_prefs(self.db.db)
        init_activity(self.db.db)
        DOCUMENTS.mkdir(parents=True, exist_ok=True)
        self.active_model_location = ""
        self.loading_model_location = ""
        self.pending_send = None
        self.events: queue.Queue[tuple] = queue.Queue()
        self.tray = MiloTray(self.events, BRAND_ICON, BRAND_AVATAR)
        self._tray_hidden_panels = []
        self._exiting = False
        self.quick_hotkey = GlobalQuickChatHotkey(self.events)
        self.session_captures: list[Path] = []
        self.active_tool: str | None = None
        capture_folder = DATA_DIR / "captures"
        if capture_folder.exists() and not capture_folder.is_symlink():
            # Screenshots are temporary; remove orphaned images from old sessions.
            for old in capture_folder.glob("screen-*.png"):
                if old.is_file() and not old.is_symlink():
                    try:
                        old.unlink()
                    except OSError:
                        pass
        self.busy = False
        self.cancel = threading.Event()
        self.model_download_cancel = threading.Event()
        self.model_download_active = False
        self.models: list[str] = []
        self.images = {}
        self.release_candidate = None
        self.module_catalog = []
        self.plugin_actions, self.plugin_errors = load_module_actions(DATA_DIR)
        self.current_chat = self.config.get("current_chat") or "main"
        self.pending_message = None
        self.queued_attachments = []
        self._build()
        self._refresh_profile()
        self._autoselect_local_model()
        self._refresh_bot_sidebar()
        self._refresh_chat_list()
        self._load_chat()
        self._refresh_chat_context()
        self._ensure_chat_model()
        self.root.after(70, self._pump)
        # Close (X / Alt+F4) and the minimise button leave Milo running.
        # The only normal quit action is "Close Milo" in the tray menu.
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.root.bind("<Unmap>", self._on_main_unmap, add="+")
        if "--ui-self-test" not in sys.argv:
            self.tray.start()
        if ("--ui-self-test" not in sys.argv
                and self.config.get("global_shortcut") == "on"):
            self.quick_hotkey.start()

    def _build(self):
        self.side = tk.Frame(self.root, bg=SIDEBAR, width=260,
                             highlightbackground=EDGE, highlightthickness=1)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)

        logo = tk.Frame(self.side, bg=SIDEBAR)
        logo.pack(fill="x", padx=18, pady=(17, 14))
        tk.Label(logo, text="M I L O", bg=SIDEBAR, fg=VIOLET,
                 font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(logo, text="LOCAL  /  PERSONAL  /  PRIVATE", bg=SIDEBAR,
                 fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        tk.Frame(self.side, bg=EDGE, height=1).pack(fill="x", padx=15)

        new_button(self.side, "＋   New conversation", self.new_chat, primary=True).pack(
            fill="x", padx=12, pady=(14, 9))
        tk.Label(self.side, text="DIRECT MESSAGES", bg=SIDEBAR, fg=MUTED,
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=17, pady=(9, 7))
        self.chat_list = ConversationList(
            self.side, self._select_chat, on_delete=self.delete_chat,
            background=SIDEBAR)
        self.chat_list.pack(fill="both", expand=True, padx=(5, 5))
        self.root.bind("<Control-k>", lambda _e: self.search_conversations())
        self.root.bind("<Control-Shift-m>", lambda _e: self.show_quick_chat())

        search_chats = new_button(
            self.side, "⌕   Search conversations (Ctrl+K)",
            self.search_conversations)
        search_chats.configure(bg=SIDEBAR, fg=MUTED, anchor="w",
                               font=(FONT, 9), pady=7, padx=9)
        search_chats.pack(fill="x", padx=10, pady=(0, 4))

        # Chat and conversations stay visible; the setup/admin tools don't
        # compete for attention. Their original functionality remains intact.
        self.advanced_open = False
        self.advanced_wrap = tk.Frame(self.side, bg=SIDEBAR)
        self.advanced_wrap.pack(fill="x", padx=12, pady=(7, 8))
        self.advanced_toggle = new_button(
            self.advanced_wrap, "⚙  Setup & modules  ▸",
            self.toggle_advanced)
        self.advanced_toggle.configure(
            bg=SIDEBAR, fg=MUTED, anchor="w",
            activebackground=COLORS["hover"], padx=9, pady=7,
            font=(FONT, 9))
        self.advanced_toggle.pack(fill="x")
        self.advanced_controls = tk.Frame(self.advanced_wrap, bg=SIDEBAR)
        actions = [
            ("Models", self.models_window, "models"),
            ("Updates & modules", self.updates_modules_window, None),
            ("Tool activity", self.activity_window, None),
            ("Memory", self.memory_window, "memory"),
            ("Restore Milo personality", self.restore_milo_personality, "memory"),
            ("Find files", self.find_files_window, "files"),
            ("Organise files", self.organise_files_window, "files"),
            ("Windows tools", self.windows_tools_window, "files"),
            ("Ask SOL", self.sol_window, "sol"),
            ("Back up / Restore", self.backup_window, "backup"),
            ("Settings", self.settings_window, None),
        ]
        for caption, action, feature in actions:
            if feature is not None and not module_enabled(DATA_DIR, feature):
                continue
            control = new_button(self.advanced_controls, caption, action)
            control.configure(
                bg=SIDEBAR, fg=MUTED, anchor="w",
                activebackground=COLORS["hover"], padx=15, pady=5,
                font=(FONT, 9))
            control.pack(fill="x", pady=1)
        for plugin_action in self.plugin_actions:
            extra = new_button(self.advanced_controls, plugin_action.label,
                               plugin_action.open_panel)
            extra.configure(bg=SIDEBAR, fg=MUTED, anchor="w",
                            padx=15, pady=5, font=(FONT, 9))
            extra.pack(fill="x", pady=1)

        self.profile_row = tk.Frame(self.side, bg=PANEL)
        self.profile_row.pack(fill="x", side="bottom", padx=10, pady=(7, 9))
        self.user_icon = tk.Label(self.profile_row, text="U", bg=EDGE, fg=ORANGE,
                                  font=(FONT, 14, "bold"), width=3)
        self.user_icon.pack(side="left", padx=(8, 9), pady=9)
        self.user_label = tk.Label(self.profile_row, text="You", bg=PANEL, fg=FG,
                                   font=(FONT, 10, "bold"))
        self.user_label.pack(anchor="w", pady=(12, 0))
        tk.Label(self.profile_row, text="Local profile", bg=PANEL, fg=MUTED,
                 font=(FONT, 8)).pack(anchor="w", pady=(0, 9))

        # Reserve the members panel before packing the expandable chat region.
        self.bot_side = tk.Frame(
            self.root, bg=SIDEBAR, width=225,
            highlightthickness=1, highlightbackground=EDGE)
        self.bot_side.pack(side="right", fill="y")
        self.bot_side.pack_propagate(False)
        self._build_bot_sidebar()

        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        heading = tk.Frame(main, bg=PANEL, height=72)
        heading.pack(fill="x")
        heading.pack_propagate(False)
        self.milo_icon = tk.Label(heading, text="M", bg=EDGE, fg=VIOLET,
                                  font=(FONT, 18, "bold"), width=3)
        self.milo_icon.pack(side="left", padx=(20, 12), pady=10)
        label_stack = tk.Frame(heading, bg=PANEL)
        label_stack.pack(side="left", pady=10)
        title_row = tk.Frame(label_stack, bg=PANEL)
        title_row.pack(fill="x", anchor="w")
        self.milo_title = tk.Label(title_row, text="Milo", bg=PANEL, fg=FG,
                                   font=(FONT, 13, "bold"))
        self.milo_title.pack(side="left")
        self.persona_status_label = tk.Label(
            title_row, text="LOCAL ASSISTANT", bg=COLORS["chip"],
            fg=VIOLET, font=(FONT, 7, "bold"), padx=6, pady=2)
        self.persona_status_label.pack(side="left", padx=(9, 0))
        self.connection_label = tk.Label(label_stack, text="Checking local model...",
                                         bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.connection_label.pack(anchor="w", pady=(3, 0))
        quick_button = new_button(
            heading, "Quick chat  Ctrl+Shift+M", self.show_quick_chat)
        quick_button.configure(bg=PANEL_BRIGHT, fg=VIOLET,
                               padx=9, pady=6, font=(FONT, 9))
        quick_button.pack(side="right", padx=(0, 6), pady=17)
        reconnect = new_button(heading, "↻  Reconnect", self.check_connection)
        reconnect.configure(bg=PANEL_BRIGHT, fg=MUTED,
                            padx=12, pady=6, font=(FONT, 9))
        reconnect.pack(side="right", padx=16, pady=17)
        copy_chat = new_button(heading, "Copy chat", self.copy_conversation)
        copy_chat.configure(bg=PANEL_BRIGHT, fg=MUTED,
                            padx=12, pady=6, font=(FONT, 9))
        copy_chat.pack(side="right", padx=(0, 2), pady=17)
        select_text = new_button(
            heading, "Select text", self.chat_view_selection)
        select_text.configure(bg=PANEL_BRIGHT, fg=MUTED,
                              padx=12, pady=6, font=(FONT, 9))
        select_text.pack(side="right", padx=(0, 2), pady=17)

        model_strip = tk.Frame(main, bg=BG, height=37)
        model_strip.pack(fill="x", padx=18, pady=(8, 0))
        self.channel_label = tk.Label(
            model_strip, text="MILO · SHARED CHAT", bg=BG, fg=MUTED,
            font=(FONT, 8, "bold"))
        self.channel_label.pack(side="left", padx=(1, 8))
        tk.Label(model_strip, text="MODEL", bg=BG, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(8, 7))
        self.chat_model_var = tk.StringVar(value="Loading models...")
        self.model_selector = ttk.Combobox(
            model_strip, textvariable=self.chat_model_var, width=29,
            state="readonly", font=(FONT, 9), exportselection=False)
        self.model_selector.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.model_selector.bind("<<ComboboxSelected>>", self._select_chat_model)
        self.chat_model_hint = tk.Label(
            model_strip, text="", bg=BG, fg=MUTED, font=(FONT, 8))
        self.chat_model_hint.pack(side="right")

        # Compact, avatar-led DM timeline. View-only; conversation logic
        # remains in the original MiloConversation and SQLite store.
        self.chat_view = ChatView(main, background=BG)
        self.chat_view.pack(fill="both", expand=True)

        composer_border = tk.Frame(main, bg=EDGE)
        composer_border.pack(fill="x", padx=18, pady=(10, 5))
        composer = tk.Frame(composer_border, bg=PANEL)
        composer.pack(fill="both", expand=True, padx=1, pady=1)
        browse = new_button(
            composer, "＋ Open file", self.choose_local_file)
        browse.configure(
            bg=PANEL, fg=VIOLET, padx=9, pady=7, font=(FONT, 9))
        browse.pack(side="left", padx=(6, 2), pady=7)
        attach = new_button(
            composer, "＋ Attach", self.choose_attachments)
        attach.configure(bg=PANEL, fg=VIOLET,
                         padx=7, pady=7, font=(FONT, 9))
        attach.pack(side="left", padx=(0, 2), pady=7)
        capture = new_button(composer, "▣ Screen", self.capture_screen)
        capture.configure(bg=PANEL, fg=VIOLET,
                          padx=7, pady=7, font=(FONT, 9))
        capture.pack(side="left", padx=(0, 2), pady=7)
        self.entry = tk.Text(
            composer, height=2, wrap="word", font=(FONT, 11),
            bg=PANEL, fg=FG, insertbackground=VIOLET,
            selectbackground=ACCENT, selectforeground=FG,
            relief="flat", highlightthickness=0, borderwidth=0,
            padx=12, pady=10, undo=True)
        self.entry.pack(side="left", fill="both", expand=True, padx=(3, 0), pady=3)
        self.entry.bind("<Return>", self._enter_key)
        self.entry_hint = "Message Milo or @sage / @nova / @forge…"
        self.entry_placeholder = False
        self._show_composer_hint()
        self.entry.bind("<FocusIn>", self._composer_focus_in)
        self.entry.bind("<FocusOut>", self._composer_focus_out)
        self.entry.bind("<Control-a>", self._composer_select_all)

        actions = tk.Frame(composer, bg=PANEL)
        actions.pack(side="right", padx=(6, 9), pady=8)
        self.send_button = new_button(actions, "Send  ➤", self.send, primary=True)
        self.send_button.configure(padx=15, pady=7)
        self.send_button.pack(fill="x", pady=(0, 5))
        self.stop_button = new_button(actions, "Stop", self.stop)
        self.stop_button.configure(bg=PANEL_BRIGHT, fg=MUTED,
                                   font=(FONT, 9), padx=8, pady=5)
        self.stop_button.pack(fill="x")
        self.stop_button.configure(state="disabled")
        attach_row = tk.Frame(main, bg=BG)
        attach_row.pack(fill="x", padx=19, pady=(0, 2))
        self.attachment_status = tk.Label(
            attach_row, text="", bg=BG, fg=VIOLET, font=(FONT, 8),
            anchor="w")
        self.attachment_status.pack(side="left", fill="x", expand=True)
        new_button(attach_row, "Clear attachments",
                   self.clear_attachments).pack(side="right")
        self._install_drop_targets()
        tk.Label(main,
                 text="Private on your device   ·   Enter to send   ·   Shift+Enter for a new line",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(
                     anchor="w", padx=22, pady=(2, 9))

    # ---- Private bot roster, Chat model routing and chat creation ----

    def _installed_model_choices(self):
        """Only model directories that Colibri has validated can be selected."""
        return [
            str(path.relative_to(self.models_root))
            for path in discover_models(self.models_root)
        ]

    def _build_bot_sidebar(self):
        tk.Label(self.bot_side, text="LOCAL ASSISTANTS", bg=SIDEBAR, fg=FG,
                 font=(FONT, 10, "bold")).pack(
                     fill="x", anchor="w", padx=15, pady=(22, 3))
        tk.Label(self.bot_side, text="Private bots · @mentions in shared chat",
                 bg=SIDEBAR, fg=MUTED, font=(FONT, 8),
                 wraplength=195, justify="left").pack(
                     anchor="w", padx=15, pady=(0, 12))
        tk.Frame(self.bot_side, bg=EDGE, height=1).pack(fill="x", padx=10)
        self.bot_canvas = tk.Canvas(
            self.bot_side, bg=SIDEBAR, bd=0, highlightthickness=0)
        bar = tk.Scrollbar(self.bot_side, orient="vertical",
                           command=self.bot_canvas.yview, width=9)
        self.bot_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.bot_canvas.pack(side="top", fill="both", expand=True)
        self.bot_inner = tk.Frame(self.bot_canvas, bg=SIDEBAR)
        self.bot_canvas_id = self.bot_canvas.create_window(
            (0, 0), window=self.bot_inner, anchor="nw")
        self.bot_inner.bind(
            "<Configure>",
            lambda _e: self.bot_canvas.configure(
                scrollregion=self.bot_canvas.bbox("all")))
        self.bot_canvas.bind(
            "<Configure>",
            lambda e: self.bot_canvas.itemconfigure(
                self.bot_canvas_id, width=e.width))
        self.bot_avatar_bank = AvatarBank()
        self.new_bot_button = new_button(
            self.bot_side, "＋   Create specialist bot", self._edit_bot_window)
        self.new_bot_button.configure(bg=SIDEBAR, fg=VIOLET, anchor="w",
                                      font=(FONT, 9), pady=9)
        self.new_bot_button.pack(side="bottom", fill="x", padx=10, pady=(7, 9))
        self.core_docs_button = new_button(
            self.bot_side, "▤   Core Knowledge", lambda: self.documents_window("core"))
        self.core_docs_button.configure(
            bg=SIDEBAR, fg=MUTED, font=(FONT, 9), anchor="w", pady=8)
        self.core_docs_button.pack(side="bottom", fill="x", padx=10)

    def _bot_avatar_path(self, bot):
        if bot is None:
            return (AVATARS / self.config["avatar_milo"]
                    if self.config.get("avatar_milo") else BRAND_AVATAR)
        # User-selected portraits win; bundled art is a read-only fallback.
        if bot.avatar:
            return AVATARS / bot.avatar
        if bot.id in SPECIALIST_HANDLES:
            filename = public_handle(bot.id) + ".webp"
            bundled = self.install_root / "Assets" / "specialists" / filename
            if bundled.is_file():
                return bundled
            source = Path(__file__).resolve().parent / "assets" / "specialists" / filename
            if source.is_file():
                return source
        return None

    def _bot_row(self, bot_id, name, meta, *, avatar=None, disabled=False):
        row = tk.Frame(self.bot_inner, bg=SIDEBAR)
        row.pack(fill="x", padx=(10, 4), pady=(6, 3))
        portrait = avatar_widget(
            row, self.bot_avatar_bank, avatar,
            name, VIOLET if bot_id == "milo" else ORANGE, size=32,
            background=SIDEBAR)
        portrait.pack(side="left", padx=(2, 8))
        col = tk.Frame(row, bg=SIDEBAR)
        col.pack(side="left", fill="both", expand=True)
        title = tk.Label(col, text=name[:20], bg=SIDEBAR,
                         fg=MUTED if disabled else FG, font=(FONT, 9, "bold"),
                         anchor="w")
        title.pack(anchor="w")
        subtitle = tk.Label(col, text=meta[:35], bg=SIDEBAR, fg=MUTED,
                            font=(FONT, 7), anchor="w", wraplength=165,
                            justify="left")
        subtitle.pack(anchor="w", pady=(2, 0))
        if bot_id == "milo":
            action = lambda: self._open_main_room()
        else:
            action = lambda handle=bot_id: self._open_bot_dm(handle)
        if not disabled:
            for widget in (row, portrait, col, title, subtitle):
                widget.bind("<Button-1>", lambda _e, cb=action: cb())
                try:
                    widget.configure(cursor="hand2")
                except tk.TclError:
                    pass
        if bot_id != "milo":
            docs = new_button(
                row, "▤", lambda k=bot_id: self.documents_window(k))
            docs.configure(bg=SIDEBAR, fg=VIOLET, font=(FONT, 10),
                           padx=3, pady=3)
            docs.pack(side="right", padx=(0, 1))
            edit = new_button(row, "⋯", lambda k=bot_id: self._edit_bot_window(k))
            edit.configure(bg=SIDEBAR, fg=MUTED, font=(FONT, 11),
                           padx=3, pady=3)
            edit.pack(side="right", padx=(2, 0))
        return row

    def _refresh_bot_sidebar(self):
        for child in self.bot_inner.winfo_children():
            child.destroy()
        self.bot_avatar_bank = AvatarBank()
        self._bot_row(
            "milo", self.config.get("milo_name", "Milo"),
            "MAIN · @milo", avatar=self._bot_avatar_path(None))
        active_bots = list_bots(self.db.db, include_disabled=True)
        if not active_bots:
            tk.Label(
                self.bot_inner, text="Create your first specialist bot\nfor code, research or another role.",
                bg=SIDEBAR, fg=MUTED, font=(FONT, 8),
                justify="left", wraplength=185).pack(
                    anchor="w", padx=17, pady=(18, 4))
        for bot in active_bots:
            self._bot_row(bot.id, bot.name,
                          ("@" + public_handle(bot.id) + (" · DISABLED" if not bot.active
                                          else " · PRIVATE DM")),
                          avatar=self._bot_avatar_path(bot), disabled=not bot.active)

    def _save_bot_avatar(self, source):
        """Copy a user-selected image into Milo's managed avatar folder."""
        from PIL import Image
        original = Path(source)
        if original.is_symlink() or not original.is_file() or original.stat().st_size > 15_000_000:
            raise ValueError("Avatar must be a regular image under 15 MB")
        with Image.open(original) as image:
            if image.width * image.height > 24_000_000:
                raise ValueError("Avatar dimensions are too large")
            image = image.convert("RGBA")
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            name = uuid.uuid4().hex + ".png"
            destination = AVATARS / name
            image.save(destination, format="PNG", optimize=True)
        return name

    def documents_window(self, scope="core"):
        """Add/remove owner-selected reference files; never upload to a server."""
        if scope != "core" and get_bot(self.db.db, scope) is None:
            messagebox.showerror(
                "Documents", "Save the specialist bot before attaching documents.")
            return None
        title = ("Core Knowledge" if scope == "core" else
                 "Documents · @" + scope)
        window = self._panel(
            title,
            "Core documents are accessible to every bot. Specialist documents "
            "are visible only to their named bot. Files and extracted text stay "
            "on this computer; web research never uploads them.")
        window.geometry("710x570")
        tk.Label(
            window, text="Supported: PDF, DOCX, TXT, MD, CSV, JSON, code, HTML",
            bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(
                anchor="w", padx=24, pady=(9, 7))
        listing = tk.Listbox(
            window, bg=BG, fg=FG, selectbackground=EDGE,
            selectforeground=FG, borderwidth=0, highlightthickness=0,
            font=(FONT, 10), exportselection=False)
        listing.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        current_ids = []
        status = tk.Label(
            window, text="", bg=PANEL, fg=MUTED, font=(FONT, 9),
            wraplength=650, justify="left")
        status.pack(anchor="w", padx=24, pady=(4, 9))

        def refresh():
            records = list_documents(self.db.db, scope)
            listing.delete(0, "end")
            current_ids.clear()
            for record in records:
                current_ids.append(record["id"])
                listing.insert(
                    "end", record["name"] + "  ·  " +
                    str(record["chars"]) + " searchable characters")
            status.configure(text=str(len(records)) +
                             " documents · local copy and extracted text retained")

        def add():
            paths = filedialog.askopenfilenames(
                parent=window, title="Import reference documents",
                filetypes=[("Documents",
                            "*.pdf *.docx *.txt *.md *.markdown *.csv *.json "
                            "*.py *.js *.ts *.yaml *.yml *.rst *.log *.html *.htm"),
                           ("All files", "*.*")])
            if not paths:
                return
            successes = 0
            failed = []
            for name in paths:
                try:
                    import_document(self.db.db, DOCUMENTS, Path(name), scope)
                    successes += 1
                except (OSError, ValueError) as exc:
                    failed.append(Path(name).name + ": " + str(exc)[:110])
            refresh()
            if failed:
                messagebox.showwarning(
                    "Documents",
                    f"Imported {successes} document(s).\n\n" +
                    "\n".join(failed[:8]), parent=window)

        def remove():
            selection = listing.curselection()
            if not selection:
                return
            ident = current_ids[selection[0]]
            if not messagebox.askyesno(
                "Remove document",
                "Remove this local document from " + title +
                " and stop using it for retrieval?",
                parent=window):
                return
            try:
                remove_document(self.db.db, DOCUMENTS, ident, scope)
                refresh()
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Remove document", str(exc)[:220], parent=window)
        actions = tk.Frame(window, bg=PANEL)
        actions.pack(fill="x", padx=24, pady=(0, 18))
        new_button(actions, "＋  Add documents", add, primary=True).pack(
            side="left", padx=(0, 9))
        new_button(actions, "Remove selected", remove).pack(side="left")
        refresh()
        return window

    def _edit_bot_window(self, bot_id=None):
        existing = get_bot(self.db.db, bot_id) if bot_id else None
        panel = self._panel(
            "Edit specialist bot" if existing else "Create specialist bot",
            "Each bot has its own instructions and optional model. "
            "Specialists do not automatically inherit Milo's private memories.")
        panel.geometry("635x780")
        handle = tk.StringVar(
            value=public_handle(existing.id) if existing else "")
        display = tk.StringVar(value=existing.name if existing else "")
        enabled = tk.BooleanVar(value=existing.active if existing else True)
        avatar_path = [existing.avatar if existing else ""]
        labels = [
            ("Handle · used in @mentions (lowercase, no spaces)", handle),
            ("Display name", display)
        ]
        for text, variable in labels:
            tk.Label(panel, text=text, bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(
                anchor="w", padx=24, pady=(11, 3))
            field = tk.Entry(panel, textvariable=variable,
                             bg=BG, fg=FG, relief="flat", font=(FONT, 11))
            field.pack(fill="x", padx=24, ipady=8)
            if existing and variable is handle:
                field.configure(state="disabled")
        tk.Label(panel, text="Instructions · what this bot specialises in",
                 bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(
                     anchor="w", padx=24, pady=(14, 4))
        instructions = tk.Text(
            panel, height=6, wrap="word", bg=BG, fg=FG, insertbackground=FG,
            font=(FONT, 10), padx=9, pady=8, relief="flat", bd=0)
        instructions.pack(fill="both", expand=True, padx=24)
        if existing:
            instructions.insert("1.0", existing.instructions)
        tk.Label(panel, text="Preferred model · optional (chat model may override)",
                 bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(
                     anchor="w", padx=24, pady=(14, 4))
        choices = ["Use chat / Milo default"] + self._installed_model_choices()
        model = tk.StringVar(
            value=(existing.model_location if existing and existing.model_location
                   else choices[0]))
        picker = ttk.Combobox(
            panel, values=choices, state="readonly",
            textvariable=model, font=(FONT, 9))
        picker.pack(fill="x", padx=24, ipady=4)
        tk.Checkbutton(panel, text="Bot enabled", variable=enabled,
                       bg=PANEL, fg=FG, selectcolor=BG,
                       activebackground=PANEL, activeforeground=FG).pack(
                           anchor="w", padx=24, pady=(10, 5))
        file_hint = tk.Label(
            panel, text=("Using custom avatar" if avatar_path[0]
                         else "No avatar · uses initial badge"),
            bg=PANEL, fg=MUTED, font=(FONT, 9))
        file_hint.pack(anchor="w", padx=24)
        def choose_avatar():
            path = filedialog.askopenfilename(
                title="Select bot avatar",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif")],
                parent=panel)
            if not path:
                return
            try:
                avatar_path[0] = self._save_bot_avatar(path)
                file_hint.configure(text="Custom avatar saved locally")
            except Exception as exc:
                messagebox.showerror("Bot avatar", str(exc)[:280], parent=panel)
        new_button(panel, "Choose avatar image", choose_avatar).pack(
            anchor="w", padx=24, pady=(7, 4))
        permission = tk.BooleanVar(
            value=web_enabled(self.db.db, existing.id) if existing else False)
        tk.Checkbutton(
            panel, text="Allow web research (each search needs confirmation)",
            variable=permission, bg=PANEL, fg=FG, selectcolor=BG,
            activebackground=PANEL, activeforeground=FG,
            font=(FONT, 9)).pack(anchor="w", padx=24, pady=(8, 2))
        if existing:
            new_button(
                panel, "Manage specialist documents",
                lambda k=existing.id: self.documents_window(k)).pack(
                    anchor="w", padx=24, pady=(4, 3))
        def save():
            try:
                handle_id = existing.id if existing else handle.get().strip().lower()
                raw_instructions = instructions.get("1.0", "end-1c").strip()
                chosen_model = ("" if model.get() == choices[0] else model.get())
                if chosen_model and chosen_model not in self._installed_model_choices():
                    raise ValueError("Preferred model is no longer installed")
                profile = Bot(
                    handle_id, display.get().strip(), raw_instructions,
                    chosen_model, avatar_path[0], bool(enabled.get()))
                save_bot(self.db.db, profile)
                set_web_enabled(self.db.db, profile.id, permission.get())
                panel.destroy()
                self._refresh_bot_sidebar()
                self._refresh_chat_context()
            except (ValueError, sqlite3.Error) as exc:
                messagebox.showerror("Bot settings", str(exc)[:320], parent=panel)
        new_button(panel, "Save bot", save, primary=True).pack(
            fill="x", padx=24, pady=(14, 18))

    def _open_main_room(self):
        if self.busy:
            return
        if chat_settings(self.db.db, self.current_chat).kind == "room":
            return
        rows = self.db.db.execute(
            "SELECT c.chat_id FROM conversations c "
            "LEFT JOIN chat_settings s ON c.chat_id=s.chat_id "
            "WHERE s.chat_id IS NULL OR s.kind='room' "
            "GROUP BY c.chat_id ORDER BY MAX(c.id) DESC LIMIT 1").fetchall()
        if rows:
            self._select_chat(rows[0][0])
        else:
            self._start_chat("room", "milo", "")

    def _open_bot_dm(self, bot_id):
        if self.busy:
            return
        bot = get_bot(self.db.db, bot_id)
        if bot is None or not bot.active:
            messagebox.showinfo(
                "Specialist bot", "This bot is disabled; edit it to enable messaging.")
            return
        rows = self.db.db.execute(
            "SELECT chat_id FROM chat_settings WHERE kind='private' "
            "AND bot_id=? ORDER BY rowid DESC LIMIT 1", (bot_id,)).fetchall()
        if rows:
            self._select_chat(rows[0][0])
        else:
            self._start_chat("private", bot_id, "")

    def _start_chat(self, kind, bot_id, model_location):
        if self.busy:
            return
        fresh = uuid.uuid4().hex
        set_chat_settings(
            self.db.db, ChatSettings(fresh, kind, bot_id, model_location))
        self.current_chat = fresh
        self.config["current_chat"] = fresh
        save_config(self.config)
        self._refresh_chat_list()
        self._load_chat()
        self._refresh_chat_context()
        self._ensure_chat_model()

    def _model_label(self, selected):
        return selected.split("/")[-1].split("\\")[-1][:38] if selected else "Use default"

    def _refresh_chat_context(self):
        if not hasattr(self, "chat_model_var"):
            return
        settings = chat_settings(self.db.db, self.current_chat)
        bot = (get_bot(self.db.db, settings.bot_id)
               if settings.kind == "private" and settings.bot_id != "milo" else None)
        if bot is not None:
            self.milo_title.configure(text=bot.name)
            self.persona_status_label.configure(text="SPECIALIST BOT", fg=ORANGE)
            self.channel_label.configure(text="PRIVATE · @" + public_handle(bot.id))
            image_path = self._bot_avatar_path(bot)
            self.chat_view.names["assistant"] = bot.name
            self.chat_view.paths["assistant"] = image_path
            self._header_bot_icon(image_path, bot.name)
        else:
            self.milo_title.configure(text=self.config.get("milo_name", "Milo"))
            restored = loaded_persona(self.db.db) is not None
            self.persona_status_label.configure(
                text="RESTORED PROFILE" if restored else "BASE PROFILE",
                fg=COLORS["green"] if restored else VIOLET)
            self.channel_label.configure(
                text="SHARED · @MENTION BOTS" if settings.kind == "room"
                else "PRIVATE · @milo")
            self.chat_view.names["assistant"] = self.config.get("milo_name", "Milo")
            self.chat_view.paths["assistant"] = self._bot_avatar_path(None)
            self._avatar(self.milo_icon, "avatar_milo", self.config["milo_name"])
        installed = self._installed_model_choices()
        default_display = "Auto · bot/Milo default"
        self.model_option_map = {default_display: ""}
        for index, path in enumerate(installed):
            label = self._model_label(path)
            if label in self.model_option_map:
                label += " #" + str(index + 1)
            self.model_option_map[label] = path
        options = list(self.model_option_map)
        self.model_selector.configure(values=options,
                                     state="readonly" if installed else "disabled")
        current = next(
            (label for label, value in self.model_option_map.items()
             if value == settings.model_location), default_display)
        self.chat_model_var.set(current)
        current_bot = bot if bot is not None else None
        effective = model_for_turn(
            settings, current_bot, self.config.get("model_location", ""))
        self.chat_model_hint.configure(
            text=self._model_label(effective) if effective else "No model")

    def _header_bot_icon(self, path, fallback):
        if not path or not Path(path).is_file():
            self.milo_icon.configure(image="", text=fallback[:1].upper(),
                                     width=3, height=1)
            return
        try:
            # Pillow loads bundled WebP portraits; native Tk PhotoImage cannot.
            from PIL import Image, ImageTk
            with Image.open(path) as source:
                image = source.convert("RGBA")
                image.thumbnail((50, 50), Image.Resampling.LANCZOS)
                picture = ImageTk.PhotoImage(image)
            self.images["bot_header"] = picture
            self.milo_icon.configure(image=picture, text="",
                                     width=50, height=50)
        except (OSError, tk.TclError):
            self.milo_icon.configure(image="", text=fallback[:1].upper())

    def _select_chat_model(self, _event=None):
        if self.busy or self.engine_starting:
            messagebox.showinfo(
                "Model", "Finish the current response or model load before switching.")
            self._refresh_chat_context()
            return
        model_location = self.model_option_map.get(self.chat_model_var.get())
        if model_location is None:
            self._refresh_chat_context()
            return
        settings = chat_settings(self.db.db, self.current_chat)
        set_chat_settings(
            self.db.db, ChatSettings(
                self.current_chat, settings.kind, settings.bot_id, model_location))
        self._refresh_chat_context()
        self._ensure_chat_model()

    def _ensure_chat_model(self):
        settings = chat_settings(self.db.db, self.current_chat)
        bot = (get_bot(self.db.db, settings.bot_id)
               if settings.kind == "private" and settings.bot_id != "milo" else None)
        selected = model_for_turn(
            settings, bot, self.config.get("model_location", ""))
        if self.engine_starting:
            return
        if selected:
            path = chosen_model(self.models_root, selected)
            if path is None:
                self.connection_label.configure(
                    text="Model not installed · Select a different checkpoint", fg=ORANGE)
                self.model_ready = False
                return
            if self.active_model_location == selected and self.model_ready:
                return
            self._launch_managed_model(path)
        elif not self.model_ready:
            self.check_connection()

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        self.advanced_toggle.configure(
            text=("⚙  Setup & modules  ▾" if self.advanced_open
                  else "⚙  Setup & modules  ▸"))
        if self.advanced_open:
            self.advanced_controls.pack(fill="x", pady=(5, 0))
        else:
            self.advanced_controls.pack_forget()

    def _show_composer_hint(self):
        if not self.entry.get("1.0", "end-1c").strip():
            self.entry.insert("1.0", self.entry_hint)
            self.entry.configure(fg=MUTED)
            self.entry_placeholder = True

    def _composer_focus_in(self, _event):
        if self.entry_placeholder:
            self.entry.delete("1.0", "end")
            self.entry.configure(fg=FG)
            self.entry_placeholder = False

    def _composer_focus_out(self, _event):
        if not self.entry.get("1.0", "end-1c").strip():
            self._show_composer_hint()

    def _composer_select_all(self, _event):
        if not self.entry_placeholder:
            self.entry.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _enter_key(self, event):
        if event.state & 0x1:  # Shift+Enter
            return None
        self.send()
        return "break"

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _restore_service_status(self):
        if self.model_ready and self.models:
            self.connection_label.configure(
                text="● Bundled Colibri  •  " + self.models[0],
                fg="#9fdfb4")
        elif self.engine_starting:
            self.connection_label.configure(
                text="● Loading local model…", fg=ORANGE)
        else:
            self.connection_label.configure(
                text="● Model offline · local tools available", fg=MUTED)

    def _append(self, text: str, tag: str = "notice"):
        # Legacy helper retained only for non-message status notices.
        if text.strip():
            self.chat_view.notice(text.strip())

    def _display_turn(self, role: str, text: str, timestamp: str = "",
                      *, author_id=None, author_name=None, avatar=None):
        return self.chat_view.add_message(
            role, text, timestamp, author_id=author_id,
            author_name=author_name, avatar=avatar)

    def _load_chat(self):
        self.chat_view.clear()
        self.pending_message = None
        rows = attributed_messages(self.db.db, self.current_chat, limit=1000)
        if not rows:
            chat_mode = chat_settings(self.db.db, self.current_chat)
            if chat_mode.kind == "private" and chat_mode.bot_id != "milo":
                archive_notice = (
                    "Private chat with @" + public_handle(chat_mode.bot_id) + ". "
                    "This specialist does not inherit Milo's archived "
                    "relationship memories or private memory notes.\n"
                )
            else:
                archive_notice = (
                    "The original Milo conversation archive is available for "
                    "evidence-backed recall.\n"
                    if loaded_persona(self.db.db) is not None else
                    "Your original Milo personality has not been imported yet. "
                    "Choose Restore Milo personality in the sidebar.\n"
                )
            self._append(
                "This conversation is stored only on your computer.\n"
                + archive_notice + "Start a message below.", "notice"
            )
        for role, text, date, author_id, author_name, avatar in rows:
            # Historical provenance remains untouched in SQLite. Display the
            # current specialist identity and bundled art where appropriate.
            display_bot = (get_bot(self.db.db, author_id)
                           if role == "assistant" and author_id in SPECIALIST_HANDLES
                           else None)
            self._display_turn(
                role, text, date,
                author_id=author_id if role == "assistant" else None,
                author_name=(display_bot.name if display_bot else author_name)
                            if role == "assistant" else None,
                avatar=(AVATARS / avatar if avatar else
                        self._bot_avatar_path(display_bot)
                        if display_bot else None))
        self.chat_view.show_latest()

    def _refresh_chat_list(self):
        self.chats = []
        rows = self.db.db.execute(
            "SELECT c.chat_id, COALESCE((SELECT substr(u.content, 1, 32) "
            "FROM conversations u WHERE u.chat_id=c.chat_id AND u.role='user' "
            "ORDER BY u.id LIMIT 1), 'Conversation'), "
            "COALESCE(s.kind, 'room'), COALESCE(s.bot_id,'milo') "
            "FROM conversations c LEFT JOIN chat_settings s ON c.chat_id=s.chat_id "
            "GROUP BY c.chat_id ORDER BY MAX(c.id) DESC LIMIT 100"
        ).fetchall()
        chat_avatars = {}
        for chat_id, title, kind, bot_id in rows:
            if kind == "private":
                bot = get_bot(self.db.db, bot_id) if bot_id != "milo" else None
                if bot:
                    chat_avatars[chat_id] = self._bot_avatar_path(bot)
                title = "@" + public_handle(bot_id) + " · " + title
            else:
                title = "# " + title
            self.chats.append((chat_id, title))
        if self.current_chat not in {x[0] for x in self.chats}:
            context = chat_settings(self.db.db, self.current_chat)
            title = ("DM @" + public_handle(context.bot_id) if context.kind == "private"
                     else "# New conversation")
            self.chats.insert(0, (self.current_chat, title))
            if context.kind == "private" and context.bot_id != "milo":
                bot = get_bot(self.db.db, context.bot_id)
                if bot:
                    chat_avatars[self.current_chat] = self._bot_avatar_path(bot)
        self.chat_list.set_chats(
            self.chats, self.current_chat, avatar_by_chat=chat_avatars)

    def _select_chat(self, selected):
        if self.busy:
            return
        if selected != self.current_chat:
            self.current_chat = selected
            self.config["current_chat"] = selected
            save_config(self.config)
            self._refresh_chat_list()
            self._load_chat()
            self._refresh_chat_context()
            self._ensure_chat_model()

    def search_conversations(self):
        """Privacy-preserving local history search, no model or web request."""
        if self.busy:
            messagebox.showinfo(
                "Chat search", "Finish the current response before opening a chat.")
            return
        panel = self._panel(
            "Find a conversation",
            "Search your local conversation history. Nothing is uploaded.")
        panel.geometry("760x520")
        term = tk.StringVar()
        field = tk.Entry(
            panel, textvariable=term, bg=BG, fg=FG,
            insertbackground=VIOLET, relief="flat", font=(FONT, 11))
        field.pack(fill="x", padx=22, pady=(15, 9), ipady=8)
        found = tk.Listbox(
            panel, bg=BG, fg=FG, selectbackground=EDGE,
            font=(FONT, 10), relief="flat", activestyle="none",
            exportselection=False)
        found.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        ids = []
        def search(*_args):
            pattern = term.get().strip()
            found.delete(0, "end")
            ids.clear()
            if len(pattern) < 2:
                return
            escaped = (pattern[:120].replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_"))
            rows = self.db.db.execute(
                "SELECT chat_id, substr(content,1,110) FROM conversations "
                "WHERE content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 80",
                ("%" + escaped + "%",)).fetchall()
            seen = set()
            for chat_id, excerpt in rows:
                if chat_id in seen:
                    continue
                seen.add(chat_id)
                ids.append(chat_id)
                single_line = " ".join(excerpt.split())
                found.insert("end", single_line[:90])
        def open_result(_event=None):
            index = found.curselection()
            if not index or index[0] >= len(ids):
                return
            chat_id = ids[index[0]]
            panel.destroy()
            self._select_chat(chat_id)
        term.trace_add("write", search)
        found.bind("<Double-Button-1>", open_result)
        found.bind("<Return>", open_result)
        new_button(panel, "Open selected conversation", open_result,
                   primary=True).pack(fill="x", padx=22, pady=(0, 17))
        field.focus_set()
        return panel

    def delete_chat(self, chat_id):
        """Explicit destructive action only; don't touch historical archives."""
        if self.busy or self.engine_starting:
            messagebox.showinfo(
                "Delete chat", "Finish the active message or model change first.")
            return
        title = next((name for ident, name in self.chats if ident == chat_id),
                     "this conversation")
        if not messagebox.askyesno(
            "Delete conversation",
            "Permanently delete the conversation:\n\n" + title +
            "\n\nIts messages cannot be restored unless you have a backup. "
            "Your original Milo history, approved memories and other chats "
            "will remain untouched."):
            return
        try:
            delete_conversation(self.db.db, chat_id)
            if self.current_chat == chat_id:
                remaining = self.db.db.execute(
                    "SELECT chat_id FROM conversations GROUP BY chat_id "
                    "ORDER BY MAX(id) DESC LIMIT 1").fetchone()
                if remaining:
                    self.current_chat = remaining[0]
                else:
                    self.current_chat = uuid.uuid4().hex
                    set_chat_settings(
                        self.db.db,
                        ChatSettings(self.current_chat, "room", "milo", ""))
                self.config["current_chat"] = self.current_chat
                save_config(self.config)
            self._refresh_chat_list()
            self._load_chat()
            self._refresh_chat_context()
            self._ensure_chat_model()
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Delete conversation", str(exc)[:260])

    def copy_conversation(self):
        """Copy complete transcript with author attribution, without a model call."""
        try:
            rows = attributed_messages(self.db.db, self.current_chat, limit=5000)
            if not rows:
                messagebox.showinfo(
                    "Copy chat", "This conversation has no saved messages yet.")
                return
            lines = []
            for role, content, date, bot_id, bot_name, _avatar in rows:
                speaker = (self.config.get("user_name", "You")
                           if role == "user" else bot_name)
                lines.append(f"{speaker} ({date}):\n{content}")
            transcript = "\n\n".join(lines)
            if len(transcript) > 2_000_000:
                messagebox.showerror(
                    "Copy chat", "This conversation is too large for the clipboard.")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(transcript)
        except (sqlite3.Error, tk.TclError) as exc:
            messagebox.showerror("Copy chat", str(exc)[:220])

    def new_chat(self):
        if self.busy or self.engine_starting:
            messagebox.showinfo(
                "Milo", "Finish the current response/model load before creating a chat.")
            return
        panel = self._panel(
            "New conversation",
            "Choose a shared Milo room or a private DM with one of your bots. "
            "Select the model for this conversation.")
        panel.geometry("565x365")
        bots = list_bots(self.db.db)
        room = "Milo · shared chat"
        type_options = [room] + [
            f"Private · @{bot.id} ({bot.name})" for bot in bots]
        tk.Label(panel, text="Conversation", bg=PANEL, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w", padx=24, pady=(14, 6))
        type_var = tk.StringVar(value=room)
        chooser = ttk.Combobox(
            panel, textvariable=type_var, values=type_options,
            state="readonly", font=(FONT, 10))
        chooser.pack(fill="x", padx=24, ipady=5)
        tk.Label(panel, text="Local model for this chat", bg=PANEL, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w", padx=24, pady=(17, 6))
        paths = self._installed_model_choices()
        labels = ["Auto · use bot / Milo default"] + paths
        configured = self.config.get("model_location", "")
        model_var = tk.StringVar(value=(
            configured if configured in paths else
            paths[0] if paths else labels[0]))
        model_chooser = ttk.Combobox(
            panel, textvariable=model_var,
            values=labels, state="readonly" if paths else "disabled",
            font=(FONT, 10))
        model_chooser.pack(fill="x", padx=24, ipady=5)
        tk.Label(
            panel,
            text="One local Colibri model runs at a time. Switching chats to "
                 "different checkpoints may require a brief reload; no models are removed.",
            bg=PANEL, fg=MUTED, justify="left", wraplength=510,
            font=(FONT, 9)).pack(anchor="w", padx=24, pady=(13, 4))
        def create():
            choice = type_var.get()
            if choice == room:
                kind, bot_id = "room", "milo"
            else:
                index = type_options.index(choice) - 1
                if not 0 <= index < len(bots):
                    return
                kind, bot_id = "private", bots[index].id
            chosen = "" if model_var.get() == labels[0] else model_var.get()
            if chosen and chosen not in self._installed_model_choices():
                messagebox.showerror("Model", "Selected model is no longer installed.", parent=panel)
                return
            panel.destroy()
            self._start_chat(kind, bot_id, chosen)
        new_button(panel, "Create conversation", create, primary=True).pack(
            fill="x", padx=24, pady=(18, 12))


    def _install_drop_targets(self):
        """Native Windows Explorer drops; manual picker remains fallback."""
        self.drop_ready = False
        for widget in (self.root, self.chat_view.canvas, self.entry):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop_files)
                self.drop_ready = True
            except (AttributeError, tk.TclError):
                continue
        self._update_attachment_status()

    def _on_drop_files(self, event):
        try:
            paths = parse_dropped_paths(self.root, event.data)
            self._queue_attachments(paths)
        except (AttachmentError, OSError, ValueError) as exc:
            messagebox.showerror("Attach files", str(exc)[:250], parent=self.root)

    def _queue_attachments(self, paths):
        """Inspect locally; don't transmit files anywhere on selection."""
        checked = list(self.queued_attachments)
        if len(paths) + len(checked) > MAX_ATTACHMENTS:
            raise AttachmentError("Attach up to five files per message")
        for source in paths:
            new = inspect_attachment(Path(source))
            if new.path not in {a.path for a in checked}:
                checked.append(new)
        self.queued_attachments = checked
        self._update_attachment_status()

    def _update_attachment_status(self):
        if not hasattr(self, "attachment_status"):
            return
        if self.queued_attachments:
            labels = ", ".join(a.name for a in self.queued_attachments)
            self.attachment_status.configure(
                text="Attached · " + labels[:140] +
                ("…" if len(labels) > 140 else ""))
        else:
            self.attachment_status.configure(
                text=("Drop images/documents here · local files only"
                      if getattr(self, "drop_ready", False)
                      else "Attach images/documents with ＋ Attach"))

    def choose_attachments(self):
        files = filedialog.askopenfilenames(
            title="Attach images or documents for this chat",
            filetypes=[
                ("Images and documents",
                 "*.png *.jpg *.jpeg *.webp *.gif *.bmp *.pdf *.docx "
                 "*.txt *.md *.markdown *.rst *.csv *.json *.py *.js "
                 "*.ts *.yaml *.yml *.log *.html *.htm"),
                ("All files", "*.*"),
            ], parent=self.root)
        if files:
            try:
                self._queue_attachments([Path(file) for file in files])
            except (AttachmentError, OSError, ValueError) as exc:
                messagebox.showerror("Attach files", str(exc)[:250],
                                     parent=self.root)

    def clear_attachments(self):
        self.queued_attachments = []
        # Screenshots are private, short-lived scratch files. Staged copies
        # remain in the conversation's attachment store if already sent.
        for path in self.session_captures:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self.session_captures.clear()
        self._update_attachment_status()

    # ---- Global quick chat, consented screen attachment and activity ----

    def _audit(self, tool: str, outcome: str) -> None:
        """Fixed-label event only. Do not put user prompts or paths in this log."""
        try:
            record_activity(self.db.db, tool, outcome)
        except sqlite3.Error:
            pass

    def _tool_begin(self, tool: str) -> None:
        self.active_tool = tool
        self._audit(tool, "started")

    def _tool_finish(self, outcome: str) -> None:
        tool = self.active_tool
        self.active_tool = None
        if tool:
            self._audit(tool, outcome)

    def show_quick_chat(self):
        """Small, separate composition window; same chat and model as main UI."""
        existing = getattr(self, "quick_popup", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        panel = tk.Toplevel(self.root)
        self.quick_popup = panel
        panel.title("Quick chat · Milo")
        panel.configure(bg=BG)
        panel.geometry("560x185")
        panel.minsize(420, 160)
        panel.attributes("-topmost", True)
        tk.Label(panel, text="MILO · QUICK CHAT", bg=BG, fg=VIOLET,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=18, pady=(14, 5))
        text_entry = tk.Text(panel, height=3, wrap="word", font=(FONT, 11),
                             bg=PANEL, fg=FG, insertbackground=VIOLET,
                             relief="flat", padx=10, pady=8)
        text_entry.pack(fill="both", expand=True, padx=18, pady=(0, 6))
        footer = tk.Frame(panel, bg=BG)
        footer.pack(fill="x", padx=18, pady=(0, 12))

        def submit(_event=None):
            prompt = text_entry.get("1.0", "end-1c").strip()
            if not prompt:
                return "break"
            if self.busy:
                messagebox.showinfo("Milo is responding",
                    "Finish or stop the current response first. "
                    "Your quick-chat text will stay here.", parent=panel)
                return "break"
            if len(prompt) > 20000:
                messagebox.showerror("Message too long",
                                     "Keep messages below 20,000 characters.",
                                     parent=panel)
                return "break"
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.entry.delete("1.0", "end")
            self.entry_placeholder = False
            self.entry.configure(fg=FG)
            self.entry.insert("1.0", prompt)
            panel.destroy()
            self.quick_popup = None
            self._audit("quick_chat", "completed")
            self.send()
            return "break"

        text_entry.bind("<Return>",
            lambda event: None if event.state & 0x1 else submit(event))
        panel.bind("<Escape>", lambda _event: panel.destroy())
        tk.Label(footer, text="Enter to send · Shift+Enter for newline",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(side="left")
        new_button(footer, "Send to Milo", submit, primary=True).pack(side="right")
        panel.after(50, text_entry.focus_set)

    def capture_screen(self):
        """Every capture needs consent; no permanent screen-watch capability."""
        if len(self.queued_attachments) >= MAX_ATTACHMENTS:
            messagebox.showinfo("Attachments full",
                "Clear an attachment before capturing the screen.",
                parent=self.root)
            return
        if not messagebox.askyesno(
                "Share a one-time screenshot",
                "Capture your PRIMARY screen now?\n\n"
                "Milo will temporarily hide, capture the display once, "
                "then show the resulting image for review. It will be "
                "queued as a LOCAL chat attachment, not uploaded.\n\n"
                "Screenshots can contain private information. "
                "The current text-only Colibri model cannot interpret pixels.",
                parent=self.root):
            self._audit("screen_capture", "denied")
            return
        self._audit("screen_capture", "approved")
        self.root.withdraw()
        self.root.after(320, self._capture_screen_after_hiding)

    def _capture_screen_after_hiding(self):
        image_path = None
        failure = None
        try:
            image_path = capture_primary_screen(DATA_DIR / "captures")
            self._queue_attachments([image_path])
            self.session_captures.append(image_path)
            self._audit("screen_capture", "completed")
        except (ScreenCaptureError, OSError, ValueError) as exc:
            if image_path is not None:
                image_path.unlink(missing_ok=True)
            self._audit("screen_capture", "failed")
            failure = str(exc)[:260]
        finally:
            self.root.deiconify()
            self.root.lift()
        if failure:
            messagebox.showerror("Screen capture", failure,
                                 parent=self.root)
        if image_path is not None and image_path.exists():
            try:
                open_preview(self.root, image_path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Screenshot preview", str(exc)[:220],
                                     parent=self.root)

    def windows_tools_window(self):
        panel = self._panel(
            "Windows tools",
            "Fixed, verified Windows Settings shortcuts. "
            "Every open action asks permission. No arbitrary commands.")
        tk.Label(
            panel,
            text="Choose a settings page to open. Milo will show the exact "
                 "Windows URI before launching it, and won't change any settings.",
            bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=490,
            justify="left").pack(anchor="w", padx=24, pady=(10, 15))
        for action in available_actions():
            button = new_button(
                panel, action.title,
                lambda selected=action: self._perform_windows_action(selected))
            button.configure(anchor="w", padx=16, pady=9)
            button.pack(fill="x", padx=24, pady=4)
        return panel

    def _perform_windows_action(self, action: WindowsAction, chat_context=None):
        if self.busy:
            messagebox.showinfo("Milo is responding",
                                "Finish the current response first.",
                                parent=self.root)
            return
        if not messagebox.askyesno(
                "Approve Windows action",
                "Open " + action.title + "?\n\n"
                "Exact local Windows Settings URI:\n" + action.uri +
                "\n\nMilo won't click, type or change any settings.",
                parent=self.root):
            self._audit("windows_settings", "denied")
            return
        self._audit("windows_settings", "approved")
        try:
            execute_action(action, approved=True)
            reply = "Opened Windows " + action.title + ". No settings were changed."
            outcome = "completed"
        except (OSError, ValueError) as exc:
            reply = "I couldn't open " + action.title + ": " + str(exc)[:220]
            outcome = "failed"
        self._audit("windows_settings", outcome)
        if chat_context is None:
            if outcome == "failed":
                messagebox.showerror("Windows tools", reply, parent=self.root)
            else:
                messagebox.showinfo("Windows tools", reply, parent=self.root)
            return
        chat_id, prompt, target_id, author_name, author_avatar, shared = chat_context
        self._begin_tool_turn(chat_id, prompt, target_id,
                              author_name, author_avatar)
        self.events.put((
            "windows_action_result", chat_id, prompt, reply, target_id,
            author_name, "", shared))

    def activity_window(self):
        window = self._panel("Tool activity",
            "Local history of approvals, completions and failures. "
            "Search terms, filenames, prompts and screenshots are not logged.")
        listing = tk.Text(window, height=15, wrap="none",
                          bg=BG, fg=FG, font=("Consolas", 9),
                          relief="flat", padx=12, pady=9)
        listing.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        def refresh():
            entries = list_activity(self.db.db)
            listing.configure(state="normal")
            listing.delete("1.0", "end")
            if not entries:
                listing.insert("end", "No tool events recorded yet.")
            else:
                for timestamp, tool, outcome in entries:
                    listing.insert(
                        "end", timestamp[:19].replace("T", " ") + " UTC · " +
                        tool.replace("_", " ") + " · " + outcome + "\n")
            listing.configure(state="disabled")
        controls = tk.Frame(window, bg=PANEL)
        controls.pack(fill="x", padx=20, pady=(0, 12))
        new_button(controls, "Refresh", refresh).pack(side="left")
        def clear():
            if messagebox.askyesno(
                    "Delete activity history",
                    "Delete the stored tool event history? "
                    "This does not delete your chats or memories.",
                    parent=window):
                clear_activity(self.db.db)
                refresh()
        new_button(controls, "Clear activity history", clear).pack(side="right")
        refresh()
        return window

    def chat_view_selection(self):
        """One continuous selectable text area spanning all visible messages."""
        return self.chat_view.selectable_view()

    def choose_local_file(self):
        """Explicit file picker; preview doesn't require LLM inference."""
        name = filedialog.askopenfilename(
            title="Open a local image or document in Milo",
            filetypes=[
                ("Images and documents",
                 "*.png *.jpg *.jpeg *.webp *.gif *.bmp *.pdf *.docx "
                 "*.txt *.md *.markdown *.rst *.csv *.json *.py *.js *.ts "
                 "*.yaml *.yml *.log *.html *.htm"),
                ("All files", "*.*"),
            ], parent=self.root)
        if name:
            try:
                return open_preview(self.root, validate_preview_path(Path(name)))
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Milo file preview", str(exc)[:300], parent=self.root)

    def _begin_tool_turn(self, chat_id, prompt, target_id, author_name, avatar):
        """Show deterministic tool activity with the correct real bot identity."""
        self.entry.delete("1.0", "end")
        self.entry_placeholder = False
        self.entry.configure(fg=FG)
        self._display_turn("user", prompt, datetime.now().astimezone())
        self.pending_message = self.chat_view.add_message(
            "assistant", "", datetime.now().astimezone(),
            streaming=True, author_id=target_id,
            author_name=author_name, avatar=avatar)
        self.cancel.clear()
        self._set_busy(True)

    @staticmethod
    def _save_direct_turn(chat_id, prompt, reply, target_id, author_name, avatar_name,
                          shared_room):
        from colibri.milo_core.bots import append_attributed_turn
        with LocalMemoryStore(DB_PATH) as store:
            if target_id != "milo" or shared_room:
                append_attributed_turn(
                    store.db, chat_id, prompt, reply,
                    bot_id=target_id, bot_name=author_name,
                    avatar=avatar_name)
            else:
                store.append_turn(chat_id, prompt, reply)

    def send(self):
        if self.busy:
            return
        prompt = ("" if self.entry_placeholder else
                  self.entry.get("1.0", "end-1c").strip())
        if not prompt:
            return
        if len(prompt) > 20000:
            messagebox.showerror("Message too long", "Keep messages below 20,000 characters.")
            return
        settings = chat_settings(self.db.db, self.current_chat)
        try:
            target_id, clean_prompt = resolve_target(self.db.db, prompt, settings)
        except ValueError as exc:
            messagebox.showerror("Mentioned bot", str(exc)[:260])
            return
        bot = get_bot(self.db.db, target_id) if target_id != "milo" else None
        author_name = bot.name if bot else self.config.get("milo_name", "Milo")
        author_avatar = self._bot_avatar_path(bot)
        model_location = model_for_turn(
            settings, bot, self.config.get("model_location", ""))

        # A small Windows action allowlist is handled without model inference.
        # The user sees the exact operation and must approve execution.
        requested_windows = (parse_action_request(clean_prompt)
                             if target_id == "milo" and
                             module_enabled(DATA_DIR, "files") else None)
        if requested_windows is not None:
            self._perform_windows_action(
                requested_windows,
                chat_context=(self.current_chat, prompt, target_id,
                              author_name, author_avatar,
                              settings.kind == "room"))
            return

        # A filename-specific "open" request uses deterministic local file
        # resolution. It NEVER invokes the model or executes the named file.
        requested_open = (parse_open_request(clean_prompt)
                          if target_id == "milo" and
                          module_enabled(DATA_DIR, "files") else None)
        if requested_open is not None:
            self._tool_begin("local_preview")
            chat_id = self.current_chat
            self._begin_tool_turn(chat_id, prompt, target_id,
                                  author_name, author_avatar)
            def locate_worker():
                try:
                    found = locate_file(requested_open)
                    if self.cancel.is_set():
                        self.events.put(("cancelled", chat_id))
                    else:
                        self.events.put((
                            "file_preview_ready", chat_id, prompt,
                            str(found), target_id, author_name, "",
                            settings.kind == "room"))
                except (OSError, ValueError) as exc:
                    self.events.put((
                        "tool_error", chat_id, prompt,
                        "I couldn't open that file: " + str(exc)[:350],
                        target_id, author_name, "",
                        settings.kind == "room"))
            threading.Thread(
                target=locate_worker, name="milo-local-preview",
                daemon=True).start()
            return

        # Milo's local deterministic file tools remain a separate, read-only
        # path. A specialist bot does not silently inherit Milo's tools.
        requested_files = (parse_file_query(clean_prompt)
                           if target_id == "milo" and module_enabled(DATA_DIR, "files")
                           else None)
        if requested_files is not None:
            self._tool_begin("local_file_search")
            chat_id = self.current_chat
            self.entry.delete("1.0", "end")
            self.entry_placeholder = False
            self.entry.configure(fg=FG)
            self._display_turn("user", prompt, datetime.now().astimezone())
            self.pending_message = self.chat_view.add_message(
                "assistant", "", datetime.now().astimezone(), streaming=True,
                author_id="milo", author_name=author_name, avatar=author_avatar)
            self._set_busy(True)
            def file_worker():
                try:
                    reply = answer_file_query(requested_files)
                    if len(reply) > 19000:
                        reply = reply[:18800] + "\n[Results truncated]"
                    with LocalMemoryStore(DB_PATH) as store:
                        store.append_turn(chat_id, prompt, reply)
                    self.events.put(("local_tool_done", chat_id, reply))
                except Exception as exc:
                    self.events.put(("error", chat_id, "Local file listing failed: "
                                     + str(exc)[:250]))
            threading.Thread(
                target=file_worker, name="milo-local-files", daemon=True).start()
            return

        # Explicit web requests route through a real search tool, even if no
        # specialist exists. Milo's own web capability requires this exact
        # per-request confirmation; specialists additionally need their toggle.
        if (wants_research(clean_prompt) or
                (target_id == "research" and len(clean_prompt.strip()) >= 3)):
            if target_id != "milo" and not web_enabled(self.db.db, target_id):
                messagebox.showinfo(
                    "Internet research disabled",
                    "Web research is disabled for @" + target_id +
                    ". Enable it in the specialist bot editor. "
                    "Every actual search will still require approval.")
                return
            try:
                query = (query_for_research(clean_prompt)
                         if wants_research(clean_prompt)
                         else clean_prompt.strip()[:240])
            except ResearchError as exc:
                messagebox.showerror("Web research", str(exc)[:260])
                return
            if not messagebox.askyesno(
                "Search public web for @" + target_id,
                "Search the public web for:\n\n" + query +
                "\n\nOnly this topic will be sent to the search provider. "
                "Milo will never include your uploaded files, private memory "
                "or previous conversations in the web request. "
                "Read-only HTTPS sources only. Continue?"):
                self._audit("web_research", "denied")
                return
            self._audit("web_research", "approved")
            self._tool_begin("web_research")
            chat_id = self.current_chat
            available_model = (
                self.model_ready and bool(self.models) and
                (not model_location or self.active_model_location == model_location))
            model_name = (self.config.get("model") if
                          self.config.get("model") in self.models else
                          (self.models[0] if self.models else ""))
            provider_url = self.active_base_url
            self._begin_tool_turn(chat_id, prompt, target_id,
                                  author_name, author_avatar)
            self.connection_label.configure(
                text="● Live web search · " + query[:50], fg=ORANGE)
            def search_worker():
                try:
                    results = search_public_web(query)
                    if self.cancel.is_set():
                        self.events.put(("cancelled", chat_id))
                        return
                    if available_model and model_name:
                        self.events.put(("research_status",
                                         "● Retrieved live sources · preparing answer"))
                        with LocalMemoryStore(DB_PATH) as store:
                            provider = LocalOpenAIProvider(
                                model=model_name, base_url=provider_url)
                            identity = bot.instructions if bot else MiloProfile().instructions
                            conversation = MiloConversation(
                                provider, store,
                                profile=MiloProfile(
                                    name=author_name, instructions=identity),
                                bot_id=target_id, bot_name=author_name,
                                bot_avatar=bot.avatar if bot else "",
                                shared_room=(settings.kind == "room"),
                                research_context=source_context(results),
                                answer_suffix=(harvard_references(results)
                                               if target_id == "research"
                                               else source_footer(results)))
                            chunks = conversation.stream_reply(
                                clean_prompt, chat_id=chat_id,
                                stored_user_text=prompt)
                            try:
                                for chunk in chunks:
                                    if self.cancel.is_set():
                                        chunks.close()
                                        self.events.put(("cancelled", chat_id))
                                        return
                                    self.events.put(("chunk", chat_id, chunk))
                            finally:
                                chunks.close()
                    else:
                        # The web tool can still give grounded clickable
                        # sources while Colibri is offline or changing models.
                        reply = search_results_reply(query, results)
                        if target_id == "research":
                            reply += harvard_references(results)
                        if self.cancel.is_set():
                            self.events.put(("cancelled", chat_id))
                            return
                        self._save_direct_turn(
                            chat_id, prompt, reply, target_id, author_name,
                            bot.avatar if bot else "", settings.kind == "room")
                        self.events.put(("chunk", chat_id, reply))
                    self.events.put(("done", chat_id))
                except Exception as exc:
                    self.events.put((
                        "tool_error", chat_id, prompt,
                        "The live search failed; I won't invent results. " +
                        str(exc)[:310], target_id, author_name,
                        bot.avatar if bot else "", settings.kind == "room"))
            threading.Thread(
                target=search_worker, name="milo-live-web",
                daemon=True).start()
            return

        if model_location:
            candidate = chosen_model(self.models_root, model_location)
            if candidate is None:
                messagebox.showerror(
                    "Model not installed",
                    "This chat or bot is assigned a missing model: " + model_location +
                    "\nChoose an installed checkpoint from the model selector.")
                return
            if (self.engine_starting or not self.model_ready
                    or self.active_model_location != model_location):
                # Keep the typed prompt until its requested checkpoint is
                # ready; never send to whichever model happens to be active.
                self.pending_send = (self.current_chat, prompt, model_location)
                self.connection_label.configure(
                    text="Switching local model for @" + target_id + "  •  " +
                         candidate.name, fg=ORANGE)
                if not self.engine_starting:
                    self._launch_managed_model(candidate)
                return
        elif not self.model_ready:
            messagebox.showinfo(
                "Choose a model",
                "Install/select a local Colibri model before sending. "
                "Each new chat can choose its own checkpoint.")
            self.check_connection()
            return

        model = self.config.get("model", "")
        if model not in self.models and self.models:
            model = self.models[0]
        if not model:
            messagebox.showinfo(
                "Connect a model", "Milo needs an active local chat-capable Colibri model.")
            return
        try:
            LocalOpenAIProvider(model=model, base_url=self.active_base_url)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid model settings", str(exc))
            return

        research_query = ""
        if target_id != "milo" and wants_research(clean_prompt):
            if not web_enabled(self.db.db, target_id):
                messagebox.showinfo(
                    "Internet research disabled",
                    "Web research is disabled for @" + target_id +
                    ". Open its bot editor (⋯) and enable Web research. "
                    "Milo will confirm each actual search before connecting.")
                return
            try:
                research_query = query_for_research(clean_prompt)
            except ResearchError as exc:
                messagebox.showerror("Web research", str(exc)[:240])
                return
            if not messagebox.askyesno(
                "Allow web research for @" + target_id,
                "Milo will send ONLY this current search request to a public "
                "web search service, then fetch a limited number of public "
                "HTTPS pages.\n\n" + research_query[:240] +
                "\n\nUploaded documents, memory, private transcripts and "
                "specialist instructions are NOT uploaded. "
                "Continue with this one search?"):
                self._audit("web_research", "denied")
                return
            self._audit("web_research", "approved")

        # User-selected attachments enter the private application folder only
        # when a real chat turn is submitted. Attachments are not uploaded.
        staged = []
        local_context = ""
        vision_job = None
        if self.queued_attachments:
            try:
                staged = stage_attachments(
                    self.queued_attachments, DATA_DIR)
                local_context, has_images = attachment_context(staged)
            except (AttachmentError, OSError, ValueError) as exc:
                messagebox.showerror("Attachment", str(exc)[:300])
                return
            if has_images:
                image_items = [item for item in staged if item.kind == "image"]
                vision_allowed = (
                    module_enabled(DATA_DIR, "vision")
                    and self.config.get("vision_enabled", "off") == "on")
                if vision_allowed and len(image_items) <= 3:
                    try:
                        backend = OllamaLocalVision(
                            model=self.config.get("vision_model", ""),
                            endpoint=self.config.get("vision_endpoint", ""))
                    except ValueError as exc:
                        messagebox.showerror("Local vision settings",
                                             str(exc), parent=self.root)
                        for item in staged:
                            item.path.unlink(missing_ok=True)
                        return
                    approved = messagebox.askyesno(
                        "Run local image analysis",
                        "Send " + str(len(image_items)) +
                        " selected image(s) and only this message to the "
                        "configured LOCAL Ollama service?\n\n" +
                        backend.endpoint + "\nModel: " + backend.model +
                        "\n\nNothing is sent to a cloud provider. "
                        "The resulting description will be passed to Milo's "
                        "normal chat model as attributed evidence. "
                        "No desktop actions will be taken.",
                        parent=self.root)
                    self._audit("vision_inference",
                                "approved" if approved else "denied")
                    if approved:
                        vision_job = (backend, image_items)
                        self._tool_begin("vision_inference")
                    else:
                        for item in staged:
                            item.path.unlink(missing_ok=True)
                        return
                else:
                    explanation = (
                        "Only three images can be analysed in one request."
                        if vision_allowed else
                        "Local vision is disabled or not enabled in Settings. "
                        "The current Colibri text model cannot see pixels.")
                    if not messagebox.askokcancel(
                        "Images are not analysed",
                        explanation + "\n\nContinue WITHOUT image understanding?",
                        parent=self.root):
                        for item in staged:
                            item.path.unlink(missing_ok=True)
                        return
        attachment_names = (", ".join(f.name for f in staged) if staged else "")
        stored_prompt = (prompt + ("\n[Attached: " + attachment_names + "]"
                                   if attachment_names else ""))
        self.clear_attachments()
        chat_id = self.current_chat
        self.pending_send = None
        self.entry.delete("1.0", "end")
        self.entry_placeholder = False
        self.entry.configure(fg=FG)
        self._display_turn("user", stored_prompt, datetime.now().astimezone())
        self.pending_message = self.chat_view.add_message(
            "assistant", "", datetime.now().astimezone(),
            streaming=True, author_id=target_id, author_name=author_name,
            avatar=author_avatar)
        self.cancel.clear()
        self._set_busy(True)

        def worker():
            try:
                research_context = ""
                answer_suffix = ""
                actual_context = local_context
                if vision_job is not None:
                    self.events.put(("research_status",
                                     "Running approved LOCAL image analysis…"))
                    backend, image_items = vision_job
                    try:
                        findings = backend.analyse(
                            [item.path for item in image_items],
                            clean_prompt, approved=True)
                        if self.cancel.is_set():
                            self.events.put(("cancelled", chat_id))
                            return
                        # Use original owner-visible filenames, never the
                        # random private attachment storage paths.
                        findings = [
                            type(finding)(item.name, finding.model,
                                          finding.observation)
                            for item, finding in zip(image_items, findings)
                        ]
                        actual_context += "\n\n" + vision_context(findings)
                        self.events.put(("research_status",
                            "Local vision completed · composing Milo's answer…"))
                    except (VisionError, OSError, ValueError) as exc:
                        raise VisionError(
                            "Image analysis failed; no visual result was "
                            "supplied to the chat model. " + str(exc)[:180]
                        ) from exc
                if research_query:
                    self.events.put(("research_status", "Searching the public web…"))
                    sources = search_public_web(research_query)
                    research_context = source_context(sources)
                    answer_suffix = source_footer(sources)
                with LocalMemoryStore(DB_PATH) as store:
                    provider = LocalOpenAIProvider(
                        model=model, base_url=self.active_base_url)
                    instructions = (bot.instructions if bot else
                                    MiloProfile().instructions)
                    conversation = MiloConversation(
                        provider, store,
                        profile=MiloProfile(
                            name=author_name, instructions=instructions),
                        bot_id=target_id, bot_name=author_name,
                        bot_avatar=bot.avatar if bot else "",
                        shared_room=(settings.kind == "room"),
                        research_context=research_context,
                        answer_suffix=answer_suffix,
                        local_attachment_context=actual_context)
                    chunks = conversation.stream_reply(
                        clean_prompt, chat_id=chat_id,
                        stored_user_text=stored_prompt)
                    try:
                        for chunk in chunks:
                            if self.cancel.is_set():
                                chunks.close()
                                self.events.put(("cancelled", chat_id))
                                return
                            self.events.put(("chunk", chat_id, chunk))
                    finally:
                        chunks.close()
                self.events.put(("done", chat_id))
            except Exception as exc:
                # Do not include prompt text or private archive contents in logs.
                self.events.put(("error", chat_id, str(exc)[:300]))
        threading.Thread(target=worker, name="milo-chat-" + target_id,
                         daemon=True).start()

    def stop(self):
        if self.busy:
            self.cancel.set()

    def _autoselect_local_model(self):
        selected = chosen_model(
            self.models_root, self.config.get("model_location", ""))
        if selected is not None:
            return
        found = discover_models(self.models_root)
        if len(found) == 1:
            self.config["model_location"] = str(
                found[0].relative_to(self.models_root))
            save_config(self.config)

    def check_connection(self):
        selected = chosen_model(
            self.models_root, self.config.get("model_location", ""))
        if selected is not None:
            self._launch_managed_model(selected)
            return
        self.active_base_url = self.config["base_url"]
        self.model_ready = False
        self.connection_label.configure(
            text="No bundled model selected  •  Open Models", fg=ORANGE)
        # Preserve a previously configured local Colibri server as an optional
        # fallback, but never start it or send prompts to a remote endpoint.
        base = self.config["base_url"]
        def worker():
            try:
                models = list_models(base)
                self.events.put(("connection", True, models))
            except Exception:
                self.events.put(("connection", False, []))
        threading.Thread(target=worker, daemon=True).start()

    def _launch_managed_model(self, selected: Path):
        if self.engine_starting or self.busy:
            return
        model_relative = os.path.relpath(selected, self.models_root)
        if self.active_model_location == model_relative and self.model_ready:
            return
        self.loading_model_location = model_relative
        self.engine_starting = True
        self.model_ready = False
        self.connection_label.configure(
            text="Loading bundled Colibri engine  •  " + selected.name,
            fg=ORANGE)
        backend = self.config.get("backend", "vulkan")
        def worker():
            try:
                # A model switch shuts down the previously OWNED engine, never
                # a separately installed external Colibri service.
                if self.engine.process is not None:
                    self.engine.stop()
                endpoint = self.engine.start(selected, backend=backend)
                self.engine.await_ready()
                models = list_models(endpoint)
                if not models:
                    raise EngineStartupError("model loaded but chat API has no chat-capable models")
                self.events.put(("managed_ready", endpoint, models, model_relative))
            except Exception as exc:
                self.engine.stop()
                self.events.put(("engine_error", str(exc)[:400]))
        threading.Thread(target=worker, name="milo-engine-launch", daemon=True).start()

    def _pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "tray_open":
                    self._restore_from_tray()
                elif kind == "tray_exit":
                    self._close()
                    return
                elif kind == "tray_unavailable":
                    # A crashed notification-area host must not strand a
                    # hidden application with no way to reopen it.
                    self._restore_from_tray()
                    messagebox.showwarning(
                        "Milo tray unavailable",
                        "Milo could not keep its Windows tray icon active. "
                        "The main window has been restored.",
                        parent=self.root)
                elif kind == "quick_chat":
                    self.show_quick_chat()
                elif kind == "hotkey_unavailable":
                    # The OS shortcut is already reserved by another app.
                    # The visible Quick chat button remains available.
                    pass
                elif kind == "managed_ready":
                    self.engine_starting = False
                    self.loading_model_location = ""
                    self.active_base_url = event[1]
                    self.models = event[2]
                    self.active_model_location = event[3]
                    if self.config.get("model") not in self.models:
                        self.config["model"] = self.models[0]
                        save_config(self.config)
                    self.model_ready = True
                    self.connection_label.configure(
                        text="● Bundled Colibri  •  " + self.config["model"],
                        fg="#9fdfb4")
                    waiting = self.pending_send
                    if waiting and waiting[0] == self.current_chat:
                        if waiting[2] == self.active_model_location:
                            self.pending_send = None
                            if (not self.busy and not self.entry_placeholder and
                                self.entry.get("1.0", "end-1c").strip() == waiting[1]):
                                self.root.after(40, self.send)
                        else:
                            # A user-selected model may differ from the chat's
                            # default or the specialist bot's preferred model.
                            candidate = chosen_model(self.models_root, waiting[2])
                            if candidate:
                                self._launch_managed_model(candidate)
                    else:
                        self.pending_send = None
                        self._ensure_chat_model()
                    self._refresh_chat_context()
                elif kind == "engine_error":
                    self.engine_starting = False
                    self.active_model_location = ""
                    self.loading_model_location = ""
                    self.pending_send = None
                    self.model_ready = False
                    self.connection_label.configure(
                        text="● Colibri startup failed  •  Open Models", fg=ORANGE)
                    messagebox.showerror(
                        "Colibri startup",
                        event[1] + "\n\nSee %LOCALAPPDATA%\\Milo\\logs\\colibri-service.log")
                elif kind == "update_check":
                    self.release_candidate = event[1]
                    version = release_version(event[1]) if event[1] else ""
                    if event[1] and newer(version):
                        update_text = "Update available: " + version
                    elif event[1] and newer(VERSION, version):
                        update_text = (
                            "This Milo " + VERSION + " build is a preview. "
                            "Latest published release: " + version +
                            ". Preview builds are distributed separately.")
                    elif event[1]:
                        update_text = "You're on the latest published Milo version."
                    else:
                        update_text = "No public Milo release published yet."
                    self.update_label.configure(
                        text=update_text,
                        fg=ORANGE if event[1] and newer(version) else MUTED)
                    self.update_button.configure(
                        state="normal" if event[1] and newer(version)
                        else "disabled")
                elif kind == "update_status":
                    self.update_label.configure(text=event[1], fg=MUTED)
                elif kind == "download_ready":
                    destination = event[1]
                    if messagebox.askyesno(
                        "Verified update ready",
                        "Milo " + event[2] +
                        " has downloaded and passed SHA-256 verification.\n\n"
                        "Close Milo and open the installer now? "
                        "Your existing Models and private memories stay in place."):
                        try:
                            import subprocess
                            subprocess.Popen(
                                [str(destination)],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                close_fds=True)
                            self._close()
                        except OSError as exc:
                            messagebox.showerror("Updater", str(exc)[:250])
                elif kind == "module_catalog":
                    self.module_catalog = event[1]
                    self.catalog_list.delete(0, "end")
                    for module in self.module_catalog:
                        self.catalog_list.insert(
                            "end", f"{module['name']}  ·  v{module['version']}")
                    if not self.module_catalog:
                        self.catalog_list.insert("end", "No optional modules published.")
                    self.update_label.configure(
                        text=f"{len(self.module_catalog)} modules in the verified release catalog",
                        fg=MUTED)
                elif kind == "module_installed":
                    messagebox.showinfo(
                        "Milo module installed",
                        event[1] + " installed and verified. "
                        "Enable it in the list and restart Milo to load it.")
                elif kind == "updater_error":
                    self.update_label.configure(
                        text="Update check failed: " + event[1][:270] +
                             "\nExisting installation unchanged. "
                             "You can open the official releases page in a browser.",
                        fg=ORANGE)
                    messagebox.showerror(
                        "Updater connection",
                        event[1] + "\n\n"
                        "If GitHub is blocked on this PC, use the manual "
                        "installer from a trusted source and check its hash. "
                        "Milo has not installed or changed anything.",
                        parent=self.root)
                elif kind == "gguf_model_ready":
                    if len(event) > 3 and event[3] == "research":
                        original = get_bot(self.db.db, "research")
                        if original:
                            save_bot(self.db.db, Bot(
                                original.id, original.name,
                                original.instructions, event[1],
                                original.avatar, original.active))
                        self._refresh_chat_context()
                        messagebox.showinfo(
                            "Research model installed",
                            "The optional Thinking checkpoint is now the "
                            "Research specialist's preferred model. Milo's "
                            "current model and your existing chats are unchanged.")
                    else:
                        self.config["model_location"] = event[1]
                        self.config["backend"] = event[2]
                        save_config(self.config)
                        self.check_connection()
                elif kind == "model_download_progress":
                    self.connection_label.configure(
                        text=f"Downloading optional Qwen model  •  {event[1]}%",
                        fg=ORANGE)
                elif kind == "model_download_done":
                    self.model_download_active = False
                    self._import_loose_gguf(
                        Path(event[1]), event[2], parent=None)
                elif kind == "research_model_download_done":
                    self.model_download_active = False
                    self._import_loose_gguf(
                        Path(event[1]), event[2], parent=None,
                        assigned_to="research")
                elif kind == "model_download_error":
                    self.model_download_active = False
                    self.connection_label.configure(
                        text="Optional model download interrupted  •  Open Models",
                        fg=ORANGE)
                    messagebox.showerror("Optional Qwen model", event[1])
                elif kind == "model_imported":
                    relative = event[1]
                    self.config["model_location"] = relative
                    save_config(self.config)
                    messagebox.showinfo(
                        "Model installed",
                        "Copied to " + str(self.models_root / relative) +
                        "\n\nStarting the bundled Colibri service.")
                    self.check_connection()
                elif kind == "model_import_error":
                    messagebox.showerror("Model import", event[1])
                elif kind == "windows_action_result":
                    chat_id, prompt, reply = event[1:4]
                    self._save_direct_turn(
                        chat_id, prompt, reply, event[4], event[5],
                        event[6], event[7])
                    if chat_id == self.current_chat and self.pending_message is not None:
                        self.chat_view.append_to(self.pending_message, reply)
                        self.chat_view.finish(self.pending_message)
                        self.pending_message = None
                    self._set_busy(False)
                    self._refresh_chat_list()
                    self._restore_service_status()
                elif kind == "file_preview_ready":
                    chat_id, prompt, chosen_path = event[1:4]
                    try:
                        preview, details = open_preview(self.root, Path(chosen_path))
                        if details["kind"] == "image":
                            w, h = details["dimensions"]
                            reply = (
                                "Opened a local image preview: " +
                                Path(chosen_path).name +
                                f" ({w} × {h} pixels).\n\n"
                                "This displays the actual file from your PC, "
                                "but the current text model cannot analyse its "
                                "visual content. You can also open it in your "
                                "default Windows viewer using the preview window."
                            )
                        else:
                            reply = (
                                "Opened a local, read-only document preview: " +
                                Path(chosen_path).name +
                                f" ({details['chars']} extractable characters). "
                                "Select and copy text from the preview."
                            )
                    except Exception as exc:
                        reply = "I couldn't preview that file: " + str(exc)[:280]
                        self._tool_finish("failed")
                    else:
                        self._tool_finish("completed")
                    self._save_direct_turn(
                        chat_id, prompt, reply, event[4], event[5],
                        event[6], event[7])
                    if chat_id == self.current_chat and self.pending_message is not None:
                        self.chat_view.append_to(self.pending_message, reply)
                        self.chat_view.finish(self.pending_message)
                        self.pending_message = None
                    self._set_busy(False)
                    self._refresh_chat_list()
                    self._restore_service_status()
                elif kind == "tool_error":
                    self._tool_finish("failed")
                    chat_id, prompt, reply = event[1:4]
                    self._save_direct_turn(
                        chat_id, prompt, reply, event[4], event[5],
                        event[6], event[7])
                    if chat_id == self.current_chat and self.pending_message is not None:
                        self.chat_view.append_to(self.pending_message, reply)
                        self.chat_view.finish(self.pending_message)
                        self.pending_message = None
                    self._set_busy(False)
                    self._refresh_chat_list()
                    self._restore_service_status()
                elif kind == "local_tool_done":
                    self._tool_finish("completed")
                    if event[1] == self.current_chat and self.pending_message is not None:
                        self.chat_view.append_to(self.pending_message, event[2])
                        self.chat_view.finish(self.pending_message)
                        self.pending_message = None
                    self._set_busy(False)
                    self._refresh_chat_list()
                elif kind == "research_status":
                    self.connection_label.configure(text=event[1], fg=ORANGE)
                elif kind == "chunk":
                    if event[1] == self.current_chat and self.pending_message is not None:
                        self.chat_view.append_to(self.pending_message, event[2])
                elif kind == "done":
                    self._tool_finish("completed")
                    if event[1] == self.current_chat and self.pending_message is not None:
                        self.chat_view.finish(self.pending_message)
                        self.pending_message = None
                    self._set_busy(False)
                    self._refresh_chat_list()
                    self._refresh_chat_context()
                    self._restore_service_status()
                elif kind == "cancelled":
                    self._tool_finish("cancelled")
                    if self.pending_message is not None:
                        self.chat_view.finish(
                            self.pending_message, status="Response cancelled · not saved")
                        self.pending_message = None
                    self._set_busy(False)
                    self._restore_service_status()
                elif kind == "error":
                    self._tool_finish("failed")
                    if self.pending_message is not None:
                        self.chat_view.finish(
                            self.pending_message, status="Response incomplete · not saved")
                        self.pending_message = None
                    self._set_busy(False)
                    messagebox.showerror("Milo connection", event[2])
                elif kind == "connection":
                    connected, models = event[1:]
                    self.models = models
                    # Ignore external-probe results if a managed model has
                    # already been started during the asynchronous request.
                    if not self.config.get("model_location") and not self.engine_starting:
                        self.models = models
                        if connected:
                            if self.config.get("model") not in models and models:
                                self.config["model"] = models[0]
                                save_config(self.config)
                            self.model_ready = bool(models)
                            self.connection_label.configure(
                                text="● Existing local service  •  " +
                                     (self.config.get("model") or "No model"), fg="#9fdfb4")
                        else:
                            self.connection_label.configure(
                                text="● No model installed  •  Open Models", fg=ORANGE)
                elif kind == "backup_ok":
                    messagebox.showinfo("Milo backup", event[1])
                elif kind == "backup_error":
                    messagebox.showerror("Milo backup", event[1])
                elif kind == "file_organize_done":
                    self._audit("file_organize", "completed")
                    messagebox.showinfo(
                        "File organiser",
                        f"Moved {event[1]} approved file(s) successfully. "
                        "No existing files were overwritten.", parent=self.root)
                elif kind == "file_organize_error":
                    self._audit("file_organize", "failed")
                    messagebox.showerror(
                        "File organiser", event[1], parent=self.root)
                elif kind == "file_search":
                    self._show_search_results(event[1], event[2])
                elif kind == "file_search_error":
                    messagebox.showerror("File search", event[1])
        except queue.Empty:
            pass
        try:
            self.root.after(70, self._pump)
        except tk.TclError:
            pass

    def _avatar(self, widget: tk.Label, setting: str, fallback: str):
        widget.configure(image="", text=fallback[:1].upper(), width=3, height=1)
        path = self.config.get(setting)
        # Use the owner's official Milo illustration by default, but preserve
        # custom avatars already set in earlier Milo versions.
        full = (AVATARS / path if path else
                BRAND_AVATAR if setting == "avatar_milo" else None)
        if full is None or not full.exists():
            return
        try:
            picture = tk.PhotoImage(file=full)
            factor = max(1, (max(picture.width(), picture.height()) + 49) // 50)
            picture = picture.subsample(factor)
            self.images[setting] = picture
            widget.configure(image=picture, text="", width=50, height=50)
        except (tk.TclError, OSError):
            pass

    def _refresh_profile(self):
        self.user_label.configure(text=self.config["user_name"])
        self.milo_title.configure(text=self.config["milo_name"])
        has_persona = loaded_persona(self.db.db) is not None
        self.persona_status_label.configure(
            text=("RESTORED PROFILE" if has_persona else "BASE PROFILE"),
            fg=COLORS["green"] if has_persona else VIOLET,
        )
        self._avatar(self.user_icon, "avatar_user", self.config["user_name"])
        self._avatar(self.milo_icon, "avatar_milo", self.config["milo_name"])
        user_path = AVATARS / self.config["avatar_user"] if self.config.get("avatar_user") else None
        milo_path = (AVATARS / self.config["avatar_milo"]
                     if self.config.get("avatar_milo") else BRAND_AVATAR)
        self.chat_view.identity(
            user_name=self.config["user_name"],
            milo_name=self.config["milo_name"],
            user_avatar=user_path,
            milo_avatar=milo_path)
        self.chat_list.set_avatar(milo_path)

    def updates_modules_window(self):
        panel = self._panel(
            "Updates & modules",
            "Milo updates directly from BoomForge/colibri GitHub Releases.")
        panel.geometry("660x710")
        tk.Label(panel, text="Installed version: " + VERSION,
                 bg=PANEL, fg=FG, font=(FONT, 11, "bold")).pack(
                     padx=24, anchor="w", pady=(11, 6))
        self.update_label = tk.Label(
            panel, text="Choose Check for updates to contact GitHub.",
            bg=PANEL, fg=MUTED, wraplength=570,
            font=(FONT, 9), justify="left")
        self.update_label.pack(anchor="w", padx=24, pady=(0, 8))
        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill="x", padx=24, pady=(0, 13))
        def check():
            self.update_label.configure(text="Checking official Milo releases...")
            def worker():
                try:
                    self.events.put(("update_check", latest_milo_release()))
                except Exception as exc:
                    self.events.put(("updater_error", str(exc)[:350]))
            threading.Thread(target=worker, name="milo-check-updates", daemon=True).start()
        new_button(buttons, "Check for updates", check, primary=True).pack(
            side="left", padx=(0, 9))
        def get_update():
            release = self.release_candidate
            if not release or not newer(release_version(release)):
                return
            version = release_version(release)
            if not messagebox.askyesno(
                "Download verified update",
                "Download Milo " + version + " from your GitHub release? "
                "The installer will be verified before you can launch it.",
                parent=panel):
                return
            def worker():
                try:
                    asset = installer_asset(release)
                    destination = DATA_DIR / "updates" / asset.name
                    def progress(done, size):
                        step = int(100 * done / size)
                        if step in (25, 50, 75, 100) or done == size:
                            self.events.put(("update_status", f"Downloading Milo {version}: {step}%"))
                    download_verified(asset, destination, progress=progress)
                    self.events.put(("download_ready", destination, version))
                except Exception as exc:
                    self.events.put(("updater_error", str(exc)[:350]))
            threading.Thread(target=worker, name="milo-download-update", daemon=True).start()
        self.update_button = new_button(
            buttons, "Download verified installer", get_update)
        self.update_button.configure(state="disabled")
        self.update_button.pack(side="left")
        def open_official_releases():
            # User-triggered browser navigation; never opens remote installers
            # in the background and never bypasses checksum verification.
            if not webbrowser.open(
                    "https://github.com/BoomForge/colibri/releases"):
                messagebox.showinfo(
                    "GitHub releases",
                    "Open https://github.com/BoomForge/colibri/releases "
                    "in your browser.", parent=panel)
        new_button(
            buttons, "Open GitHub Releases", open_official_releases).pack(
                side="left", padx=(9, 0))

        tk.Frame(panel, height=1, bg=EDGE).pack(fill="x", padx=24, pady=(4, 12))
        tk.Label(panel, text="Built-in features", bg=PANEL, fg=VIOLET,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=24)
        tk.Label(panel,
                 text="Disable optional panels without deleting data. "
                      "Changes take effect after restarting Milo.",
                 bg=PANEL, fg=MUTED, wraplength=560).pack(anchor="w", padx=24, pady=5)
        for module_id, (name, description, _default) in BUILTINS.items():
            var = tk.BooleanVar(value=module_enabled(DATA_DIR, module_id))
            line = tk.Frame(panel, bg=PANEL)
            line.pack(fill="x", padx=24, pady=2)
            def update_feature(value, key=module_id):
                try:
                    set_enabled(DATA_DIR, key, bool(value.get()))
                except Exception as exc:
                    messagebox.showerror("Modules", str(exc)[:250], parent=panel)
            tk.Checkbutton(
                line, text=name, variable=var,
                command=lambda v=var, k=module_id: update_feature(v, k),
                bg=PANEL, fg=FG, selectcolor=BG,
                activebackground=PANEL, activeforeground=FG,
                font=(FONT, 10)).pack(anchor="w")
            tk.Label(line, text=description, bg=PANEL, fg=MUTED,
                     font=(FONT, 8)).pack(anchor="w", padx=25)

        tk.Label(panel, text="Optional feature packages", bg=PANEL, fg=VIOLET,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=24, pady=(14, 4))
        self.catalog_list = tk.Listbox(
            panel, bg=BG, fg=FG, height=5, relief="flat",
            selectbackground=EDGE, font=(FONT, 10),
            exportselection=False)
        self.catalog_list.pack(fill="both", expand=True, padx=24, pady=5)
        self.catalog_list.insert("end", "Check the release catalog to see available modules.")

        existing = load_module_state(DATA_DIR).get("installed", {})
        for module_id, version in sorted(existing.items()):
            flag = tk.BooleanVar(value=module_enabled(DATA_DIR, module_id))
            tk.Checkbutton(
                panel, text=f"Enable {module_id}  v{version}",
                variable=flag,
                command=lambda key=module_id, v=flag:
                    set_enabled(DATA_DIR, key, bool(v.get())),
                bg=PANEL, fg=FG, selectcolor=BG,
                activebackground=PANEL, activeforeground=FG,
                font=(FONT, 9)).pack(anchor="w", padx=24, pady=1)
        catalog_buttons = tk.Frame(panel, bg=PANEL)
        catalog_buttons.pack(fill="x", padx=24, pady=(5, 16))
        def find_modules():
            self.update_label.configure(text="Checking first-party module catalog...")
            def worker():
                try:
                    release = latest_milo_release()
                    if release is None:
                        raise UpdateError("no public Milo release with modules exists yet")
                    catalog = inspect_module_catalog(release)
                    self.events.put(("module_catalog", catalog))
                except Exception as exc:
                    self.events.put(("updater_error", str(exc)[:350]))
            threading.Thread(target=worker, name="milo-catalog", daemon=True).start()
        new_button(catalog_buttons, "Check module catalog", find_modules).pack(
            side="left", padx=(0, 8))
        def install_selected():
            index = self.catalog_list.curselection()
            if not index or index[0] >= len(self.module_catalog):
                return
            entry = self.module_catalog[index[0]]
            current = load_module_state(DATA_DIR).get("installed", {}).get(entry["id"])
            if current == entry["version"]:
                messagebox.showinfo("Modules", "This module is already installed.",
                                    parent=panel)
                return
            if not messagebox.askyesno(
                "Install first-party module",
                "Download and install " + entry["name"] +
                " v" + entry["version"] + " from BoomForge/colibri?\n\n"
                "Python feature modules are trusted executable code, not "
                "sandboxed. They are disabled until you explicitly enable "
                "them and restart Milo.",
                parent=panel):
                return
            def worker():
                try:
                    release = latest_milo_release()
                    if release is None:
                        raise UpdateError("release catalog unavailable")
                    catalog = inspect_module_catalog(release)
                    if not any(x == entry for x in catalog):
                        raise UpdateError("module catalog changed during installation")
                    asset = get_release_asset(
                        release, entry["asset"], max_bytes=MAX_MODULE)
                    downloaded = download_verified(
                        asset, DATA_DIR / "module-downloads" / asset.name)
                    install_verified_module(
                        DATA_DIR, downloaded,
                        module_id=entry["id"], version=entry["version"])
                    self.events.put(("module_installed", entry["name"]))
                except Exception as exc:
                    self.events.put(("updater_error", str(exc)[:350]))
            threading.Thread(target=worker, name="milo-module-install",
                             daemon=True).start()
        new_button(catalog_buttons, "Install selected module",
                   install_selected, primary=True).pack(side="left")
        tk.Label(
            panel, text="Updates preserve Models and local memories. "
                        "Installed modules activate after a restart.",
            bg=PANEL, fg=MUTED, font=(FONT, 8),
            wraplength=590).pack(anchor="w", padx=24, pady=(0, 8))

    def models_window(self):
        panel = self._panel(
            "Local models",
            "Models stay in Milo's Models folder. The engine starts automatically.")
        tk.Label(panel, text="Model storage", bg=PANEL, fg=MUTED).pack(
            anchor="w", padx=24, pady=(10, 2))
        location = tk.Entry(
            panel, bg=BG, fg=FG, relief="flat", font=(FONT, 9))
        location.insert(0, str(self.models_root))
        location.configure(state="readonly",
                           readonlybackground=BG, highlightthickness=0)
        location.pack(fill="x", padx=24, ipady=6)

        tk.Label(panel, text="Installed models", bg=PANEL, fg=MUTED).pack(
            anchor="w", padx=24, pady=(15, 4))
        found = discover_models(self.models_root)
        loose = [
            item for item in discover_loose_gguf(self.models_root)
            if not model_valid(gguf_target(self.models_root, item))
        ]
        entries = [("ready", x) for x in found] + [("gguf", x) for x in loose]
        items = tk.Listbox(
            panel, bg=BG, fg=FG, font=(FONT, 10), height=9,
            selectbackground=EDGE, activestyle="none", relief="flat")
        items.pack(fill="both", expand=True, padx=24, pady=(0, 7))
        for index, (kind, model) in enumerate(entries):
            rel = str(model.relative_to(self.models_root))
            marker = "●  " if rel == self.config.get("model_location") else "   "
            label = marker + rel
            if "abliterat" in rel.casefold():
                label += "   ·   ABLITERATED"
            if kind == "gguf":
                label += "   ·   GGUF  [Import metadata]"
            items.insert("end", label)
            if rel == self.config.get("model_location"):
                items.selection_set(index)
        if not entries:
            items.insert("end", "No model checkpoints found. Add a GGUF or model folder.")
        elif len(entries) == 1:
            items.selection_set(0)

        backend = tk.StringVar(value=self.config.get("backend", "vulkan"))
        choices = tk.Frame(panel, bg=PANEL)
        choices.pack(fill="x", padx=24, pady=(8, 5))
        tk.Label(choices, text="Compute backend", bg=PANEL,
                 fg=MUTED).pack(side="left", padx=(0, 12))
        for name, value in (("Vulkan GPU", "vulkan"), ("CPU", "cpu")):
            tk.Radiobutton(choices, text=name, value=value, variable=backend,
                           bg=PANEL, fg=FG, selectcolor=BG,
                           activebackground=PANEL, activeforeground=FG).pack(
                               side="left", padx=(0, 10))

        def select():
            if self.busy or self.engine_starting:
                messagebox.showinfo(
                    "Milo", "Finish the current inference or model load first.",
                    parent=panel)
                return
            indexes = items.curselection()
            if not indexes or indexes[0] >= len(entries):
                messagebox.showinfo("Milo", "Select a model first.",
                                    parent=panel)
                return
            kind, selected = entries[indexes[0]]
            if kind == "gguf":
                self._import_loose_gguf(selected, backend.get(), parent=panel)
                return
            if not model_valid(selected):
                messagebox.showerror(
                    "Milo", "This model folder is incomplete.",
                    parent=panel)
                return
            self.config["model_location"] = str(
                selected.relative_to(self.models_root))
            self.config["backend"] = backend.get()
            self.config["model_directory"] = str(self.models_root)
            save_config(self.config)
            panel.destroy()
            self.check_connection()

        def add():
            if self.busy or self.engine_starting:
                messagebox.showinfo("Milo", "Finish the current response first.",
                                    parent=panel)
                return
            chosen = filedialog.askdirectory(
                title="Choose an existing Colibri model folder", parent=panel)
            if not chosen:
                return
            source = Path(chosen).resolve()
            if not model_valid(source):
                messagebox.showerror(
                    "Not a complete model",
                    "Choose the model folder containing config.json, tokenizer.json "
                    "and GGUF or Safetensors weights. A single GGUF file on its own "
                    "is not sufficient for Colibri's model gateway.",
                    parent=panel)
                return
            try:
                if source.is_relative_to(self.models_root.resolve()):
                    # Nothing to copy; simply select the managed model.
                    self.config["model_location"] = str(
                        source.relative_to(self.models_root.resolve()))
                    self.config["backend"] = backend.get()
                    save_config(self.config)
                    panel.destroy()
                    self.check_connection()
                    return
            except ValueError:
                pass
            dest = self.models_root / source.name
            if dest.exists():
                messagebox.showerror(
                    "Already installed",
                    "A model with this folder name already exists. "
                    "Rename the source folder or choose the installed copy.",
                    parent=panel)
                return
            if not messagebox.askyesno(
                "Import model",
                "Copy the selected model into:\n" + str(dest) +
                "\n\nModel weights may require several gigabytes of disk space. "
                "The existing model will not be removed.",
                parent=panel):
                return
            self.connection_label.configure(
                text="Copying model weights to Models  •  " + source.name,
                fg=ORANGE)
            panel.destroy()
            requested_backend = backend.get()
            def worker():
                scratch = self.models_root / (
                    "." + source.name + ".importing-" + uuid.uuid4().hex[:8])
                try:
                    size = 0
                    for directory, dirs, files in os.walk(source, followlinks=False):
                        for folder in dirs:
                            if (Path(directory) / folder).is_symlink():
                                raise ValueError("symlinked model directories cannot be imported")
                        for filename in files:
                            full = Path(directory) / filename
                            if full.is_symlink() or not full.is_file():
                                raise ValueError("symlinked model weight files cannot be imported")
                            size += full.stat().st_size
                    free = shutil.disk_usage(self.models_root).free
                    if size + (512 << 20) > free:
                        raise OSError(
                            "not enough free disk space to copy model and retain a 512 MiB reserve")
                    shutil.copytree(source, scratch)
                    if not model_valid(scratch):
                        raise ValueError("imported model failed post-copy validation")
                    scratch.rename(dest)  # Make completed imports visible atomically.
                    self.config["backend"] = requested_backend
                    self.events.put(("model_imported", dest.name))
                except Exception as exc:
                    self.events.put(("model_import_error", str(exc)[:320]))
                finally:
                    if scratch.exists():
                        shutil.rmtree(scratch, ignore_errors=True)
            threading.Thread(target=worker, name="milo-import-model", daemon=True).start()

        actions = tk.Frame(panel, bg=PANEL)
        actions.pack(fill="x", padx=24, pady=12)
        new_button(actions, "Use selected model", select, primary=True).pack(
            side="left", padx=(0, 8))
        new_button(actions, "Import model folder", add).pack(side="left")
        def import_gguf_file():
            selected = filedialog.askopenfilename(
                title="Choose GGUF checkpoint",
                filetypes=[("GGUF model weights", "*.gguf")],
                parent=panel)
            if selected:
                self._import_loose_gguf(Path(selected), backend.get(), parent=panel)
        new_button(actions, "Import GGUF file", import_gguf_file).pack(
            side="left", padx=(8, 0))
        variant_row = tk.Frame(panel, bg=PANEL)
        variant_row.pack(fill="x", padx=24, pady=(4, 10))
        def start_variant_download():
            if self.model_download_active or self.busy or self.engine_starting:
                messagebox.showinfo(
                    "Milo models",
                    "Finish your current response or download first.", parent=panel)
                return
            if not messagebox.askyesno(
                "Optional abliterated Qwen model",
                "Download an experimental community-modified version of "
                "Qwen3-4B-Instruct-2507 (GGUF Q4_K_M, approximately 2.5 GB)?\n\n"
                "It reduces refusal behaviour but is NOT guaranteed to be more accurate "
                "or universally unrestricted. Your standard Qwen model and conversations "
                "remain unchanged. The published SHA-256 is verified after download.\n\n"
                "Source: " + MODEL_PAGE + "\n\nContinue?",
                parent=panel):
                return
            chosen_backend = backend.get()
            self.model_download_cancel.clear()
            self.model_download_active = True
            self.connection_label.configure(
                text="Downloading optional Qwen abliterated model...", fg=ORANGE)
            def download_worker():
                try:
                    asset = inspect_abliterated_release()
                    progress_sent = -1
                    def progress(done, size):
                        nonlocal progress_sent
                        pct = int(done * 100 / size)
                        if pct // 5 != progress_sent:
                            progress_sent = pct // 5
                            self.events.put(("model_download_progress", pct))
                    installed = download_abliterated(
                        asset, self.models_root, on_progress=progress,
                        cancel=self.model_download_cancel)
                    self.events.put(("model_download_done", str(installed), chosen_backend))
                except Exception as exc:
                    self.events.put(("model_download_error", str(exc)[:350]))
            threading.Thread(
                target=download_worker, name="milo-optional-model-download",
                daemon=True).start()
        new_button(
            variant_row, "Download Qwen3-4B Abliterated (optional)",
            start_variant_download).pack(side="left", padx=(0, 8))
        new_button(
            variant_row, "Cancel download",
            self.model_download_cancel.set).pack(side="left")
        research_row = tk.Frame(panel, bg=PANEL)
        research_row.pack(fill="x", padx=24, pady=(0, 8))
        def install_research_thinking():
            if self.model_download_active or self.busy or self.engine_starting:
                messagebox.showinfo(
                    "Models", "Finish the current response or download first.",
                    parent=panel)
                return
            if not messagebox.askyesno(
                "Optional Research Thinking model",
                "Download Qwen3-4B-Thinking-2507 Q4_K_M (~2.5 GB)?\n\n"
                "This is an optional quantised Qwen3 model, not a new engine. "
                "Colibri registers the Qwen3 family, but its actual "
                "performance on this PC still requires your test.\n\n"
                "Only the Research specialist will use this model by default; "
                "Milo and coding specialists will retain the working checkpoint.\n\n"
                "Publisher: " + RESEARCH_MODEL_PAGE + "\n\nContinue?",
                parent=panel):
                return
            chosen_backend = backend.get()
            self.model_download_cancel.clear()
            self.model_download_active = True
            self.connection_label.configure(
                text="Downloading Research Thinking model…", fg=ORANGE)
            def worker():
                try:
                    asset = inspect_research_asset()
                    def report(done, size):
                        percent = int(100 * done / size)
                        if percent % 10 == 0:
                            self.events.put(("model_download_progress", percent))
                    source = download_research_asset(
                        self.models_root, asset,
                        progress=report, cancel=self.model_download_cancel)
                    self.events.put((
                        "research_model_download_done", str(source), chosen_backend))
                except Exception as exc:
                    self.events.put(("model_download_error", str(exc)[:350]))
            threading.Thread(
                target=worker, daemon=True,
                name="milo-research-model-download").start()
        new_button(
            research_row,
            "Download Research Qwen3 Thinking (optional)",
            install_research_thinking).pack(side="left")
        folder_actions = tk.Frame(panel, bg=PANEL)
        folder_actions.pack(fill="x", padx=24, pady=(0, 14))
        new_button(
            folder_actions, "Open Models folder",
            lambda: os.startfile(str(self.models_root))
            if sys.platform == "win32" else None,
        ).pack(side="left", padx=(0, 8))
        new_button(
            folder_actions, "View Colibri log",
            lambda: os.startfile(str(DATA_DIR / "logs" / "colibri-service.log"))
            if sys.platform == "win32" and
               (DATA_DIR / "logs" / "colibri-service.log").is_file()
            else messagebox.showinfo("Colibri", "No local engine log yet.", parent=panel),
        ).pack(side="left")

    def _import_loose_gguf(self, source: Path, backend: str, *,
                           parent=None, assigned_to=None):
        """Run the bundled Colibri GGUF reader silently, then select model."""
        if self.busy or self.engine_starting:
            messagebox.showinfo(
                "Milo", "Finish the current response or model load first.",
                parent=parent)
            return
        if source.suffix.lower() != ".gguf":
            messagebox.showerror("Model", "Choose a GGUF file.", parent=parent)
            return
        source = source.resolve(strict=True)
        destination = self.models_root / source.name
        if not source.is_relative_to(self.models_root.resolve()):
            if destination.exists():
                messagebox.showerror(
                    "Model already exists",
                    "A file with that name already exists in Models.",
                    parent=parent)
                return
            if not messagebox.askyesno(
                "Copy GGUF into Models",
                "Copy the model weight file into your managed Models folder?\n\n"
                "This may need several gigabytes of free disk space. "
                "The original file stays unchanged.",
                parent=parent):
                return
        else:
            destination = source
        if parent is not None:
            parent.destroy()
        self.connection_label.configure(
            text="Preparing GGUF model  •  " + destination.name, fg=ORANGE)
        requested_backend = backend
        def worker():
            copied = False
            try:
                if destination != source:
                    if source.is_symlink() or not source.is_file():
                        raise ValueError("model source cannot be a symbolic link")
                    if shutil.disk_usage(self.models_root).free < (
                        source.stat().st_size + 512 * 1024 * 1024
                    ):
                        raise OSError("not enough disk space to import the model")
                    pending = destination.with_suffix(destination.suffix + ".incomplete")
                    if pending.exists():
                        raise FileExistsError("incomplete model copy already exists")
                    try:
                        shutil.copyfile(source, pending)
                        pending.rename(destination)
                        copied = True
                    finally:
                        pending.unlink(missing_ok=True)
                import subprocess
                command = (
                    [sys.executable] if getattr(sys, "frozen", False)
                    else [sys.executable, "-m", "desktop.milo_desktop"]
                )
                command += ["--gguf-import", str(destination)]
                logdir = DATA_DIR / "logs"
                logdir.mkdir(parents=True, exist_ok=True)
                with (logdir / "gguf-import.log").open("a", encoding="utf-8") as logfile:
                    child = subprocess.run(
                        command, cwd=str(self.install_root),
                        stdout=logfile, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=180,
                    )
                if child.returncode != 0:
                    raise RuntimeError(
                        "Colibri could not import this GGUF. See "
                        "%LOCALAPPDATA%\\Milo\\logs\\gguf-import.log "
                        "for unsupported formats or missing metadata.")
                converted = gguf_target(self.models_root, destination)
                if not model_valid(converted):
                    raise RuntimeError("Colibri did not create a loadable model folder")
                relative = str(converted.relative_to(self.models_root))
                self.events.put(("gguf_model_ready", relative, requested_backend,
                                 assigned_to))
            except Exception as exc:
                self.events.put(("model_import_error", str(exc)[:450]))
        threading.Thread(target=worker, name="milo-gguf-import", daemon=True).start()

    def restore_milo_personality(self):
        """Private, approved local import; no GitHub upload or model training."""
        if self.busy:
            messagebox.showinfo(
                "Milo", "Finish the current response before importing historical memory."
            )
            return
        source = filedialog.askopenfilename(
            title="Select Milo original private history / continuity ZIP",
            filetypes=[
                ("Milo private archive", "*.zip *.json"),
                ("JSON chat archive", "*.json"),
                ("Continuity package", "*.zip"),
            ],
        )
        if not source:
            return
        try:
            preview = inspect_legacy_archive(source)
        except (LegacyArchiveError, OSError, ValueError) as exc:
            messagebox.showerror("Milo archive", str(exc))
            return
        old_profile = loaded_persona(self.db.db)
        replacement = old_profile is not None and preview.has_persona
        explanation = (
            f"Original chat messages: {preview.messages}\n"
            f"Earlier Milo: {preview.milo_messages}\n"
            f"Your messages: {preview.user_messages}\n"
            f"Covered period: {preview.first_date} to {preview.last_date} (UTC)\n"
            f"Image references: {preview.image_references}\n"
            f"Other attachment references: {preview.attachment_references}\n"
            f"Personality profile included: {'yes' if preview.has_persona else 'no'}\n\n"
            "This imports historical messages into a SEPARATE, source-labelled "
            "local archive. It does not insert them into your current chats. "
            "Referenced images are not downloaded.\n"
            "The selected private source file is retained locally for backup.\n\n"
        )
        if replacement:
            explanation += (
                "Your current imported Milo personality will be replaced by "
                "this newly selected historical profile.\n\n"
            )
        explanation += "Nothing is sent to GitHub or other computers. Import now?"
        if not messagebox.askyesno("Import original Milo history", explanation):
            return
        try:
            ORIGINAL_ARCHIVES.mkdir(parents=True, exist_ok=True)
            source_path = Path(source)
            # Name the retained file by the ENTIRE selected package digest,
            # not only chat.json. Distinct personal profile revisions sharing
            # the same chat export must not silently resolve to stale files.
            with source_path.open("rb") as handle:
                package_hash = hashlib.sha256()
                for part in iter(lambda: handle.read(1024 * 1024), b""):
                    package_hash.update(part)
            archive_path = ORIGINAL_ARCHIVES / (
                "milo-original-" + package_hash.hexdigest()[:20]
                + source_path.suffix.lower()
            )
            if not archive_path.exists():
                scratch = archive_path.with_suffix(archive_path.suffix + ".part")
                if scratch.exists():
                    raise LegacyArchiveError(
                        "a partial previous archive file exists; inspect it first"
                    )
                try:
                    shutil.copyfile(source_path, scratch)
                    def fingerprint(path):
                        h = hashlib.sha256()
                        with path.open("rb") as handle:
                            for block in iter(lambda: handle.read(1024 * 1024), b""):
                                h.update(block)
                        return h.digest()
                    if fingerprint(scratch) != fingerprint(source_path):
                        raise LegacyArchiveError("archive copy verification failed")
                    scratch.replace(archive_path)
                except Exception:
                    scratch.unlink(missing_ok=True)
                    raise
            # Consistent migration and persona installation within SQLite.
            import_legacy_archive(
                self.db.db, archive_path, user_approved=True,
                replace_profile=replacement,
            )
            self._refresh_profile()
            self._load_chat()
            messagebox.showinfo(
                "Milo history imported",
                f"Original history indexed: {preview.messages} messages.\n"
                f"Personality installed: {'yes' if preview.has_persona else 'no'}.\n\n"
                "You can now start a new chat. Milo will use the local profile "
                "and retrieve dated archive excerpts where relevant.\n\n"
                "This reconstructs her documented manner and memories; "
                "it cannot reproduce the original model exactly.",
            )
        except (OSError, LegacyArchiveError, sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Milo import failed", str(exc)[:350])

    def settings_window(self):
        panel = self._panel("Settings", "Manage models, identity and optional tools.")
        panel.geometry("620x780")
        panel.minsize(560, 650)
        values = {}
        fields = [
            ("Name", "user_name"),
            ("Milo's display name", "milo_name"),
            ("Local API base URL", "base_url"),
            ("Model ID", "model"),
        ]
        for label, key in fields:
            tk.Label(panel, text=label, bg=PANEL, fg=MUTED,
                     font=(FONT, 10)).pack(anchor="w", padx=24, pady=(11, 3))
            var = tk.StringVar(value=self.config.get(key, ""))
            values[key] = var
            entry = tk.Entry(panel, textvariable=var, bg=BG, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=(FONT, 10), highlightthickness=1,
                             highlightbackground=EDGE, highlightcolor=VIOLET)
            entry.pack(fill="x", padx=24, ipady=8)
        if self.models:
            tk.Label(panel, text="Detected models: " + ", ".join(self.models[:4]),
                     bg=PANEL, fg=MUTED, wraplength=475).pack(
                         padx=24, pady=7, anchor="w")
        tk.Label(
            panel, text="Optional LOCAL vision — disabled by default",
            bg=PANEL, fg=VIOLET, font=(FONT, 10, "bold")).pack(
                anchor="w", padx=24, pady=(10, 4))
        vision_enabled = tk.BooleanVar(
            value=self.config.get("vision_enabled", "off") == "on")
        tk.Checkbutton(
            panel, text="Allow image analysis (ask approval each time)",
            variable=vision_enabled, bg=PANEL, fg=FG, selectcolor=BG,
            activebackground=PANEL, activeforeground=FG,
            font=(FONT, 9)).pack(anchor="w", padx=24)
        for title, key in (
            ("Installed Ollama vision model", "vision_model"),
            ("Local Ollama URL", "vision_endpoint"),
        ):
            tk.Label(panel, text=title, bg=PANEL, fg=MUTED,
                     font=(FONT, 9)).pack(anchor="w", padx=24, pady=(5, 2))
            var = tk.StringVar(value=self.config.get(key, ""))
            values[key] = var
            tk.Entry(panel, textvariable=var, bg=BG, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=(FONT, 9), highlightthickness=1,
                     highlightbackground=EDGE, highlightcolor=VIOLET).pack(
                         fill="x", padx=24, ipady=5)
        tk.Label(
            panel,
            text="Uses a separate local vision model. Never downloads models "
                 "or sends images to a cloud service. A 2B vision model "
                 "is recommended for lower-memory computers.",
            bg=PANEL, fg=MUTED, font=(FONT, 8),
            wraplength=535, justify="left").pack(
                anchor="w", padx=24, pady=(4, 7))
        shortcut_enabled = tk.BooleanVar(
            value=self.config.get("global_shortcut", "on") == "on")
        tk.Checkbutton(
            panel, text="Enable global Ctrl+Shift+M quick-chat shortcut",
            variable=shortcut_enabled, bg=PANEL, fg=FG, selectcolor=BG,
            activebackground=PANEL, activeforeground=FG,
            font=(FONT, 9)).pack(anchor="w", padx=24, pady=(12, 5))
        avatars = tk.Frame(panel, bg=PANEL)
        avatars.pack(fill="x", padx=24, pady=15)
        new_button(avatars, "Change your avatar (PNG/GIF)",
                   lambda: self._pick_avatar("avatar_user")).pack(side="left", padx=(0, 5))
        new_button(avatars, "Change Milo avatar",
                   lambda: self._pick_avatar("avatar_milo")).pack(side="left")
        def save():
            next_config = dict(self.config)
            for key, var in values.items():
                next_config[key] = var.get().strip()
            try:
                LocalOpenAIProvider(model=next_config["model"] or "placeholder",
                                    base_url=next_config["base_url"])
            except (ValueError, TypeError) as exc:
                messagebox.showerror("Invalid local endpoint", str(exc), parent=panel)
                return
            next_config["vision_enabled"] = (
                "on" if vision_enabled.get() else "off")
            if next_config["vision_enabled"] == "on":
                try:
                    OllamaLocalVision(
                        model=next_config["vision_model"],
                        endpoint=next_config["vision_endpoint"])
                except ValueError as exc:
                    messagebox.showerror("Invalid local vision settings",
                                         str(exc), parent=panel)
                    return
            if not next_config["user_name"] or not next_config["milo_name"]:
                messagebox.showerror("Invalid names", "Enter both display names.",
                                     parent=panel)
                return
            next_config["model_directory"] = str(self.models_root)
            previous_shortcut = self.config.get("global_shortcut", "on")
            next_config["global_shortcut"] = (
                "on" if shortcut_enabled.get() else "off")
            self.config = next_config
            save_config(self.config)
            if previous_shortcut != next_config["global_shortcut"]:
                self.quick_hotkey.stop()
                self.quick_hotkey = GlobalQuickChatHotkey(self.events)
                if next_config["global_shortcut"] == "on":
                    self.quick_hotkey.start()
            self._refresh_profile()
            self._load_chat()
            panel.destroy()
            self.check_connection()
        new_button(panel, "Save settings", save, primary=True).pack(
            fill="x", padx=24, pady=12)

    def _pick_avatar(self, key):
        source = filedialog.askopenfilename(
            title="Choose a profile PNG or GIF",
            filetypes=[("Supported images", "*.png *.gif")])
        if not source:
            return
        try:
            suffix = Path(source).suffix.lower()
            destination = AVATARS / (key + suffix)
            shutil.copyfile(source, destination)
            self.config[key] = destination.name
            save_config(self.config)
            self._refresh_profile()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Avatar", str(exc))

    def memory_window(self):
        panel = self._panel(
            "Approved memory",
            "Review and correct source-labelled permanent memories. "
            "Nothing is changed without your approval.")
        listbox = tk.Listbox(panel, height=10, bg=BG, fg=FG,
                             selectbackground=EDGE, font=(FONT, 10), relief="flat")
        listbox.pack(fill="both", expand=True, padx=24, pady=(12, 6))
        note_ids = []
        provenance = tk.Label(panel, text="Select a note to inspect its source.",
                              bg=PANEL, fg=MUTED, justify="left", anchor="w",
                              wraplength=515, font=(FONT, 9))
        provenance.pack(fill="x", padx=24, pady=(3, 8))
        editor = tk.Text(panel, height=5, wrap="word")
        style_text(editor, small=True)
        editor.pack(fill="x", padx=24, pady=6)

        def refresh():
            listbox.delete(0, "end")
            note_ids.clear()
            rows = self.db.db.execute(
                "SELECT id,text FROM approved_notes ORDER BY id DESC LIMIT 200"
            ).fetchall()
            for note_id, note_text in rows:
                note_ids.append(note_id)
                listbox.insert("end", note_text[:110].replace("\n", " "))
            provenance.configure(
                text="Select a note to see its source and correction history.")

        def selected_id():
            selection = listbox.curselection()
            return note_ids[selection[0]] if selection else None

        def inspect(_event=None):
            note_id = selected_id()
            if note_id is None:
                return
            note = self.db.note_provenance(note_id)
            if not note:
                refresh()
                return
            editor.delete("1.0", "end")
            editor.insert("1.0", note["text"])
            provenance.configure(
                text="Source: " + note["original_source_kind"] + " · " +
                note["original_source_ref"] + "\nCreated: " +
                note["created_at"][:19] +
                (" · Owner corrections: " + str(note["revision_count"])
                 if note["revision_count"] else ""))

        listbox.bind("<<ListboxSelect>>", inspect)

        def add():
            note = editor.get("1.0", "end-1c").strip()
            if not note:
                return
            if not messagebox.askyesno(
                    "Approve a NEW long-term memory",
                    "Save this text as a separate, owner-approved memory note? "
                    "Use Correct selected to replace an existing note.",
                    parent=panel):
                return
            try:
                self.db.add_approved_note(
                    note, source_kind="owner_approved",
                    source_ref="desktop:" + datetime.now().isoformat(),
                    approved=True)
                editor.delete("1.0", "end")
                refresh()
            except ValueError as exc:
                messagebox.showerror("Memory", str(exc), parent=panel)

        def correct():
            note_id = selected_id()
            if note_id is None:
                messagebox.showinfo("Correct memory",
                                    "Select an existing memory first.",
                                    parent=panel)
                return
            note = editor.get("1.0", "end-1c").strip()
            if not note:
                return
            if not messagebox.askyesno(
                    "Approve memory correction",
                    "Replace the selected memory with the edited text?\n\n"
                    "Milo will retain its original source and a local revision "
                    "record. No other notes or chats are changed.",
                    parent=panel):
                return
            try:
                changed = self.db.revise_note(note_id, note, approved=True)
                if changed:
                    refresh()
                else:
                    messagebox.showinfo("Memory",
                                        "The selected memory is unchanged.",
                                        parent=panel)
            except (ValueError, sqlite3.Error) as exc:
                messagebox.showerror("Memory", str(exc)[:250], parent=panel)

        def remove():
            note_id = selected_id()
            if note_id is None:
                return
            if messagebox.askyesno(
                    "Delete note",
                    "Permanently delete this memory and its correction history?",
                    parent=panel):
                self.db.delete_note(note_id)
                editor.delete("1.0", "end")
                refresh()

        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill="x", padx=24, pady=16)
        new_button(buttons, "Save new memory", add, primary=True).pack(
            side="left", padx=(0, 6))
        new_button(buttons, "Correct selected", correct).pack(
            side="left", padx=(0, 6))
        new_button(buttons, "Delete selected", remove).pack(side="left")
        refresh()

    def organise_files_window(self):
        """Explicit owner folders and reviewed list; no LLM-driven file moves."""
        panel = self._panel(
            "Organise files",
            "Preview-only until you approve the complete file list. "
            "No overwrites or executable files; same drive only.")
        panel.geometry("690x680")
        source_var = tk.StringVar()
        target_var = tk.StringVar()
        match_var = tk.StringVar()
        for caption, variable in (
            ("Source folder", source_var),
            ("Destination folder", target_var),
        ):
            tk.Label(panel, text=caption, bg=PANEL, fg=MUTED).pack(
                anchor="w", padx=24, pady=(10, 3))
            row = tk.Frame(panel, bg=PANEL)
            row.pack(fill="x", padx=24)
            tk.Entry(row, textvariable=variable, bg=BG, fg=FG,
                     font=(FONT, 9), relief="flat").pack(
                         side="left", fill="x", expand=True, ipady=7)
            new_button(row, "Browse…", lambda v=variable: v.set(
                filedialog.askdirectory(parent=panel) or v.get())).pack(
                    side="left", padx=(8, 0))
        tk.Label(panel, text="Filename contains (literal text, e.g. stone)",
                 bg=PANEL, fg=MUTED).pack(
                     anchor="w", padx=24, pady=(14, 3))
        tk.Entry(panel, textvariable=match_var, bg=BG, fg=FG,
                 font=(FONT, 9), relief="flat").pack(
                     fill="x", padx=24, ipady=8)
        listing = tk.Listbox(panel, height=10, bg=BG, fg=FG,
                             selectbackground=EDGE, relief="flat",
                             font=("Consolas", 9))
        listing.pack(fill="both", expand=True, padx=24, pady=(12, 4))
        status = tk.Label(panel, text="No files selected or moved.",
                          bg=PANEL, fg=MUTED, wraplength=610,
                          justify="left")
        status.pack(anchor="w", padx=24, pady=(5, 10))
        approved_plan = [None]
        controls = tk.Frame(panel, bg=PANEL)
        controls.pack(fill="x", padx=24, pady=(4, 16))
        move_button = new_button(controls, "Move reviewed files", lambda: None,
                                 primary=True)
        move_button.configure(state="disabled")
        move_button.pack(side="right", padx=(6, 0))

        def invalidate(*_):
            approved_plan[0] = None
            move_button.configure(state="disabled")
            listing.delete(0, "end")
            status.configure(text="Folders or filter changed · preview again.")
        for variable in (source_var, target_var, match_var):
            variable.trace_add("write", invalidate)

        def preview():
            listing.delete(0, "end")
            move_button.configure(state="disabled")
            approved_plan[0] = None
            try:
                plan = plan_file_moves(
                    source_var.get(), target_var.get(), match_var.get())
            except (FileMoveError, OSError, ValueError) as exc:
                status.configure(text="Preview failed: " + str(exc)[:250],
                                 fg=ORANGE)
                return
            if not plan.files:
                status.configure(text="No supported matching files found.",
                                 fg=MUTED)
                return
            for item in plan.files:
                listing.insert(
                    "end", item.source.name + "  (" +
                    format(item.size, ",") + " bytes)")
            approved_plan[0] = plan
            status.configure(
                text=f"Preview: {len(plan.files)} file(s). Originals remain "
                     "untouched until you approve the move.", fg=FG)
            move_button.configure(state="normal")

        def move():
            plan = approved_plan[0]
            if plan is None:
                return
            if not messagebox.askyesno(
                    "Approve file move",
                    "Move ALL " + str(len(plan.files)) + " reviewed files?\n\n"
                    + "FROM: " + str(plan.source_root)
                    + "\nTO: " + str(plan.destination_root)
                    + "\n\nNo files will be overwritten. This changes your "
                    "original project folders; make a backup if needed.",
                    parent=panel):
                self._audit("file_organize", "denied")
                return
            self._audit("file_organize", "approved")
            self._audit("file_organize", "started")
            approved_plan[0] = None
            move_button.configure(state="disabled")
            status.configure(text="Moving reviewed files…", fg=ORANGE)
            def worker():
                try:
                    moved = execute_file_moves(plan, approved=True)
                    self.events.put(("file_organize_done", moved))
                except (FileMoveError, PermissionError, OSError) as exc:
                    self.events.put(("file_organize_error", str(exc)[:350]))
            threading.Thread(
                target=worker, name="milo-owner-file-move", daemon=True).start()

        move_button.configure(command=move)
        new_button(controls, "Preview matching files", preview).pack(side="left")
        return panel

    def find_files_window(self):
        panel = self._panel("Find files", "Deterministic search. No model or network used.")
        chosen = tk.StringVar(value=str(Path.home() / "Documents"))
        prefix = tk.StringVar()
        tk.Label(panel, text="Folder", bg=PANEL, fg=MUTED).pack(
            anchor="w", padx=24, pady=(12, 4))
        row = tk.Frame(panel, bg=PANEL)
        row.pack(fill="x", padx=24)
        tk.Entry(row, textvariable=chosen, bg=BG, fg=FG, relief="flat",
                 font=(FONT, 10)).pack(side="left", fill="x", expand=True, ipady=6)
        new_button(row, "Browse", lambda: chosen.set(
            filedialog.askdirectory() or chosen.get())).pack(side="left", padx=7)
        tk.Label(panel, text="Filename starts with", bg=PANEL, fg=MUTED).pack(
            anchor="w", padx=24, pady=(16, 4))
        tk.Entry(panel, textvariable=prefix, bg=BG, fg=FG, relief="flat",
                 font=(FONT, 10)).pack(fill="x", padx=24, ipady=7)
        recurse = tk.BooleanVar(value=False)
        tk.Checkbutton(panel, text="Search subfolders", variable=recurse,
                       bg=PANEL, fg=FG, selectcolor=BG,
                       activebackground=PANEL, activeforeground=FG).pack(
                           anchor="w", padx=24, pady=12)
        def search():
            root = chosen.get()
            pfx = prefix.get()
            recursive = bool(recurse.get())  # Read Tk state on the UI thread.
            panel.destroy()
            def worker():
                try:
                    matches = find_files_by_prefix(
                        root, pfx, recursive=recursive)
                    self.events.put(("file_search", root, matches))
                except Exception as exc:
                    self.events.put(("file_search_error", str(exc)[:250]))
            threading.Thread(target=worker, daemon=True).start()
        new_button(panel, "Search files", search, primary=True).pack(
            fill="x", padx=24, pady=10)

    def _show_search_results(self, root: str, matches: list[Path]):
        panel = self._panel("Matching files", str(len(matches)) + " matches (no files opened)")
        lst = tk.Listbox(panel, bg=BG, fg=FG, selectbackground=EDGE,
                         relief="flat", font=(FONT, 10))
        lst.pack(fill="both", expand=True, padx=24, pady=12)
        for match in matches:
            lst.insert("end", str(match))
        def copy():
            selection = lst.curselection()
            if selection:
                self.root.clipboard_clear()
                self.root.clipboard_append(str(Path(root) / matches[selection[0]]))
        new_button(panel, "Copy selected path", copy).pack(
            fill="x", padx=24, pady=12)

    def sol_window(self):
        panel = self._panel("Ask SOL for an opinion",
             "Manual approval gate. Nothing is sent to ChatGPT automatically.")
        tk.Label(panel, text="Question", bg=PANEL, fg=MUTED).pack(
            anchor="w", padx=24, pady=(12, 3))
        question = tk.Text(panel, height=4)
        style_text(question, small=True)
        question.pack(fill="x", padx=24)
        tk.Label(panel, text="Context you choose to share (optional)",
                 bg=PANEL, fg=MUTED).pack(anchor="w", padx=24, pady=(12, 3))
        context = tk.Text(panel, height=6)
        style_text(context, small=True)
        context.pack(fill="x", padx=24)
        tk.Label(panel,
                 text="SOL in ChatGPT is not a generic API model. "
                      "This preview must be copied and shared by you.",
                 bg=PANEL, fg=MUTED, wraplength=480,
                 font=(FONT, 9)).pack(anchor="w", padx=24, pady=10)
        def copy():
            try:
                draft = prepare_consultation(
                    question.get("1.0", "end-1c").strip(),
                    context=context.get("1.0", "end-1c").strip(),
                )
            except ValueError as exc:
                messagebox.showerror("Consultation", str(exc), parent=panel)
                return
            preview = draft.preview()
            self.root.clipboard_clear()
            self.root.clipboard_append(preview)
            messagebox.showinfo("Copied", "The exact question and context are copied. "
                                "Paste into your SOL ChatGPT conversation when ready.",
                                parent=panel)
        new_button(panel, "Copy consultation for SOL", copy, primary=True).pack(
            fill="x", padx=24, pady=12)

    def backup_window(self):
        panel = self._panel("Portable backup (preview)",
             "Encrypted data migration; full one-click PC restore is still in development.")
        tk.Label(panel,
                 text="Included: SQLite chats, recovered persona and archived messages,\n"
                      "preferences and avatars; any locally retained source archives.\n"
                      "Optional model weights: only when their folder is configured.\n"
                      "Not yet included: automatic runtime discovery, external credentials,\n"
                      "or complete restored-app activation. This is NOT a full PC clone.",
                 bg=PANEL, fg=MUTED, justify="left",
                 wraplength=500).pack(anchor="w", padx=24, pady=18)
        def export():
            destination = filedialog.asksaveasfilename(
                title="Save encrypted Milo archive",
                defaultextension=".milobak",
                filetypes=[("Milo encrypted backup", "*.milobak")])
            if not destination:
                return
            passphrase = simpledialog.askstring(
                "Recovery passphrase", "Choose a recovery passphrase (12+ characters). "
                "Keep it somewhere safe; without it, restoration is impossible.",
                show="*", parent=panel)
            if not passphrase:
                return
            if len(passphrase) < 12:
                messagebox.showerror("Passphrase", "Use at least 12 characters.",
                                     parent=panel)
                return
            model_dir = self.config.get("model_directory", "")
            roots = {"settings": CONFIG_PATH.parent, "avatars": AVATARS,
                     "documents": DOCUMENTS}
            if (DATA_DIR / "attachments").is_dir():
                roots["attachments"] = DATA_DIR / "attachments"
            if ORIGINAL_ARCHIVES.is_dir():
                roots["archives"] = ORIGINAL_ARCHIVES
            if model_dir:
                roots["models"] = Path(model_dir)
            if not messagebox.askyesno(
                "Confirm backup coverage",
                ("Create encrypted backup of memory, settings, avatars and private archives"
                 + (", and selected model folder" if model_dir else
                    " WITHOUT model weights")
                 + "?\n\nThis preview is not yet an exhaustive PC migration."),
                parent=panel,
            ):
                return
            panel.destroy()
            def worker():
                try:
                    result = export_bundle(
                        destination, passphrase, memory_db=DB_PATH, roots=roots)
                    self.events.put(("backup_ok",
                        f"Encrypted preview archive created:\n{destination}\n"
                        f"{result.files} files verified in the package manifest.\n"
                        "It is not a complete PC migration backup."))
                except Exception as exc:
                    self.events.put(("backup_error", str(exc)[:400]))
            threading.Thread(target=worker, daemon=True).start()
        def restore():
            source = filedialog.askopenfilename(
                title="Choose Milo encrypted backup",
                filetypes=[("Milo encrypted backup", "*.milobak")])
            if not source:
                return
            passphrase = simpledialog.askstring("Recovery passphrase",
                "Enter your portable backup recovery passphrase.",
                show="*", parent=panel)
            if not passphrase:
                return
            destination = DATA_DIR.parent / ("Milo-Restored-" + uuid.uuid4().hex[:8])
            panel.destroy()
            def worker():
                try:
                    result = import_bundle(
                        source, passphrase, destination=destination)
                    self.events.put(("backup_ok",
                        f"Authenticated backup restored to a safe staging folder:\n"
                        f"{destination}\n{result.files} files checked.\n\n"
                        "The current active Milo profile was not overwritten. "
                        "Full automatic activation is not yet implemented."))
                except Exception as exc:
                    self.events.put(("backup_error", str(exc)[:400]))
            threading.Thread(target=worker, daemon=True).start()
        new_button(panel, "Export encrypted preview backup", export, primary=True).pack(
            fill="x", padx=24, pady=(6, 10))
        new_button(panel, "Restore backup to staging", restore).pack(
            fill="x", padx=24, pady=5)

    def _panel(self, title: str, subtitle: str):
        window = tk.Toplevel(self.root)
        window.title("Milo · " + title)
        window.geometry("580x540")
        window.minsize(500, 430)
        window.configure(bg=PANEL)
        tk.Label(window, text=title, bg=PANEL, fg=VIOLET,
                 font=(FONT, 18, "bold")).pack(anchor="w", padx=24,
                                                 pady=(22, 5))
        tk.Label(window, text=subtitle, bg=PANEL, fg=MUTED,
                 font=(FONT, 10), wraplength=520,
                 justify="left").pack(anchor="w", padx=24, pady=(0, 8))
        return window

    def _on_main_unmap(self, event):
        """Windows minimise button behaves like Close: hide to the tray."""
        if event.widget is not self.root or self._exiting:
            return
        try:
            if self.root.state() == "iconic":
                self.root.after_idle(self._hide_to_tray)
        except tk.TclError:
            pass

    def _hide_to_tray(self):
        """Retain the process and local Colibri model when X/minimise is used."""
        if self._exiting:
            return
        if not self.tray.ready:
            # Never withdraw a window without a working restoration icon.
            try:
                self.root.deiconify()
            except tk.TclError:
                return
            messagebox.showwarning(
                "Milo tray unavailable",
                "Milo will stay open because its Windows tray icon is not "
                "ready. Check that your notification area is available.",
                parent=self.root)
            return
        self._tray_hidden_panels = []
        for widget in self.root.winfo_children():
            if not isinstance(widget, tk.Toplevel):
                continue
            try:
                if widget.winfo_viewable():
                    self._tray_hidden_panels.append(widget)
                    widget.withdraw()
            except tk.TclError:
                continue
        self.root.withdraw()

    def _restore_from_tray(self):
        """Bring back Milo from a tray double-click or Open Milo action."""
        if self._exiting:
            return
        try:
            self.root.deiconify()
            self.root.state("normal")
            for widget in self._tray_hidden_panels:
                try:
                    if widget.winfo_exists():
                        widget.deiconify()
                except tk.TclError:
                    continue
            self._tray_hidden_panels = []
            self.root.lift()
            self.root.focus_set()
        except tk.TclError:
            pass

    def _close(self):
        """Explicit shutdown from tray or approved installer handoff."""
        if self._exiting:
            return
        self._exiting = True
        self.tray.stop()
        self.quick_hotkey.stop()
        for path in self.session_captures:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self.session_captures.clear()
        self.cancel.set()
        self.model_download_cancel.set()
        self.engine.stop()
        try:
            self.db.close()
        except Exception:
            pass
        self.root.destroy()


def self_test() -> int:
    """CI smoke check without starting Tk or needing a model/GUI display."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "chat.sqlite3"
        with LocalMemoryStore(db_path) as store:
            store.append_turn("main", "Hello", "Hi!")
            assert store.recent("main")[-1]["content"] == "Hi!"
        assert status_endpoint("http://127.0.0.1:8000/v1").endswith("/models")
    print("Milo desktop smoke check passed.")
    return 0


def ui_self_test() -> int:
    """Windows-only native widget smoke check; debug notes are synthetic."""
    import faulthandler
    diagnostics = Path(tempfile.gettempdir()) / "milo-ui-diagnostics.log"
    with diagnostics.open("w", encoding="utf-8") as report:
        def mark(stage):
            report.write("Stage: " + stage + "\n")
            report.flush()
        faulthandler.dump_traceback_later(12, repeat=False, file=report)
        mark("before tk.Tk")
        root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
        root.withdraw()
        mark("before MiloDesktop")
        app = MiloDesktop(root)
        mark("after MiloDesktop")
        try:
            assert not app.advanced_open, "setup should begin collapsed"
            app.toggle_advanced()
            assert app.advanced_open
            app.toggle_advanced()
            mark("after sidebar")
            # The frozen installer must include pystray's native Windows backend,
            # not merely compile the lazy-loading tray wrapper successfully.
            if getattr(sys, "frozen", False) and sys.platform == "win32":
                import pystray
                from pystray import _win32 as tray_win32
                assert pystray.Icon and tray_win32, (
                    "Frozen app has no native Windows notification-area backend")
            # Never hide the app unless a healthy system tray can restore it.
            # Fake readiness tests Tk's actual withdraw/deiconify lifecycle
            # without running a shell tray host in CI.
            root.deiconify()
            root.update_idletasks()
            app.tray._ready.set()
            app._hide_to_tray()
            assert root.state() == "withdrawn", (
                "Close/minimise must hide Milo, not destroy it")
            assert not app._exiting and app.db.db is not None, (
                "Minimisation unexpectedly shut down Milo's database")
            app._restore_from_tray()
            assert root.state() in ("normal", "zoomed"), (
                "Open Milo from tray did not restore the desktop")
            app.tray._ready.clear()
            assert root.protocol("WM_DELETE_WINDOW"), (
                "Close button must have a non-destructive tray handler")
            mark("after minimize-to-tray and restore")
            # The real images must load from bundled installation paths,
            # not just exist as placeholder names in the database.
            from PIL import Image as PillowImage
            for internal_id, alias, display in (
                ("research", "sage", "Sage"),
                ("webdev", "nova", "Nova"),
                ("gamedev", "forge", "Forge"),
            ):
                bot = get_bot(app.db.db, internal_id)
                assert bot is not None and bot.name == display, (
                    "Specialist identity migration failed: " + alias)
                avatar = app._bot_avatar_path(bot)
                assert avatar and avatar.is_file(), (
                    "Bundled " + alias + " image missing in native app")
                with PillowImage.open(avatar) as portrait:
                    portrait.verify()
                target, stripped = resolve_target(
                    app.db.db, "@" + alias + " hello",
                    ChatSettings("avatar-smoke"))
                assert target == internal_id and stripped == "hello", (
                    "Mention routing failed for @" + alias)
            mark("after bundled specialist avatars and mention routing")
            # Exercise the ACTUAL native menu command after the popup handler
            # returns. Previous code destroyed this menu immediately, so a
            # right-click visibly offered Delete but never ran the action.
            delete_events = []
            prior_delete = app.chat_list._on_delete
            try:
                app.chat_list._on_delete = delete_events.append
                app.chat_list._menu_chat_id = "synthetic-test-chat"
                assert app.chat_list._delete_menu.winfo_exists()
                app.chat_list._delete_menu.invoke(0)
                root.update_idletasks()
                root.update()
                assert delete_events == ["synthetic-test-chat"], (
                    "Delete menu command was not dispatched")
                assert app.chat_list._delete_menu.winfo_exists(), (
                    "Menu destroyed prematurely")
            finally:
                app.chat_list._on_delete = prior_delete
                app.chat_list._menu_chat_id = None
            mark("after native delete menu regression")
            app.models_window()
            app.updates_modules_window()
            assert app.bot_side.winfo_exists(), "bot roster sidebar missing"
            assert app.model_selector.winfo_exists(), "per-chat model selector missing"
            app._edit_bot_window()  # create-bot editor must open
            app.documents_window("core")  # local Core Knowledge panel must open
            assert app.core_docs_button.winfo_exists(), "Core Knowledge button missing"
            assert app.attachment_status.winfo_exists(), "Attachment UI missing"
            app.show_quick_chat()
            assert app.quick_popup.winfo_exists(), "Quick-chat overlay missing"
            app.quick_popup.destroy()
            app.quick_popup = None
            activity = app.activity_window()
            assert activity.winfo_exists(), "Tool activity viewer missing"
            activity.destroy()
            windows_tools = app.windows_tools_window()
            assert windows_tools.winfo_exists(), "Windows action picker missing"
            windows_tools.destroy()
            organiser = app.organise_files_window()
            assert organiser.winfo_exists(), "File organiser preview missing"
            organiser.destroy()
            settings_panel = app.settings_window()
            assert settings_panel is None or settings_panel.winfo_exists()
            # Settings can open without activating vision or network access.
            mark("after optional vision and file tools UI")
            app._audit("quick_chat", "completed")
            assert list_activity(app.db.db)[0][1:] == (
                "quick_chat", "completed"), "Activity log did not persist"
            if TkinterDnD is not None:
                assert app.drop_ready, "Native file drag and drop is not active"
            assert get_bot(app.db.db, "research") is not None
            assert get_bot(app.db.db, "webdev") is not None
            assert get_bot(app.db.db, "gamedev") is not None
            mark("after tool windows, bot editor and documents")
            app.chat_view.clear()
            mark("after clear")
            at = datetime.now().astimezone()
            app.chat_view.add_message("user", "First UI test message", at)
            mark("after first")
            second = app.chat_view.add_message("user", "Grouped follow-up", at)
            mark("after second")
            reply = app.chat_view.add_message("assistant", "", at, streaming=True)
            mark("after reply shell")
            app.chat_view.append_to(reply, "Hello from compact Milo chat")
            app.chat_view.finish(reply)
            mark("after messages")
            fence = chr(96) * 3
            code = app.chat_view.add_message(
                "assistant", "Example code:\n" + fence + "python\n"
                + "print(42)\n" + fence + "\n", at)
            mark("after code block")
            assert second["grouped"], "consecutive messages should group"
            assert not reply["grouped"], "different authors must show headers"
            assert len(app.chat_view.messages) == 4
            assert reply["content"] == "Hello from compact Milo chat"
            specialist = app.chat_view.add_message(
                "assistant", "A specialist test reply", at,
                author_id="qa_code", author_name="QA Code")
            assert specialist["author_name"] == "QA Code"
            assert not specialist["grouped"], "different bot speakers must not merge"
            assert app.model_option_map, "per-chat model mapping missing"
            assert code["body"].copy_widgets, "copyable Tk text widgets missing"
            assert len(code["body"].copy_widgets) >= 2, "code block missing"
            app.chat_view.show_latest()
            root.update_idletasks()
            app.chat_view._finish_reload_scroll(app.chat_view._load_scroll_revision)
            assert app.chat_view.canvas.yview()[1] >= 0.9, "chat should open at latest turn"
            assert app.entry_placeholder, "composer hint missing"
            from desktop.rich_text import copy_selection
            reader_window, reader_text = app.chat_view.selectable_view()
            reader_text.tag_add("sel", "1.0", "1.11")
            copy_selection(reader_text)
            assert root.clipboard_get() == reader_text.get("1.0", "1.11"), \
                "Ctrl+C selection did not reach the system clipboard"
            mark("after full transcript selection and clipboard")
            from PIL import Image as PILImage
            sample = Path(tempfile.gettempdir()) / "milo-ui-preview-smoke.png"
            try:
                PILImage.new("RGBA", (36, 28), (40, 50, 80, 255)).save(sample)
                image_window, image_info = open_preview(root, sample)
                assert image_info["kind"] == "image"
                assert image_info["dimensions"] == (36, 28)
                image_window.destroy()
            finally:
                sample.unlink(missing_ok=True)
            mark("after native image preview")
            reader_window.destroy()
            mark("before update_idletasks")
            root.update_idletasks()
            mark("before update")
            root.update()
            mark("after update")
            for child in list(root.winfo_children()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()
        except Exception:
            import traceback
            traceback.print_exc(file=report)
            report.flush()
            return 1  # fail cleanly; PyInstaller --windowed must not display a modal
        finally:
            mark("before close")
            app._close()
            mark("after close")
            faulthandler.cancel_dump_traceback_later()
    return 0


def embedded_self_test() -> int:
    """Verify the built package includes the first-party Colibri gateway and engines."""
    runtime = install_root() / "Runtime"
    if not (runtime / "openai_server.py").is_file():
        print("Missing bundled openai_server.py", file=sys.stderr)
        return 1
    if not (runtime / "qwen36.exe").is_file():
        print("Missing bundled qwen36.exe", file=sys.stderr)
        return 1
    import importlib
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    gateway = importlib.import_module("openai_server")
    registry = importlib.import_module("family_registry")
    assert hasattr(gateway, "serve") and hasattr(registry, "resolve_model")
    print("Bundled Colibri gateway and Qwen inference executable found.")
    return 0


def main() -> int:
    if "--gguf-import" in sys.argv:
        from desktop.model_import_service import worker_main
        args = sys.argv[sys.argv.index("--gguf-import") + 1:]
        return worker_main(args)
    if "--colibri-service" in sys.argv:
        from desktop.colibri_service import run_service
        args = sys.argv[sys.argv.index("--colibri-service") + 1:]
        return run_service(args)
    if "--ui-self-test" in sys.argv:
        return ui_self_test()
    if "--embedded-self-test" in sys.argv:
        return embedded_self_test()
    if "--self-test" in sys.argv:
        return self_test()
    if TkinterDnD is not None:
        try:
            root = TkinterDnD.Tk()
        except tk.TclError:
            root = tk.Tk()
    else:
        root = tk.Tk()
    MiloDesktop(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
