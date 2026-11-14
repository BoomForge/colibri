"""Read-only system details; optional Milo module installed from first-party release."""
from __future__ import annotations
from pathlib import Path
import os
import platform
import shutil
import sys
import tkinter as tk


def show_details():
    root = tk._default_root
    window = tk.Toplevel(root)
    window.title("Milo · System information")
    window.geometry("480x280")
    window.configure(bg="#1c2030")
    home = Path.home()
    total, used, free = shutil.disk_usage(home)
    lines = [
        ("Platform", platform.platform()),
        ("Python runtime", platform.python_version()),
        ("User home", str(home)),
        ("Free disk", f"{free / (1024**3):.1f} GiB"),
        ("Total disk", f"{total / (1024**3):.1f} GiB"),
        ("Local application data", os.environ.get("LOCALAPPDATA", "(not set)")),
    ]
    for key, value in lines:
        tk.Label(window, text=f"{key}: {value}", anchor="w", justify="left",
                 bg="#1c2030", fg="#f3f0fc", wraplength=450,
                 font=("Segoe UI", 10)).pack(fill="x", padx=16, pady=5)


def register(api):
    api.add_action("System information", show_details)
