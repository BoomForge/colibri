"""Milo's compact, avatar-led Discord-inspired conversation presentation.

Purely view-layer code: no model calls, memory writes, external requests, or
parsing of model output as executable markup. The UI uses local profile art and
plain text. Historical transcripts remain selectable through Copy Message.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from desktop.rich_text import RichMessage, _copy, copy_selection, selected_text

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # Python source mode without Pillow still works.
    Image = ImageDraw = ImageTk = None


COLORS = {
    "background": "#15161e",
    "sidebar": "#191a21",
    "header": "#21222b",
    "hover": "#22232d",
    "selected": "#353046",
    "text": "#e9e9f2",
    "muted": "#90929f",
    "milo": "#c59bff",
    "user": "#f2b47e",
    "border": "#30313d",
    "chip": "#383048",
    "green": "#91d8ab",
}
FONT = "Segoe UI"
GROUP_SECONDS = 6 * 60


def parse_timestamp(value: str | datetime | None) -> datetime:
    """SQLite UTC timestamps and old local naive timestamps stay distinct."""
    if isinstance(value, datetime):
        date = value
    elif value:
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            date = datetime.now().astimezone()
    else:
        date = datetime.now().astimezone()
    if date.tzinfo is None:
        # Earlier desktop previews wrote local naive ISO dates.
        return date.astimezone()
    return date.astimezone()


def natural_time(value: str | datetime | None, now: datetime | None = None) -> str:
    date = parse_timestamp(value)
    reference = now or datetime.now().astimezone()
    if reference.tzinfo is None:
        reference = reference.astimezone()
    date = date.astimezone(reference.tzinfo)
    now = reference
    clock = date.strftime("%I:%M %p").lstrip("0")
    delta = (now.date() - date.date()).days
    if delta == 0:
        return f"Today at {clock}"
    if delta == 1:
        return f"Yesterday at {clock}"
    return date.strftime("%d %b %Y") + " at " + clock


def day_label(value: str | datetime | None, now: datetime | None = None) -> str:
    date = parse_timestamp(value)
    reference = now or datetime.now().astimezone()
    if reference.tzinfo is None:
        reference = reference.astimezone()
    date = date.astimezone(reference.tzinfo)
    now = reference
    delta = (now.date() - date.date()).days
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "YESTERDAY"
    return date.strftime("%A, %d %B %Y").upper()


def should_group(previous_role: str | None,
                 previous_time: datetime | None,
                 new_role: str, current_time: datetime) -> bool:
    if previous_role != new_role or previous_time is None:
        return False
    if current_time.date() != previous_time.date():
        return False
    gap = (current_time - previous_time).total_seconds()
    return 0 <= gap <= GROUP_SECONDS


class AvatarBank:
    """Caches circular local avatars for the current view lifetime."""
    def __init__(self):
        self._images = {}

    def get(self, path: str | Path | None, size: int):
        if Image is None or not path:
            return None
        source = Path(path)
        try:
            if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
                return None
            key = (str(source.resolve()), source.stat().st_mtime_ns, size)
            if key in self._images:
                return self._images[key]
            with Image.open(source) as file:
                if file.width * file.height > 24_000_000:
                    return None
                img = file.convert("RGBA")
                side = min(img.size)
                left = (img.width - side) // 2
                top = (img.height - side) // 2
                img = img.crop((left, top, left + side, top + side))
                resolution = getattr(Image, "Resampling", Image).LANCZOS
                img = img.resize((size * 3, size * 3), resolution)
                mask = Image.new("L", img.size, 0)
                ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1),
                                             fill=255)
                img.putalpha(mask)
                img = img.resize((size, size), resolution)
                picture = ImageTk.PhotoImage(img)
            self._images[key] = picture
            return picture
        except (OSError, ValueError, RuntimeError):
            return None


def avatar_widget(parent, bank: AvatarBank, path, initial: str,
                  color: str, *, size: int = 38, background: str | None = None):
    bg = background or COLORS["background"]
    canvas = tk.Canvas(parent, width=size, height=size, bg=bg,
                       borderwidth=0, highlightthickness=0)
    picture = bank.get(path, size)
    if picture is not None:
        canvas.create_image(0, 0, anchor="nw", image=picture)
        canvas.image = picture
    else:
        canvas.create_oval(1, 1, size - 2, size - 2,
                           fill=COLORS["selected"], outline="")
        canvas.create_text(size // 2, size // 2, text=(initial[:1] or "?").upper(),
                           fill=color, font=(FONT, max(11, size // 2), "bold"))
    return canvas


class ChatView(tk.Frame):
    """Scrollable DM timeline with grouping, day separators and streaming edits."""
    def __init__(self, parent, *, background=None):
        bg = background or COLORS["background"]
        super().__init__(parent, bg=bg)
        self.bg_color = bg
        self.avatars = AvatarBank()
        self.names = {"user": "You", "assistant": "Milo"}
        self.paths = {"user": None, "assistant": None}
        self._previous_role = None
        self._previous_sender = None
        self._previous_time = None
        self._previous_day = None
        self.messages = []
        self._wheel_active = False
        self._queued_scroll = False
        self._follow_output = True
        self._load_scroll_revision = 0
        self.canvas = tk.Canvas(self, bg=bg, borderwidth=0, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical",
                                     command=self._scroll_command,
                                     background=COLORS["header"],
                                     activebackground=COLORS["selected"],
                                     troughcolor=bg, width=11)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor="nw")
        self.inner.bind("<Configure>", self._on_content)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self._set_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._set_wheel(False))
        self.canvas.bind_all("<MouseWheel>", self._scroll_wheel, add="+")
        self.canvas.bind_all("<Button-4>", self._scroll_linux, add="+")
        self.canvas.bind_all("<Button-5>", self._scroll_linux, add="+")
        self._wrap_width = 660

    def _scroll_command(self, *args):
        self.canvas.yview(*args)
        self._follow_output = self._near_bottom()

    def show_latest(self):
        """Ensure restored chats end on newest turn after geometry settles."""
        self._load_scroll_revision += 1
        revision = self._load_scroll_revision
        self._follow_output = True
        for delay in (0, 70, 220):
            self.after(delay, lambda rev=revision: self._finish_reload_scroll(rev))

    def _finish_reload_scroll(self, revision):
        if revision != self._load_scroll_revision or not self._follow_output:
            return
        if self.canvas.winfo_exists():
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(1.0)

    def _set_wheel(self, active: bool):
        self._wheel_active = active

    def _scroll_wheel(self, event):
        if self._wheel_active and self.canvas.winfo_exists():
            self.canvas.yview_scroll(-int(event.delta / 120 or (1 if event.delta > 0 else -1)),
                                     "units")
            self._follow_output = self._near_bottom()

    def _scroll_linux(self, event):
        if self._wheel_active and self.canvas.winfo_exists():
            self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
            self._follow_output = self._near_bottom()

    def _on_content(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        new_width = max(250, event.width - 118)
        if abs(new_width - self._wrap_width) > 6:
            self._wrap_width = new_width
            for message in self.messages:
                message["body"].set_wrap_width(new_width)

    def _scroll_bottom(self):
        self._queued_scroll = False
        if self.canvas.winfo_exists():
            # Never call update_idletasks() from an idle callback: it can
            # recursively drive Tk geometry and wedge a windowed build.
            self.canvas.yview_moveto(1.0)

    def _near_bottom(self) -> bool:
        try:
            return self.canvas.yview()[1] >= 0.96
        except (tk.TclError, IndexError):
            return True

    def _maybe_follow(self, *, always=False):
        if (always or (self._follow_output and self._near_bottom())) and not self._queued_scroll:
            self._queued_scroll = True
            self.after_idle(self._scroll_bottom)

    def identity(self, *, user_name: str, milo_name: str,
                 user_avatar=None, milo_avatar=None):
        self.names = {"user": user_name, "assistant": milo_name}
        self.paths = {"user": user_avatar, "assistant": milo_avatar}
        self.avatars = AvatarBank()

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.messages.clear()
        self._previous_day = self._previous_role = self._previous_time = None
        self._previous_sender = None
        self._follow_output = True
        self._maybe_follow(always=True)

    def notice(self, content: str):
        label = tk.Label(self.inner, text=content,
                         bg=self.bg_color, fg=COLORS["muted"], justify="left",
                         wraplength=self._wrap_width, anchor="w",
                         font=(FONT, 10))
        label.pack(fill="x", padx=(62, 18), pady=(14, 4))

    def _separator(self, date):
        row = tk.Frame(self.inner, bg=self.bg_color)
        row.pack(fill="x", padx=(20, 20), pady=(18, 12))
        tk.Frame(row, height=1, bg=COLORS["border"]).pack(
            side="left", fill="x", expand=True, pady=(9, 0))
        tk.Label(row, text=day_label(date), bg=self.bg_color,
                 fg=COLORS["muted"], font=(FONT, 8, "bold"),
                 padx=12).pack(side="left")
        tk.Frame(row, height=1, bg=COLORS["border"]).pack(
            side="left", fill="x", expand=True, pady=(9, 0))

    def add_message(self, role: str, content: str, timestamp=None, *,
                    streaming=False, author_id: str | None = None,
                    author_name: str | None = None, avatar=None):
        if role not in {"assistant", "user"}:
            raise ValueError("invalid chat role")
        date = parse_timestamp(timestamp)
        day = date.date()
        sender = ("user" if role == "user" else author_id or "milo")
        display = self.names[role] if role == "user" else (
            author_name or self.names[role])
        image_path = (self.paths["user"] if role == "user" else
                      avatar if avatar is not None else self.paths["assistant"])
        if day != self._previous_day:
            self._separator(date)
        grouped = (self._previous_sender == sender and
                   should_group(self._previous_role, self._previous_time, role, date))
        row = tk.Frame(self.inner, bg=self.bg_color)
        row.pack(fill="x", padx=(21, 24), pady=(3, 2) if grouped else (10, 3))
        column = tk.Frame(row, bg=self.bg_color, width=43,
                          height=42 if not grouped else 6)
        column.pack(side="left", anchor="n", padx=(0, 13))
        column.pack_propagate(False)
        if not grouped:
            portrait = avatar_widget(column, self.avatars,
                                     image_path, display,
                                     COLORS["milo" if role == "assistant" else "user"],
                                     size=39, background=self.bg_color)
            portrait.pack(anchor="n", pady=(1, 0))
        else:
            tk.Frame(column, width=39, height=4, bg=self.bg_color).pack()
        content_col = tk.Frame(row, bg=self.bg_color)
        content_col.pack(side="left", fill="x", expand=True, anchor="n")
        if not grouped:
            header = tk.Frame(content_col, bg=self.bg_color)
            header.pack(fill="x", anchor="w", pady=(0, 2))
            who = tk.Label(header, text=display, bg=self.bg_color,
                           fg=COLORS["milo" if role == "assistant" else "user"],
                           font=(FONT, 10, "bold"))
            who.pack(side="left", padx=(0, 7))
            badge = tk.Label(header, text="LOCAL AI" if role == "assistant" else "YOU",
                             bg=COLORS["chip"] if role == "assistant" else COLORS["header"],
                             fg=COLORS["milo" if role == "assistant" else "muted"],
                             font=(FONT, 7, "bold"), padx=5, pady=2)
            badge.pack(side="left", padx=(0, 8))
            tk.Label(header, text=natural_time(date), bg=self.bg_color,
                     fg=COLORS["muted"], font=(FONT, 8)).pack(side="left")
        body = RichMessage(
            content_col, width=self._wrap_width, background=self.bg_color)
        body.pack(fill="x", anchor="w")
        body.render(content)
        message = {"role": role, "date": date, "body": body, "row": row,
                   "content": content, "grouped": grouped,
                   "author_id": sender, "author_name": display,
                   "streaming": streaming}
        self.messages.append(message)
        for widget in [body, content_col, row]:
            widget.bind("<Button-3>", lambda event, msg=message: self._copy_menu(event, msg))
        self._previous_role = role
        self._previous_sender = sender
        self._previous_day = day
        self._previous_time = date
        self._maybe_follow(always=self._follow_output)
        return message

    def _copy_menu(self, event, message):
        menu = tk.Menu(self, tearoff=False,
                       bg=COLORS["header"], fg=COLORS["text"],
                       activebackground=COLORS["selected"])
        def copy():
            self.clipboard_clear()
            self.clipboard_append(message["content"])
        menu.add_command(label="Copy message", command=copy)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()

    def selectable_view(self):
        """One continuous selectable transcript, unlike the per-message widgets.

        Users can drag across multiple messages, Ctrl+C, Ctrl+A, copy selected,
        or copy all. No changes to the permanent conversation store.
        """
        window = tk.Toplevel(self)
        window.title("Milo · Select conversation text")
        window.geometry("820x700")
        window.minsize(520, 340)
        window.configure(bg=COLORS["header"])
        tk.Label(
            window,
            text="Select text across messages · Ctrl+C copies selection · Ctrl+A selects all",
            bg=COLORS["header"], fg=COLORS["muted"],
            font=(FONT, 10)).pack(anchor="w", padx=18, pady=(15, 10))
        reader = tk.Frame(window, bg=COLORS["background"])
        reader.pack(fill="both", expand=True, padx=18)
        bar = tk.Scrollbar(reader)
        bar.pack(side="right", fill="y")
        body = tk.Text(
            reader, wrap="word", relief="flat", borderwidth=0,
            background=COLORS["background"], foreground=COLORS["text"],
            selectbackground=COLORS["selected"],
            selectforeground=COLORS["text"], font=(FONT, 10),
            padx=14, pady=14, exportselection=False, cursor="xterm",
            yscrollcommand=bar.set)
        bar.configure(command=body.yview)
        body.pack(side="left", fill="both", expand=True)
        segments = []
        for message in self.messages:
            speaker = message["author_name"]
            segments.append(
                speaker + " · " + natural_time(message["date"]) + "\n" +
                message["content"])
        transcript = "\n\n".join(segments)
        if len(transcript) > 2_000_000:
            transcript = transcript[:2_000_000] + "\n[Long chat truncated]"
        body.insert("1.0", transcript)
        body.configure(state="disabled")
        body.bind("<Control-c>", lambda _event: copy_selection(body))
        body.bind("<Control-Insert>", lambda _event: copy_selection(body))
        body.bind(
            "<Control-a>", lambda _event: (
                body.tag_add("sel", "1.0", "end-1c") or "break"))
        actions = tk.Frame(window, bg=COLORS["header"])
        actions.pack(fill="x", padx=18, pady=12)
        tk.Button(
            actions, text="Copy selection", relief="flat", bg=COLORS["selected"],
            fg=COLORS["text"], activebackground=COLORS["chip"],
            command=lambda: copy_selection(body), padx=14, pady=8,
            cursor="hand2").pack(side="left", padx=(0, 9))
        tk.Button(
            actions, text="Copy entire conversation", relief="flat",
            bg=COLORS["selected"], fg=COLORS["text"],
            activebackground=COLORS["chip"],
            command=lambda: _copy(window, transcript), padx=14, pady=8,
            cursor="hand2").pack(side="left")
        body.focus_set()
        return window, body

    def append_to(self, message: dict, addition: str):
        if not addition:
            return
        follow = self._near_bottom()
        message["content"] += addition
        message["body"].render(message["content"])
        self._maybe_follow(always=follow)

    def finish(self, message: dict, *, status: str | None = None):
        message["streaming"] = False
        if status:
            suffix = "\n" + status
            message["content"] += suffix
            message["body"].render(message["content"])
        self._maybe_follow(always=self._follow_output)


class ConversationList(tk.Frame):
    """Compact scrollable DM list with selected state and avatar icons."""
    def __init__(self, parent, on_select, *, on_delete=None, background=None):
        bg = background or COLORS["sidebar"]
        super().__init__(parent, bg=bg)
        self._on_select = on_select
        self._on_delete = on_delete
        # Persistent menu: Tk's tk_popup() returns after posting, not after
        # the user chooses a command. Destroying it in the popup's finally
        # block made "Delete conversation" silently do nothing on Windows.
        self._menu_chat_id = None
        self._delete_menu = tk.Menu(
            self, tearoff=False, bg=COLORS["header"],
            fg=COLORS["text"], activebackground=COLORS["selected"])
        self._delete_menu.add_command(
            label="Delete conversation…", command=self._queue_delete)
        self.avatars = AvatarBank()
        self.avatar = None
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                width=9, troughcolor=bg)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win_id = self.canvas.create_window((0, 0), window=self.inner,
                                                anchor="nw")
        self.inner.bind("<Configure>", lambda _e:
                        self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e:
                         self.canvas.itemconfigure(self.win_id, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e:
                         self.canvas.yview_scroll(-int(e.delta / 120 or 1), "units"))

    def _queue_delete(self):
        """Dismiss the popup before the confirmation dialog starts."""
        chat_id = self._menu_chat_id
        if self._on_delete is not None and chat_id is not None:
            # Defer until Tk has finished processing the menu selection.
            self.after_idle(self._on_delete, chat_id)

    def _conversation_menu(self, event, chat_id):
        if self._on_delete is None:
            return
        self._menu_chat_id = chat_id
        try:
            self._delete_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._delete_menu.grab_release()
        # Do not destroy the menu here: tk_popup() is non-blocking on
        # Windows. The same menu survives until the sidebar is destroyed.

    def set_avatar(self, path):
        self.avatar = path
        self.avatars = AvatarBank()

    def set_chats(self, chats, selected, avatar_by_chat=None):
        """Each private DM may display a different specialist portrait."""
        avatar_by_chat = avatar_by_chat or {}
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.rows = {}
        for chat_id, title in chats:
            active = chat_id == selected
            bg = COLORS["selected"] if active else COLORS["sidebar"]
            row = tk.Frame(self.inner, bg=bg, cursor="hand2")
            row.pack(fill="x", padx=(7, 6), pady=2)
            tk.Frame(row, bg=COLORS["milo"] if active else bg,
                     width=3).pack(side="left", fill="y")
            portrait = avatar_by_chat.get(chat_id) or self.avatar
            fallback = (title.lstrip("#@ ").strip() or "M")[:1].upper()
            image = avatar_widget(row, self.avatars, portrait, fallback,
                                  COLORS["milo"], size=28, background=bg)
            image.pack(side="left", padx=(8, 9), pady=7)
            clean_title = title.strip().replace("\n", " ")
            if len(clean_title) > 26:
                clean_title = clean_title[:25] + "…"
            title_label = tk.Label(row, text=clean_title or "Conversation",
                                   bg=bg, fg=COLORS["text"] if active else COLORS["muted"],
                                   anchor="w", font=(FONT, 10, "bold" if active else "normal"))
            title_label.pack(side="left", fill="x", expand=True, padx=(0, 5))
            for item in (row, image, title_label):
                item.bind("<Button-1>", lambda _e, ident=chat_id: self._on_select(ident))
                item.bind("<Button-3>", lambda e, ident=chat_id:
                          self._conversation_menu(e, ident))
            self.rows[chat_id] = row
