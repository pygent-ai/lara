# Builds a single-file, self-contained lara CLI exe with PyInstaller.
#
# The build analyzes only the published lara wheel (no --paths src), so the
# result carries all Python code inside the exe and target machines need
# neither Python, uv, nor this repository. Output:
#   build\package\lara-standalone\lara.exe  (PyInstaller output)
#   dist\lara.exe                           (shippable copy)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$distPath = Join-Path $repoRoot "build\package\lara-standalone"
$releasePath = Join-Path $repoRoot "dist"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build\pyinstaller"
$entryPoint = Join-Path $PSScriptRoot "lara_standalone_entry.py"
$wheelPath = Join-Path $workPath "wheel"
$laraWheel = $null

Push-Location $repoRoot
try {
    if (Test-Path $wheelPath) {
        Remove-Item -LiteralPath $wheelPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $distPath, $releasePath, $workPath, $specPath, $wheelPath | Out-Null

    & uv build --wheel --out-dir $wheelPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the isolated Lara package wheel"
    }

    $builtWheels = @(Get-ChildItem -LiteralPath $wheelPath -Filter "lara-*.whl" -File)
    if ($builtWheels.Count -ne 1) {
        throw "Expected exactly one Lara wheel, found $($builtWheels.Count)"
    }
    $laraWheel = $builtWheels[0].FullName

    & uv run --no-project --reinstall-package lara `
        --with pyinstaller `
        --with $laraWheel `
        pyinstaller `
        --noconfirm `
        --clean `
        --onefile `
        --name lara `
        --collect-submodules lara `
        --collect-submodules pygent `
        --collect-submodules pygent_ai `
        --collect-all tzdata `
        --distpath $distPath `
        --workpath $workPath `
        --specpath $specPath `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller target lara failed with exit code $LASTEXITCODE"
    }

    Copy-Item `
        -LiteralPath (Join-Path $distPath "lara.exe") `
        -Destination (Join-Path $releasePath "lara.exe") `
        -Force
}
finally {
    Pop-Location
}
