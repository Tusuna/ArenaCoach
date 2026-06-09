$ErrorActionPreference = "Stop"

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Step
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Display = "python"
            Command = "python"
            Args = @()
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Display = "py -3"
            Command = "py"
            Args = @("-3")
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $PythonCommand,

        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    & $PythonCommand.Command @($PythonCommand.Args + $Arguments)
}

function Test-StaleCopiedConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ProjectRoot,

        [Parameter(Mandatory = $true)]
        [string] $ConfigPath
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return $false
    }

    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    }
    catch {
        return $false
    }

    foreach ($propertyName in @("raw_log_dir", "database_path")) {
        $value = [string]$config.$propertyName
        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }
        if (-not [System.IO.Path]::IsPathRooted($value)) {
            continue
        }

        $fullPath = [System.IO.Path]::GetFullPath($value)
        $insideProject = $fullPath.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)
        $exists = Test-Path -LiteralPath $fullPath
        if (-not $insideProject -and -not $exists) {
            return $true
        }
    }

    return $false
}

function Reset-StaleCopiedConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ProjectRoot,

        [Parameter(Mandatory = $true)]
        [string] $ConfigPath
    )

    $defaults = @{
        echo_api_host = "127.0.0.1"
        echo_api_port = 6721
        echo_api_path = "/session"
        poll_interval_seconds = 0.5
        request_timeout_seconds = 1.0
        raw_log_dir = "logs\\raw"
        database_path = "data\\arena_coach.db"
        use_guided_match_review = $true
    }

    $defaults | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
Set-Location $projectRoot

$pythonCommand = Get-PythonCommand
if (-not $pythonCommand) {
    Write-Host "Python was not found. Install Python 3.12 with winget install Python.Python.3.12"
    exit 1
}

Write-Host "Using Python command: $($pythonCommand.Display)"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    Invoke-Python $pythonCommand @("-m", "venv", ".venv")
    Assert-LastExitCode "Virtual environment creation"
}
else {
    Write-Host "Virtual environment already exists."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
Write-Host "Installing requirements..."
& $venvPython -m pip install -r requirements.txt
Assert-LastExitCode "Requirements installation"

$env:PYTHONPATH = "$projectRoot\src"
$configPath = Join-Path $projectRoot "arena_coach_config.json"
if (Test-StaleCopiedConfig -ProjectRoot $projectRoot -ConfigPath $configPath) {
    Write-Host "Resetting stale copied config so Arena Coach can use this PC's folders..."
    Reset-StaleCopiedConfig -ProjectRoot $projectRoot -ConfigPath $configPath
}

Write-Host "Preparing local folders and database..."
& $venvPython -c "from arena_coach.config import load_config; from arena_coach.database import initialize_database; config=load_config(); initialize_database(config.database_path); print('Config:', config.config_path); print('Database:', config.database_path); print('Raw logs:', config.raw_log_dir); print('Exports:', config.exports_dir); print('Imports:', config.imports_dir); print('Backups:', config.backups_dir)"
Assert-LastExitCode "Arena Coach setup"

Write-Host ""
Write-Host "Setup complete. Launch with run_arena_coach.pyw"
