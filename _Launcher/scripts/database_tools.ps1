function Get-DatabaseDumpCandidates {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    $candidates = New-Object System.Collections.Generic.List[string]
    $seen = @{}

    foreach ($rawDir in (($env:PATH -split ";") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $directory = [Environment]::ExpandEnvironmentVariables($rawDir.Trim().Trim('"'))
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            continue
        }
        foreach ($name in @("mysqldump.exe", "mariadb-dump.exe")) {
            $path = Join-Path $directory $name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                continue
            }
            $resolved = (Resolve-Path -LiteralPath $path).Path
            $key = $resolved.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $candidates.Add($resolved)
            }
        }
    }

    $repoTools = Join-Path $RepoRoot "_Prj\SD_SMA_DB_ADMIN\_tools"
    if (Test-Path -LiteralPath $repoTools -PathType Container) {
        foreach ($name in @("mysqldump.exe", "mariadb-dump.exe")) {
            Get-ChildItem -LiteralPath $repoTools -Recurse -File -Filter $name -ErrorAction SilentlyContinue |
                Sort-Object FullName |
                ForEach-Object {
                    $resolved = $_.FullName
                    $key = $resolved.ToLowerInvariant()
                    if (-not $seen.ContainsKey($key)) {
                        $seen[$key] = $true
                        $candidates.Add($resolved)
                    }
                }
        }
    }

    return @($candidates)
}

function Find-CachingSha2Plugin {
    param([Parameter(Mandatory = $true)][string]$DumpPath)

    $binDir = Split-Path -Parent $DumpPath
    $installRoot = Split-Path -Parent $binDir
    foreach ($candidate in @(
        (Join-Path $binDir "caching_sha2_password.dll"),
        (Join-Path $installRoot "lib\plugin\caching_sha2_password.dll")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Resolve-DatabaseClientCandidate {
    param([Parameter(Mandatory = $true)][string]$DumpPath)

    try {
        $versionText = (& $DumpPath --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
            Write-Warning "[db-tools] cannot execute candidate: $DumpPath"
            return $null
        }
    }
    catch {
        Write-Warning "[db-tools] cannot execute candidate $DumpPath : $($_.Exception.Message)"
        return $null
    }

    $isMariaDb = $versionText -match "(?i)MariaDB"
    $binDir = Split-Path -Parent $DumpPath
    $clientNames = if ($isMariaDb) { @("mariadb.exe", "mysql.exe") } else { @("mysql.exe") }
    $clientPath = $null
    foreach ($name in $clientNames) {
        $candidate = Join-Path $binDir $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $clientPath = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
    if (-not $clientPath) {
        Write-Warning "[db-tools] incomplete candidate; companion client missing beside $DumpPath"
        return $null
    }

    $pluginPath = Find-CachingSha2Plugin -DumpPath $DumpPath
    if ($isMariaDb -and -not $pluginPath) {
        Write-Warning "[db-tools] incomplete MariaDB candidate; caching_sha2_password.dll missing for $DumpPath"
        return $null
    }

    return [pscustomobject]@{
        Family = if ($isMariaDb) { "MariaDB" } else { "MySQL" }
        Version = $versionText
        Dump = $DumpPath
        Client = $clientPath
        Plugin = $pluginPath
        BinDir = $binDir
    }
}

function Install-DatabaseClientTools {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$ProjectsRoot
    )

    $dbAdminRoot = Join-Path $ProjectsRoot "SD_SMA_DB_ADMIN"
    $toolsRoot = Join-Path $dbAdminRoot "_tools"
    if (Test-Path -LiteralPath $toolsRoot) {
        Remove-Item -LiteralPath $toolsRoot -Recurse -Force
    }

    $candidatePaths = @(Get-DatabaseDumpCandidates -RepoRoot $RepoRoot)
    $selected = $null
    foreach ($candidatePath in $candidatePaths) {
        $selected = Resolve-DatabaseClientCandidate -DumpPath $candidatePath
        if ($selected) {
            break
        }
    }

    if (-not $selected) {
        $searched = if ($candidatePaths.Count) { $candidatePaths -join "; " } else { "PATH and _Prj\SD_SMA_DB_ADMIN\_tools" }
        Write-Warning "[db-tools] no complete MySQL/MariaDB client found. Checked: $searched"
        Write-Warning "[db-tools] package will be built without database CLI tools; configure an absolute path or SYSTEM PATH on the target machine."
        return [pscustomobject]@{ Included = $false; Family = ""; Dump = ""; Client = ""; Plugin = "" }
    }

    $targetBin = Join-Path $toolsRoot "database-client\bin"
    $targetPlugin = Join-Path $toolsRoot "database-client\lib\plugin"
    New-Item -ItemType Directory -Force -Path $targetBin | Out-Null

    Copy-Item -LiteralPath $selected.Dump -Destination $targetBin -Force
    if ($selected.Client -ne $selected.Dump) {
        Copy-Item -LiteralPath $selected.Client -Destination $targetBin -Force
    }
    Get-ChildItem -LiteralPath $selected.BinDir -File -Filter "*.dll" -ErrorAction SilentlyContinue |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $targetBin -Force }

    $packagedPlugin = ""
    if ($selected.Plugin) {
        New-Item -ItemType Directory -Force -Path $targetPlugin | Out-Null
        Copy-Item -LiteralPath $selected.Plugin -Destination $targetPlugin -Force
        $packagedPlugin = Join-Path $targetPlugin (Split-Path -Leaf $selected.Plugin)
    }

    $packagedDump = Join-Path $targetBin (Split-Path -Leaf $selected.Dump)
    $packagedClient = Join-Path $targetBin (Split-Path -Leaf $selected.Client)
    Write-Host "[db-tools] included $($selected.Family) client: $packagedDump"
    if ($packagedPlugin) {
        Write-Host "[db-tools] included authentication plugin: $packagedPlugin"
    }
    return [pscustomobject]@{
        Included = $true
        Family = $selected.Family
        Dump = $packagedDump
        Client = $packagedClient
        Plugin = $packagedPlugin
    }
}
