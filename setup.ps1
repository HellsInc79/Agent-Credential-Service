param(
    [ValidateSet('None', 'CPU', 'GPU')]
    [string]$TrainingDevice = 'None'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

Write-Host 'Preparing private local data folders...'
$PrivateFolders = @(
    'uploads',
    'video_studio',
    'trained_models',
    'trained_models\.training-data',
    'base_models',
    'base_models\.staging',
    'hf_cache',
    'tools\runtime'
)
foreach ($Folder in $PrivateFolders) {
    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot $Folder) -Force | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe'))) {
    Write-Host 'Creating this project''s Python environment...'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv .venv
    }
    else {
        python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. Install Python 3.11 or newer and try again.' }
}

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Could not find .venv\Scripts\python.exe. Install Python 3.11 or newer, then run setup.ps1 again.'
}

Write-Host 'Installing the application requirements...'
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Application package installation failed.' }

if ($TrainingDevice -ne 'None') {
    $TrainingRequirements = "requirements-training-$($TrainingDevice.ToLowerInvariant()).txt"
    Write-Host "Installing optional $TrainingDevice model-training packages..."
    & $Python -m pip install -r $TrainingRequirements
    if ($LASTEXITCODE -ne 0) { throw "Training package installation failed: $TrainingRequirements" }
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example. Change JWT_SECRET before using the app.'
}

Write-Host ''
Write-Host 'Setup complete. Start the app with:'
Write-Host '  .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000'
