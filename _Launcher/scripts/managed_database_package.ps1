function Get-ManagedDatabasePackage {
    param([string]$LauncherDir, [string]$ManifestName = 'mariadb-package.json')
    $manifest = Get-Content -LiteralPath (Join-Path $LauncherDir "installer\$ManifestName") -Raw | ConvertFrom-Json
    $archive = Join-Path $LauncherDir ('installer\tools\' + $manifest.filename)
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        throw "Package missing: $archive. Download $($manifest.url); expected SHA-256 $($manifest.sha256)"
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $manifest.sha256) {
        throw "Package SHA-256 mismatch: $archive"
    }
    return $archive
}

function Install-ManagedDatabasePackage {
    param([string]$Archive, [string]$RuntimeRoot, [string]$LauncherDir)
    $manifestPath = Join-Path $LauncherDir 'installer\mariadb-package.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $unpack = Join-Path $RuntimeRoot '_Database_package'
    Expand-Archive -LiteralPath $Archive -DestinationPath $unpack
    $source = [IO.Path]::GetFullPath((Join-Path $unpack $manifest.directory))
    $parent = [IO.Path]::GetFullPath($unpack).TrimEnd('\') + '\'
    if (-not $source.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid MariaDB directory' }
    foreach ($required in @('bin\mariadbd.exe', 'bin\mariadb-install-db.exe', 'bin\mariadb.exe', 'bin\mariadb-dump.exe', 'COPYING', 'CREDITS', 'THIRDPARTY')) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $required))) { throw "MariaDB payload missing: $required" }
    }
    $target = Join-Path $RuntimeRoot '_Database'
    Move-Item -LiteralPath $source -Destination $target
    Remove-Item -LiteralPath $unpack
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $target 'PACKAGE.json')
    foreach ($name in @('mariadbd.exe', 'mariadb.exe', 'mariadb-dump.exe')) {
        & (Join-Path $target "bin\$name") --version
        if ($LASTEXITCODE -ne 0) { throw "MariaDB dependency check failed: $name" }
    }
}
