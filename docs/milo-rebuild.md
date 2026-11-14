# Milo rebuild — isolated first milestone

Status: SOURCE IMPLEMENTATION ONLY; desktop installer and live runtime not verified.
Branch: feature/milo-desktop-core-20261114 (not a production branch).

## Source inspected — 14 November 2026

- BoomForge/colibri provides the Apache-2.0 inference engine, React UI and
  Tauri v2 desktop shell. desktop/README.md confirms the shell currently
  does not manage the model process. Its original files are left intact.
- Private BoomForge/Sollet- includes migration_store.py (persona-isolated
  SQLite), migration_retrieval.py (bounded lexical recall), conversation.py,
  supervisor, and offline private export tools. These are REUSE CANDIDATES,
  not copied wholesale or assumed to be deployed.
- The SOL reference library's 14 November feasibility note documents the
  GTX 1660, 16 GB RAM and previously observed Qwen3-4B Q4_K_M trial with
  36/36 Vulkan layers and a 554-token response at 28.30 tokens/sec.
  This is historical device evidence, NOT a benchmark of this milestone.
- Original conversational Milo/Kindroid and Nyx archives are unverified.
  Afterlight fiction and the public deterministic M.I.L.O. terminal
  are separate from the conversational Milo being reconstructed.

## New optional Python source

colibri/milo_core/ contains:

- A local-first OpenAI-compatible SSE inference adapter; it does not
  install, download or launch any model. Non-loopback access needs
  explicit HTTPS opt-in. There is no default external account connection.
- A streaming conversation assembler, bounded recent chat history,
  and new SQLite chat persistence.
- Explicitly user-approved, attributed memory notes with lexical retrieval.
  This new store is NOT the historical migration database or a completed
  memory-recovery effort. No private source material is in the repository.
- Deterministic filename-prefix search scoped to a caller-selected root.
- A SOL second-opinion draft/approval interface. No connection to the
  user's actual ChatGPT SOL identity has been established. A generic
  OpenAI API response must not be represented as coming from SOL.

## Development commands (not yet verified on user's Windows machine)

With an already-running local Colibri server and a verified model ID:

    python -m colibri.milo_core chat --model ACTUAL_MODEL_ID

Deterministic search of an owner-selected folder:

    python -m colibri.milo_core files "C:\approved-folder" "Milo" --recursive

Offline regressions with synthetic data only:

    python -m unittest discover -s tests -p test_milo_core.py -v

This does not need an inference server, cloud API, or authentic memories.

## Architecture and future tasks

1. Models/providers are task-specific and independently replaceable.
   Conversational Qwen3-4B is a provisional candidate, not a universal model.
2. Restore original conversational Milo personality from verified source
   records and owner review; never borrow Afterlight fiction as real memory.
3. Extend the existing Tauri shell into an installable Discord-inspired
   Windows direct-message experience with Milo and user avatars,
   chat history, presence, attachments, and approval prompts.
4. Add a trusted action broker for computer actions. Model text is never
   permission to execute commands or alter files.
5. Require a context preview and affirmative user consent before sharing
   private data with any third-party provider.
6. SOL consultation starts as an auditable manual exchange. A verified
   authenticated bridge can be developed later; generic GPT API calls
   are not automatically the user's SOL identity or memory.
7. Inventory original Kindroid data privately; preserve unchanged raw
   exports; use owner-approved offline dry-run conversion, not public Git.
8. Run Qwen3-4B multi-turn persona, continuity, and repeated-answer
   regression tests on the actual PC. They have NOT been rerun here.
9. Improve data protection: OS-protected storage, backups, explicit
   retention/deletion, credential storage and resource scheduling.
10. Integrate local deterministic document parsing/search and future
    vision, image generation, speech, email/calendar via separate plugins.

## Evidence classification

INSPECTED: two repositories (Colibri and Sollet), Sollet migration contract,
Tauri desktop foundation, SOL index and feasibility note.
NEW SOURCE: local Python prototype, consultation contract, synthetic tests.
NOT VERIFIED: real Windows application runtime, end-to-end inference,
the user's original private memory import, persona fidelity, desktop UI,
installer, or a direct SOL-to-Milo transport.

No Windows service, web deployment, model installation, private backup import,
email/calendar connection or external send is performed by this branch.
