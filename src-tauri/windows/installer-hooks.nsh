; Close the UI and sidecar so NSIS can overwrite jarvis-backend.exe.
!macro NSIS_HOOK_PREINSTALL
  nsExec::ExecToLog 'taskkill /F /T /IM Jarvis.exe'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /T /IM jarvis-backend.exe'
  Pop $0
  Sleep 1500
  ClearErrors
  Delete "$INSTDIR\jarvis-backend.exe"
  IfErrors 0 +2
    Delete /REBOOTOK "$INSTDIR\jarvis-backend.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'taskkill /F /T /IM Jarvis.exe'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /T /IM jarvis-backend.exe'
  Pop $0
  Sleep 1500
!macroend
