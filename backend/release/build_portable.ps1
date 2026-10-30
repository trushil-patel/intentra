param([switch]$SkipModel)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv-win\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Create .venv-win and install requirements first." }
& $Python -c "import PyInstaller,sys; assert sys.platform=='win32'"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller or Windows runtime unavailable." }
$Dist = Join-Path $PSScriptRoot "dist"
$Work = Join-Path $PSScriptRoot "build"
& $Python -m PyInstaller --noconfirm --clean --onedir --console --name Intentra --contents-directory _internal --distpath $Dist --workpath $Work --specpath $PSScriptRoot --add-data "$Root\config;config" --collect-all sherpa_onnx --collect-all pywinauto --hidden-import pythoncom --hidden-import pywintypes --hidden-import win32com.client --hidden-import faster_whisper.vad --collect-data faster_whisper (Join-Path $Root "main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
$Target = Join-Path $Dist "Intentra"
Copy-Item (Join-Path $PSScriptRoot "README_RELEASE.md") $Target
Copy-Item (Join-Path $PSScriptRoot "TROUBLESHOOTING.md") $Target
Copy-Item (Join-Path $PSScriptRoot "THIRD_PARTY_LICENSES.md") $Target
Copy-Item (Join-Path $PSScriptRoot "Start-Intentra.cmd") $Target
Copy-Item (Join-Path $PSScriptRoot "RELEASE_MANIFEST.json") $Target
if (-not $SkipModel) {
  $Model = Join-Path $Root "models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
  $Required = @("encoder.int8.onnx","decoder.int8.onnx","joiner.int8.onnx","tokens.txt")
  foreach ($Name in $Required) { if (-not (Test-Path (Join-Path $Model $Name))) { throw "Missing Parakeet model file: $Name" } }
  $ModelTarget = Join-Path $Target "models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
  New-Item -ItemType Directory -Force $ModelTarget | Out-Null
  foreach ($Name in $Required) { Copy-Item (Join-Path $Model $Name) $ModelTarget }
}
& (Join-Path $PSScriptRoot "write_checksums.ps1") -ReleaseDirectory $Target
Write-Host "Portable release: $Target"



