# Milo 0.9 — Desktop awareness, local vision and reviewed actions

**Status:** working development branch and unsigned Windows installer PREVIEW.
**Released baseline:** Milo 0.9.2 is available from GitHub Releases. This document
covers Milo 0.9.4, built and released separately after its Windows CI checks.
**Branch:** feature/milo-09-desktop-awareness.
**Draft PR:** https://github.com/BoomForge/colibri/pull/1

## Install/update behaviour — 0.9.4

- **Automatic restart:** A normal interactive Windows install of Milo 0.9.4
  opens the newly installed Milo executable automatically after the files,
  shortcuts and version metadata finish installing. No extra launch button
  or manual Start menu action is required.
- **Safe in-app upgrade:** After the owner approves a verified update, Milo
  passes its own Windows PID to the installer, shuts down the local model,
  tray service and database, then the installer waits for the old process
  to exit (up to 120 seconds) before replacing its installed program files.
  A failed wait aborts the installation rather than overwriting running
  binaries. The installer then reopens Milo.
- **Upgrading from 0.9.3:** Older clients cannot pass their PID; the new
  interactive installer includes a short grace period for their shutdown.
  Manual installers should be run after choosing **Close Milo** in its tray
  menu to avoid a running copy during replacement.
- **Unattended safety:** Silent /S installation does not launch Milo and
  the uninstaller never opens the app. Windows CI covers silent install
  no-restart, a controlled PID wait, and preserving installed Models.
- **What is still unverified:** Windows automation validates the installer,
  compile and PID wait but does not exercise a real desktop installer GUI
  launch on the owner's PC. If Windows blocks the unsigned installer or
  app, reopening may require owner action.

## Implemented

### Windows system tray (0.9.3)
- The application's window X and Alt+F4 hide Milo in the notification area;
  the Windows minimise button also hides it to the same tray.
- Tray left-click or **Open Milo** from its right-click menu restores the
  main window, including panels hidden alongside it.
- **Close Milo** in the tray right-click menu is the normal shutdown action.
  This stops the bundled Colibri engine, saves/retains local data and removes
  the notification-area icon. A separately approved app update also exits
  deliberately to allow installer replacement.
- Milo's conversation database, running model, hotkey and background process
  are not shut down when its windows are merely hidden.
- The tray must be available before Milo hides. If icon creation fails,
  the main app stays accessible instead of vanishing; a failed icon host
  triggers recovery of any hidden window.
- The tray backend is bundled with PyInstaller using pystray 0.19.5 and
  Pillow; no always-on external service, new user account, or telemetry.
- Native Windows CI verifies the tray package is present in the frozen
  executable and tests the Tk hide/restore lifecycle. Real mouse and shell
  tray behaviour must still be checked on the owner's machine.

### Quick chat and screen
- Windows global Ctrl+Shift+M shortcut using RegisterHotKey (no key
  interception or keylogging), with an opt-out in Settings.
- Compact, always-on-top quick-chat composer; it submits into the existing
  Milo conversation rather than creating a parallel identity or database.
- Every PRIMARY-screen capture requires an explicit yes/no confirmation.
  The application hides during capture, previews the temporary local PNG and
  queues it as a chat attachment. No continuous screen sharing.

### Independent local image understanding
- Local vision is OFF by default and is separately toggleable in Milo's
  feature manager and Settings. It does not replace Colibri's text model.
- The first backend is Ollama's local image API with an explicitly selected
  model, default suggestion qwen3-vl:2b. Milo does NOT install Ollama or model
  weights automatically.
- To enable after installing Ollama, the owner may choose to run:
      ollama pull qwen3-vl:2b
  Qwen3-VL requires a compatible Ollama version (0.12.7 or later).
- Settings: Enable local image analysis; choose installed Ollama model and
  loopback URL, normally http://127.0.0.1:11434.
- On sending attached images, Milo asks permission for that exact request.
  Only the selected image(s) and present question are sent to loopback.
  The adapter rejects remote hosts, proxy configuration and redirects;
  images are resized for inference. Maximum three images per vision request.
- The returned analysis is labelled as a separate model's observation and
  supplied to the normal conversation model only after completion.
  Failure produces an explicit error, not an invented visual description.
- The vision model is requested to unload after each analysis. This helps
  conserve VRAM, but real performance with a GTX 1660 6GB and a concurrent
  chat model is **not yet verified**.

### Local actions and memory
- Read-only deterministic filename and file-preview tools remain available.
- A new File organiser accepts two owner-selected local folders and a literal
  filename filter; it shows the EXACT proposed file list before approving
  a same-drive move. Maximum 100 supported non-executable asset files per
  plan. Preflight checks detect modified sources, symlinks and destination
  collisions; operations use no-overwrite hardlinks on the same filesystem.
  Depending on permissions or filesystem type, a move may be rejected rather
  than falling back to an unsafe operation.
- Explicit Windows Settings requests (Display, Sound, Bluetooth, Storage,
  Startup Apps) are allowlisted and confirmed separately before launch.
  No arbitrary executable or shell commands are accepted.
- Source-labelled approved memory notes can be inspected, corrected with
  explicit approval and removed. The original source and correction count
  remain local. No automatic promotion of chat text to long-term memory.
- Fixed-label local activity events record tool type and outcome without
  logging screenshots, file paths, prompts or source document contents.

## Build verification and download

The unsigned 0.9 installer preview is produced by:
https://github.com/BoomForge/colibri/actions/workflows/milo-09-preview-installer.yml

Use the latest SUCCESSFUL run for this branch; open its Artifacts section and
download **Milo-Installer-0.9.4-Preview**. Extract the artifact ZIP and run
Milo-Setup-0.9.4.exe. Do not install if the run is failed or incomplete.
No external Ollama or vision weights are bundled in the Windows installer.
Milo 0.9.2 remains available as the previous GitHub Release.

The preview workflow verifies its pinned 13-engine Colibri runtime checksums,
runs Milo regressions, packages the current Tk Windows app and official Milo
icon, tests the frozen binary, runs a silent NSIS install, and tests an upgrade
with a model preservation sentinel. A successful up-to-date branch run publishes
an unsigned, SHA-256-labelled GitHub pre-release, which the built-in updater
can discover. Failed or superseded runs cannot publish.

## Not yet tested/implemented

- End-to-end local vision output on the owner's Windows GPU and actual
  selected Ollama model (CI uses synthetic / mocked image responses).
- Real desktop hotkey conflicts, screenshot capture and tray click behaviour on
  the owner's Windows desktop.
- Voice, computer-wide autonomous clicking/typing, arbitrary Windows settings
  changes, email/calendar actions and ongoing agentic automation.
- Full-featured cross-volume file moves or bulk operations without previews.
- Code signing. Do not bypass Windows Smart App Control.

## Owner acceptance

1. Back up %LOCALAPPDATA%\Milo and the existing Models directory.
2. Install the verified preview, confirm your original conversations,
   personality, avatars and model selections remain.
3. Test Ctrl+Shift+M, disable it in Settings, and re-enable if desired.
4. Capture a screen only after consent; inspect its local preview.
5. Optionally install Ollama and a small vision model manually, enable vision
   in Settings, attach one screenshot, approve its analysis and verify that
   Milo uses the output rather than claiming direct visual perception.
6. Try File organiser on disposable test assets. Preview, approve and check
   contents; test destination name collisions before production files.
7. Confirm Tool activity records only actions/statuses, not file paths.
8. Test Windows Settings shortcuts with explicit approval.
9. Close the main window with X: Milo must disappear from the taskbar while its
   purple icon remains in the Windows tray (possibly in the ^ overflow area).
   Click the tray icon to reopen Milo and confirm the same conversation exists.
10. Press Minimise: Milo must hide to the tray without stopping local inference.
    Right-click the tray icon and choose Close Milo; verify the process exits.
    Reopen Milo from Start to check that conversations and models are retained.

Do not commit owner screenshots, chat transcripts, private memory archives,
personal backups, or model weights to this repository or public releases.
