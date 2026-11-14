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

from colibri.milo_core.consultation import prepare_consultation
from colibri.milo_core.conversation import MiloConversation, MiloProfile
from colibri.milo_core.filesystem import find_files_by_prefix
from colibri.milo_core.local_engine import (
    LocalEngine, EngineStartupError, chosen_model, discover_models,
    install_root, managed_models_root, model_valid,
)
from colibri.milo_core.legacy_archive import (
    LegacyArchiveError, import_legacy_archive, inspect_legacy_archive,
    loaded_persona,
)
from colibri.milo_core.portable_backup import BackupError, export_bundle, import_bundle
from colibri.milo_core.provider import LocalOpenAIProvider, ProviderError
from colibri.milo_core.store import LocalMemoryStore

VERSION = "0.1.0-preview"
BG = "#11131e"
SIDEBAR = "#171a29"
PANEL = "#1c2030"
PANEL_BRIGHT = "#252a3d"
FG = "#f3f0fc"
MUTED = "#a5a6b8"
VIOLET = "#b29aff"
ORANGE = "#ffbb85"
ACCENT = "#6653b9"
EDGE = "#3e345f"
FONT = "Segoe UI"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "Milo"
DB_PATH = DATA_DIR / "memory.sqlite3"
CONFIG_PATH = DATA_DIR / "settings" / "preferences.json"
AVATARS = DATA_DIR / "avatars"
ORIGINAL_ARCHIVES = DATA_DIR / "original_archives"
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
        self.root.title("MILO  •  Personal Assistant")
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
        self.current_chat = self.config.get("current_chat") or "main"
        self._build()
        self._refresh_chat_list()
        self._load_chat()
        self._refresh_profile()
        self._autoselect_local_model()
        self.check_connection()
        self.root.after(70, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        self.side = tk.Frame(self.root, bg=SIDEBAR, width=255,
                             highlightbackground=EDGE, highlightthickness=1)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)

        logo = tk.Frame(self.side, bg=SIDEBAR)
        logo.pack(fill="x", padx=16, pady=(22, 16))
        tk.Label(logo, text="M I L O", bg=SIDEBAR, fg=VIOLET,
                 font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(logo, text="LOCAL  /  PERSONAL  /  PRIVATE", bg=SIDEBAR,
                 fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        tk.Frame(self.side, bg=EDGE, height=1).pack(fill="x", padx=15)

        new_button(self.side, "＋  New conversation", self.new_chat, primary=True).pack(
            fill="x", padx=14, pady=(17, 10))
        tk.Label(self.side, text="DIRECT MESSAGES", bg=SIDEBAR, fg=MUTED,
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=17, pady=(9, 7))
        self.chat_list = tk.Listbox(self.side, bg=SIDEBAR, fg=FG,
                                    selectbackground=EDGE, selectforeground=FG,
                                    relief="flat", borderwidth=0, highlightthickness=0,
                                    activestyle="none", font=(FONT, 10),
                                    exportselection=False)
        self.chat_list.pack(fill="both", expand=True, padx=12)
        self.chat_list.bind("<<ListboxSelect>>", self._select_chat)

        tools = tk.Frame(self.side, bg=SIDEBAR)
        tools.pack(fill="x", padx=12, pady=12)
        for caption, action in [
            ("Models", self.models_window),
            ("Restore Milo personality", self.restore_milo_personality),
            ("Memory", self.memory_window),
            ("Find files", self.find_files_window),
            ("Ask SOL", self.sol_window),
            ("Back up / Restore", self.backup_window),
            ("Settings", self.settings_window),
        ]:
            new_button(tools, caption, action).pack(fill="x", pady=3)

        self.profile_row = tk.Frame(self.side, bg=PANEL)
        self.profile_row.pack(fill="x", side="bottom", padx=12, pady=(6, 13))
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

        heading = tk.Frame(main, bg=PANEL, height=76)
        heading.pack(fill="x")
        heading.pack_propagate(False)
        self.milo_icon = tk.Label(heading, text="M", bg=EDGE, fg=VIOLET,
                                  font=(FONT, 18, "bold"), width=3)
        self.milo_icon.pack(side="left", padx=(24, 14), pady=12)
        label_stack = tk.Frame(heading, bg=PANEL)
        label_stack.pack(side="left", pady=12)
        self.milo_title = tk.Label(label_stack, text="Milo", bg=PANEL, fg=FG,
                                    font=(FONT, 15, "bold"))
        self.milo_title.pack(anchor="w")
        self.connection_label = tk.Label(label_stack, text="Checking local model...",
                                         bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.connection_label.pack(anchor="w")
        self.persona_status_label = tk.Label(
            label_stack, text="Personality not imported",
            bg=PANEL, fg=MUTED, font=(FONT, 8))
        self.persona_status_label.pack(anchor="w")
        new_button(heading, "Reconnect", self.check_connection).pack(
            side="right", padx=16, pady=18)

        self.transcript = tk.Text(main, wrap="word", bg=BG, fg=FG,
                                  font=(FONT, 11), padx=28, pady=20,
                                  relief="flat", borderwidth=0,
                                  highlightthickness=0, state="disabled")
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_config("user_name", foreground=ORANGE,
                                   font=(FONT, 11, "bold"), spacing1=17)
        self.transcript.tag_config("milo_name", foreground=VIOLET,
                                   font=(FONT, 11, "bold"), spacing1=17)
        self.transcript.tag_config("body", foreground=FG,
                                   font=(FONT, 11), lmargin1=3, lmargin2=3,
                                   spacing3=8)
        self.transcript.tag_config("time", foreground=MUTED, font=(FONT, 8))
        self.transcript.tag_config("notice", foreground=MUTED, font=(FONT, 10, "italic"))
        self.transcript.bind("<Control-c>", lambda _e: None)

        composer = tk.Frame(main, bg=PANEL, highlightbackground=EDGE,
                             highlightthickness=1)
        composer.pack(fill="x", padx=20, pady=(10, 18))
        self.entry = tk.Text(composer, height=3, wrap="word")
        style_text(self.entry)
        self.entry.pack(side="left", fill="both", expand=True, padx=7, pady=7)
        self.entry.bind("<Return>", self._enter_key)
        self.entry.focus_set()
        actions = tk.Frame(composer, bg=PANEL)
        actions.pack(side="right", padx=(0, 9), pady=8)
        self.send_button = new_button(actions, "Send  ↗", self.send, primary=True)
        self.send_button.pack(fill="x", pady=(0, 5))
        self.stop_button = new_button(actions, "Stop", self.stop)
        self.stop_button.pack(fill="x")
        self.stop_button.configure(state="disabled")
        tk.Label(main, text="Local-first  •  Enter to send  •  Shift+Enter for a new line",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=24,
                                                          pady=(0, 8))

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
        if not path:
            return
        full = AVATARS / path
        if not full.exists():
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
        items = tk.Listbox(
            panel, bg=BG, fg=FG, font=(FONT, 10), height=9,
            selectbackground=EDGE, activestyle="none", relief="flat")
        items.pack(fill="both", expand=True, padx=24, pady=(0, 7))
        for index, model in enumerate(found):
            rel = str(model.relative_to(self.models_root))
            marker = "●  " if rel == self.config.get("model_location") else "   "
            items.insert("end", marker + rel)
            if rel == self.config.get("model_location"):
                items.selection_set(index)
        if not found:
            items.insert("end", "No compatible model folders found yet.")

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
            if not indexes or indexes[0] >= len(found):
                messagebox.showinfo("Milo", "Select a model folder first.",
                                    parent=panel)
                return
            selected = found[indexes[0]]
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
        new_button(actions, "Import existing model", add).pack(side="left")
        new_button(
            panel, "Open Models folder",
            lambda: os.startfile(str(self.models_root))
            if sys.platform == "win32" else None,
        ).pack(fill="x", padx=24, pady=(0, 14))

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
            ("Model weights folder (optional backup source)", "model_directory"),
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
    if "--colibri-service" in sys.argv:
        from desktop.colibri_service import run_service
        args = sys.argv[sys.argv.index("--colibri-service") + 1:]
        return run_service(args)
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
