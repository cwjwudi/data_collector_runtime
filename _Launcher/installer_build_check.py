"""Run the installer tool probe in Windows PowerShell without building a package."""

import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    builder = Path(__file__).parent / "scripts" / "build_installer_package.ps1"
    source = builder.read_text(encoding="utf-8-sig")
    portable = (builder.parent / 'build_portable_package.ps1').read_text(encoding='utf-8-sig')
    assert '"launcher_database.py"' in source and '"launcher_database.py"' in portable
    assert '--include-package=sqlite3' in source
    probe = source[source.index("function Ensure-Nuitka {"):source.index("function Resolve-InnoSetup {")]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        batch = root / "modules.bat"
        preamble = (builder.parent.parent / "build.bat").read_text().split("chcp ", 1)[0]
        batch.write_text(
            preamble + 'powershell -NoProfile -Command "$ErrorActionPreference = \'Stop\'; '
            'Get-Command Get-FileHash | Out-Null"\nexit /b %ERRORLEVEL%\n',
            encoding="ascii",
        )
        result = subprocess.run(
            ["cmd", "/c", str(batch)], capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for name in ("nuitka", "psutil", "zstandard"):
            (root / f"{name}.py").write_text("", encoding="utf-8")
        script = root / "probe.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n$SkipToolInstall = $true\n"
            + probe
            + "\ntry { Ensure-Nuitka -PythonExe $env:BUILD_CHECK_PYTHON; 'present' }\n"
            + "catch { if ($_.Exception.Message -like '*-SkipToolInstall was supplied.*') "
            + "{ 'missing' } else { throw } }\n",
            encoding="utf-8-sig",
        )
        env = dict(os.environ, PYTHONPATH=str(root), BUILD_CHECK_PYTHON=sys.executable)
        for missing in (None, "nuitka", "psutil", "zstandard"):
            if missing:
                (root / f"{missing}.py").write_text("raise ImportError('test missing tool')\n", encoding="utf-8")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                env=env, cwd=root, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == ("missing" if missing else "present"), result.stdout
            if missing:
                (root / f"{missing}.py").write_text("", encoding="utf-8")
    print("installer build checks: 5/5 passed")
    helper = builder.parent / 'managed_database_package.ps1'
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        tools = root / 'installer/tools'
        tools.mkdir(parents=True)
        archive = tools / 'mariadb.zip'
        (root / 'installer/mariadb-package.json').write_text(json.dumps({
            'filename': archive.name, 'url': 'https://example.invalid/test-fixture',
            'sha256': hashlib.sha256(b'checked-package').hexdigest(),
        }))
        command = ['powershell', '-NoProfile', '-Command',
                   "$ErrorActionPreference='Stop'; . '" + str(helper) + "'; Get-ManagedDatabasePackage -LauncherDir '" + str(root) + "'"]
        for content, expected in ((None, False), (b'tampered', False), (b'checked-package', True)):
            if content is not None:
                archive.write_bytes(content)
            result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                    env={key: value for key, value in os.environ.items() if key.lower() != 'psmodulepath'})
            assert (result.returncode == 0) == expected, result.stdout + result.stderr
    print('MariaDB package: missing and corrupt archives rejected; matching SHA-256 accepted')


if __name__ == "__main__":
    main()
