# PL-01 — Milo baseline, private backup and rollback gate

Status: **SOURCE WORK UNDER WAY / OWNER-PC VERIFICATION PENDING**
Plan: https://docs.google.com/document/d/1Rp4ZiWArCyjpLNE0ey0NaNHVTskGFYKvJlwlUWy54mI/edit

## Immutable baseline (verified 15 Nov 2026)

- Repository: `BoomForge/colibri` (public source only).
- Existing integration branch: `feature/milo-09-desktop-awareness`.
- Integration-branch commit *before* creating the PL-01 branch: `cadfc1ea347406220a98bd9a29d8c26c54709abb` ("Milo Drive setup: block folder scope changes during active Google refresh").
- Isolated development branch: `feature/milo-persistent-life-pl01-20261115`; forked from that commit. Never push PL-01 work to the original integration branch or `main`.
- Latest VERIFIED published Windows installer at baseline: Milo 0.9.7, release source `05417679fb5897a9c45976882c27f6afc84bb2ce`; official installer SHA-256: `c88263ae18c1aabfbeff6f80488e9dfb5e555cc7d535ea97f054e50049715ae1`.
- The integration branch also contains 0.9.8 **source** work for read-only Drive memory. A published installer and user-account OAuth acceptance are **not** verified and MUST NOT be inferred.
- Expected owner data root (from source): `%LOCALAPPDATA%\Milo\`; database: `memory.sqlite3`; preferences: `settings\preferences.json`; other local content includes avatars, documents, attachments, and `original_archives` when present. Installed model files may be elsewhere.
- Backups, restored data, owner model files, OAuth credentials, private transcripts, and recovery passphrases MUST remain off GitHub.

## Verified existing backup mechanism

The desktop already exposes "Portable backup (preview)" with "Export encrypted preview backup" and "Restore backup to staging". The code calls `export_bundle`/`import_bundle` from `colibri/milo_core/portable_backup.py`. Backups use an encrypted `.milobak` file (passphrase >=12 characters) and a consistent SQLite snapshot; restore authenticates the contents and extracts into a **new** `%LOCALAPPDATA%\Milo-Restored-<random>` staging folder. It does **not** replace an active profile or automatically activate restored files.

Coverage is bounded: memory SQLite; preferences; avatars; documents; attachments and original archives if available; **model weights only when a model folder is explicitly configured**. The exporter is not a whole-PC clone or automatic discovery of all caches/runtimes/credentials. Existing synthetic backup tests validate round trips, failed authentication, tampering, destination non-overwrite and a linked-model case.

## Owner-side private checkpoint (required to finish PL-01)

This checklist MUST be done on the owner's Windows PC. No private data is requested in chat or uploaded to the repository.

1. Record the **actual installed version** in Milo's UI. Check whether its portable backup interface is present. If not, stop and report the version rather than updating Milo merely to complete this step.
2. Open Milo's **Portable backup (preview)** screen; select **Export encrypted preview backup**. Use a strong unique passphrase and save the `.milobak` **outside** both `%LOCALAPPDATA%\Milo` and the selected model directory (preferably a protected external/offline location).
3. Read the coverage prompt. Confirm the selected model directory is included **only if** that is the intended folder and storage space allows. If not configured, record that model weights need separate protected copies; never assume the .milobak covers them.
4. Confirm Milo reports **"Encrypted preview archive created"** with a file count. Preserve the file and passphrase privately. Check that the file exists at the chosen private destination.
5. In Milo choose **Restore backup to staging**, select the newly created `.milobak`, provide its passphrase, and confirm a successful **"Authenticated backup restored to a safe staging folder"** message. Verify the source profile remains active and unchanged.
6. With File Explorer, confirm the staging folder has `data\memory.sqlite3`, and (as present) `settings`, `avatars`, `archives`, `documents` and `models`. **Do not manually copy staged files over the active profile.** No private names or paths need be shared.
7. Reopen Milo and confirm normal conversation/history and specialist roster still work. Record only yes/no outcomes, local version, whether model weights were included, and any error wording.
8. Keep the encrypted backup secure and inaccessible to public sync; if the backup is partial, inventory missing model/runtime locations for a separate non-destructive safety plan.

Local acceptance record (fill without private paths):
- Installed Milo version: **OWNER PENDING**
- Encrypted .milobak export and secure storage: **OWNER PENDING**
- Model coverage verified or separately accounted for: **OWNER PENDING**
- Staging restore successful and distinct from active profile: **OWNER PENDING**
- Active history / preferences / specialists unchanged: **OWNER PENDING**

## Recovery and rollback

- **Source rollback:** PL-01 changes stay on `feature/milo-persistent-life-pl01-20261115`. Switch back to `feature/milo-09-desktop-awareness` or the original commit `cadfc1ea347406220a98bd9a29d8c26c54709abb`; do not force-push or overwrite branches. No release or install is part of PL-01.
- **Windows rollback:** do not upgrade/install as part of this gate. Preserve the current Milo installer and working profile. The authenticated staged restore is an inspection/DR drill, not an automatic reactivation tool. If the current profile becomes damaged, stop; plan a user-approved restore/migration using the verified backup rather than copying files blindly.
- **No unwarranted approval:** completion of PL-01 grants no permission for autonomous file changes, external connections, model installs, or a new deployment.

## Completion evidence and limits

- [x] Verify repository, release baseline and source commit.
- [x] Create isolated development branch.
- [x] Document data scope, recovery and safe staging rules.
- [ ] Record independent synthetic CI run for additional PL-01 backup test.
- [ ] Verify the owner's **actual** Windows backup/export and safe staging restore.
- [ ] Verify actual installed version and source/model coverage on the owner's PC.

**Do not mark PL-01 VERIFIED COMPLETE until remaining owner-side checks pass.** The implementation plan must keep 0/16 verified in the meantime.
