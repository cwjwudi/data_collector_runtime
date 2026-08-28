const state={status:null,unlocked:false,pinMode:"undecided",credentials:[],assignments:{},currentPath:"",parentPath:"",selectedPath:"",previewToken:"",rootSignature:""};
const $=id=>document.getElementById(id);

async function api(url,options={}){
  const response=await fetch(url,{credentials:"same-origin",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  let data={}; try{data=await response.json();}catch{data={detail:await response.text()};}
  if(!response.ok){const error=new Error(data.detail||`请求失败 (${response.status})`);error.status=response.status;throw error;}
  return data;
}
function toast(message){const node=$("toast");node.textContent=message;node.classList.add("show");clearTimeout(node.timer);node.timer=setTimeout(()=>node.classList.remove("show"),3500);}
function formatSeconds(value){const seconds=Math.max(0,Number(value)||0);if(seconds<60)return `${Math.floor(seconds)} 秒`;if(seconds<3600)return `${Math.floor(seconds/60)} 分`;return `${Math.floor(seconds/3600)} 小时 ${Math.floor(seconds%3600/60)} 分`;}
const stateLabel={running:"运行中",starting:"启动中",stopping:"停止中",stopped:"已停止",restarting:"重启中",failed:"故障"};

async function refreshStatus(){
  try{state.status=await api("/api/launcher/status");state.pinMode=state.status.security?.pin_mode||"undecided";renderServices();renderSecurityState();renderNetworkState();$("clock").textContent=`本机服务管理 · ${new Date().toLocaleString()}`;}
  catch(error){$("clock").textContent=`管理服务连接失败：${error.message}`;}
}
function metric(label,value){const node=document.createElement("div");node.className="metric";const strong=document.createElement("strong");strong.textContent=value;node.append(strong,document.createTextNode(label));return node;}
function actionButton(label,action,service,kind="secondary"){
  const button=document.createElement("button");button.textContent=label;button.className=kind;button.onclick=()=>runServiceAction(service,action,label);return button;
}
function renderServices(){
  const grid=$("serviceGrid");grid.replaceChildren();
  for(const service of state.status?.services||[]){
    const card=document.createElement("article");card.className="service-card";
    const title=document.createElement("div");title.className="service-title";const heading=document.createElement("h2");heading.textContent=service.title;const badge=document.createElement("span");badge.className=`state ${service.state}`;badge.textContent=stateLabel[service.state]||service.state;title.append(heading,badge);
    const metrics=document.createElement("div");metrics.className="metrics";metrics.append(metric("端口",String(service.port)),metric("进程 PID",service.pid?String(service.pid):"—"),metric("运行时长",formatSeconds(service.uptime_seconds)),metric("重启次数",String(service.restart_count)),metric("CPU（整机）",service.cpu_percent==null?"—":`${service.cpu_percent}%`),metric("内存（进程树）",service.memory_mb==null?"—":`${service.memory_mb} MB`));
    const error=document.createElement("p");error.className="error-summary";error.textContent=service.last_error||"";
    const actions=document.createElement("div");actions.className="card-actions";actions.append(actionButton("启动","start",service,"primary"),actionButton("停止","stop",service,"danger"),actionButton("重启","restart",service));
    actions.children[0].disabled=["running","starting","restarting"].includes(service.state);actions.children[1].disabled=["stopped","stopping","failed"].includes(service.state);actions.children[2].disabled=service.state==="stopped";
    card.append(title,metrics,error,actions);
    if(service.url_path){const link=document.createElement("a");link.className="open-link";link.href=`${window.location.protocol}//${window.location.hostname}:${service.port}${service.url_path}`;link.target="_blank";link.rel="noopener";link.textContent="打开服务页面";card.append(link);}
    grid.append(card);
  }
}

function confirmAction(title,text){return new Promise(resolve=>{const dialog=$("confirmDialog");$("confirmTitle").textContent=title;$("confirmText").textContent=text;dialog.onclose=()=>resolve(dialog.returnValue==="ok");dialog.showModal();});}
async function runServiceAction(service,action,label){
  if(!await ensureAdmin())return;
  if(!await confirmAction(`${label}${service.title}`,`确定要${label}“${service.title}”吗？`))return;
  try{await api(`/api/launcher/services/${encodeURIComponent(service.name)}/${action}`,{method:"POST"});toast(`已提交：${label}${service.title}`);await refreshStatus();}catch(error){toast(error.message);}
}

function buildPinPad(){const pad=$("pinPad");for(const value of [1,2,3,4,5,6,7,8,9,"清除",0,"退格"]){const button=document.createElement("button");button.type="button";button.textContent=value;button.onclick=()=>{if(value==="清除")$("pinInput").value="";else if(value==="退格")$("pinInput").value=$("pinInput").value.slice(0,-1);else if($("pinInput").value.length<12)$("pinInput").value+=value;};pad.append(button);}}
async function ensureAdmin(){
  try{const session=await api("/api/launcher/auth/session");state.pinMode=session.pin_mode;if(session.unlocked){state.unlocked=true;updateLockButton();renderSecurityState();return true;}return await showPinDialog(session.pin_mode==="undecided");}catch(error){toast(error.message);return false;}
}
function showPinDialog(setup){return new Promise(resolve=>{const dialog=$("pinDialog");let settled=false;const finish=value=>{if(settled)return;settled=true;resolve(value);};$("pinTitle").textContent=setup?"设置管理员 PIN":"管理员解锁";$("pinHelp").textContent=setup?"首次使用：可设置 6–12 位数字 PIN，或明确选择暂不启用":"输入管理员 PIN 以继续";$("pinInput").value="";$("pinError").textContent="";$("pinSkip").classList.toggle("hidden",!setup);dialog.dataset.setup=setup?"1":"0";dialog.onclose=()=>finish(dialog.returnValue==="success");dialog._finish=finish;dialog.showModal();});}
function closePinDialog(result=false){const dialog=$("pinDialog");dialog.returnValue=result?"success":"cancel";if(dialog.open)dialog.close();dialog._finish?.(result);}
$("pinCancel").onclick=()=>closePinDialog(false);
$("pinDialog").addEventListener("click",event=>{if(event.target===$("pinDialog"))closePinDialog(false);});
$("pinSkip").onclick=async()=>{const dialog=$("pinDialog");dialog.onclose=null;dialog.close();if(!await confirmAction("不启用管理员 PIN","关闭 PIN 后，本机和远程访问者都能执行服务控制、配置导入和密码管理。确定继续？")){dialog._finish?.(false);return;}try{await api("/api/launcher/auth/disable",{method:"POST"});state.pinMode="disabled";state.unlocked=true;dialog._finish?.(true);updateLockButton();renderSecurityState();toast("管理员 PIN 已关闭");}catch(error){dialog._finish?.(false);toast(error.message);}};
$("pinForm").addEventListener("submit",async event=>{event.preventDefault();const dialog=$("pinDialog");try{const endpoint=dialog.dataset.setup==="1"?"setup":"unlock";const result=await api(`/api/launcher/auth/${endpoint}`,{method:"POST",body:JSON.stringify({pin:$("pinInput").value})});state.pinMode=result.pin_mode||"enabled";state.unlocked=true;updateLockButton();renderSecurityState();closePinDialog(true);toast("管理操作已解锁");}catch(error){$("pinError").textContent=error.message;}});
function updateLockButton(){if(state.pinMode==="disabled")$("lockButton").textContent="PIN 未启用";else $("lockButton").textContent=state.unlocked?"锁定管理":"管理操作";}
$("lockButton").onclick=async()=>{if(state.pinMode==="disabled"){document.querySelector('[data-page="settings"]').click();return;}if(state.unlocked){await api("/api/launcher/auth/lock",{method:"POST"});state.unlocked=false;updateLockButton();toast("已锁定");}else await ensureAdmin();};

document.querySelectorAll(".nav").forEach(button=>button.onclick=async()=>{document.querySelectorAll(".nav").forEach(item=>item.classList.toggle("active",item===button));document.querySelectorAll(".page").forEach(page=>page.classList.remove("active"));$(`${button.dataset.page}Page`).classList.add("active");if(button.dataset.page==="credentials"&&await ensureAdmin())await loadCredentials();if(button.dataset.page==="import"&&await ensureAdmin())await loadRoots();if(button.dataset.page==="settings")await refreshStatus();});

async function loadCredentials(){try{const data=await api("/api/launcher/credentials");state.credentials=data.credentials;state.assignments=data.assignments;renderCredentials();}catch(error){if(error.status===401)state.unlocked=false;toast(error.message);}}
function renderCredentials(){
  const list=$("credentialList");list.replaceChildren();
  if(!state.credentials.length){const empty=document.createElement("p");empty.className="hint";empty.textContent="尚未创建凭据档案";list.append(empty);}
  for(const row of state.credentials){const item=document.createElement("div");item.className="credential";const info=document.createElement("div");const name=document.createElement("strong");name.textContent=row.name;const detail=document.createElement("div");detail.textContent=`${row.username}@${row.host}:${row.port}${row.database?` / ${row.database}`:""} · ${row.password_configured?"密码已配置":"无密码"}`;info.append(name,detail);const actions=document.createElement("div");actions.className="credential-actions";const edit=document.createElement("button");edit.textContent="编辑";edit.onclick=()=>openCredential(row);const test=document.createElement("button");test.textContent="测试";test.onclick=()=>testCredential(row.id);const remove=document.createElement("button");remove.textContent="删除";remove.className="danger";remove.onclick=()=>deleteCredential(row);actions.append(edit,test,remove);item.append(info,actions);list.append(item);}
  const assignments=$("assignmentList");assignments.replaceChildren();for(const [service,label] of Object.entries({collector_web:"Collector",query_web:"Query Web",db_admin:"DB Admin"})){const item=document.createElement("div");item.className="assignment";const title=document.createElement("strong");title.textContent=label;const select=document.createElement("select");select.append(new Option("不使用中央凭据",""));for(const row of state.credentials)select.append(new Option(row.name,row.id));select.value=state.assignments[service]||"";select.onchange=()=>saveAssignment(service,select.value);item.append(title,select);assignments.append(item);}
}
function openCredential(row={}){$("credentialId").value=row.id||"";$("credentialName").value=row.name||"";$("credentialHost").value=row.host||"127.0.0.1";$("credentialPort").value=row.port||3306;$("credentialUsername").value=row.username||"root";$("credentialDatabase").value=row.database||"";$("credentialPassword").value="";$("credentialPassword").required=!row.id;$("credentialError").textContent="";$("credentialDialog").showModal();}
$("newCredential").onclick=async()=>{if(await ensureAdmin())openCredential();};
$("credentialCancel").onclick=()=>$("credentialDialog").close("cancel");
$("credentialForm").addEventListener("submit",async event=>{event.preventDefault();if(event.submitter&&event.submitter.id!=="credentialSave"){$("credentialDialog").close("cancel");return;}const body={id:$("credentialId").value||undefined,name:$("credentialName").value,engine:"mysql",host:$("credentialHost").value,port:Number($("credentialPort").value),username:$("credentialUsername").value,database:$("credentialDatabase").value,password:$("credentialPassword").value};try{await api("/api/launcher/credentials",{method:"POST",body:JSON.stringify(body)});$("credentialDialog").close();toast("凭据已加密保存");await loadCredentials();}catch(error){$("credentialError").textContent=error.message;}});
async function saveAssignment(service,credential_id){try{await api(`/api/launcher/credentials/assignments/${service}`,{method:"PUT",body:JSON.stringify({credential_id})});toast("分配已保存，运行中的服务正在重启");await loadCredentials();}catch(error){toast(error.message);}}
async function testCredential(id){try{const result=await api(`/api/launcher/credentials/${id}/test`,{method:"POST"});toast(result.message);}catch(error){toast(error.message);}}
async function deleteCredential(row){if(!await confirmAction("删除凭据",`删除“${row.name}”并取消所有服务分配？`))return;try{await api(`/api/launcher/credentials/${row.id}`,{method:"DELETE"});toast("凭据已删除");await loadCredentials();}catch(error){toast(error.message);}}

function renderRoots(roots,preserveBrowser=false){const signature=JSON.stringify(roots.map(row=>[row.path,row.exists]));if(preserveBrowser&&signature===state.rootSignature)return;state.rootSignature=signature;const grid=$("rootGrid");grid.replaceChildren();for(const row of roots){const button=document.createElement("button");button.className="root";button.textContent=`${row.name}\n${row.path}`;button.disabled=!row.exists;button.onclick=()=>browse(row.path);grid.append(button);}if(!preserveBrowser){$("entryGrid").replaceChildren();$("browserPath").textContent="请选择 U 盘或导入箱";}}
async function refreshImportRoots(preserveBrowser=true,silent=false){try{const data=await api("/api/launcher/filesystem/roots");renderRoots(data.roots||[],preserveBrowser);}catch(error){if(!silent)toast(error.message);}}
async function loadRoots(){try{const [data,settings]=await Promise.all([api("/api/launcher/filesystem/roots"),api("/api/launcher/import/settings")]);$("allowedImportRoots").value=(settings.allowed_import_roots||[]).join("\n");renderRoots(data.roots||[],false);}catch(error){toast(error.message);}}
$("refreshImportRoots").onclick=()=>refreshImportRoots(true,false);
$("saveImportRoots").onclick=async()=>{const roots=$("allowedImportRoots").value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);try{await api("/api/launcher/import/settings",{method:"PUT",body:JSON.stringify({allowed_import_roots:roots})});toast("本地目录白名单已保存");await loadRoots();}catch(error){toast(error.message);}};
async function browse(path){try{const data=await api(`/api/launcher/filesystem/entries?path=${encodeURIComponent(path)}`);state.currentPath=data.current;state.parentPath=data.parent;state.selectedPath="";$("browserPath").textContent=data.current;$("browserUp").disabled=!data.parent;$("selectedPath").textContent="无";$("inspectImport").disabled=true;$("importPreview").classList.add("hidden");const grid=$("entryGrid");grid.replaceChildren();const current=document.createElement("button");current.className="entry";current.textContent="选择当前目录";current.onclick=()=>selectEntry(current,data.current);grid.append(current);for(const row of data.entries){const button=document.createElement("button");button.className="entry";button.textContent=`${row.type==="directory"?"📁":"📄"} ${row.name}`;if(row.type==="directory")button.onclick=()=>browse(row.path);else button.onclick=()=>selectEntry(button,row.path);grid.append(button);}}catch(error){toast(error.message);}}
function selectEntry(button,path){document.querySelectorAll(".entry.selected").forEach(node=>node.classList.remove("selected"));button.classList.add("selected");state.selectedPath=path;$("selectedPath").textContent=path;$("inspectImport").disabled=false;}
$("browserUp").onclick=()=>state.parentPath&&browse(state.parentPath);
$("inspectImport").onclick=async()=>{try{const result=await api("/api/launcher/import/inspect",{method:"POST",body:JSON.stringify({service:$("importService").value,paths:[state.selectedPath]})});state.previewToken=result.preview_token;const preview=$("importPreview");preview.replaceChildren();const heading=document.createElement("h3");heading.textContent=`检查通过：${result.services.length} 个插件 / ${result.files.length} 个文件`;preview.append(heading);for(const file of result.files){const row=document.createElement("p");row.textContent=`[${file.service}] ${file.target_name}${file.will_overwrite?"（将覆盖）":"（新增）"}`;preview.append(row);}for(const warning of result.warnings){const row=document.createElement("p");row.className="error";row.textContent=warning;preview.append(row);}const apply=document.createElement("button");apply.className="danger";apply.textContent="确认导入并应用";apply.onclick=applyImport;preview.append(apply);preview.classList.remove("hidden");}catch(error){toast(error.message);}};
async function applyImport(){if(!await confirmAction("应用配置","系统会先备份现有配置；运行中的目标服务将自动重启。确定继续？"))return;try{const result=await api("/api/launcher/import/apply",{method:"POST",body:JSON.stringify({preview_token:state.previewToken})});toast(`导入成功，已备份到 ${result.backup_dir}`);$("importPreview").classList.add("hidden");await refreshStatus();}catch(error){toast(error.message);}}

function renderSecurityState(){
  const mode=state.pinMode;
  $("securityWarning").classList.toggle("hidden",mode!=="disabled");
  $("pinStatusText").textContent=mode==="enabled"?"PIN 保护已启用，管理操作需要解锁。":mode==="disabled"?"PIN 保护已关闭，所有访问者都可以执行管理操作。":"尚未选择是否启用管理员 PIN。";
  $("configurePin").classList.toggle("hidden",mode==="enabled");
  $("disablePin").classList.toggle("hidden",mode!=="enabled");
  updateLockButton();
}
function renderNetworkState(){
  const network=state.status?.network;if(!network)return;
  const radio=document.querySelector(`input[name="networkMode"][value="${network.mode}"]`);if(radio)radio.checked=true;
  $("saveNetworkMode").disabled=Boolean(network.applying);
  $("networkMessage").textContent=network.last_error||network.warning||(network.applying?"正在切换监听地址并重启运行中的服务…":network.mode==="global"?"当前允许其他设备访问 8090–8094。":"当前仅允许本机访问。");
}
$("configurePin").onclick=async()=>{if(await showPinDialog(true))await refreshStatus();};
$("disablePin").onclick=async()=>{if(!await ensureAdmin())return;if(!await confirmAction("关闭 PIN 保护","关闭后，本机和远程访问者都可以执行全部管理操作。确定关闭？"))return;try{await api("/api/launcher/auth/disable",{method:"POST"});state.pinMode="disabled";state.unlocked=true;toast("管理员 PIN 已关闭");await refreshStatus();}catch(error){toast(error.message);}};
$("saveNetworkMode").onclick=async()=>{const selected=document.querySelector('input[name="networkMode"]:checked');if(!selected||!await ensureAdmin())return;const mode=selected.value;const remote=!['127.0.0.1','localhost','::1'].includes(window.location.hostname);const warning=mode==="local"&&remote?"切换后当前远程连接会中断，只能在设备本机打开 http://127.0.0.1:8090。确定继续？":"切换时会重启当前运行的四个业务服务，确定继续？";if(!await confirmAction("应用网络设置",warning))return;try{const result=await api("/api/launcher/settings/network",{method:"PUT",body:JSON.stringify({mode})});toast(result.warning||"网络模式正在切换");if(mode==="local"&&remote){$("networkMessage").textContent="远程连接即将中断，请到设备本机继续操作。";}else setTimeout(refreshStatus,1500);}catch(error){toast(error.message);}};

buildPinPad();refreshStatus();setInterval(refreshStatus,2000);setInterval(()=>{if($("importPage").classList.contains("active"))refreshImportRoots(true,true);},1000);
