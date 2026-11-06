"""Compile the real installer with a tiny payload; never install or change services."""

import os
import json
import socket
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    launcher = Path(__file__).resolve().parent
    installer = launcher / "installer" / "SD_SMA.iss"
    compiler = shutil.which("ISCC.exe")
    if not compiler:
        for parent in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"),
                       os.environ.get("ProgramFiles")):
            if parent:
                folder = "Programs/Inno Setup 6" if parent == os.environ.get("LOCALAPPDATA") else "Inno Setup 6"
                candidate = Path(parent) / folder / "ISCC.exe"
                if candidate.is_file():
                    compiler = str(candidate)
                    break
    assert compiler, "Install Inno Setup 6 to check installer modes"

    source = installer.read_text(encoding="utf-8-sig")
    assert '<depend>SD_SMA_DB</depend>' not in (launcher / 'installer/SD_SMA_Service.xml').read_text()
    service_script = (launcher / 'installer/Install-SD_SMA-Service.ps1').read_text()
    assert 'sc.exe config SD_SMA depend= /' in service_script
    assert "-Action Start" not in (launcher / 'installer/Start-SD_SMA.ps1').read_text()
    sections = {}
    section = []
    for line in source.splitlines():
        if line.startswith("["):
            section = sections.setdefault(line, [])
        elif line.strip():
            section.append(line)
    tasks = sections["[Tasks]"]
    assert 'Flags: exclusive' in tasks[0] and 'unchecked' not in tasks[0]
    assert 'Flags: exclusive unchecked' in tasks[1]
    assert "RunRequiredScript('Initialize-SD_SMA.ps1', '')" in source
    assert "RunRequiredScript('Manage-SD_SMA-Database.ps1', '-Action Initialize')" in source
    assert "if WizardIsTaskSelected('servicemode') then" in source
    assert 'if ResultCode <> 0 then' in source and 'RaiseException(' in source
    postinstall = source[source.index('procedure RunRequiredScript('):source.index('procedure CurStepChanged(')]
    assert "if Name = 'Manage-SD_SMA-Database.ps1' then" in postinstall
    assert "Arguments := Arguments + ' -ErrorFile" in postinstall
    assert 'LoadStringFromFile(ErrorFile, ErrorText)' in postinstall
    assert 'UTF8Decode(ErrorText)' in postinstall and 'IntToStr(ResultCode)' in postinstall
    assert 'logs\\launcher\\launcher.log' in postinstall and 'logs\\database' in postinstall
    assert 'Log(Failure)' in postinstall and 'RaiseException(Failure)' in postinstall
    icons = sections["[Icons]"]
    assert len(icons) == 4
    assert all('Tasks:' in line for line in icons)
    assert sum('manualmode' in line and 'start.bat' in line for line in icons) == 2
    assert "WizardIsTaskSelected('manualmode')" in source

    with tempfile.TemporaryDirectory(prefix="sd_sma_installer_") as directory:
        root = Path(directory)
        payload = root / "Runtime"
        payload.mkdir()
        (payload / "placeholder.txt").write_text("installer compile check", encoding="ascii")
        (payload / '_Service').mkdir()
        (payload / '_Launcher').mkdir()
        shutil.copy(launcher / 'installer/Manage-SD_SMA-Database.ps1', payload / '_Service')
        shutil.copy(launcher / 'launcher_config.json', payload / '_Launcher')
        (payload / '_Service/VC_redist.x64.exe').write_bytes(b'compile-only fixture')
        result = subprocess.run(
            [compiler, f"/DSourceRoot={payload}", f"/DOutputDir={root / 'Output'}",
             "/DAppVersion=0.0.0", "/DVCRuntimeVersion=14.0.0.0", str(installer)],
            capture_output=True, text=True, errors="replace", timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "Output" / "SD_SMA_ManagedDB_Setup_0.0.0.exe").is_file()

        installed = root / 'SmartData/SD SMA/config/launcher/launcher_config.json'
        installed.parent.mkdir(parents=True)
        error_file = root / 'preflight-error.txt'
        env = {key: value for key, value in os.environ.items() if key.lower() not in ('programdata', 'psmodulepath')}
        env['ProgramData'] = str(root)
        check_service_registration(launcher, root, env)
        for content, expected in (('{"managed_database":{"enabled":false}}', 'Existing standard installation'),
                                  ('invalid-json', None)):
            installed.write_text(content, encoding='utf-8')
            error_file.write_text('stale error', encoding='utf-8')
            result = subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(launcher / 'installer/Manage-SD_SMA-Database.ps1'), '-Action', 'Preflight',
                 '-ProgramRoot', str(payload), '-ErrorFile', str(error_file)],
                env=env, capture_output=True, timeout=30,
            )
            assert result.returncode == 1, result.stdout + result.stderr
            message = error_file.read_text(encoding='utf-8')
            assert message and message != 'stale error'
            if expected:
                assert expected in message, message

        installed.write_text(json.dumps({'managed_database': {'enabled': True}}), encoding='utf-8')
        secret = root / 'SmartData/SD SMA/secrets/launcher_security.json'
        secret.parent.mkdir()
        damaged = '{"password":"test-only-must-not-leak",invalid-json}'
        secret.write_text(damaged, encoding='utf-8')
        for action in ('Preflight', 'Initialize', 'Start'):
            error_file.write_text('stale error', encoding='utf-8')
            result = subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(launcher / 'installer/Manage-SD_SMA-Database.ps1'), '-Action', action,
                 '-ProgramRoot', str(payload), '-ErrorFile', str(error_file)],
                env=env, capture_output=True, timeout=30,
            )
            assert result.returncode == 1, result.stdout + result.stderr
            message = error_file.read_text(encoding='utf-8')
            assert 'Cannot read database selection' in message, message
            assert 'test-only-must-not-leak' not in message
            assert secret.read_text(encoding='utf-8') == damaged
        original = json.dumps({'database_binding': {'mode': 'external', 'external_credential_id': 'test-only'}})
        secret.write_text(original, encoding='utf-8')
        for action in ('Initialize', 'Start'):
            result = subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(launcher / 'installer/Manage-SD_SMA-Database.ps1'), '-Action', action,
                 '-ProgramRoot', str(payload), '-ErrorFile', str(error_file)],
                env=env, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            assert not error_file.exists(), 'Successful initialization retained a stale error'
            assert secret.read_text(encoding='utf-8') == original
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            installed.write_text(json.dumps({'managed_database': {'enabled': True, 'port': occupied.getsockname()[1]}}), encoding='utf-8')
            script_path = str(launcher / 'installer/Manage-SD_SMA-Database.ps1').replace("'", "''")
            fixture = str(payload).replace("'", "''")
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 "function Get-CimInstance { return $null }; & '" + script_path + "' -Action Preflight -ProgramRoot '" + fixture + "'"],
                env=env, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr

    for script in [*(launcher / 'installer').glob('*.ps1'), *(launcher / 'scripts').glob('*.ps1')]:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$tokens=$null; $errors=$null; "
             "[System.Management.Automation.Language.Parser]::ParseFile('"
             + str(script).replace("'", "''") + "', [ref]$tokens, [ref]$errors) | Out-Null; "
             "if($errors.Count){$errors | Out-String | Write-Error; exit 1}"],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    print("installer modes: new/existing service registration, database error reporting, Inno compilation and PowerShell syntax passed")


def check_service_registration(launcher, root, env):
    """Run both real scripts with only the Windows service/process boundary replaced."""
    harness = root / 'service-registration.ps1'
    harness.write_text(r'''
param([string]$InstallerDir, [string]$ProgramRoot, [string]$Kind, [int]$Exists, [int]$InstallExit)
$ErrorActionPreference = 'Stop'
$registered = [bool]$Exists
$calls = [Collections.Generic.List[string]]::new()
$status = [pscustomobject]@{Status='Stopped'}
$status | Add-Member ScriptMethod WaitForStatus { param($state, $timeout) }
function Get-Service {
    param($Name)
    if ($registered) { return $status }
}
function Get-CimInstance {
    if ($registered) { return [pscustomobject]@{PathName=(Join-Path $ProgramRoot '_Service\SD_SMA_DB.exe')} }
}
function Start-Service { $calls.Add('start-database') }
function Invoke-Wrapper {
    param($Command)
    $calls.Add($Command)
    if ($Command -notin @('install', 'start')) { throw "Unsupported WinSW 2 command: $Command" }
    $global:LASTEXITCODE = if ($Command -eq 'install') { $InstallExit } else { 0 }
    $script:registered = $true
}
function Invoke-Launcher {
    $calls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
}
function Invoke-ServiceConfig {
    $calls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
}
Set-Alias -Name (Join-Path $InstallerDir 'SD_SMA_Service.exe') -Value Invoke-Wrapper
Set-Alias -Name (Join-Path $ProgramRoot '_Service\SD_SMA_DB.exe') -Value Invoke-Wrapper
Set-Alias -Name (Join-Path $ProgramRoot '_Launcher\SD_SMA_Launcher.exe') -Value Invoke-Launcher
Set-Alias -Name sc.exe -Value Invoke-ServiceConfig
if ($Kind -eq 'database') {
    & (Join-Path $InstallerDir 'Manage-SD_SMA-Database.ps1') -Action Initialize -ProgramRoot $ProgramRoot
} else {
    & (Join-Path $InstallerDir 'Install-SD_SMA-Service.ps1')
}
$calls | ConvertTo-Json -Compress
''', encoding='utf-8-sig')
    fixture_env = dict(env, ProgramData=str(root / 'service-data'))
    for kind in ('database', 'launcher'):
        for exists, install_exit in ((0, 0), (1, 0), (0, 23)):
            result = subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(harness),
                 '-InstallerDir', str(launcher / 'installer'), '-ProgramRoot', str(root / 'service-package'),
                 '-Kind', kind, '-Exists', str(exists), '-InstallExit', str(install_exit)],
                env=fixture_env, capture_output=True, text=True, errors='replace', timeout=30,
            )
            if install_exit:
                assert result.returncode != 0 and 'Failed to register' in result.stderr, result.stderr
                continue
            assert result.returncode == 0, result.stdout + result.stderr
            calls = json.loads(result.stdout)
            if kind == 'database':
                assert calls == [
                    f'--config {root / "service-data/SmartData/SD SMA/config/launcher/launcher_config.json"} --initialize-database',
                    *([] if exists else ['install']), 'start-database',
                    f'--config {root / "service-data/SmartData/SD SMA/config/launcher/launcher_config.json"} --check-database',
                ], calls
            else:
                assert calls == [*([] if exists else ['install']), 'config SD_SMA depend= /', 'start'], calls


if __name__ == "__main__":
    main()
