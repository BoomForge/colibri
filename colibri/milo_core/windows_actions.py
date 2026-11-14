"""Allowlisted, approval-gated Windows Settings actions.

No shell, arbitrary executable, user-provided URI, or settings mutation.
These plans only OPEN official Windows settings pages after confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys


@dataclass(frozen=True)
class WindowsAction:
    action_id: str
    title: str
    uri: str


_WINDOWS_SETTINGS = {
    "display": WindowsAction("display", "Display settings", "ms-settings:display"),
    "sound": WindowsAction("sound", "Sound settings", "ms-settings:sound"),
    "bluetooth": WindowsAction(
        "bluetooth", "Bluetooth settings", "ms-settings:bluetooth"),
    "storage": WindowsAction("storage", "Storage settings", "ms-settings:storagesense"),
    "startup": WindowsAction("startup", "Startup apps", "ms-settings:startupapps"),
}


def available_actions() -> list[WindowsAction]:
    return list(_WINDOWS_SETTINGS.values())


def plan_action(action_id: str) -> WindowsAction:
    try:
        return _WINDOWS_SETTINGS[action_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("Unsupported Windows action") from exc


def parse_action_request(message: str) -> WindowsAction | None:
    """Only explicit commands; do not turn hypothetical questions into actions."""
    if not isinstance(message, str) or not 0 < len(message) <= 240:
        return None
    text = message.casefold().strip()
    if (re.search(r"\b(?:how|why|what if|explain|tutorial)\b", text)
            or "?" in text
            or not re.search(r"\b(?:open|launch|show)\b", text)):
        return None
    for token, action in (
        ("display settings", "display"),
        ("sound settings", "sound"),
        ("bluetooth settings", "bluetooth"),
        ("storage settings", "storage"),
        ("startup apps", "startup"),
    ):
        if re.search(r"\b" + re.escape(token) + r"\b", text):
            return plan_action(action)
    return None


def execute_action(action: WindowsAction, *, approved: bool = False,
                   launcher=None, platform: str | None = None) -> None:
    if not approved:
        raise PermissionError("Opening Windows Settings needs owner approval")
    if not isinstance(action, WindowsAction):
        raise ValueError("Invalid Windows action")
    if plan_action(action.action_id) != action:
        raise ValueError("Action was modified after approval")
    if (platform or sys.platform) != "win32":
        raise OSError("Windows Settings actions require Windows")
    if launcher is None:
        launcher = os.startfile
    launcher(action.uri)
