$ErrorActionPreference = "Stop"

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
}
else {
    Write-Host "Virtual environment already exists."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
Write-Host "Installing requirements..."
& $venvPython -m pip install -r requirements.txt

$env:PYTHONPATH = "$projectRoot\src"
Write-Host "Preparing local folders and database..."
& $venvPython -c "from arena_coach.config import load_config; from arena_coach.database import initialize_database; config=load_config(); initialize_database(config.database_path); print('Config:', config.config_path); print('Database:', config.database_path); print('Raw logs:', config.raw_log_dir); print('Exports:', config.exports_dir); print('Imports:', config.imports_dir); print('Backups:', config.backups_dir)"

Write-Host ""
Write-Host "Setup complete. Launch with run_arena_coach.pyw"
