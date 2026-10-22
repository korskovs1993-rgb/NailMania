param([string]$Destination = "$PSScriptRoot/backups")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& .venv/Scripts/python.exe manage.py backup --output "$Destination/nailbook-$stamp.nbak"
if ($LASTEXITCODE -ne 0) { throw 'Backup failed. Previous backups were left unchanged.' }
# No automatic deletion. Retention and a second encrypted copy must be configured by the operator.
