param([string]$ReleaseDirectory = (Join-Path $PSScriptRoot "dist\Intentra"))
$ErrorActionPreference = "Stop"
$Exe = Join-Path (Resolve-Path $ReleaseDirectory) "Intentra.exe"
if (-not (Test-Path $Exe)) { throw "Intentra.exe not found." }
& $Exe --plan "Open Chrome."
if ($LASTEXITCODE -ne 0) { throw "Packaged plan smoke test failed." }
& $Exe --list-apps
if ($LASTEXITCODE -ne 0) { throw "Packaged app discovery smoke test failed." }

# CLI override makes this check independent of the operator's VOICE_MODE setting.
$Startup = & $Exe --voice-mode push_to_talk --startup-check
if ($LASTEXITCODE -ne 0 -or $Startup -notcontains "[READY] Hold F8 to speak.") { throw "Packaged F8 startup check failed." }
$Startup
