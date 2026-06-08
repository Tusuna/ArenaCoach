$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
$outputPath = Join-Path $projectRoot "ArenaCoach_TestBuild.zip"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ArenaCoach_TestBuild_" + [guid]::NewGuid().ToString("N"))
$stagingProject = Join-Path $stagingRoot "ArenaCoach"

$excludeDirNames = @(
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

$excludeFileNames = @(
    "arena_coach_config.json"
)

$excludeExtensions = @(
    ".pyc"
)

function Copy-IncludedFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourceRoot,

        [Parameter(Mandatory = $true)]
        [string] $TargetRoot
    )

    Get-ChildItem -LiteralPath $SourceRoot -Recurse -Force | ForEach-Object {
        $fullPath = $_.FullName
        $relativePath = $fullPath.Substring($SourceRoot.Length).TrimStart('\')
        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            return
        }

        $pathParts = $relativePath -split '[\\/]'
        foreach ($part in $pathParts) {
            if ($excludeDirNames -contains $part) {
                return
            }
        }

        if ($_.PSIsContainer) {
            return
        }

        if ($excludeFileNames -contains $_.Name) {
            return
        }

        if ($excludeExtensions -contains $_.Extension) {
            return
        }

        $targetPath = Join-Path $TargetRoot $relativePath
        $targetDirectory = Split-Path -Parent $targetPath
        if (-not (Test-Path -LiteralPath $targetDirectory)) {
            New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        }
        Copy-Item -LiteralPath $fullPath -Destination $targetPath -Force
    }
}

try {
    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Force
    }

    New-Item -ItemType Directory -Path $stagingProject -Force | Out-Null
    Copy-IncludedFiles -SourceRoot $projectRoot -TargetRoot $stagingProject

    Compress-Archive -Path (Join-Path $stagingProject "*") -DestinationPath $outputPath -Force
    Write-Host "Created tester zip:"
    Write-Host $outputPath
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
