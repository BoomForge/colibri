; Milo Windows installer. Per-user, preserve Models and private data on upgrade.
Unicode True
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define PRODUCT "Milo"
!define VERSION "0.9.4"
!define COMPANY "Pixelwolf"
!define REG "Software\Microsoft\Windows\CurrentVersion\Uninstall\PixelwolfMilo"
!define MUI_ICON "..\assets\Milo.ico"
!define MUI_UNICON "..\assets\Milo.ico"

Name "${PRODUCT}"
OutFile "..\..\Milo-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\Milo"
InstallDirRegKey HKCU "Software\Pixelwolf\Milo" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
BrandingText "Milo — Local Personal Assistant"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; From the in-app updater, /WAITFORPID=<old-Milo-PID> prevents overwriting
; a running exe/DLL while its model and SQLite store are shutting down.
; The previous 0.9.3 updater cannot pass this yet, so an interactive setup
; also gets a short grace period before replacing program files.
Function .onInit
    ${GetParameters} $R0
    ClearErrors
    ${GetOptions} $R0 "/WAITFORPID=" $R1
    IfErrors no_wait
    StrCmp $R1 "" no_wait
    System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i $R1) p .r1'
    StrCmp $1 0 no_wait
    DetailPrint "Waiting for the previous Milo process to close..."
    System::Call 'kernel32::WaitForSingleObject(p r1, i 120000) i .r2'
    System::Call 'kernel32::CloseHandle(p r1)'
    StrCmp $2 0 done_wait
    MessageBox MB_ICONSTOP|MB_OK "Milo is still running. Right-click its tray icon and choose Close Milo before reinstalling."
    Abort
no_wait:
    ; This also covers upgrades launched by Milo 0.9.3, which did not yet
    ; pass a PID. Silent CI installs and uninstallers never wait or relaunch.
    IfSilent done_wait
    Sleep 2000
done_wait:
FunctionEnd

Section "Install Milo" SEC_MAIN
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    ; Never delete install directory on upgrade: Models is user-owned.
    File /r "..\..\dist\Milo\*"

    WriteUninstaller "$INSTDIR\Uninstall.exe"
    CreateDirectory "$SMPROGRAMS\Milo"
    CreateShortCut "$SMPROGRAMS\Milo\Milo.lnk" "$INSTDIR\Milo.exe" "" "$INSTDIR\Milo.exe" 0
    CreateShortCut "$SMPROGRAMS\Milo\Uninstall Milo.lnk" "$INSTDIR\Uninstall.exe"
    CreateShortCut "$DESKTOP\Milo.lnk" "$INSTDIR\Milo.exe" "" "$INSTDIR\Milo.exe" 0

    WriteRegStr HKCU "Software\Pixelwolf\Milo" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "${REG}" "DisplayName" "Milo — Personal Assistant"
    WriteRegStr HKCU "${REG}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${REG}" "Publisher" "${COMPANY}"
    WriteRegStr HKCU "${REG}" "DisplayIcon" "$INSTDIR\Milo.exe"
    WriteRegStr HKCU "${REG}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${REG}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
    WriteRegDWORD HKCU "${REG}" "NoModify" 1
    WriteRegDWORD HKCU "${REG}" "NoRepair" 1

    ; Restart automatically AFTER copying the updated application. The
    ; /S installer used by Windows CI/deployment never starts interactive Milo.
    ; Uninstall has its own section and never launches the program.
    IfSilent skip_launch
    ClearErrors
    Exec '"$INSTDIR\Milo.exe"'
    IfErrors 0 skip_launch
    MessageBox MB_ICONEXCLAMATION|MB_OK "Milo was installed, but Windows could not start it automatically. Launch Milo from the Start menu."
skip_launch:
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    Delete "$SMPROGRAMS\Milo\Milo.lnk"
    Delete "$SMPROGRAMS\Milo\Uninstall Milo.lnk"
    RMDir "$SMPROGRAMS\Milo"
    Delete "$DESKTOP\Milo.lnk"
    DeleteRegKey HKCU "Software\Pixelwolf\Milo"
    DeleteRegKey HKCU "${REG}"

    ; Only remove program files. Don't wipe user's multi-GB model weights.
    RMDir /r "$INSTDIR\_internal"
    RMDir /r "$INSTDIR\Runtime"
    RMDir /r "$INSTDIR\Assets"
    Delete "$INSTDIR\Milo.exe"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    ; Never remove %LOCALAPPDATA%\Milo (chat, persona, backups, modules).
SectionEnd
