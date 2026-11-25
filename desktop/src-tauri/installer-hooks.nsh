; Sniff keeps its data, models and logs next to the program in %LOCALAPPDATA%\Sniff.
; The uninstaller removes them only when "Delete the application data" is ticked (or /DELETEAPPDATA is given).

; Tauri 2.12's uninstaller has only the checkbox; this lets a silent uninstall (/S /DELETEAPPDATA) ask for the same.
!macro NSIS_HOOK_PREUNINSTALL
  ClearErrors
  ${GetOptions} $CMDLINE "/DELETEAPPDATA" $R0
  ${IfNot} ${Errors}
    StrCpy $DeleteAppDataCheckboxState 1
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    RMDir /r "$LOCALAPPDATA\Sniff\data"
    RMDir /r "$LOCALAPPDATA\Sniff\models"
    RMDir /r "$LOCALAPPDATA\Sniff\logs"
    RMDir /r "$LOCALAPPDATA\Sniff\secrets"
    RMDir "$LOCALAPPDATA\Sniff"
  ${EndIf}
!macroend
