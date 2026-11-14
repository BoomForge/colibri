# Milo 0.9 — Desktop awareness foundation

**Status: implementation branch, not a packaged 0.9 release.**
Base: Milo 0.8 from feature/milo-desktop-core-20261114.
Work branch: feature/milo-09-desktop-awareness.

This code adds usable, opt-in features without replacing Milo's conversation
model, personality, private database or installed model checkpoints.

## Implemented in source

- **Global quick chat:** Windows OS RegisterHotKey for Ctrl+Shift+M.
  Uses a message-only worker, no keyboard hook or key recording. A native
  compact quick-chat window submits into the selected Milo conversation.
  The shortcut can be switched off in Settings. A visible button remains
  available if another app owns the hotkey.
- **One-time screen capture:** Screen button beside chat attachments. Explicit
  user confirmation before EVERY capture. Milo temporarily hides, captures
  only the primary screen once, and presents the local PNG for review.
  The resulting file is a queued attachment, not an automatic network
  upload or background screenshot. Unsaved captures are removed when cleared,
  on exit and on the next startup. 15 MB attachment limit applies.
- **Tool activity:** local SQLite history of approved/denied/started/
  completed/failed/cancelled events, capped to 300 rows. Only predefined
  tool names and statuses are stored; no prompts, filenames, tokens,
  screenshots, URLs or full search queries. The Tool activity panel can
  inspect and clear it. Existing file and web tool routes report status.
- **Windows settings actions:** deterministic, fixed allowlist for Display,
  Sound, Bluetooth, Storage and Startup Apps settings. Works through the
  Windows tools panel or explicit conversation commands such as "open display
  settings". Shows the exact ms-settings: URI for individual approval.
  It does not use a shell or arbitrary executable, and never modifies
  settings directly.
- **Memory provenance and corrections:** memory manager displays the original
  source of an approved note and allows an explicit owner-approved correction.
  SQLite keeps a local correction-history record; deleting the note also
  deletes its stored correction history. Specialist bots do not automatically
  inherit Milo's private notes.

## Validation

- Core regressions use GitHub Actions milo-core.yml.
- Native Windows GUI smoke uses
  .github/workflows/milo-09-windows-smoke.yml.
- Smoke checks exercise the Tk quick-chat window, activity UI and Windows
  tools panel without triggering a real screen capture or OS-settings launch.
- Automated tests verify the screenshot module, activity bounds, Windows
  action allowlist, and memory correction approval/provenance.

## Still NOT implemented or verified

- **Actual image understanding**: the existing Colibri chat model is
  text-only; a vision-provider module and image inference routing are still
  required. A screenshot attachment cannot yet be analysed as pixels.
- Desktop-wide automated clicking/typing, filesystem moves, task scheduling,
  voice conversations or generalized computer-use agents.
- A new signed or installable 0.9 release. App version remains 0.8
  until an independently checked installer is built and deliberately released.
- Real use of the global hotkey on the owner's PC or screen capture on the
  owner's display; CI tests use mocked capture or GUI smoke only.

## Manual acceptance checklist for the next packaged build

1. With Milo installed, Ctrl+Shift+M opens quick chat from another app.
   In Settings, disabling it frees the shortcut.
2. Screen shows consent first, hides Milo during one primary-screen
   capture, then displays an image preview and queues a local attachment.
3. "Open display settings" asks first, launches only the approved Windows
   page, and confirms the result without pretending to change a setting.
4. Tool activity shows only approved fixed-label statuses and is clearable.
5. An existing approved memory can be viewed with its source, corrected
   with approval, and deleted without orphaned revision records.
6. Conversation, imported personality, model checkpoints, avatar and
   installed module settings survive a later installer upgrade.

Do not publish owner archives, captured screenshots or personal memory data
into GitHub or the updater catalog.
