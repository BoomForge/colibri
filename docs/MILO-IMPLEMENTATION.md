# Milo rebuild — implementation progress & full-PC migration requirement

**14 November 2026 — development branch only.** Not deployed, installed or merged.

## Implemented source foundations

- \`colibri/milo_core/provider.py\`: replaceable, local-first OpenAI-compatible
  streaming inference client. It does not start the Colibri model.
- \`store.py\` and \`conversation.py\`: local SQLite conversation history,
  limited retrieval from explicitly approved notes and a provisional persona.
  The private original Milo/Kindroid memories have **not** been imported.
- \`filesystem.py\`: deterministic filename prefix search.
- \`consultation.py\`: consent-gated request/preview contract for a verified SOL
  second-opinion channel; no external SOL account is connected.
- \`portable_backup.py\`: streaming AES-256-GCM + scrypt encrypted ZIP64 backup,
  with a consistent SQLite snapshot, listed file SHA-256 hashes, copied linked
  model weight files, and authenticated extraction to a **new** directory.
  This is a **backend foundation**, not the finished desktop migration feature.
- \`tests/test_milo_core.py\` and \`tests/test_milo_portable_backup.py\`:
  isolated synthetic regressions without private data or model downloads.

## Required end-user experience: Move Milo to another PC

**Export Milo** (one-click guided flow in desktop Settings): detect and
inventory **all** data managed by Milo, including profiles and avatars,
authentic persona/identity configuration, transcripts, permanent/episodic
memory stores and source provenance, corrections, attachments, custom
settings, optional plugins and their configuration, downloaded model weights
(including weights reached through Colibri link-mode paths), model/runtime
binaries, provider preferences and locally managed capability state. Present
the estimated size, destination picker, coverage list and omissions. Export a
single portable **encrypted** file with a separately retained recovery key/
passphrase, show progress and cancellation, then independently verify the
archive before reporting completion.

**Import Milo** (guided flow after fresh install on new PC): select archive,
unlock it with the recovery key, authenticate/check all bytes, stage the data
without overwriting existing profiles, translate old Windows paths to the new
location, restore profile/assets/memory/models/settings, verify schema
compatibility and model hardware/runtime availability, reconnect only the
services the user explicitly authorises, run a real model-chat and memory
regression check, and present a clear ready/partially-restored status. Keep
the original archive until the user chooses to delete it.

**Strict acceptance criterion:** post-restore data/state parity for chat
history, memories, source records, attachment hashes, profiles, avatar files,
settings, installed model bytes, and plugin configurations, with an explicit
report for any incompatible hardware driver, external service/OAuth token,
machine-bound Windows credential or paid model licence that requires
re-activation. Do not claim a complete migration with silent omissions.

## Backup safety design

- Never rely on machine-bound DPAPI as the **only** backup encryption key;
  newly formatted computers cannot recover such keys.
- Encrypt the **entire** package, not just the memories. Models can be many
  GiB; use streaming ZIP64 rather than loading them all into RAM.
- Copy linked model weights by value instead of archiving a broken pointer
  back to the old PC.
- Handle a running SQLite database through the SQLite backup API, not a raw
  open-file copy.
- Reject damaged/unverifiable bundles and do not overwrite existing active
  profiles. Keep raw originals unchanged during export.
- Archive exports must remain local/private; never commit generated backups,
  conversation histories or private credentials to public GitHub.
- The current \`portable_backup.py\` deliberately accepts only *registered*
  source folders and explicitly records \`complete: false\` in the manifest.
  Whole-installation inventory, a native UI, path rebinding and hardware checks
  are **still to be built**, so current exports must not be described as
  exhaustive one-click migrations.

## Project boundaries

SOL, Conversational Milo, fictional Afterlight Milo and Nyx remain distinct.
Consulting SOL must have visible approval for the exact information transmitted
and requires a genuinely authenticated channel, not an arbitrary GPT API model.

No action in this branch reconfigures the user's running Colibri engine, local
PC, other services, public Pixelwolf site or historical Kindroid/Workshop tools.
