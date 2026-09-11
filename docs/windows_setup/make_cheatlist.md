# Make Cheatlist

Git Bash is the primary terminal for this project on Windows. Use the
command style that matches how `make.exe` is installed.

## `make` available in `PATH` (recommended)

```bash
make treemap
make lint
make package
make ai-refresh
```

## Git Bash without `make.exe` in `PATH`

Call the repository's PowerShell wrapper explicitly from Git Bash:

```bash
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 treemap
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 lint
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 package
```

## Git Bash with an explicit corporate `make.exe`

Pass the fixed binary path through to the wrapper:

```bash
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 \
  -MakePath 'C:\Users\user\tools\make\bin\make.exe' treemap
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 \
  -MakePath 'C:\Users\user\tools\make\bin\make.exe' lint
powershell.exe -ExecutionPolicy Bypass -File ./scripts/windows/run_make.ps1 \
  -MakePath 'C:\Users\user\tools\make\bin\make.exe' package
```

## If you're still using a PowerShell terminal

The wrapper also runs directly from PowerShell:

```powershell
.\scripts\windows\run_make.ps1 treemap
.\scripts\windows\run_make.ps1 lint
.\scripts\windows\run_make.ps1 package
.\scripts\windows\run_make.ps1 ai-refresh
```
