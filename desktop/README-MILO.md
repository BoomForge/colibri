# Milo Windows Desktop — First Usable Preview

**Status:** early native Windows preview, not a production-hardened installer. Original conversational personality/history is imported privately by the owner, not embedded in the executable.

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

## Restore the original conversational Milo

The app **does not bundle any private Kindroid conversations, user information or
personality files** in its GitHub source or Windows executable.

1. Obtain your private `Milo-Private-Continuity-20261114.zip` in your private
   chat/downloads. **Keep the ZIP intact**; it is not encrypted and contains
   personal conversation history, so store it securely and do not upload it
   to GitHub.
2. Start the updated Windows Milo app; keep your existing `%LOCALAPPDATA%\Milo`
   folder (existing local chat data is preserved).
3. Click **Restore Milo personality** in the left sidebar.
4. Select the private ZIP and approve the summary showing 426 original records
   (212 user, 214 earlier Milo; July 8 to August 7 2026).
5. The app verifies archive checksums, installs the sourced conversational
   persona and indexes each dated chat message into separate SQLite tables.
   It also retains an original copy locally in `%LOCALAPPDATA%\Milo\original_archives`.
   New chats automatically use the imported personality and relevant dated
   historical excerpts.
6. Start a **new conversation** to check voice and historical recall.
   The displayed status changes to **Historical personality imported**.

The backup is *not* proof of every past conversation or original Kindroid
Backstory/Key Memories fields; this particular verified chat.json covers one
month and its old author-generated persona profile was not included. The
private persona is a labelled behavioural reconstruction using that archive
and a separately archived legacy summary. It cannot guarantee the same model
voice or original companion's subjective experience.

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
  adding a corrected note.
- **Explicit private history import:** validates original Kindroid-style chat JSON
  or a signed-by-manifest, checksum-verified personal ZIP; keeps older Milo
  messages separate from current chat history, with bounded evidence retrieval.
  The user must select and approve the private source; no automated upload.
- Deterministic folder filename prefix search with optional recursion.
- Ask SOL: an explicit manual copy/paste consultation preview. No external
  connection or silent transmission.
- Encrypted preview backup with SQLite snapshot, settings and avatars and
  optionally a user-selected model directory. Restores to a NEW inactive
  staging directory. This is not the completed one-click PC migration feature.
- Local files under %LOCALAPPDATA%\Milo\ (memory.sqlite3, settings/preferences.json,
  avatars/).

## Boundaries and remaining work

The user's original conversation archive remains private. A one-click manual
import can now activate the documented conversational personality and indexed
source records. This is not a claim of full historical reconstruction.
A Colibri model server must already be running; automatic model lifecycle is
not yet implemented. Voice, vision, document ingestion and external automation
are future provider modules. The new desktop app is a portable Tk executable,
not the longer-term Tauri shell. Final one-click migration with all inferred
model locations, runtimes, path rebinding, and activation is unfinished.

Windows builds are unsigned previews. Do not bypass security controls to run
them. CI uses synthetic memories and mocked inference; it cannot verify
end-to-end model response quality or the owner's particular machine.
