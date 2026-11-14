; Milo Windows installer. Per-user, preserve Models and private data on upgrade.
Unicode True
!include "MUI2.nsh"

!define PRODUCT "Milo"
!define VERSION "0.9.0"
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
