# Build Jarvis_x.y.z_x64-setup.exe (NSIS). Requires: Node, Poetry, Rust, Python 3.11+.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvScripts = Join-Path $Root "venv\Scripts"
if (-not (Get-Command poetry -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $venvScripts "poetry.exe"))) {
  $env:Path = "$venvScripts;$env:Path"
}

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
if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }
npm install --prefix frontend
if ($LASTEXITCODE -ne 0) { throw "frontend npm install failed with exit code $LASTEXITCODE" }
npm run tauri:build
if ($LASTEXITCODE -ne 0) { throw "tauri build failed with exit code $LASTEXITCODE" }

$search = @(
  (Join-Path $Root "src-tauri\target\release\bundle\nsis"),
  (Join-Path $Root "dist")
)
if ($env:CARGO_TARGET_DIR) {
  $search += (Join-Path $env:CARGO_TARGET_DIR "release\bundle\nsis")
}
$nsis = $search | ForEach-Object {
  if (Test-Path $_) {
    Get-ChildItem -Recurse -Path $_ -Filter "Jarvis_*setup.exe" -ErrorAction SilentlyContinue
  }
} | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $nsis) {
  $nsis = Get-ChildItem -Recurse -Path "$Root\src-tauri" -Filter "Jarvis_*setup.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$distDir = Join-Path $Root "dist"
if ($nsis) {
  New-Item -ItemType Directory -Force -Path $distDir | Out-Null
  Copy-Item $nsis.FullName (Join-Path $distDir $nsis.Name) -Force
  Write-Host "Installer: $(Join-Path $distDir $nsis.Name)"
} else {
  Write-Host "Tauri build finished. Look under src-tauri/target/release/bundle/nsis/"
}
