"""Run the installer tool probe in Windows PowerShell without building a package."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    builder = Path(__file__).parent / "scripts" / "build_installer_package.ps1"
    source = builder.read_text(encoding="utf-8-sig")
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


if __name__ == "__main__":
    main()
