$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$zipPath = Join-Path $projectRoot "ArenaCoach_TestBuild.zip"
$tempBase = [System.IO.Path]::GetTempPath()
$stagingRoot = Join-Path $tempBase ("ArenaCoach_TestBuild_" + [System.Guid]::NewGuid().ToString("N"))

$excludedDirectoryNames = @(
    ".venv",
    "data",
    "logs",
    "exports",
    "imports",
    "backups",
    "__pycache__",
    ".pytest_cache",
    ".git"
)

$excludedFileNames = @(
    "arena_coach_config.json",
    "ArenaCoach_TestBuild.zip"
)

function Test-IsExcluded {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileInfo] $File
    )

    if ($excludedFileNames -contains $File.Name) {
        return $true
    }

    if ($File.Extension -eq ".pyc") {
        return $true
    }

    $relativePath = $File.FullName.Substring($projectRoot.Length).TrimStart("\", "/")
    $parts = $relativePath -split "[\\/]+"
    foreach ($part in $parts) {
        if ($excludedDirectoryNames -contains $part) {
            return $true
        }
    }

    return $false
}

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

try {
    Get-ChildItem -LiteralPath $projectRoot -Recurse -Force -File |
        Where-Object { -not (Test-IsExcluded $_) } |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($projectRoot.Length).TrimStart("\", "/")
            $destination = Join-Path $stagingRoot $relativePath
            $destinationDirectory = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }

    Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $zipPath -Force
    Write-Host "Created $zipPath"
}
finally {
    $resolvedStaging = (Resolve-Path $stagingRoot).ProviderPath
    $resolvedTemp = (Resolve-Path $tempBase).ProviderPath
    if ($resolvedStaging.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}
