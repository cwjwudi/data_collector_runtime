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

script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const html=fs.readFileSync(process.argv[2],'utf8');
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{
  value:'',disabled:false,hidden:false,textContent:'',required:false,
  reportValidity(){return true;},replaceChildren(){},append(){},addEventListener(){},
}]));
const requests=[];
let failure=false;
const profile={id:'saved',name:'saved',host:'example.invalid',port:3306,database:'test_only',username:'operator',password_configured:true};
const current={binding:{mode:'external',external_credential_id:'saved'},targets:{},legacy:false,managed_installed:false};
const context=vm.createContext({
  $:id=>{assert.ok(id in elements,id);return elements[id];},
  state:{credentials:[],status:{services:[]}},window:{location:{protocol:'http:',hostname:'localhost'}},
  document:{createElement:()=>({textContent:''})},Option:class {constructor(text,value){this.text=text;this.value=value;}},
  SdI18n:{t:key=>key},toast(){},ensureAdmin:async()=>true,confirmAction:async()=>true,refreshStatus:async()=>{},
  api:async(url,options={})=>{
    requests.push({url,options});
    if(url.endsWith('/credentials'))return {credentials:[profile]};
    if(url.endsWith('/database'))return current;
    if(failure)throw Error('injected network failure');
    return {ok:true,services:{},...(url.endsWith('/apply')?{stage:'complete'}:{})};
  },
});
(async()=>{
  vm.runInContext(source,context);
  await vm.runInContext('loadDatabaseManagement()',context);
  // A real select retains a saved value once an option exists.
  elements.databaseProfile.value='saved';
  vm.runInContext('databaseProfileChanged()',context);
  assert.equal(elements.credentialPassword.value,'');
  assert.equal(elements.credentialUsername.value,'operator');
  assert.equal(elements.credentialPassword.required,false);
  elements.credentialPassword.value='synthetic-only';
  await vm.runInContext('runDatabaseAction(false)',context);
  assert.equal(requests.filter(r=>r.url.endsWith('/apply')).length,0);
  assert.equal(requests.at(-1).url,'/api/launcher/database/test');
  assert.equal(JSON.parse(requests.at(-1).options.body).credential.password,'synthetic-only');
  assert.equal(elements.databaseResult.textContent,'launcher.database.tested\nlauncher.database.grantsHint');
  failure=true;
  await vm.runInContext('runDatabaseAction(true)',context);
  assert.equal(elements.databaseResult.textContent,'injected network failure');
  for(const id of ['databaseTest','databaseApply','databaseRefresh'])assert.equal(elements[id].disabled,false);
  failure=false;
  await vm.runInContext('runDatabaseAction(true)',context);
  assert.equal(elements.credentialPassword.value,'');
  assert.equal(elements.databaseResult.textContent,'launcher.database.applied');
  for(const row of requests)assert.ok(!row.url.includes('synthetic-only'));
  console.log('PASS database UI: saved password not filled, unsaved test, failed buttons restored, apply success');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
subprocess.run(['node', '-e', script, str(ROOT / '_Launcher/static/database.js'),
                str(ROOT / '_Launcher/static/index.html')], check=True)
print("launcher_auth_check: OK")
