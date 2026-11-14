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

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from desktop.chat_view import ChatView, ConversationList, COLORS

from colibri.milo_core.consultation import prepare_consultation
from colibri.milo_core.conversation import MiloConversation, MiloProfile
from colibri.milo_core.filesystem import find_files_by_prefix
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
        self.root.geometry("1180x780")
        self.root.minsize(860, 580)
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
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self.models: list[str] = []
        self.images = {}
        self.release_candidate = None
        self.module_catalog = []
        self.plugin_actions, self.plugin_errors = load_module_actions(DATA_DIR)
        self.current_chat = self.config.get("current_chat") or "main"
        self.pending_message = None
        self._build()
        self._refresh_profile()
        self._refresh_chat_list()
        self._load_chat()
        self._autoselect_local_model()
        self.check_connection()
        self.root.after(70, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

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
            self.side, self._select_chat, background=SIDEBAR)
        self.chat_list.pack(fill="both", expand=True, padx=(5, 5))

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
            ("Memory", self.memory_window, "memory"),
            ("Restore Milo personality", self.restore_milo_personality, "memory"),
            ("Find files", self.find_files_window, "files"),
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
        reconnect = new_button(heading, "↻  Reconnect", self.check_connection)
        reconnect.configure(bg=PANEL_BRIGHT, fg=MUTED,
                            padx=12, pady=6, font=(FONT, 9))
        reconnect.pack(side="right", padx=16, pady=17)

        # Compact, avatar-led DM timeline. View-only; conversation logic
        # remains in the original MiloConversation and SQLite store.
        self.chat_view = ChatView(main, background=BG)
        self.chat_view.pack(fill="both", expand=True)

        composer_border = tk.Frame(main, bg=EDGE)
        composer_border.pack(fill="x", padx=18, pady=(10, 5))
        composer = tk.Frame(composer_border, bg=PANEL)
        composer.pack(fill="both", expand=True, padx=1, pady=1)
        self.entry = tk.Text(
            composer, height=2, wrap="word", font=(FONT, 11),
            bg=PANEL, fg=FG, insertbackground=VIOLET,
            selectbackground=ACCENT, selectforeground=FG,
            relief="flat", highlightthickness=0, borderwidth=0,
            padx=12, pady=10, undo=True)
        self.entry.pack(side="left", fill="both", expand=True, padx=(3, 0), pady=3)
        self.entry.bind("<Return>", self._enter_key)
        self.entry_hint = "Message Milo…"
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
        tk.Label(main,
                 text="Private on your device   ·   Enter to send   ·   Shift+Enter for a new line",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(
                     anchor="w", padx=22, pady=(2, 9))

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        self.advanced_toggle.configure(
            text=("⚙  Setup & modules  ▾" if self.advanced_open
                  else "⚙  Setup & modules  ▸"))
        if self.advanced_open:
            self.advanced_controls.pack(fill="x", pady=(5, 0))
        else:
            self.advanced_controls.pack_forget()

    def _enter_key(self, event):
        if event.state & 0x1:  # Shift+Enter
            return None
        self.send()
        return "break"

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _append(self, text: str, tag: str = "body"):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _display_turn(self, role: str, text: str, timestamp: str = ""):
        display_name = (self.config["user_name"] if role == "user"
                        else self.config["milo_name"])
        self._append("\n" + display_name + "  ", role + "_name")
        if timestamp:
            self._append(timestamp[:16].replace("T", " "), "time")
        self._append("\n" + text + "\n", "body")

    def _load_chat(self):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        rows = self.db.db.execute(
            "SELECT role, content, created_at FROM conversations WHERE chat_id=? "
            "ORDER BY id LIMIT 1000", (self.current_chat,)
        ).fetchall()
        if not rows:
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
        for role, text, date in rows:
            self._display_turn(role, text, date)

    def _refresh_chat_list(self):
        self.chats = []
        rows = self.db.db.execute(
            "SELECT c.chat_id, COALESCE((SELECT substr(u.content, 1, 32) "
            "FROM conversations u WHERE u.chat_id=c.chat_id AND u.role='user' "
            "ORDER BY u.id LIMIT 1), 'Conversation') "
            "FROM conversations c GROUP BY c.chat_id ORDER BY MAX(c.id) DESC "
            "LIMIT 100"
        ).fetchall()
        self.chats.extend((chat_id, title) for chat_id, title in rows)
        if self.current_chat not in {x[0] for x in self.chats}:
            self.chats.insert(0, (self.current_chat, "New conversation"))
        self.chat_list.delete(0, "end")
        selected_index = 0
        for index, (chat_id, title) in enumerate(self.chats):
            self.chat_list.insert("end", "#  " + title.replace("\n", " "))
            if chat_id == self.current_chat:
                selected_index = index
        self.chat_list.selection_set(selected_index)

    def _select_chat(self, _event):
        selection = self.chat_list.curselection()
        if self.busy or not selection:
            return
        selected = self.chats[selection[0]][0]
        if selected != self.current_chat:
            self.current_chat = selected
            self.config["current_chat"] = selected
            save_config(self.config)
            self._load_chat()

    def new_chat(self):
        if self.busy:
            messagebox.showinfo("Milo", "Finish or stop the current response first.")
            return
        self.current_chat = uuid.uuid4().hex
        self.config["current_chat"] = self.current_chat
        save_config(self.config)
        self._refresh_chat_list()
        self._load_chat()

    def send(self):
        if self.busy:
            return
        prompt = self.entry.get("1.0", "end-1c").strip()
        if not prompt:
            return
        if len(prompt) > 20000:
            messagebox.showerror("Message too long", "Keep messages below 20,000 characters.")
            return
        model = self.config.get("model", "")
        if self.config.get("model_location") and not self.model_ready:
            messagebox.showinfo("Loading model",
                "The local model is not ready yet. Check the status above, "
                "or open Models to change it.")
            return
        if not model:
            messagebox.showinfo("Connect a model",
                "Milo needs a local Colibri model. Start Colibri and select a model in Settings.")
            self.check_connection()
            return
        try:
            LocalOpenAIProvider(model=model, base_url=self.active_base_url)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid model settings", str(exc))
            return
        chat_id = self.current_chat
        self.entry.delete("1.0", "end")
        self._display_turn("user", prompt, datetime.now().isoformat())
        self._append("\n" + self.config["milo_name"] + "  ", "milo_name")
        self._append(datetime.now().strftime("%Y-%m-%d %H:%M"), "time")
        self._append("\n")
        self.cancel.clear()
        self._set_busy(True)

        def worker():
            try:
                with LocalMemoryStore(DB_PATH) as store:
                    provider = LocalOpenAIProvider(
                        model=model, base_url=self.active_base_url
                    )
                    conversation = MiloConversation(
                        provider, store,
                        profile=MiloProfile(
                            name=self.config["milo_name"],
                            instructions=MiloProfile().instructions,
                        ),
                    )
                    chunks = conversation.stream_reply(prompt, chat_id=chat_id)
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
                # Do not expose model requests or transcript content in logs.
                self.events.put(("error", chat_id, str(exc)[:300]))
        threading.Thread(target=worker, daemon=True).start()

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
                self.events.put(("managed_ready", endpoint, models))
            except Exception as exc:
                self.engine.stop()
                self.events.put(("engine_error", str(exc)[:400]))
        threading.Thread(target=worker, name="milo-engine-launch", daemon=True).start()

    def _pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "managed_ready":
                    self.engine_starting = False
                    self.active_base_url = event[1]
                    self.models = event[2]
                    if self.config.get("model") not in self.models:
                        self.config["model"] = self.models[0]
                        save_config(self.config)
                    self.model_ready = True
                    self.connection_label.configure(
                        text="● Bundled Colibri  •  " + self.config["model"],
                        fg="#9fdfb4")
                elif kind == "engine_error":
                    self.engine_starting = False
                    self.model_ready = False
                    self.connection_label.configure(
                        text="● Colibri startup failed  •  Open Models", fg=ORANGE)
                    messagebox.showerror(
                        "Colibri startup",
                        event[1] + "\n\nSee %LOCALAPPDATA%\\Milo\\logs\\colibri-service.log")
                elif kind == "update_check":
                    self.release_candidate = event[1]
                    version = release_version(event[1]) if event[1] else ""
                    self.update_label.configure(
                        text=("Update available: " + version
                              if event[1] and newer(version)
                              else "You're running the latest published Milo version."
                              if event[1] else "No public Milo release published yet."),
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
                        text="Update failed; existing installation unchanged.",
                        fg=ORANGE)
                    messagebox.showerror("Updater", event[1])
                elif kind == "gguf_model_ready":
                    self.config["model_location"] = event[1]
                    self.config["backend"] = event[2]
                    save_config(self.config)
                    self.check_connection()
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
                elif kind == "chunk":
                    if event[1] == self.current_chat:
                        self._append(event[2], "body")
                elif kind == "done":
                    if event[1] == self.current_chat:
                        self._append("\n")
                    self._set_busy(False)
                    self._refresh_chat_list()
                elif kind == "cancelled":
                    self._append("\n[Response cancelled; not saved]\n", "notice")
                    self._set_busy(False)
                elif kind == "error":
                    self._append("\n[Response incomplete; not saved]\n", "notice")
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
            text=("● Historical personality imported"
                  if has_persona else "○ Temporary personality"),
            fg="#9fdfb4" if has_persona else ORANGE,
        )
        self._avatar(self.user_icon, "avatar_user", self.config["user_name"])
        self._avatar(self.milo_icon, "avatar_milo", self.config["milo_name"])

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

    def _import_loose_gguf(self, source: Path, backend: str, *, parent=None):
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
                self.events.put(("gguf_model_ready", relative, requested_backend))
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
        panel = self._panel("Settings", "Manage your local model and identity.")
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
            if not next_config["user_name"] or not next_config["milo_name"]:
                messagebox.showerror("Invalid names", "Enter both display names.",
                                     parent=panel)
                return
            next_config["model_directory"] = str(self.models_root)
            self.config = next_config
            save_config(self.config)
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
        panel = self._panel("Approved memory", "Milo remembers chat history; permanent notes need your approval.")
        listbox = tk.Listbox(panel, height=10, bg=BG, fg=FG,
                             selectbackground=EDGE, font=(FONT, 10), relief="flat")
        listbox.pack(fill="both", expand=True, padx=24, pady=12)
        note_ids = []
        def refresh():
            listbox.delete(0, "end")
            note_ids.clear()
            rows = self.db.db.execute(
                "SELECT id,text FROM approved_notes ORDER BY id DESC LIMIT 200"
            ).fetchall()
            for note_id, text in rows:
                note_ids.append(note_id)
                listbox.insert("end", text[:110].replace("\n", " "))
        refresh()
        editor = tk.Text(panel, height=4, wrap="word")
        style_text(editor, small=True)
        editor.pack(fill="x", padx=24, pady=6)
        def add():
            note = editor.get("1.0", "end-1c").strip()
            if not note:
                return
            if not messagebox.askyesno("Approve long-term memory",
                 "Save this information as an approved persistent memory note?",
                 parent=panel):
                return
            try:
                self.db.add_approved_note(
                    note, source_kind="owner_approved",
                    source_ref="desktop:" + datetime.now().isoformat(),
                    approved=True,
                )
                editor.delete("1.0", "end")
                refresh()
            except ValueError as exc:
                messagebox.showerror("Memory", str(exc), parent=panel)
        def remove():
            idx = listbox.curselection()
            if not idx:
                return
            if messagebox.askyesno("Delete note", "Permanently delete this memory note?",
                                   parent=panel):
                self.db.delete_note(note_ids[idx[0]])
                refresh()
        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill="x", padx=24, pady=16)
        new_button(buttons, "Save approved note", add, primary=True).pack(
            side="left", padx=(0, 8))
        new_button(buttons, "Delete selected", remove).pack(side="left")

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
            roots = {"settings": CONFIG_PATH.parent, "avatars": AVATARS}
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

    def _close(self):
        self.cancel.set()
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
    """Windows-only, no model: construct key panels and shut down cleanly."""
    root = tk.Tk()
    root.withdraw()
    app = MiloDesktop(root)
    try:
        assert not app.advanced_open, "setup should begin collapsed"
        app.toggle_advanced()
        assert app.advanced_open
        app.toggle_advanced()
        models_panel = app.models_window()
        updates_panel = app.updates_modules_window()
        root.update_idletasks()
        root.update()
        for child in list(root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                child.destroy()
    finally:
        app._close()
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
    root = tk.Tk()
    MiloDesktop(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
