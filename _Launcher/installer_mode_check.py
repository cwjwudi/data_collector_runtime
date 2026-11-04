"""Compile the real installer with a tiny payload; never install or change services."""

import os
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
    print("installer modes: task/shortcut checks, Inno compilation and PowerShell syntax passed")


if __name__ == "__main__":
    main()
