"""Safe selectable text and fenced code in the native Milo transcript."""
from __future__ import annotations

from dataclasses import dataclass
import re
import webbrowser
import tkinter as tk
from tkinter import font as tkfont

TICKS = chr(96) * 3
FENCE_OPEN = re.compile(r"^" + re.escape(TICKS) + r"([A-Za-z0-9_+.#-]{0,30})[ \t]*$")
FENCE_CLOSE = re.compile(r"^" + re.escape(TICKS) + r"[ \t]*$")
BG = "#15161e"
TEXT = "#e9e9f2"
MUTED = "#9fa0b2"
CODE_BG = "#20222c"
CODE_HEADER = "#292b36"
ACCENT = "#ad92ff"
SELECTION = "#4c4279"
LINK = "#93b9ff"
_URLS = re.compile(r"https://[^\s<>'\"()\[\]]+")


@dataclass(frozen=True)
class ContentBlock:
    kind: str
    content: str
    language: str = ""


def split_fenced_blocks(source: str) -> list[ContentBlock]:
    """Preserve code whitespace without executing any generated markup."""
    if not isinstance(source, str):
        raise TypeError("message must be text")
    result: list[ContentBlock] = []
    buf: list[str] = []
    language = ""
    in_code = False
    for line in source.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if not in_code:
            opening = FENCE_OPEN.fullmatch(stripped)
            if opening:
                if buf:
                    result.append(ContentBlock("text", "".join(buf)))
                buf = []
                language = opening.group(1).lower()
                in_code = True
                continue
        elif FENCE_CLOSE.fullmatch(stripped):
            result.append(ContentBlock("code", "".join(buf), language))
            buf = []
            language = ""
            in_code = False
            continue
        buf.append(line)
    if buf:
        result.append(ContentBlock("code" if in_code else "text", "".join(buf), language))
    return result if result else [ContentBlock("text", "")]


def visual_rows(content: str, width: int, font: tkfont.Font,
                *, nowrap=False, limit=110) -> int:
    width = max(80, width)
    rows = 0
    for line in content.rstrip("\n").split("\n"):
        if nowrap or not line:
            rows += 1
            continue
        pixels = font.measure(line)
        rows += max(1, (pixels + width - 1) // width)
        if rows >= limit:
            return limit
    return max(1, min(rows, limit))


def _copy(widget, value: str):
    """Copy into the Windows clipboard instead of only highlighting text."""
    widget.clipboard_clear()
    widget.clipboard_append(value)
    widget.update_idletasks()


def selected_text(widget: tk.Text) -> str:
    try:
        return widget.get("sel.first", "sel.last")
    except tk.TclError:
        return ""


def copy_selection(widget: tk.Text):
    selected = selected_text(widget)
    if selected:
        _copy(widget, selected)
    return "break"


def find_https_links(text: str):
    """Safe link annotation only; generated text never executes as markup."""
    for match in _URLS.finditer(text):
        url = match.group(0).rstrip(".,;:!?)}]")
        if url.startswith("https://") and len(url) <= 1700:
            yield match.start(), match.start() + len(url), url



class RichMessage(tk.Frame):
    """Read-only selectable Tk Text widgets with per-code Copy buttons."""
    def __init__(self, parent, *, width=660, background=BG):
        super().__init__(parent, bg=background)
        self.background = background
        self.width = width
        self.content = ""
        self.copy_widgets = []
        self.render("")

    def set_wrap_width(self, width: int):
        width = max(220, int(width))
        if abs(self.width - width) > 5:
            self.width = width
            self.render(self.content)

    def _context(self, event, widget, block):
        menu = tk.Menu(self, tearoff=False, bg=CODE_HEADER, fg=TEXT,
                       activebackground=SELECTION)
        selected = selected_text(widget)
        if selected:
            menu.add_command(label="Copy selection",
                             command=lambda: _copy(self, selected))
        menu.add_command(label="Copy block", command=lambda: _copy(self, block))
        menu.add_command(label="Copy full message",
                         command=lambda: _copy(self, self.content))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()

    @staticmethod
    def _select_all(widget):
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _readonly(self, parent, text, *, mono=False, bg=BG, limit=110):
        font = tkfont.Font(family="Consolas" if mono else "Segoe UI", size=10)
        lines = visual_rows(text, self.width - 20, font, nowrap=mono, limit=limit)
        widget = tk.Text(parent, height=lines,
                         wrap="none" if mono else "word",
                         font=font, bg=bg, fg=TEXT,
                         selectbackground=SELECTION, selectforeground=TEXT,
                         relief="flat", bd=0, highlightthickness=0,
                         padx=4 if mono else 0, pady=1, takefocus=True,
                         cursor="xterm", exportselection=False, undo=False)
        widget.insert("1.0", text if text else " ")
        widget.configure(state="disabled")
        widget.bind("<Button-3>",
                    lambda e, w=widget, s=text: self._context(e, w, s))
        widget.bind("<Control-a>",
                    lambda e, w=widget: self._select_all(w))
        widget.bind("<Control-c>",
                    lambda e, w=widget: copy_selection(w))
        widget.bind("<Control-Insert>",
                    lambda e, w=widget: copy_selection(w))
        if not mono:
            for n, (start, end, url) in enumerate(find_https_links(text)):
                tag = "url_" + str(n)
                widget.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
                widget.tag_configure(tag, foreground=LINK, underline=True)
                widget.tag_bind(
                    tag, "<Button-1>",
                    lambda e, address=url: webbrowser.open(address, new=2))
                widget.tag_bind(tag, "<Enter>",
                                lambda e, w=widget: w.configure(cursor="hand2"))
                widget.tag_bind(tag, "<Leave>",
                                lambda e, w=widget: w.configure(cursor="xterm"))
        self.copy_widgets.append(widget)
        return widget

    def _text_block(self, text):
        if text:
            widget = self._readonly(self, text, bg=self.background)
            widget.pack(fill="x", anchor="w", pady=(0, 2))

    def _code_block(self, block: ContentBlock):
        container = tk.Frame(self, bg=CODE_BG, highlightthickness=1,
                             highlightbackground="#3a3a4c")
        container.pack(fill="x", pady=(5, 9))
        toolbar = tk.Frame(container, bg=CODE_HEADER)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text=block.language.upper() if block.language else "CODE",
                 bg=CODE_HEADER, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=11, pady=6)
        tk.Button(toolbar, text="Copy code",
                  command=lambda text=block.content: _copy(self, text),
                  bg=CODE_HEADER, fg=ACCENT, activebackground=SELECTION,
                  activeforeground=TEXT, relief="flat", bd=0,
                  font=("Segoe UI", 8, "bold"), cursor="hand2",
                  padx=8, pady=4).pack(side="right", padx=(3, 6))
        widget = self._readonly(container, block.content,
                                mono=True, bg=CODE_BG, limit=28)
        widget.pack(fill="x", padx=8, pady=(5, 8))
        if visual_rows(block.content, self.width - 20,
                       tkfont.Font(family="Consolas", size=10),
                       nowrap=True, limit=10000) > 28:
            bar = tk.Scrollbar(container, command=widget.yview)
            bar.pack(side="right", fill="y")
            widget.configure(yscrollcommand=bar.set)
        # Horizontal lines remain selectable and copyable verbatim.

    def render(self, content: str):
        if content == self.content and self.copy_widgets:
            return
        self.content = content
        for child in self.winfo_children():
            child.destroy()
        self.copy_widgets = []
        for block in split_fenced_blocks(content):
            if block.kind == "code":
                self._code_block(block)
            else:
                self._text_block(block.content)
        if not self.copy_widgets:
            self._text_block(" ")
