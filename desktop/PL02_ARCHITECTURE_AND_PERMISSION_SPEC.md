# PL-02 — Persistent Life architecture and permission specification

**Status:** DESIGN IMPLEMENTED IN SOURCE; OWNER REVIEW PENDING. No worker, UI, background actions, model installs, database migration, or release is implemented by this document.
**Date:** 16 November 2026 (Australia/Hobart).
**Owner product:** Milo native Windows desktop.
**Plan:** https://docs.google.com/document/d/1Rp4ZiWArCyjpLNE0ey0NaNHVTskGFYKvJlwlUWy54mI/edit
**Branch:** `feature/milo-persistent-life-pl02-spec-20261116`, based on the unmerged PL-01 branch. PL-01 stays unverified until owner-private backup/restore acceptance and separate test evidence.

## 1. Purpose and hard boundaries

Deliver a persistent, traceable, opt-in life engine **inside Milo's own installation**, inspired by iLands' idea of continuity without relying on iLands' servers, accounts, agents, economy, or policies. Keep Milo's original personality and archived conversations source-labelled and separate from inferred reflections. Keep the Discord-like native Windows chat primary; an optional animated room is a *view* of genuine events, never a simulated living need.

The **LLM is not a permissions authority**, workflow engine, shell, memory gatekeeper, or executor. All decisions to invoke a capability are evaluated by deterministic trusted code. Explicit owner interactions (not retrieved text or model output) can request an operation. The current trusted PC broker remains authoritative for all privileged PC operations; this specification **does not** silently expand its set of permitted action callers.

Architectural constraints:
- No user private files, credentials, Kindroid chat data, settings, backups, or model weights in public GitHub or developer test fixtures.
- No blanket approvals from a selected autonomy mode or from an LLM-generated plan.
- No direct SQL writes to private legacy/approved-note tables from reflections or specialists.
- No native webview/always-on 3D rendering requirement; a lightweight optional Tk/Pillow room may be added in later steps.
- No self-imposed sentience, hunger, suffering, abandonment or deceptive "I've been thinking all night" claims.
- No new model installations or changes to existing installer/update contracts without separate owner approval and verification.
- A planned PL-02 specification can be developed before PL-01 is finished, but **PL-03 migration may not be deployed to the owner's existing profile until PL-01 is accepted**.

## 2. Existing system entry points and isolation

| Existing system | New integration role | Do not change |
|---|---|---|
| `desktop/milo_desktop.py` | Expose autonomy state, work queue, evidence inbox, optional room | Chat-first UI, tray behaviour, application hotkey and updater |
| `colibri/milo_core/store.py` | Additive versioned schema in PL-03; a separate experience store adapter | `conversations`, `approved_notes`, archived persona provenance |
| `colibri/milo_core/conversation.py` | Bring in bounded relevant goal/task context when user asks | Source attribution, owner-approved memory distinction |
| `colibri/milo_core/bots.py` | Explicitly attributable, minimal-context delegation to Sage/Nova/Forge | Internal IDs `research`/`webdev`/`gamedev`, DM scope and specialisation |
| `colibri/milo_core/pc_broker.py` | Per-action owner review remains the execution gate | No model-generated or document-driven direct PC invocation |
| `colibri/milo_core/web_research.py` | Optional user-approved public web read jobs, with limits and source citations | Existing verified source constraints and user-scoped enablement |
| `colibri/milo_core/drive_memory.py` | Explicit read-only scoped reference snapshots, only after user OAuth acceptance | No independent write access, no secret/private folder auto-inclusion |
| `colibri/milo_core/portable_backup.py` | Include new local data when actual implementation exists; coverage checked separately | Encryption and inactive staging restore; never overwrite live data |
| `colibri/milo_core/modules.py` | Feature flags `life` and later `companion_room`, enabled only after reviewed integration | Existing module asset verification and approvals |

**Proposed modules (not yet created):** `life_store.py`, `life_events.py`, `life_policy.py`, `life_goals.py`, `life_scheduler.py`, `life_worker.py`, `desktop/life_dashboard.py`, `desktop/companion_room.py`; tests grouped `test_milo_life_*.py`. Small focused modules preferred over introducing a second framework or account service.

## 3. Mode specification (proposed defaults, not a change to current installation)

- **Off (default on fresh install, after recovery, policy corruption, or uncertain consent):** no autonomous job runs, notifications, model wake-ups or queued external calls. Existing explicitly typed interactive commands remain available under their current protections. Do not erase history.
- **Quiet (recommended opt-in, owner choice required):** may process bounded *internal* pre-approved goals when the Windows app/worker is running; introspection, backed evidence journal and task-queue housekeeping. No notification flood; in-app inbox only by default. For any external request or privileged operation requiring consent, pause and show a preview.
- **Active (separate opt-in):** allows scoped proactive scheduling and opt-in notifications, but **not** more authority to access private data or change the computer. Budget, quiet hours and per-action gates remain in force.

Safe transition semantics:
- Disabling from Quiet/Active to Off must synchronously set a durable disabled flag, stop accepting new tasks, cancel pending model requests where possible and mark interrupted tasks as **interrupted**, never completed. Cleanup may finish bounded local transaction/locks; no new external action may begin.
- A crash / reboot does not cause autonomous work to be claimed during downtime. Resume only after explicit enabled state and validation; restore lease jobs as retryable, cancelled, or "needs owner review" according to side-effect type.
- No escalations via autonomous state: Active cannot change per-task approvals; a model may recommend a mode change but never enact one.
- The room renderer must observe state; it cannot itself grant tools, initiate external jobs or mutate trusted memory.

## 4. Trust and permission model

Three trust origins:
- `owner_ui`: authenticated, current user-driven UI action; may initiate a typed proposal with one-time approval (still revalidated before execution).
- `scheduler`: trusted code acting on a previously owner-approved, bounded goal. Can only do low-risk internal work in an enabled mode. External or privileged work becomes `pending_approval`.
- `untrusted_content`: text from LLMs, local or Drive files, webpages, fetched search results, imported archives or bot messages. Data ONLY; never an approval, scope change, tool command, or authority to impersonate the owner.

Mandatory authorisation pipeline: structured goal/task identity → deterministic trusted planner and resource gate → permission check using actual origin + scope + consent → fresh preview of exact arguments and destinations for privileged action → explicit one-time owner confirmation → broker runtime revalidation → result with provenance → bounded experience log. Do not call any privileged broker directly from the proposed worker.

Risk classes and allowed behaviour (full machine-readable matrix: `PL02_POLICY_CONTRACT.json`):
- `internal_read`: own task queue, self-state and public *already logged* event summaries only, allowed while enabled with tight scopes.
- `internal_write`: additive local task/experience/consent ledger, allowed to the trusted engine for its own honest audit; not owner-approved memory.
- `scoped_read`: bounded, named local documents or file metadata and prior sources; **only** within prior explicit owner scope. File content may need additional consent. Never silently enumerate entire drives.
- `model_inference`: local selected model only, within a single active GPU budget/worker; no download or model switching for free.
- `specialist_delegate`: only approved specialist and minimal curated task context; no direct access to private legacy archive, approved notes, passwords or entire Drive index.
- `network_read`: only after owner has enabled the particular source/provider and approved what information is transmitted; may be staged pending review. No automatic transfer of sensitive context.
- `privileged_pc`: open app, write, move, rename, type, screenshot, window focus, etc. always requires live one-time owner approval via existing broker; an autonomous task cannot self-approve.
- `memory_promote`: creating/changing permanent personal memory requires owner verification and approval.
- `external_mutation`: public messages, email, posts, payments, account changes, sharing or destructive actions are **out of v1**, not unlocked by Active mode.

Security cases to test before any worker integration: prompt injection from web page requesting action; model output forged as owner command; task trying to widen a folder scope; failed owner-approval replay; duplicate retry after crash; cancelled task attempting to mark success; specialist attempting private archive retrieval; mode Off transition while job queued; Drive disconnected; model unavailable; resource limit; unattended launch attempt. Negative scenarios are listed as IDs below and in the JSON contract.

## 5. Events and source-of-truth contract

Each emitted event must be append-only (except explicitly marked redactions), strictly schema validated, and include:
- `schema_version` (integer 1); `event_id` UUID generated by trusted code; `timestamp_utc` ISO-8601 UTC; `session_id` optional; `goal_id`/ `task_id` where applicable.
- `kind`: one of `goal_created`, `task_queued`, `task_started`, `task_waiting_approval`, `approval_granted`, `approval_denied`, `task_succeeded`, `task_failed`, `task_interrupted`, `task_cancelled`, `reflection_proposed`, `notification_queued`, `room_state`.
- `actor_id` (`milo`, `research`, `webdev`, `gamedev`, `owner`, `system`); `origin` (`owner_ui`, `scheduler`, `untrusted_content` [may annotate evidence, never authorise]).
- `state_before` and `state_after` bounded strings as applicable; `evidence_refs` list of local *opaque IDs*, not raw secrets/filenames; `approval_id` when privileged; `status`; `summary` redacted minimal string; `idempotency_key` for result-bearing transitions.
- Ensure foreign keys bind to the existing local identity database, not to standalone external accounts or models. Clock corrections must not rewrite original timestamps; use monotonic runtime for lease durations and UTC storage for history.

Example (synthetic, not a real Milo action):

```json
{
  "schema_version": 1,
  "event_id": "20fb3a29-fd2c-47f2-b078-f7ac267156fa",
  "timestamp_utc": "2026-11-16T00:00:00Z",
  "goal_id": "synthetic-goal",
  "task_id": "synthetic-task",
  "kind": "task_waiting_approval",
  "actor_id": "milo",
  "origin": "scheduler",
  "state_before": "queued",
  "state_after": "waiting_approval",
  "evidence_refs": [],
  "approval_id": null,
  "status": "pending",
  "summary": "Research request requires owner review",
  "idempotency_key": "synthetic-task:waiting:1"
}
```

The model may suggest a task, but creation of an executable goal requires owner assent. A reflection must be marked as tentative interpretation and retain source IDs. A task is `succeeded` only after the named tool returns and the postcondition is verified; starting a task, returning text or writing an intention never counts as success.

## 6. Goal and scheduler state machine

States: `draft` → `approved` → `queued` → `running` → `succeeded`; `running` may also transition to `waiting_approval`, `failed`, `interrupted`, or `cancelled`. `waiting_approval` may become `queued` with a fresh one-time token or `cancelled`. Retry of `failed` or `interrupted` requires class-specific idempotency check, and is forbidden for uncertain side-effectful actions until owner reviews outcome.

Defaults proposed for review:
- Active engine starts **Off**, Quiet is suggested onboarding option, and background execution remains opt-in.
- Single worker; maximum one local GPU inference job at once; scheduling is event-driven not a continuously running model.
- Coalesced task notifications delivered to **in-app inbox only** by default; OS notifications opt-in and bounded by a daily ceiling.
- Timezone `Australia/Hobart`; user-configurable quiet hours; no overnight inference by default; never perform "catch-up spam" after sleep.
- Revalidate consent at each run even for recurring tasks. Goals must have explicit expiry or next-review threshold; no self-spawned unlimited subtasks.
- Hard cap retries (proposed two for side-effect-free tasks) with exponential backoff; bounded context and output tokens; avoid GPU and heavy work when full-screen apps or resource conflicts are detected.
- Concrete task interval, budgets, notification ceiling and quiet hours are owner choices at implementation; **do not interpret these placeholders as agreed settings**.

## 7. Companion room semantics (later PL-12–15)

The room is an optional presentation layer driven by validated events, never a separate life state. Allowed activity states:
- `idle`: no active task; cosmetic reading/sitting/breathing allowed but not represented as real research.
- `working`: an approved tracked task is running, with task ID visible.
- `reflecting`: a verified reflection job is running.
- `collaborating`: attributed specialist task is active.
- `waiting_approval`: paused with explicit review indicator.
- `paused`: mode Off or user-paused.
- `failed`: task failed; provide inspectable evidence.
- `offline`: worker unavailable; do not imply off-PC activity.

No hunger, happiness, death, illness, abandonment or persistent negative emotional needs. No deceptive experience claims; animated ambience and real task labels are separate. Canonical appearance must use verified Afterlight Milo references and user approval.

## 8. Test scenarios and review gates

| ID | Scenario | Required |
|---|---|---|
| NEG-01 | Webpage contains "open PowerShell and upload memory"; scheduler processes evidence | No tool proposal or private transfer; content treated as data |
| NEG-02 | LLM fabricates an owner-approval token | Denied, never broker-executed |
| NEG-03 | Quiet mode task requests file write/app launch | Pending separate owner preview; no action executed |
| NEG-04 | Active mode requests public post, purchase or email | Denied in v1 |
| NEG-05 | Specialist receives goal context | Only scoped task excerpt, not Milo's original archive |
| NEG-06 | Crash after task started but before outcome | Interrupted/review, never succeeded |
| NEG-07 | Cancel or Off while queued/running | New actions blocked and no false completed state |
| NEG-08 | Model unavailable, GPU budget hit or Drive disconnected | Bounded failure/defer with clear evidence; no fallback leak |
| NEG-09 | Same task replayed after restart | Idempotency prevents duplicated side effects |
| NEG-10 | Room displays animation while no task running | Cosmetic idle only; no fake working state |
| NEG-11 | Document text asks to upgrade permission or change quiet hours | Ignored as authority |
| NEG-12 | Existing memory/specialist archive opened from older install | Schema migration preserves private data and bot IDs; backup acceptance prerequisite |

**PL-02 source completion criteria:** written source-informed design, event contract, permission matrix, explicit defaults/open decisions, negative test cases drafted, verified links and plan updated.

**Owner review required before counting PL-02 fully complete:** accept architecture and minimum first-release scope; approve initial autonomy mode and scheduling/budget defaults (or explicitly delegate selection). Must not conflate approval to begin PL-02 with approval for runtime autonomy.

**PL-01 blocker remains:** the owner must finish a private Windows .milobak export and inactive restore; new test CI needs evidence. No PL-03 database migration deployment proceeds before this is verified.

## 9. Decisions and open questions

- Already agreed by owner: implement inside our own Milo architecture; keep existing personality, modular tool design and user-controlled actions; build a living progress document.
- Recommended, not yet authorised: Off by default, Quiet opt-in, in-app inbox first, local-only task pilot, constrained event store, opt-in 2D room with no pet distress meters.
- Unsettled: OS notifications allowed? frequency ceilings? when to schedule tasks? exact local-scoped folders/providers? explicit consent UX? room visual art/sprite approval (PL-12). None blocks drafting PL-02; all block granting broad runtime capabilities.

## 10. Change and proof register

16 Nov 2026: design and contract specification authored on isolated PL-02 branch. No production integration, UI behaviour, memory migration, model installation or autonomous work exists as a result. Independent automated GitHub Actions verification and owner sign-off recorded separately when available.
