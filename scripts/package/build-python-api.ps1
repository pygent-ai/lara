$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$distPath = Join-Path $repoRoot "build\package"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build\pyinstaller"
$apiEntryPoint = Join-Path $PSScriptRoot "lara_api_entry.py"
$cliEntryPoint = Join-Path $PSScriptRoot "lara_entry.py"
$wheelPath = Join-Path $workPath "wheel"
$laraWheel = $null

function Invoke-PyInstaller {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [string] $EntryPoint,
        [string[]] $HiddenImports = @()
    )

    $arguments = @(
        "run",
        "--no-project",
        "--with",
        "pyinstaller",
        "--with",
        $laraWheel,
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        $Name,
        "--paths",
        "src",
        "--collect-submodules",
        "lara",
        "--collect-submodules",
        "lara_api",
        "--collect-submodules",
        "pygent",
        "--collect-submodules",
        "pygent_ai",
        "--collect-submodules",
        "uvicorn",
        "--copy-metadata",
        "lara",
        "--distpath",
        $distPath,
        "--workpath",
        $workPath,
        "--specpath",
        $specPath
    )

    foreach ($hiddenImport in $HiddenImports) {
        $arguments += @("--hidden-import", $hiddenImport)
    }

    $arguments += $EntryPoint
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller target $Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $repoRoot
try {
    if (Test-Path $wheelPath) {
        Remove-Item -LiteralPath $wheelPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath, $wheelPath | Out-Null

    & uv build --wheel --out-dir $wheelPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the isolated Lara package wheel"
    }

    $builtWheels = @(Get-ChildItem -LiteralPath $wheelPath -Filter "lara-*.whl" -File)
    if ($builtWheels.Count -ne 1) {
        throw "Expected exactly one Lara wheel, found $($builtWheels.Count)"
    }
    $laraWheel = $builtWheels[0].FullName

    Invoke-PyInstaller `
        -Name "lara-api" `
        -EntryPoint $apiEntryPoint `
        -HiddenImports @("lara_api.main")

    Invoke-PyInstaller `
        -Name "lara" `
        -EntryPoint $cliEntryPoint `
        -HiddenImports @("lara.cli.main")

    Copy-Item `
        -LiteralPath (Join-Path $distPath "lara\lara.exe") `
        -Destination (Join-Path $distPath "lara-api\lara.exe") `
        -Force

}
finally {
    Pop-Location
}
