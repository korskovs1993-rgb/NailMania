$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    & .venv/Scripts/python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path -LiteralPath '.env')) {
    & .venv/Scripts/python.exe init_local.py
}
& .venv/Scripts/python.exe manage.py migrate
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& .venv/Scripts/python.exe manage.py setup_studio
Write-Host 'Create the master account in a second terminal: .venv/Scripts/python.exe manage.py createsuperuser'
Write-Host 'Local preview: http://127.0.0.1:8000/ (not available on iPhone from this address)'
& .venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000 --noreload
