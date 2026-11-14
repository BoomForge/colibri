# Milo 0.9 — Desktop awareness, local vision and reviewed actions

**Status:** working development branch and unsigned Windows installer PREVIEW.
**Production release:** Milo 0.8.0 remains unchanged. No 0.9 GitHub Release
has been published or offered through Milo's automatic updater.
**Branch:** feature/milo-09-desktop-awareness.
**Draft PR:** https://github.com/BoomForge/colibri/pull/1

## Implemented

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
download **Milo-Installer-0.9.1-Preview**. Extract the artifact ZIP and run
Milo-Setup-0.9.1.exe. Do not install if the run is failed or incomplete.
No external Ollama or vision weights are bundled in the Windows installer.
The previous 0.8.0 is the publicly published fallback release.

The preview workflow verifies its pinned 13-engine Colibri runtime checksums,
runs Milo regressions, packages the current Tk Windows app and official Milo
icon, tests the frozen binary, runs a silent NSIS install, and tests an upgrade
with a model preservation sentinel. It NEVER publishes a GitHub Release.

## Not yet tested/implemented

- End-to-end local vision output on the owner's Windows GPU and actual
  selected Ollama model (CI uses synthetic / mocked image responses).
- Real desktop hotkey conflicts and screenshot capture on the owner's monitor.
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

Do not commit owner screenshots, chat transcripts, private memory archives,
personal backups, or model weights to this repository or public releases.
