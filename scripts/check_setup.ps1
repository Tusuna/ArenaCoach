$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
Set-Location $projectRoot

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

$pythonCommand = Get-PythonCommand

if (-not $pythonCommand) {
    Write-Host "Python was not found."
    Write-Host "Install it with:"
    Write-Host "winget install Python.Python.3.12"
    Write-Host "Then close and reopen PowerShell."
    exit 1
}

Write-Host "Python command detected: $($pythonCommand.Display)"

$versionText = Invoke-Python $pythonCommand @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
Write-Host "Python version detected: $versionText"

$versionParts = $versionText.Trim().Split(".")
$major = [int]$versionParts[0]
$minor = [int]$versionParts[1]

if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Warning "Python 3.10 or newer is recommended. Install Python 3.12 if setup fails."
}

try {
    $pipText = Invoke-Python $pythonCommand @("-m", "pip", "--version")
    Write-Host "pip works: $pipText"
}
catch {
    Write-Warning "pip did not run successfully. Try reinstalling Python and make sure pip is included."
}

if ($pythonCommand.Display -ne "python") {
    Write-Host ""
    Write-Host "Note: this machine detected '$($pythonCommand.Display)' instead of 'python'."
    Write-Host "If the commands below fail, replace 'python' with '$($pythonCommand.Display)'."
}

Write-Host ""
Write-Host "Next setup commands:"
Write-Host 'python -m venv .venv'
Write-Host '.\.venv\Scripts\Activate.ps1'
Write-Host 'pip install -r requirements.txt'
Write-Host '$env:PYTHONPATH = "$PWD\src"'
Write-Host 'python -m arena_coach.gui.app'
Write-Host ""
Write-Host "Friend/tester shortcut:"
Write-Host '.\scripts\setup_windows.ps1'
Write-Host 'or just double-click .\scripts\setup_windows.bat'
Write-Host 'Then launch with run_arena_coach.pyw'
