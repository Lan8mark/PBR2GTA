param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = if ([System.IO.Path]::IsPathRooted($Python)) { (Resolve-Path -LiteralPath $Python).Path } else { (Resolve-Path (Join-Path $Root $Python)).Path }
$Build = Join-Path $Root "build"
$Dist = Join-Path $Root "dist"
$Release = Join-Path $Root "release"
$Stage = Join-Path $Release "stage\PBR2GTA"
$TaskPreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $Root "src"

Push-Location $Root
try {
    $Version = & $PythonPath -c "import tomllib; print(tomllib.load(open('src/pbr2gta_blender/blender_manifest.toml', 'rb'))['version'])"
    if ($LASTEXITCODE -ne 0) { throw "Could not read add-on version." }
    & $PythonPath -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

    & $PythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name pbr2gta-core `
        --paths (Join-Path $Root "src") `
        --add-data "$(Join-Path $Root 'src\pbr2gta_core\runtime_shader_slots.json');pbr2gta_core" `
        (Join-Path $Root "scripts\core_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "Core build failed." }

    if (Test-Path -LiteralPath $Stage) {
        $ResolvedStage = (Resolve-Path -LiteralPath $Stage).Path
        if (-not $ResolvedStage.StartsWith($Root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clear a staging directory outside the project."
        }
        Remove-Item -Recurse -Force -LiteralPath $Stage
    }
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    Copy-Item -Recurse -Force -Path (Join-Path $Root "src\pbr2gta_blender\*") -Destination $Stage
    foreach ($CacheDirectory in Get-ChildItem -LiteralPath $Stage -Recurse -Directory -Filter "__pycache__") {
        Remove-Item -Recurse -Force -LiteralPath $CacheDirectory.FullName
    }
    Copy-Item -Force -LiteralPath (Join-Path $Root "src\pbr2gta_core\profiles.json") -Destination $Stage
    Copy-Item -Recurse -Force -LiteralPath (Join-Path $Dist "pbr2gta-core") -Destination (Join-Path $Stage "core\pbr2gta-core")
    Copy-Item -Force -LiteralPath (Join-Path $Root "LICENSE") -Destination $Stage
    Copy-Item -Force -LiteralPath (Join-Path $Root "README.md") -Destination $Stage
    Copy-Item -Force -LiteralPath (Join-Path $Root "README.ru.md") -Destination $Stage
    Copy-Item -Force -LiteralPath (Join-Path $Root "THIRD_PARTY_NOTICES.md") -Destination $Stage

    & $PythonPath (Join-Path $Root "scripts/release_package.py") $Stage
    if ($LASTEXITCODE -ne 0) { throw "Release curation or license validation failed." }

    $Executable = Join-Path $Stage "core\pbr2gta-core\pbr2gta-core.exe"
    if ($env:PBR2GTA_SIGNING_THUMBPRINT) {
        $Certificate = Get-ChildItem Cert:\CurrentUser\My | Where-Object Thumbprint -eq $env:PBR2GTA_SIGNING_THUMBPRINT | Select-Object -First 1
        if (-not $Certificate) { throw "Signing certificate was not found." }
        $Signature = Set-AuthenticodeSignature -FilePath $Executable -Certificate $Certificate -TimestampServer "http://timestamp.digicert.com"
        if ($Signature.Status -ne "Valid") { throw "Core signature failed: $($Signature.StatusMessage)" }
    }

    $Archive = Join-Path $Release "PBR2GTA_Addon_$Version.zip"
    Compress-Archive -Path $Stage -DestinationPath $Archive -CompressionLevel Optimal -Force
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash
    Set-Content -LiteralPath (Join-Path $Release "SHA256SUMS.txt") -Encoding ascii -Value "$Hash  PBR2GTA_Addon_$Version.zip"
    Set-Content -LiteralPath (Join-Path $Release "PBR2GTA_Addon_$Version.zip.sha256") -Encoding ascii -Value "$Hash  PBR2GTA_Addon_$Version.zip"
    Write-Host "Built $Archive"
    Write-Host "SHA-256 $Hash"
}
finally {
    $env:PYTHONPATH = $TaskPreviousPythonPath
    Pop-Location
}
