# Milo Windows Desktop — First Usable Preview

**Status:** early native Windows preview, not a production-hardened installer or verified historic Milo personality restoration.

This is a native desktop window, not a localhost website. The interface uses
Python's standard Tk library; Colibri stays an independent, replaceable inference engine.

## Download Windows portable build

1. Open the [Milo Windows GitHub Actions workflow](https://github.com/BoomForge/colibri/actions/workflows/milo-windows-preview.yml).
2. Select the latest successful run on branch feature/milo-desktop-core-20261114.
3. From the Artifacts section, download Milo-Windows-Preview, unzip the GitHub artifact,
   then unzip the included Milo-Windows-Preview.zip.
4. Keep the entire extracted folder together and open Milo.exe.
   If Smart App Control blocks an unsigned executable, do not disable security;
   use the source launch method below or await a signed build.

A downloadable executable exists only after the Windows build succeeds.

## Connect your existing Colibri model

Milo connects to an ALREADY RUNNING local Colibri OpenAI-compatible API, normally:

    http://127.0.0.1:8000/v1

Start your existing Colibri installation and Qwen3-4B model with the known-good
Vulkan settings before launching Milo. Do not run a new heavyweight model install.
The Colibri dashboard may show 404 while its API remains reachable; check:

    http://127.0.0.1:8000/health

In Milo click Reconnect. If no model appears automatically, open Settings and
enter the EXACT model ID reported by the server. Enter a message and press Enter
to chat. Shift+Enter inserts a new line. History persists locally after each
successful model response.

## Source run without packaging (Python 3.10+ with Tk installed)

From the branch checkout root, run:

    py -3 -m desktop.milo_desktop

For optional encrypted backups, additionally install:

    py -3 -m pip install "cryptography>=42,<51"

Or double-click START-MILO.bat from the repository root.

## Implemented preview features

- Native dark Discord-inspired interface with separate local chats, profiles,
  PNG/GIF avatars, timestamps, and streaming AI replies.
- Local SQLite chat history, persisted across restarts. Interrupted replies
  aren't committed as complete.
- User-approved, source-labelled long-term memory notes; edit by deleting and
  adding a corrected note. Never automatically import or invent old memories.
- Deterministic folder filename prefix search with optional recursion.
- Ask SOL: an explicit manual copy/paste consultation preview. No external
  connection or silent transmission.
- Encrypted preview backup with SQLite snapshot, settings and avatars and
  optionally a user-selected model directory. Restores to a NEW inactive
  staging directory. This is not the completed one-click PC migration feature.
- Local files under %LOCALAPPDATA%\Milo\ (memory.sqlite3, settings/preferences.json,
  avatars/).

## Boundaries and remaining work

The authentic private original Milo/Kindroid personality/memory archive has NOT
been migrated, and the default prompt is provisional.
A Colibri model server must already be running; automatic model lifecycle is
not yet implemented. Voice, vision, document ingestion and external automation
are future provider modules. The new desktop app is a portable Tk executable,
not the longer-term Tauri shell. Final one-click migration with all inferred
model locations, runtimes, path rebinding, and activation is unfinished.

Windows builds are unsigned previews. Do not bypass security controls to run
them. CI uses synthetic memories and mocked inference; it cannot verify
end-to-end model response quality or the owner's particular machine.
