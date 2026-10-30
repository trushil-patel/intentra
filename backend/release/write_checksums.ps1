param([Parameter(Mandatory=$true)][string]$ReleaseDirectory)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $ReleaseDirectory).Path
$Output = Join-Path $Root "SHA256SUMS.txt"
$Lines = Get-ChildItem $Root -File -Recurse | Where-Object { $_.FullName -ne $Output -and $_.FullName -notmatch '[\\/]logs[\\/]' } | Sort-Object FullName | ForEach-Object {
  $Relative = $_.FullName.Substring($Root.Length + 1).Replace("\","/")
  "$(Get-FileHash $_.FullName -Algorithm SHA256 | Select-Object -ExpandProperty Hash)  $Relative"
}
Set-Content $Output $Lines -Encoding ascii

