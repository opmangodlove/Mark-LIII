$ErrorActionPreference = 'Stop'
$src   = $PSScriptRoot
$stage = Join-Path $env:TEMP 'meow-stage'
$zip   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MEOW MEOW.zip'

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip)   { Remove-Item $zip -Force }

# Everything except heavy/junk: venv, caches, git internals, locks, logs.
# API keys stay out: config/api_keys.json and any *.key files.
robocopy $src $stage /E /NFL /NDL /NJH /NJS /XD .venv __pycache__ .git logs /XF launcher.lock *.pyc *.log api_keys.json | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $LASTEXITCODE" }

# Safety net: strip any secrets that slipped into the stage by name.
Get-ChildItem $stage -Recurse -Include 'api_keys.json','*.pem','*.key' -File -ErrorAction SilentlyContinue |
    Remove-Item -Force

Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force

$z = Get-Item $zip
Write-Output ("zip: {0}  {1:N1} MB" -f $z.FullName, ($z.Length / 1MB))
