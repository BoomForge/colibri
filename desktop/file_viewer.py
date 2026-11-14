"""Native read-only preview of an owner-requested local image or document.

Only selected/located supported files may be viewed. No executable launches
or automatic web uploads. User may explicitly choose Windows default app.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from colibri.milo_core.desktop_actions import (
    IMAGE_TYPES, validate_preview_path,
)
from colibri.milo_core.documents import extract_text

BG = "#15161e"
PANEL = "#21222b"
FG = "#e9e9f2"
MUTED = "#a5a6b8"
ACCENT = "#6753b4"


def open_preview(root, source: Path):
    """Return a visible Tk window only after successful file interpretation."""
    path = validate_preview_path(Path(source))
    window = tk.Toplevel(root)
    window.title("Milo · " + path.name)
    window.geometry("920x720")
    window.minsize(480, 360)
    window.configure(bg=BG)
    top = tk.Frame(window, bg=PANEL)
    top.pack(fill="x")
    tk.Label(top, text=path.name[:100], bg=PANEL, fg=FG,
             font=("Segoe UI", 13, "bold")).pack(
                 anchor="w", padx=16, pady=(12, 3))
    tk.Label(top, text="LOCAL PREVIEW · no file contents uploaded",
             bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(
                 anchor="w", padx=16, pady=(0, 10))
    info = {"path": path, "kind": "image" if path.suffix.lower() in IMAGE_TYPES else "text"}
    try:
        if info["kind"] == "image":
            from PIL import Image, ImageOps, ImageTk
            with Image.open(path) as source_image:
                if source_image.width * source_image.height > 32_000_000:
                    raise ValueError("Image too large to preview safely")
                source_image.load()
                image = ImageOps.exif_transpose(source_image).convert("RGBA")
            original_size = image.size
            image.thumbnail((1100, 760), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=window)
            canvas_area = tk.Frame(window, bg=BG)
            canvas_area.pack(fill="both", expand=True)
            label = tk.Label(canvas_area, image=photo, bg=BG)
            label.image = photo
            label.pack(expand=True, padx=10, pady=10)
            info["dimensions"] = original_size
        else:
            content = extract_text(path)
            pane = tk.Frame(window, bg=BG)
            pane.pack(fill="both", expand=True, padx=12, pady=8)
            bar = ttk.Scrollbar(pane)
            bar.pack(side="right", fill="y")
            body = tk.Text(
                pane, wrap="word", font=("Consolas", 10),
                bg=BG, fg=FG, selectbackground=ACCENT,
                relief="flat", bd=0, padx=12, pady=9,
                yscrollcommand=bar.set)
            bar.configure(command=body.yview)
            body.pack(side="left", fill="both", expand=True)
            body.insert("1.0", content)
            body.configure(state="disabled")
            def copy_selection(_event=None):
                try:
                    chosen = body.get("sel.first", "sel.last")
                except tk.TclError:
                    return "break"
                window.clipboard_clear()
                window.clipboard_append(chosen)
                return "break"
            body.bind("<Control-c>", copy_selection)
            info["chars"] = len(content)
    except Exception:
        window.destroy()
        raise

    buttons = tk.Frame(window, bg=PANEL)
    buttons.pack(fill="x", side="bottom")

    def copy_path():
        window.clipboard_clear()
        window.clipboard_append(str(path))

    def open_in_default_app():
        if sys.platform != "win32":
            messagebox.showinfo(
                "External viewer", "This button is for Windows.", parent=window)
            return
        if not messagebox.askyesno(
            "Open in Windows app",
            "Open this document in your configured Windows viewer?\n\n" +
            str(path), parent=window):
            return
        try:
            os.startfile(str(path))  # Windows shell, called only by explicit click.
        except OSError as exc:
            messagebox.showerror(
                "Windows viewer", str(exc)[:250], parent=window)

    tk.Button(buttons, text="Copy file path", command=copy_path,
              bg=PANEL, fg=FG, relief="flat", cursor="hand2",
              padx=12, pady=10).pack(side="left", padx=(12, 2), pady=5)
    tk.Button(buttons, text="Open in Windows viewer",
              command=open_in_default_app,
              bg=ACCENT, fg=FG, relief="flat", cursor="hand2",
              padx=12, pady=10).pack(side="right", padx=(2, 12), pady=5)
    return window, info
