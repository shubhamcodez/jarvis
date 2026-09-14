# Build Ada_x.y.z_x64-setup.exe (NSIS). Requires: Node, Poetry, Rust, Python 3.11+.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Icons"
Push-Location "$Root\backend"
poetry run python "$Root\scripts\make_tauri_icons.py"
Pop-Location

Write-Host "==> PyInstaller sidecar"
Push-Location "$Root\backend"
poetry add --group dev pyinstaller 2>$null
poetry run pyinstaller --noconfirm ada-backend.spec
Pop-Location

$sidecar = Join-Path $Root "backend\dist\ada-backend.exe"
if (-not (Test-Path $sidecar)) {
  throw "PyInstaller did not produce backend/dist/ada-backend.exe"
}
$binDir = Join-Path $Root "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item $sidecar (Join-Path $binDir "ada-backend.exe") -Force
Write-Host "Sidecar copied to src-tauri/binaries/ada-backend.exe"

Write-Host "==> Frontend + Tauri NSIS"
npm install
$env:TAURI_CONFIG = '{"bundle":{"resources":{"binaries/ada-backend.exe":"ada-backend.exe"}}}'
npm run tauri:build
Remove-Item Env:TAURI_CONFIG -ErrorAction SilentlyContinue

$nsis = Get-ChildItem -Recurse -Path "$Root\src-tauri\target\release\bundle\nsis" -Filter "*setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($nsis) {
  Write-Host "Installer: $($nsis.FullName)"
} else {
  Write-Host "Tauri build finished. Look under src-tauri/target/release/bundle/nsis/"
}
