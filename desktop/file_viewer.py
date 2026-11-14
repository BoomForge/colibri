"""Enhanced local-only Windows file preview with safe explicit app handoff.

Image controls: Fit, 100%, zoom, rotate view, mouse wheel and scrollbars.
Text controls: zoom, select/copy, search within file.
Windows Explorer's own Open With dialogue enumerates relevant installed apps.
No subprocess, shell, network or document mutation happens automatically.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from colibri.milo_core.desktop_actions import (
    IMAGE_TYPES, validate_preview_path,
)
from colibri.milo_core.documents import extract_text

BG = "#15161e"
PANEL = "#21222b"
FG = "#e9e9f2"
MUTED = "#a5a6b8"
ACCENT = "#6753b4"
HIGHLIGHT = "#b99aff"


def _button(parent, label, action, *, primary=False):
    control = tk.Button(
        parent, text=label, command=action, relief="flat",
        bg=ACCENT if primary else PANEL, fg=FG, cursor="hand2",
        font=("Segoe UI", 9), padx=10, pady=7,
        activebackground=ACCENT, activeforeground=FG)
    control.pack(side="left", padx=(4, 3))
    return control


def _windows_open_with(path: Path):
    """Windows shell's native, filtered installed-application picker."""
    if sys.platform != "win32":
        raise OSError("Open With is available on Windows only")
    try:
        os.startfile(str(path), "openas")
    except OSError:
        # The system dialog, not a hardcoded executable list; no shell=True.
        subprocess.Popen(
            ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)


def open_preview(root, source: Path):
    """Preview a supported local file; return (window, factual metadata)."""
    path = validate_preview_path(Path(source))
    window = tk.Toplevel(root)
    window.title("Milo · Preview · " + path.name)
    window.geometry("1020x760")
    window.minsize(545, 420)
    window.configure(bg=BG)

    meta = tk.Frame(window, bg=PANEL)
    meta.pack(fill="x")
    tk.Label(
        meta, text=path.name[:100], bg=PANEL, fg=FG,
        font=("Segoe UI", 14, "bold")).pack(
            anchor="w", padx=16, pady=(13, 3))
    size_mb = path.stat().st_size / (1024 * 1024)
    status = tk.Label(
        meta,
        text=f"LOCAL PREVIEW · {size_mb:.2f} MB · no data uploaded",
        bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
    status.pack(anchor="w", padx=16, pady=(0, 10))
    toolbar = tk.Frame(window, bg=PANEL)
    toolbar.pack(fill="x", padx=0, pady=(0, 1))
    stage = tk.Frame(window, bg=BG)
    stage.pack(fill="both", expand=True)
    footer = tk.Frame(window, bg=PANEL)
    footer.pack(fill="x", side="bottom")

    info = {"path": path,
            "kind": "image" if path.suffix.lower() in IMAGE_TYPES else "text"}

    try:
        if info["kind"] == "image":
            from PIL import Image, ImageOps, ImageTk
            with Image.open(path) as source_image:
                if source_image.width * source_image.height > 32_000_000:
                    raise ValueError("Image too large to preview safely")
                source_image.load()
                image = ImageOps.exif_transpose(source_image).convert("RGBA")
            info["dimensions"] = image.size
            canvas = tk.Canvas(stage, bg=BG, highlightthickness=0)
            xbar = ttk.Scrollbar(stage, orient="horizontal", command=canvas.xview)
            ybar = ttk.Scrollbar(stage, orient="vertical", command=canvas.yview)
            canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
            ybar.pack(side="right", fill="y")
            xbar.pack(side="bottom", fill="x")
            canvas.pack(side="left", fill="both", expand=True)
            state = {"image": image, "scale": None, "fit": True,
                     "photo": None, "resize_after": None}

            def draw():
                current = state["image"]
                cw, ch = max(100, canvas.winfo_width()), max(100, canvas.winfo_height())
                if state["fit"]:
                    factor = min(1.0, (cw - 20) / current.width,
                                 (ch - 20) / current.height)
                else:
                    factor = state["scale"] or 1.0
                factor = max(0.1, min(4.0, factor))
                w = max(1, int(current.width * factor))
                h = max(1, int(current.height * factor))
                thumbnail = current.resize((w, h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(thumbnail, master=window)
                state["photo"] = photo
                canvas.delete("preview")
                canvas.create_image(
                    max(0, (cw - w)//2), max(0, (ch - h)//2),
                    anchor="nw", image=photo, tags="preview")
                canvas.configure(scrollregion=(0, 0, max(cw, w), max(ch, h)))
                state["scale"] = factor
                status.configure(
                    text=f"LOCAL IMAGE · {image.width} × {image.height} · "
                         f"{int(factor * 100)}% · no data uploaded")

            def zoom(amount):
                state["fit"] = False
                state["scale"] = min(4.0, max(0.1, (state["scale"] or 1.0) * amount))
                draw()

            def fit():
                state["fit"] = True
                draw()

            def actual():
                state["fit"] = False
                state["scale"] = 1.0
                draw()

            def rotate(angle):
                state["image"] = state["image"].rotate(angle, expand=True)
                draw()

            def resized(_event):
                if state["fit"]:
                    old = state.get("resize_after")
                    if old is not None:
                        window.after_cancel(old)
                    state["resize_after"] = window.after(100, draw)

            canvas.bind("<Configure>", resized)
            canvas.bind("<Control-MouseWheel>",
                        lambda e: zoom(1.15 if e.delta > 0 else 1/1.15))
            _button(toolbar, "Fit", fit)
            _button(toolbar, "100%", actual)
            _button(toolbar, "−", lambda: zoom(1/1.25))
            _button(toolbar, "+", lambda: zoom(1.25))
            _button(toolbar, "↶ Rotate", lambda: rotate(90))
            _button(toolbar, "Rotate ↷", lambda: rotate(-90))
            window.after(80, draw)
        else:
            content = extract_text(path)
            info["chars"] = len(content)
            wrap = tk.Frame(stage, bg=BG)
            wrap.pack(fill="both", expand=True, padx=12, pady=8)
            scrollbar = ttk.Scrollbar(wrap)
            scrollbar.pack(side="right", fill="y")
            body = tk.Text(
                wrap, wrap="word", font=("Consolas", 10),
                bg=BG, fg=FG, selectbackground=ACCENT,
                selectforeground=FG, relief="flat", bd=0, padx=14,
                pady=10, yscrollcommand=scrollbar.set)
            scrollbar.configure(command=body.yview)
            body.pack(side="left", fill="both", expand=True)
            body.insert("1.0", content)
            body.configure(state="disabled")
            font_size = [10]

            def copy_selected(_event=None):
                try:
                    text = body.get("sel.first", "sel.last")
                except tk.TclError:
                    return "break"
                window.clipboard_clear()
                window.clipboard_append(text)
                return "break"

            def text_zoom(direction):
                font_size[0] = max(7, min(24, font_size[0] + direction))
                body.configure(font=("Consolas", font_size[0]))

            def find():
                needle = tk.simpledialog.askstring(
                    "Find in document", "Find text:", parent=window)
                if not needle:
                    return
                match = body.search(needle, "1.0", stopindex="end", nocase=True)
                body.tag_remove("found", "1.0", "end")
                if match:
                    body.tag_add("found", match, f"{match}+{len(needle)}c")
                    body.tag_configure("found", background=ACCENT, foreground=FG)
                    body.see(match)
                    body.focus_set()
                else:
                    messagebox.showinfo("Find", "No matching text.", parent=window)

            body.bind("<Control-c>", copy_selected)
            body.bind("<Control-Insert>", copy_selected)
            _button(toolbar, "Copy selection", copy_selected)
            _button(toolbar, "Find text", find)
            _button(toolbar, "Text −", lambda: text_zoom(-1))
            _button(toolbar, "Text +", lambda: text_zoom(1))
            status.configure(
                text=f"TEXT PREVIEW · {len(content):,} characters · no data uploaded")
    except Exception:
        window.destroy()
        raise

    def copy_path():
        window.clipboard_clear()
        window.clipboard_append(str(path))

    def launch_default():
        if sys.platform != "win32":
            messagebox.showinfo("Default app", "Windows only", parent=window)
            return
        if not messagebox.askyesno(
                "Open in Windows",
                "Open the file using its current Windows default application?\n\n"
                + str(path), parent=window):
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("Windows app", str(exc)[:250], parent=window)

    def choose_application():
        if not messagebox.askyesno(
            "Choose an installed application",
            "Show Windows' Open With dialog for this file?\n\n"
            + str(path), parent=window):
            return
        try:
            _windows_open_with(path)
        except OSError as exc:
            messagebox.showerror("Open With", str(exc)[:250], parent=window)

    tk.Button(
        footer, text="Copy path", command=copy_path,
        bg=PANEL, fg=FG, relief="flat", cursor="hand2",
        padx=12, pady=10).pack(side="left", padx=(10, 2), pady=5)
    tk.Button(
        footer, text="Open in default app", command=launch_default,
        bg=PANEL, fg=FG, relief="flat", cursor="hand2",
        padx=12, pady=10).pack(side="right", padx=(2, 10), pady=5)
    tk.Button(
        footer, text="Open With…", command=choose_application,
        bg=ACCENT, fg=FG, relief="flat", cursor="hand2",
        padx=12, pady=10).pack(side="right", padx=(2, 2), pady=5)
    return window, info
