# Build Jarvis_x.y.z_x64-setup.exe (NSIS). Requires: Node, Poetry, Rust, Python 3.11+.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Poetry deps"
Push-Location "$Root\backend"
poetry install --no-interaction --with dev --no-root
Pop-Location

$iconIco = Join-Path $Root "src-tauri\icons\icon.ico"
if (-not (Test-Path $iconIco)) {
  Write-Host "==> Icons"
  Push-Location "$Root\backend"
  poetry run python "$Root\scripts\make_tauri_icons.py"
  Pop-Location
} else {
  Write-Host "==> Icons (already present)"
}

Write-Host "==> PyInstaller sidecar"
Push-Location "$Root\backend"
poetry run pyinstaller --noconfirm jarvis-backend.spec
Pop-Location

$sidecar = Join-Path $Root "backend\dist\jarvis-backend.exe"
if (-not (Test-Path $sidecar)) {
  throw "PyInstaller did not produce backend/dist/jarvis-backend.exe"
}
$binDir = Join-Path $Root "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item $sidecar (Join-Path $binDir "jarvis-backend.exe") -Force
Write-Host "Sidecar copied to src-tauri/binaries/jarvis-backend.exe"

Write-Host "==> Frontend + Tauri NSIS"
npm install
$env:TAURI_CONFIG = '{"bundle":{"resources":{"binaries/jarvis-backend.exe":"jarvis-backend.exe"}}}'
npm run tauri:build
Remove-Item Env:TAURI_CONFIG -ErrorAction SilentlyContinue

$nsis = Get-ChildItem -Recurse -Path "$Root\src-tauri\target\release\bundle\nsis" -Filter "*setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($nsis) {
  Write-Host "Installer: $($nsis.FullName)"
} else {
  Write-Host "Tauri build finished. Look under src-tauri/target/release/bundle/nsis/"
}
