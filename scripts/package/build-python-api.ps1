$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$distPath = Join-Path $repoRoot "build\package"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build\pyinstaller"
$apiEntryPoint = Join-Path $PSScriptRoot "lara_api_entry.py"
$cliEntryPoint = Join-Path $PSScriptRoot "lara_entry.py"
$wheelPath = Join-Path $workPath "wheel"
$laraWheel = $null

function Get-LockedGitSource {
    param(
        [Parameter(Mandatory = $true)]
        [string] $PackageName
    )

    $lockText = Get-Content -LiteralPath (Join-Path $repoRoot "uv.lock") -Raw
    $pattern = 'name = "' + [regex]::Escape($PackageName) + '"\r?\nversion = "[^"\r\n]*"\r?\nsource = \{ git = "(?<url>[^"#]+)#(?<commit>[0-9a-f]{40})" \}'
    $lockMatch = [regex]::Match($lockText, $pattern)
    if (-not $lockMatch.Success) {
        throw "uv.lock does not pin a git source for $PackageName"
    }
    [pscustomobject]@{
        Url = $lockMatch.Groups["url"].Value
        Commit = $lockMatch.Groups["commit"].Value
    }
}

function Build-GitDependencyWheel {
    # uv run --no-project resolves --with requirements against the registry
    # only, so git-only dependencies must be shipped as wheels via --find-links.
    param(
        [Parameter(Mandatory = $true)]
        [string] $PackageName,
        [Parameter(Mandatory = $true)]
        [string] $OutDir,
        [Parameter(Mandatory = $true)]
        [string] $WorkDir
    )

    $source = Get-LockedGitSource -PackageName $PackageName
    $repoUrl = ($source.Url -split '\?')[0]
    $clonePath = Join-Path $WorkDir $PackageName
    if (Test-Path $clonePath) {
        Remove-Item -LiteralPath $clonePath -Recurse -Force
    }

    & git clone --quiet $repoUrl $clonePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone $repoUrl"
    }
    & git -C $clonePath checkout --quiet $source.Commit
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to checkout $($source.Commit) for $PackageName"
    }

    & uv build --wheel --out-dir $OutDir $clonePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the $PackageName wheel"
    }
}

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
        "--reinstall-package",
        "lara",
        "--with",
        "pyinstaller",
        "--with",
        $laraWheel,
        "--find-links",
        $wheelPath,
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

    Build-GitDependencyWheel `
        -PackageName "dynamic-memory-cli" `
        -OutDir $wheelPath `
        -WorkDir $workPath

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
