"""Explicit command-line smoke entry point. No model/process is started."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .conversation import MiloConversation
from .filesystem import find_files_by_prefix
from .provider import LocalOpenAIProvider, ProviderError
from .store import LocalMemoryStore


def _default_db() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Milo" / "memory.sqlite3" if local
            else Path.home() / ".local" / "share" / "milo" / "memory.sqlite3")


def main() -> int:
    parser = argparse.ArgumentParser(description="Milo local core (development)")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="Talk to an already-running local model")
    chat.add_argument("--model", required=True, help="ID reported by the inference API")
    chat.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    chat.add_argument("--db", type=Path, default=_default_db())
    chat.add_argument("--chat-id", default="main")
    files = sub.add_parser("files", help="Deterministic filename prefix search")
    files.add_argument("directory", type=Path)
    files.add_argument("prefix")
    files.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    if args.command == "files":
        for path in find_files_by_prefix(
            args.directory, args.prefix, recursive=args.recursive
        ):
            print(path)
        return 0

    provider = LocalOpenAIProvider(model=args.model, base_url=args.base_url)
    with LocalMemoryStore(args.db) as store:
        conversation = MiloConversation(provider, store)
        print("Milo development chat (type /quit to leave)")
        while True:
            try:
                prompt = input("You> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if prompt.strip() == "/quit":
                return 0
            if not prompt.strip():
                continue
            print("Milo> ", end="", flush=True)
            try:
                for chunk in conversation.stream_reply(prompt, chat_id=args.chat_id):
                    print(chunk, end="", flush=True)
                print()
            except (ProviderError, ValueError) as exc:
                print("\nError: " + str(exc), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
