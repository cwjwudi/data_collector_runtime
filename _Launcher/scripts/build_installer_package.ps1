param(
    [string]$OutputDir = "",
    [string]$Python = "",
    [string]$Version = "1.0.0",
    [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$PipTrustedHost = "pypi.tuna.tsinghua.edu.cn",
    [string]$HttpProxy = "",
    [string]$InnoSetup = "",
    [switch]$SkipToolInstall,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $LauncherDir
$PortableBuilder = Join-Path $ScriptDir "build_portable_package.ps1"
$InnoScript = Join-Path $LauncherDir "installer\SD_SMA.iss"
$WinSWSource = Join-Path $LauncherDir "installer\tools\WinSW-x64.exe"
$WinSWExpectedHash = "05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "_Build\SD_SMA_Installer_Package"
}
$InstallerPackageRoot = [System.IO.Path]::GetFullPath($OutputDir)
$BuildRoot = Join-Path $RepoRoot "_Build"
$PackageRoot = Join-Path $InstallerPackageRoot "Runtime"
$NuitkaOutput = Join-Path $InstallerPackageRoot "Nuitka"

. (Join-Path $ScriptDir "database_tools.ps1")

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\") + "\"
    $childFull = [System.IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing operation outside expected directory: $childFull"
    }
}

function Remove-DirectorySafely {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Target
    )
    Assert-ChildPath -Parent $Parent -Child $Target
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
}

function Resolve-BuildPython {
    param([string]$Requested)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates += $Requested
    }
    $candidates += (Join-Path $RepoRoot ".venv\Scripts\python.exe")
    try {
        $pyPaths = & py -0p 2>$null
        foreach ($line in $pyPaths) {
            if ($line -match '(\S:\\.*python\.exe)\s*$') {
                $candidates += $Matches[1]
            }
        }
    }
    catch {
        # Python launcher is optional.
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $versionText = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $version = [version]$versionText.Trim()
        if ($version.Major -eq 3 -and $version.Minor -ge 10 -and $version.Minor -le 12) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Installer builds require Python 3.10-3.12. Pass -Python or create the repository .venv."
}

function Ensure-Nuitka {
    param([Parameter(Mandatory = $true)][string]$PythonExe)
    & $PythonExe -c "import nuitka, psutil" 2>$null
    if ($LASTEXITCODE -eq 0) {
        return
    }
    if ($SkipToolInstall) {
        throw "Nuitka or psutil is missing in $PythonExe and -SkipToolInstall was supplied."
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        throw "Nuitka/psutil is missing and uv was not found. Install them with: uv pip install --python `"$PythonExe`" nuitka zstandard psutil"
    }
    Write-Host "[tool] installing Nuitka and launcher build dependencies"
    & $uv.Source pip install --python $PythonExe nuitka zstandard psutil `
        --index-url $PipIndexUrl --allow-insecure-host $PipTrustedHost
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install launcher build dependencies."
    }
}

function Resolve-InnoSetup {
    param([string]$Requested)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $candidates += $Requested
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += $command.Source
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Inno Setup 6 was not found. Install it with: winget install --id JRSoftware.InnoSetup --exact"
}

function Remove-DevelopmentContent {
    param([Parameter(Mandatory = $true)][string]$ProjectsRoot)
    $skipDirectories = @(
        "tests", "docs", "scripts", "tools", ".pytest_cache", "__pycache__",
        "_artifacts", "_tools", "_backup", "backups", "exports", "logs"
    )
    Get-ChildItem -LiteralPath $ProjectsRoot -Directory -Recurse -Force |
        Sort-Object { $_.FullName.Length } -Descending |
        Where-Object { $skipDirectories -contains $_.Name } |
        ForEach-Object {
            Assert-ChildPath -Parent $ProjectsRoot -Child $_.FullName
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }

    $developmentFiles = @("README.md", "CHANGELOG.md", "todo.md", "requirements.txt", "requirements-dev.txt", "pytest.ini", ".gitignore")
    Get-ChildItem -LiteralPath $ProjectsRoot -File -Recurse -Force |
        Where-Object { $developmentFiles -contains $_.Name -or $_.Extension -in @(".log", ".pyo") } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    # Collector runtime queues contain machine-local SQLite/WAL data and can be
    # hundreds of MB. They must never be copied into a customer installer.
    $collectorQueue = Join-Path $ProjectsRoot "SD_SMA_DATA_COLLECTOR\runtime\queue"
    Remove-DirectorySafely -Parent $ProjectsRoot -Target $collectorQueue
}

function Convert-ProjectSourcesToBytecode {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$ProjectsRoot
    )
    Write-Host "[bytecode] compiling service sources with legacy import layout"
    & $PythonExe -m compileall -b -q -f $ProjectsRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Service bytecode compilation failed."
    }
    $sourceFiles = @(Get-ChildItem -LiteralPath $ProjectsRoot -Filter "*.py" -File -Recurse)
    $missingBytecode = @($sourceFiles | Where-Object { -not (Test-Path -LiteralPath ($_.FullName + "c")) })
    if ($missingBytecode.Count -gt 0) {
        throw "Missing bytecode for $($missingBytecode[0].FullName)"
    }
    $sourceFiles | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    $remaining = @(Get-ChildItem -LiteralPath $ProjectsRoot -Filter "*.py" -File -Recurse)
    if ($remaining.Count -ne 0) {
        throw "Python source files remain in installer projects."
    }
    Write-Host "[bytecode] protected $($sourceFiles.Count) project source files"
}

function Reset-PackagedRuntimeState {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $configRoot = Join-Path $RuntimeRoot "config"
    if (Test-Path -LiteralPath $configRoot) {
        Get-ChildItem -LiteralPath $configRoot -File -Force |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
        Get-ChildItem -LiteralPath $configRoot -Directory -Force | ForEach-Object {
            $serviceConfig = $_.FullName
            Get-ChildItem -LiteralPath $serviceConfig -Force |
                Where-Object { -not ($_.PSIsContainer -eq $false -and $_.Name -eq "default.json") } |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
        }
    }

    $logsRoot = Join-Path $RuntimeRoot "logs"
    if (Test-Path -LiteralPath $logsRoot) {
        Get-ChildItem -LiteralPath $logsRoot -Directory -Force | ForEach-Object {
            Get-ChildItem -LiteralPath $_.FullName -Force |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
        }
        Get-ChildItem -LiteralPath $logsRoot -File -Force |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }
    Write-Host "[runtime] reset configs to default.json only and cleared smoke logs"
}

function Set-VenvRelocationProbe {
    param(
        [Parameter(Mandatory = $true)][string]$VenvDir,
        [Parameter(Mandatory = $true)][string]$MissingPythonHome
    )
    $configPath = Join-Path $VenvDir "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "Relocation probe cannot find: $configPath"
    }
    $missingPython = Join-Path $MissingPythonHome "python.exe"
    $lines = Get-Content -LiteralPath $configPath
    $rewritten = foreach ($line in $lines) {
        if ($line -match '^\s*home\s*=') {
            "home = $MissingPythonHome"
        }
        elseif ($line -match '^\s*executable\s*=') {
            "executable = $missingPython"
        }
        elseif ($line -match '^\s*command\s*=') {
            "command = $missingPython -m venv $VenvDir"
        }
        else {
            $line
        }
    }
    Set-Content -LiteralPath $configPath -Value $rewritten -Encoding ASCII
    Write-Host "[relocation] injected missing build path; launcher must repair it before smoke test"
}

$PythonExe = Resolve-BuildPython -Requested $Python
$InnoSetupExe = Resolve-InnoSetup -Requested $InnoSetup
Ensure-Nuitka -PythonExe $PythonExe
Write-Host "[python] $PythonExe"
Write-Host "[inno] $InnoSetupExe"

$actualWinSWHash = (Get-FileHash -LiteralPath $WinSWSource -Algorithm SHA256).Hash
if ($actualWinSWHash -ne $WinSWExpectedHash) {
    throw "WinSW 2.12.0 SHA-256 mismatch: $actualWinSWHash"
}
Write-Host "[winsw] verified 2.12.0: $actualWinSWHash"

Assert-ChildPath -Parent $BuildRoot -Child $InstallerPackageRoot
Remove-DirectorySafely -Parent $BuildRoot -Target $InstallerPackageRoot
foreach ($legacyOutput in @(
        (Join-Path $BuildRoot "Installer"),
        (Join-Path $BuildRoot "Nuitka_Launcher"),
        (Join-Path $BuildRoot "SD_SMA_Installer_Staging")
    )) {
    Remove-DirectorySafely -Parent $BuildRoot -Target $legacyOutput
}
$InstallerOutput = (New-Item -ItemType Directory -Force -Path $InstallerPackageRoot).FullName

Write-Host "[portable] creating shared runtime staging package"
& $PortableBuilder -OutputDir $PackageRoot -Python $PythonExe `
    -PipIndexUrl $PipIndexUrl -PipTrustedHost $PipTrustedHost -HttpProxy $HttpProxy
if ($LASTEXITCODE -ne 0) {
    throw "Portable staging package build failed."
}

$PackageProjects = Join-Path $PackageRoot "_Prj"
Remove-DevelopmentContent -ProjectsRoot $PackageProjects
$DatabaseToolsResult = Install-DatabaseClientTools -RepoRoot $RepoRoot -ProjectsRoot $PackageProjects
$StagingPython = Join-Path $PackageRoot ".venv\Scripts\python.exe"
Convert-ProjectSourcesToBytecode -PythonExe $StagingPython -ProjectsRoot $PackageProjects

Write-Host "[nuitka] compiling the single public launcher executable"
New-Item -ItemType Directory -Force -Path $NuitkaOutput | Out-Null
& $PythonExe -m nuitka `
    --mode=onefile `
    --assume-yes-for-downloads `
    --remove-output `
    --python-flag=no_asserts `
    --python-flag=no_docstrings `
    --include-package=psutil `
    --company-name=SmartData `
    --product-name="SD SMA Runtime" `
    --file-description="SD SMA Unified Launcher" `
    --file-version=$Version `
    --product-version=$Version `
    --output-dir=$NuitkaOutput `
    --output-filename=SD_SMA_Launcher.exe `
    (Join-Path $LauncherDir "sd_sma_launcher.py")
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka launcher compilation failed."
}

$CompiledLauncher = Join-Path $NuitkaOutput "SD_SMA_Launcher.exe"
if (-not (Test-Path -LiteralPath $CompiledLauncher)) {
    throw "Compiled launcher not found: $CompiledLauncher"
}
$PackageLauncher = Join-Path $PackageRoot "_Launcher"
$InstalledLauncher = Join-Path $PackageLauncher "SD_SMA_Launcher.exe"
Copy-Item -LiteralPath $CompiledLauncher -Destination $InstalledLauncher -Force
Remove-Item -LiteralPath (Join-Path $PackageLauncher "sd_sma_launcher.py") -Force
Remove-Item -LiteralPath (Join-Path $PackageLauncher "resource_monitor.py") -Force
foreach ($launcherModule in @("launcher_supervisor.py", "launcher_security.py", "launcher_imports.py", "launcher_web.py", "launcher_settings.py")) {
    Remove-Item -LiteralPath (Join-Path $PackageLauncher $launcherModule) -Force
}

$PackageService = Join-Path $PackageRoot "_Service"
New-Item -ItemType Directory -Force -Path $PackageService | Out-Null
Copy-Item -LiteralPath $WinSWSource -Destination (Join-Path $PackageService "SD_SMA_Service.exe") -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\tools\WinSW-LICENSE.txt") -Destination $PackageService -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\SD_SMA_Service.xml") -Destination $PackageService -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\Initialize-SD_SMA.ps1") -Destination $PackageService -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\Install-SD_SMA-Service.ps1") -Destination $PackageService -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\Reset-SD_SMA-LauncherPin.ps1") -Destination $PackageService -Force
Copy-Item -LiteralPath (Join-Path $LauncherDir "installer\Configure-SD_SMA-Firewall.ps1") -Destination $PackageService -Force

$stagedConfigPath = Join-Path $PackageLauncher "launcher_config.json"
$stagedConfig = Get-Content -LiteralPath $stagedConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$stagedConfig.auto_install_missing = $false
[System.IO.File]::WriteAllText(
    $stagedConfigPath,
    ($stagedConfig | ConvertTo-Json -Depth 30) + "`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

@"
@echo off
cd /d "%~dp0"
"%~dp0SD_SMA_Launcher.exe" %*
"@ | Set-Content -LiteralPath (Join-Path $PackageLauncher "start.bat") -Encoding ASCII

@"
@echo off
cd /d "%~dp0_Launcher"
"%~dp0_Launcher\SD_SMA_Launcher.exe" %*
"@ | Set-Content -LiteralPath (Join-Path $PackageRoot "start.bat") -Encoding ASCII

if (-not $SkipSmokeTest) {
    $missingProbeHome = Join-Path $InstallerPackageRoot "__missing_build_python__"
    if (Test-Path -LiteralPath $missingProbeHome) {
        throw "Relocation probe path unexpectedly exists: $missingProbeHome"
    }
    Set-VenvRelocationProbe -VenvDir (Join-Path $PackageRoot ".venv") -MissingPythonHome $missingProbeHome
    Write-Host "[smoke] running compiled launcher against bytecode-only services"
    & $InstalledLauncher --config $stagedConfigPath --smoke --no-browser
    if ($LASTEXITCODE -ne 0) {
        throw "Compiled installer staging smoke test failed."
    }
}
else {
    Write-Host "[smoke] skipped by -SkipSmokeTest"
}

Reset-PackagedRuntimeState -RuntimeRoot $PackageRoot

Write-Host "[installer] compiling Inno Setup package"
& $InnoSetupExe "/DSourceRoot=$PackageRoot" "/DOutputDir=$InstallerOutput" "/DAppVersion=$Version" $InnoScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed."
}

$InstallerExe = Join-Path $InstallerOutput "SD_SMA_Setup_$Version.exe"
if (-not (Test-Path -LiteralPath $InstallerExe)) {
    throw "Installer output not found: $InstallerExe"
}
Write-Host "[done] installer: $InstallerExe"
Write-Host "[done] one shared service runtime: $PackageRoot\.venv"
if ($DatabaseToolsResult.Included) {
    Write-Host "[done] database client included: $($DatabaseToolsResult.Family)"
}
else {
    Write-Warning "[done] database client was not included in the installer."
}
