"""Offline PIN API and browser response parsing regression checks."""
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_Prj/SD_SMA_COMMON"))
from fastapi.testclient import TestClient
from launcher_security import LauncherSecurityStore
from launcher_web import create_management_app

with TemporaryDirectory() as temp:
    path = Path(temp) / "security.json"
    store = LauncherSecurityStore(path)
    app = create_management_app(None, store, None, ROOT / "_Launcher/static")
    client = TestClient(app)
    assert client.post("/api/launcher/auth/setup", json={"pin": "123456"}).status_code == 200
    client.post("/api/launcher/auth/lock")
    assert not client.get("/api/launcher/auth/session").json()["unlocked"]
    # Reload the saved PIN to exercise the same path as an existing installation.
    store = LauncherSecurityStore(path)
    client = TestClient(create_management_app(None, store, None, ROOT / "_Launcher/static"))
    wrong = client.post("/api/launcher/auth/unlock", json={"pin": "654321"})
    assert wrong.status_code == 401 and wrong.json()["detail"]
    assert client.post("/api/launcher/auth/unlock", json={"pin": "123456"}).status_code == 200
    assert client.get("/api/launcher/auth/session").json()["unlocked"]
    client.post("/api/launcher/auth/lock")
    assert not client.get("/api/launcher/auth/session").json()["unlocked"]

# Compiled modules extract elsewhere; web assets must follow the installed static directory.
with TemporaryDirectory() as temp:
    package = Path(temp)
    static = package / "_Launcher/static"
    common = package / "_Prj/SD_SMA_COMMON/sd_sma_common/web"
    static.mkdir(parents=True)
    common.mkdir(parents=True)
    (common / "base.css").write_text("/* relocated package */", encoding="utf-8")
    client = TestClient(create_management_app(None, None, None, static))
    assert client.get("/common/base.css").text == "/* relocated package */"

# Execute the actual helper against native, single-use Fetch Response bodies.
source = (ROOT / "_Launcher/static/app.js").read_text(encoding="utf-8")
helper = source[source.index("async function api("):source.index("function toast(")]
script = helper + r"""
(async()=>{
  const assert=require('node:assert/strict');
  global.fetch=async()=>new Response(JSON.stringify({ok:true}),{status:200});
  assert.deepEqual(await api('/test'),{ok:true});
  for(const [body,status] of [['Internal Server Error',500],[JSON.stringify({detail:'Wrong PIN'}),401],['',502]]){
    global.fetch=async()=>new Response(body,{status});
    await assert.rejects(api('/test'),e=>e.status===status && !e.message.includes('already read'));
  }
  global.fetch=async()=>new Response(null,{status:204});
  assert.deepEqual(await api('/test'),{});
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
subprocess.run(["node", "-e", script], check=True)
print("launcher_auth_check: OK")
