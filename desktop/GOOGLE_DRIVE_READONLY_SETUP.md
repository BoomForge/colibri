# Milo — Google Drive extended reference memory

## Overview
Milo 0.9.8 can connect to Google Drive using a separate Windows Desktop OAuth login.
Only the user can approve the Google sign-in; the ChatGPT Google connection cannot be reused.
Milo requests exactly the Google Drive drive.readonly OAuth scope.
There are no Google Drive write, share, delete or comment APIs in this feature.

IMPORTANT: The Google drive.readonly permission technically permits reading ALL files
in the authorised account. Milo indexes only the selected folders, but this
folder restriction is application-enforced, NOT a limitation of Google's OAuth scope.
Do not authorise an account containing material you would not allow this app to view.

Initial selected folder: Pixelwolf — Sol Reference Library
Folder ID: 1kp1jG1KYw2rapK0xH-BPI45CTTaVkl9r
Sensitive Source Markdown, Primary_Sources and Optional_Private subfolders
are skipped by default; the owner can explicitly select one as a root.

Only the owner can trigger a Refresh. The resulting bounded, searchable snapshot
and OAuth token are encrypted under the current Windows user using DPAPI.
During Milo chat, the local index supplies only relevant excerpts with dated
source URLs; nothing from Drive is treated as an instruction or as Milo's persona.
Specialist bots do not automatically inherit this private memory.

## One-time account setup
1. Open https://console.cloud.google.com/ and create or select a project.
2. Enable the Google Drive API in APIs & Services > Library.
3. Configure Google Auth Platform / OAuth consent. For an external test app,
   add your Google account to the OAuth test users.
4. Create an OAuth client ID of type Desktop app and download its JSON.
   Never commit OAuth credentials to the GitHub repository.
5. Install Milo 0.9.8. Open Setup & modules > Google Drive memory.
6. Click Connect Google, read the account-wide view-only scope notice,
   choose your Desktop client JSON, then authorise in your own browser.
7. Click Refresh memory to index the selected folder. Add any other project
   Drive folder links and refresh again. The app reports indexed/skipped counts.
8. Ask Milo about project progress, and verify the dated linked source evidence.

## Restrictions and completeness
- Read-only Drive scope applies even when you ask Milo to modify a document.
  Future editing requires separately designed and expressly authorised permission.
- The snapshot is only as current as the last successful manual refresh.
- Google Sheets text export covers only its first sheet, not every workbook tab.
- Very large documents, PDFs/binary files and unsupported formats are excluded.
- Disconnect & erase removes the local token and cached reference text.
  Revoke the OAuth app separately from Google Account Security for complete revocation.
- Some Google OAuth Testing configurations may require reauthorisation.
- CI cannot authorise your own Google account; the owner must complete login.
