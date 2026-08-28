const STATE_KEY = 'sd_sma_db_admin_state_v2';
let activeJobId = '';
let pollTimer = null;
let defaultOutputDir = '';
let availableBackups = [];
let selectedManagedFiles = new Map();
let currentManagedPath = '';
let currentManagedParent = '';
let currentManagedFiles = [];
let removableRoots = [];
const notifiedJobStates = new Map();
const confirmModalOverlay = document.getElementById('confirm-modal-overlay');
const confirmModalTitle = document.getElementById('confirm-modal-title');
const confirmModalMessage = document.getElementById('confirm-modal-message');
const confirmModalCancel = document.getElementById('confirm-modal-cancel');
const confirmModalConfirm = document.getElementById('confirm-modal-confirm');

function safeStorageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function showStatusBar(text, tone = 'info') {
  const bar = document.getElementById('appStatusBar');
  if (!bar) return;
  bar.textContent = String(text || '');
  bar.dataset.tone = tone;
  bar.hidden = !text;
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

function enableButtonClickFeedback() {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    btn.addEventListener('click', () => {
      btn.classList.remove('is-clicked');
      void btn.offsetWidth;
      btn.classList.add('is-clicked');
      setTimeout(() => btn.classList.remove('is-clicked'), 220);
    });
  }
}

function setHint(id, text, cls = 'muted') {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = text;
    el.className = cls;
  }
  const classes = String(cls).split(/\s+/);
  if (classes.includes('ok')) {
    showStatusBar(text, 'ok');
  } else if (classes.includes('warn') || classes.includes('error')) {
    showStatusBar(text, classes.includes('error') ? 'error' : 'warn');
  }
}

async function openFilesystemBrowser({ purpose, initialPath = '', allowFiles = false }) {
  const overlay = document.getElementById('filesystem-modal-overlay');
  const title = document.getElementById('filesystem-modal-title');
  const pathLabel = document.getElementById('filesystem-modal-path');
  const list = document.getElementById('filesystem-modal-list');
  const cancel = document.getElementById('filesystem-modal-cancel');
  const select = document.getElementById('filesystem-modal-select');
  const refresh = document.getElementById('filesystem-modal-refresh');
  if (!overlay) throw new Error('文件浏览窗口不可用');
  let current = '';
  let removablePollTimer = null;
  let rootSignature = '';

  return new Promise((resolve, reject) => {
    const close = () => {
      if (removablePollTimer) clearInterval(removablePollTimer);
      removablePollTimer = null;
      overlay.style.display = 'none';
    };
    const finish = value => { close(); resolve(value); };
    const fail = error => { close(); reject(error); };
    cancel.onclick = () => finish('');
    overlay.onclick = event => { if (event.target === overlay) finish(''); };
    select.style.display = allowFiles ? 'none' : '';
    select.onclick = () => finish(current);
    refresh.style.display = purpose === 'removable-directory' ? '' : 'none';
    title.textContent = allowFiles
      ? `选择 ${purpose.toUpperCase()} 文件`
      : (purpose === 'removable-directory' ? '选择外部设备目录' : '选择服务器目录');
    overlay.style.display = 'flex';

    const renderButton = (label, meta, onClick) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'filesystem-entry';
      const name = document.createElement('span');
      const detail = document.createElement('span');
      name.textContent = label;
      detail.textContent = meta;
      detail.className = 'muted';
      button.append(name, detail);
      button.onclick = onClick;
      list.appendChild(button);
    };
    const renderRoots = data => {
      current = '';
      select.disabled = true;
      pathLabel.textContent = '允许访问的位置';
      list.innerHTML = '';
      for (const root of data.roots || []) {
        renderButton(root.name, root.exists ? root.path : '目录不存在', () => {
          if (root.exists) showDirectory(root.path).catch(fail);
        });
      }
      if (!(data.roots || []).some(root => root.exists)) {
        renderButton('未检测到外部设备', '插入后会自动刷新，也可点击“立即刷新”', () => {});
      }
    };
    const showRoots = async () => {
      const data = await fetchJson(`/api/filesystem/roots?purpose=${encodeURIComponent(purpose)}`);
      rootSignature = JSON.stringify((data.roots || []).map(root => [root.path, root.exists]));
      renderRoots(data);
    };
    const showDirectory = async path => {
      const data = await fetchJson(`/api/filesystem/entries?purpose=${encodeURIComponent(purpose)}&path=${encodeURIComponent(path)}`);
      current = data.current;
      select.disabled = false;
      pathLabel.textContent = current;
      list.innerHTML = '';
      if (data.parent) renderButton('..', '上一级', () => showDirectory(data.parent).catch(fail));
      else renderButton('位置列表', '返回', () => showRoots().catch(fail));
      for (const entry of data.entries || []) {
        const isDirectory = entry.type === 'directory';
        renderButton(entry.name, isDirectory ? '文件夹' : entry.modified_at, () => {
          if (isDirectory) showDirectory(entry.path).catch(fail);
          else if (allowFiles) finish(entry.path);
        });
      }
    };
    const pollRemovableRoots = async () => {
      if (purpose !== 'removable-directory') return;
      const data = await fetchJson(`/api/filesystem/roots?purpose=${encodeURIComponent(purpose)}`);
      const nextSignature = JSON.stringify((data.roots || []).map(root => [root.path, root.exists]));
      if (nextSignature === rootSignature) return;
      rootSignature = nextSignature;
      const roots = (data.roots || []).filter(root => root.exists);
      const normalizedCurrent = current.toLowerCase();
      const currentStillAvailable = roots.some(root => normalizedCurrent.startsWith(root.path.toLowerCase()));
      if (!current || !currentStillAvailable) renderRoots(data);
    };
    refresh.onclick = () => showRoots().catch(fail);
    if (purpose === 'removable-directory') {
      removablePollTimer = setInterval(() => pollRemovableRoots().catch(() => {}), 1000);
    }
    (initialPath ? showDirectory(initialPath).catch(() => showRoots()) : showRoots()).catch(fail);
  });
}

function showConfirmModal({ title, message, confirmText = '确认执行' }) {
  if (
    !confirmModalOverlay ||
    !confirmModalTitle ||
    !confirmModalMessage ||
    !confirmModalCancel ||
    !confirmModalConfirm
  ) {
    showStatusBar('无法显示操作确认，操作已取消。', 'error');
    return Promise.resolve(false);
  }

  return new Promise(resolve => {
    const oldConfirmText = confirmModalConfirm.textContent;

    const close = result => {
      confirmModalOverlay.style.display = 'none';
      confirmModalConfirm.textContent = oldConfirmText;
      confirmModalCancel.removeEventListener('click', cancel);
      confirmModalConfirm.removeEventListener('click', confirm);
      confirmModalOverlay.removeEventListener('click', clickOutside);
      document.removeEventListener('keydown', keydown);
      resolve(result);
    };
    const cancel = () => close(false);
    const confirm = () => close(true);
    const clickOutside = event => {
      if (event.target === confirmModalOverlay) close(false);
    };
    const keydown = event => {
      if (event.key === 'Escape') close(false);
    };

    confirmModalTitle.textContent = title;
    confirmModalMessage.textContent = message;
    confirmModalConfirm.textContent = confirmText;
    confirmModalOverlay.style.display = 'flex';
    confirmModalCancel.addEventListener('click', cancel);
    confirmModalConfirm.addEventListener('click', confirm);
    confirmModalOverlay.addEventListener('click', clickOutside);
    document.addEventListener('keydown', keydown);
    confirmModalConfirm.focus();
  });
}

function appendOption(select, value, label = '') {
  const name = coerceIdentName(value);
  if (!name) return;
  const op = document.createElement('option');
  op.value = name;
  if (typeof label === 'string' && label && !label.includes('[object Object]')) {
    op.textContent = label;
  } else if (value && typeof value === 'object' && !Array.isArray(value) && value.size_bytes != null) {
    op.textContent = sizedLabel(name, value.size_bytes);
  } else {
    op.textContent = name;
  }
  select.appendChild(op);
}

function formatBytes(bytes) {
  const n = Math.max(0, Number(bytes) || 0);
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n;
  let unit = 'B';
  for (const next of units) {
    value /= 1024;
    unit = next;
    if (value < 1024) break;
  }
  const digits = value >= 100 || unit === 'KB' ? 0 : 1;
  return `${value.toFixed(digits)} ${unit}`;
}

function sizedLabel(name, sizeBytes) {
  const safeName = coerceIdentName(name);
  if (!safeName) return '';
  if (sizeBytes == null || sizeBytes === '') return safeName;
  return `${safeName} (${formatBytes(sizeBytes)})`;
}

/** Extract a usable DB/table identifier; never allow Object.prototype.toString. */
function coerceIdentName(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    const text = String(value).trim();
    return text === '[object Object]' ? '' : text;
  }
  if (typeof value === 'object') {
    if (Array.isArray(value)) return coerceIdentName(value[0]);
    return coerceIdentName(value.name ?? value.table ?? value.database ?? '');
  }
  return '';
}

function normalizeSizedItems(items) {
  return (items || []).map(item => {
    if (typeof item === 'string' || typeof item === 'number') {
      const name = coerceIdentName(item);
      return name ? { name, size_bytes: null } : null;
    }
    if (!item || typeof item !== 'object') return null;
    const name = coerceIdentName(item);
    if (!name) return null;
    return {
      name,
      size_bytes: item.size_bytes == null ? null : Number(item.size_bytes),
    };
  }).filter(Boolean);
}

function hasOption(select, value) {
  const name = coerceIdentName(value);
  return name !== '' && Array.from(select.options).some(option => option.value === name);
}

function setSelectValueIfPresent(id, value) {
  const sel = document.getElementById(id);
  const name = coerceIdentName(value);
  if (name && hasOption(sel, name)) sel.value = name;
}

function updateQuickChipState() {
  const host = String(document.getElementById('dbHost').value).trim();
  const port = String(document.getElementById('dbPort').value).trim();
  const username = String(document.getElementById('dbUsername').value).trim();
  for (const chip of document.querySelectorAll('.quick-chip')) {
    const matchesHost = !chip.dataset.connHost || chip.dataset.connHost === host;
    const matchesPort = !chip.dataset.connPort || chip.dataset.connPort === port;
    const matchesUser = !chip.dataset.connUser || chip.dataset.connUser === username;
    chip.classList.toggle('is-selected', matchesHost && matchesPort && matchesUser);
  }
}

function applyConnectionShortcut(chip) {
  if (chip.dataset.connHost) document.getElementById('dbHost').value = chip.dataset.connHost;
  if (chip.dataset.connPort) document.getElementById('dbPort').value = chip.dataset.connPort;
  if (chip.dataset.connUser) document.getElementById('dbUsername').value = chip.dataset.connUser;
  saveState();
  updateQuickChipState();
  setHint('connectionHint', '已套用快捷连接参数，请输入密码后测试连接。');
}

function togglePasswordVisibility() {
  const input = document.getElementById('dbPassword');
  const btn = document.getElementById('btnTogglePassword');
  const shouldShow = input.type === 'password';
  input.type = shouldShow ? 'text' : 'password';
  btn.textContent = shouldShow ? '隐藏' : '显示';
  btn.setAttribute('aria-label', shouldShow ? '隐藏密码' : '显示密码');
  input.focus();
}

function clearPassword() {
  const input = document.getElementById('dbPassword');
  input.value = '';
  persistConnectionConfig().catch(() => saveState());
  input.focus();
}

function getConnectionPayload() {
  return {
    engine: 'mysql',
    host: document.getElementById('dbHost').value.trim() || '127.0.0.1',
    port: Number(document.getElementById('dbPort').value || 3306),
    username: document.getElementById('dbUsername').value.trim(),
    database: coerceIdentName(document.getElementById('databaseSelect').value) || '',
  };
}

function getSelectedValue(id, message, { asIdent = false } = {}) {
  const raw = document.getElementById(id).value;
  const value = asIdent ? coerceIdentName(raw) : String(raw || '').trim();
  if (!value || value === '[object Object]') throw new Error(message);
  // SQL identifiers only — do not apply to backup/CSV filenames (they contain '.').
  if (asIdent && !/^[A-Za-z0-9_]+$/.test(value)) {
    throw new Error(`${message}（非法名称: ${value}）`);
  }
  return value;
}

function getExportDatabase() {
  return getSelectedValue('databaseSelect', '请先选择数据库', { asIdent: true });
}

function getExportTable() {
  return getSelectedValue('tableSelect', '请先选择表', { asIdent: true });
}

function getOutputDir() {
  return document.getElementById('outputDir').value.trim();
}

function saveState() {
  const state = {
    host: document.getElementById('dbHost').value,
    port: document.getElementById('dbPort').value,
    username: document.getElementById('dbUsername').value,
    password: document.getElementById('dbPassword').value,
    database: document.getElementById('databaseSelect').value,
    table: document.getElementById('tableSelect').value,
    restoreDatabase: document.getElementById('restoreDatabaseSelect').value,
    importDatabase: document.getElementById('importDatabaseSelect').value,
    importTable: document.getElementById('importTableSelect').value,
    outputDir: document.getElementById('outputDir').value,
  };
  safeStorageSet(STATE_KEY, JSON.stringify(state));
}

function loadSavedState() {
  const raw = safeStorageGet(STATE_KEY);
  if (!raw) return null;
  try {
    const state = JSON.parse(raw);
    if (!state || typeof state !== 'object') return null;
    for (const key of ['database', 'table', 'restoreDatabase', 'importDatabase', 'importTable']) {
      state[key] = coerceIdentName(state[key]);
    }
    return state;
  } catch {
    return null;
  }
}

async function loadConfig() {
  const config = await fetchJson('/api/config');
  const saved = loadSavedState();
  const conn = { ...(config.default_connection || {}), ...(saved || {}) };
  defaultOutputDir = config.backup_dir || '';
  document.getElementById('dbHost').value = conn.host || '127.0.0.1';
  document.getElementById('dbPort').value = Number(conn.port || 3306);
  document.getElementById('dbUsername').value = conn.username || '';
  // Passwords are never restored into the DOM or browser storage. Launcher-managed
  // services receive the credential through their process environment.
  document.getElementById('dbPassword').value = '';
  document.getElementById('outputDir').value =
    saved?.outputDir || config.last_output_dir || defaultOutputDir;
  const passwordState = config.default_connection?.password_managed
    ? '数据库密码由 Launcher 管理'
    : (config.default_connection?.password_configured ? '数据库密码已安全保存' : '尚未配置数据库密码');
  setHint('connectionHint', `${passwordState} · 默认导出目录: ${defaultOutputDir || '-'}`);
  updateQuickChipState();
}

async function persistConnectionConfig() {
  saveState();
  await fetchJson('/api/config/connection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(getConnectionPayload()),
  });
}

async function testConnection() {
  const data = await fetchJson('/api/connect/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(getConnectionPayload()),
  });
  if (data.ok) {
    await persistConnectionConfig().catch(() => saveState());
    setHint('connectionHint', `连接成功: ${data.engine}, version=${data.version || data.path || '-'}`, 'muted ok');
  } else {
    setHint('connectionHint', `连接失败: ${data.message || '-'}`, 'muted warn');
  }
}

function populateDatabaseSelects(databases) {
  const items = normalizeSizedItems(databases);
  for (const id of ['databaseSelect', 'restoreDatabaseSelect', 'importDatabaseSelect']) {
    const sel = document.getElementById(id);
    const previous = coerceIdentName(sel.value);
    sel.innerHTML = '';
    // Pass the whole item so size remains available even if callers forget .name.
    for (const item of items) appendOption(sel, item, sizedLabel(item.name, item.size_bytes));
    if (previous && hasOption(sel, previous)) sel.value = previous;
  }
}

async function loadDatabases(preferred = {}) {
  const data = await fetchJson('/api/databases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(getConnectionPayload()),
  });
  const databases = data.databases || [];
  populateDatabaseSelects(databases);
  setSelectValueIfPresent('databaseSelect', preferred.database || '');
  setSelectValueIfPresent('restoreDatabaseSelect', preferred.restoreDatabase || preferred.database || '');
  setSelectValueIfPresent('importDatabaseSelect', preferred.importDatabase || preferred.database || '');
  setHint('objectHint', `数据库数量: ${databases.length}`);
  saveState();
  if (document.getElementById('databaseSelect').value) await loadExportTables(preferred.table || '');
  if (document.getElementById('importDatabaseSelect').value) await loadImportTables(preferred.importTable || '');
}

async function fetchTables(database) {
  const data = await fetchJson('/api/tables', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection: getConnectionPayload(), database }),
  });
  return data.tables || [];
}

async function loadExportTables(preferredTable = '') {
  const database = getExportDatabase();
  const tables = normalizeSizedItems(await fetchTables(database));
  const sel = document.getElementById('tableSelect');
  const previous = coerceIdentName(preferredTable || sel.value);
  sel.innerHTML = '';
  for (const item of tables) appendOption(sel, item, sizedLabel(item.name, item.size_bytes));
  if (previous && hasOption(sel, previous)) sel.value = previous;
  setHint('objectHint', `数据库 ${database}，表数量: ${tables.length}`);
  saveState();
}

async function loadImportTables(preferredTable = '') {
  const database = getSelectedValue('importDatabaseSelect', '请先选择导入目标数据库', { asIdent: true });
  const tables = normalizeSizedItems(await fetchTables(database));
  const sel = document.getElementById('importTableSelect');
  const previous = coerceIdentName(preferredTable || sel.value);
  sel.innerHTML = '';
  for (const item of tables) appendOption(sel, item, sizedLabel(item.name, item.size_bytes));
  if (previous && hasOption(sel, previous)) sel.value = previous;
  saveState();
}

/** Re-fetch database/table sizes while keeping current selections. */
async function refreshSizedCatalog() {
  const preferred = {
    database: document.getElementById('databaseSelect').value || '',
    restoreDatabase: document.getElementById('restoreDatabaseSelect').value || '',
    importDatabase: document.getElementById('importDatabaseSelect').value || '',
    table: document.getElementById('tableSelect').value || '',
    importTable: document.getElementById('importTableSelect').value || '',
  };
  await loadDatabases(preferred);
}

async function chooseOutputFolder() {
  setHint('backupHint', '请选择服务允许访问的输出目录...');
  const selected = await openFilesystemBrowser({ purpose: 'directory', initialPath: getOutputDir() || defaultOutputDir });
  if (selected) {
    document.getElementById('outputDir').value = selected;
    saveState();
    setHint('backupHint', `已选择导出目录: ${selected}`, 'muted ok');
  } else {
    setHint('backupHint', '已取消选择导出目录', 'muted');
  }
}

async function startBackup() {
  const database = getExportDatabase();
  const data = await fetchJson('/api/backup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
      output_dir: getOutputDir(),
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', `已开始整库 SQL 备份，输出目录: ${getOutputDir() || defaultOutputDir}`, 'muted ok');
}

async function startTableBackup() {
  const database = getExportDatabase();
  const table = getExportTable();
  const data = await fetchJson('/api/backup-table', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
      table,
      output_dir: getOutputDir(),
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', `已开始单表 SQL 备份: ${database}.${table}`, 'muted ok');
}

async function startCsvExport() {
  const database = getExportDatabase();
  const table = getExportTable();
  const data = await fetchJson('/api/export-csv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
      table,
      output_dir: getOutputDir(),
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', `已开始 CSV 导出（适合小数据）: ${database}.${table}`, 'muted ok');
}

async function requireDoubleConfirm(kind, target) {
  const first = await showConfirmModal({
    title: `${kind}确认（1/2）`,
    message: `${kind} 将写入数据库：${target}。是否继续？`,
    confirmText: '继续',
  });
  if (!first) return false;
  return showConfirmModal({
    title: `${kind}确认（2/2）`,
    message: `二次确认：${kind} 执行后可能覆盖或追加数据，确定继续？`,
    confirmText: '确认执行',
  });
}

async function requestConfirmationToken(action, database, table = '') {
  const data = await fetchJson('/api/confirmations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, database, table }),
  });
  return data.token;
}

async function registerLocalFile(kind) {
  setHint('importHint', `请选择服务允许访问的 ${kind.toUpperCase()} 文件...`);
  const selected = await openFilesystemBrowser({ purpose: kind, initialPath: getOutputDir() || defaultOutputDir, allowFiles: true });
  if (!selected) {
    setHint('importHint', '已取消登记本地文件', 'muted');
    return;
  }
  const data = await fetchJson('/api/register-file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, path: selected }),
  });
  if (kind === 'csv') {
    await loadServerCsvExports();
    setHint('importHint', `已登记 CSV: ${data.filename}`, 'muted ok');
  } else {
    await loadServerBackups();
    setHint('importHint', `已登记 SQL: ${data.filename}`, 'muted ok');
  }
}

async function loadServerBackups() {
  const data = await fetchJson('/api/backups');
  availableBackups = data.backups || [];
  const select = document.getElementById('serverBackupSelect');
  const previous = select.value;
  select.innerHTML = '';
  for (const backup of availableBackups) {
    const option = document.createElement('option');
    option.value = backup.filename;
    const scope = backup.scope === 'table' && backup.table
      ? `表 ${backup.database || ''}.${backup.table}`
      : (backup.scope === 'external' ? '外部登记' : '整库');
    option.textContent = `${backup.filename} [${scope}] (${formatBytes(backup.size_bytes)})`;
    select.appendChild(option);
  }
  if (previous && hasOption(select, previous)) select.value = previous;
}

function destinationIsOnAvailableDrive(destination) {
  const value = String(destination || '').toLowerCase();
  return removableRoots.some(root => value.startsWith(String(root.path || '').toLowerCase()));
}

function managedFileMeta(item) {
  const origin = item.scope === 'table' && item.table
    ? `单表 ${item.database || ''}.${item.table}`
    : (item.database ? `数据库 ${item.database}` : (item.scope === 'external' ? '外部登记' : '已完成'));
  return `${String(item.kind || '').toUpperCase()} · ${origin} · ${formatBytes(item.size || 0)} · ${item.completed_at || item.modified_at || '时间未知'}`;
}

function selectedManagedFileList() {
  return [...selectedManagedFiles.values()];
}

function renderManagedSelection() {
  const selected = selectedManagedFileList();
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  document.getElementById('backupSelectionSummary').textContent = `已选择 ${selected.length} 个文件，共 ${formatBytes(totalBytes)}`;
  const container = document.getElementById('backupSelectedFiles');
  container.innerHTML = '';
  for (const item of selected) {
    const row = document.createElement('div');
    row.className = 'backup-selected-item';
    const text = document.createElement('span');
    text.textContent = item.name;
    text.title = item.path;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '移除';
    remove.addEventListener('click', () => {
      selectedManagedFiles.delete(item.path);
      renderManagedEntries();
      renderManagedSelection();
    });
    row.append(text, remove);
    container.appendChild(row);
  }
  const destination = document.getElementById('backupCopyDestination').value.trim();
  document.getElementById('btnCopyBackups').disabled = !selected.length || !destination || !destinationIsOnAvailableDrive(destination);
  document.getElementById('btnDeleteBackupFiles').disabled = !selected.length;
  document.getElementById('btnClearBackupSelection').disabled = !selected.length;
  const driveText = removableRoots.length ? `${removableRoots.length} 个外部设备可用` : '未检测到外部设备';
  setHint(
    'backupManagerHint',
    `${driveText}；已选择 ${selected.length} 个文件（${formatBytes(totalBytes)}）`,
    removableRoots.length ? 'muted' : 'muted warn',
  );
}

function renderManagedEntries() {
  const list = document.getElementById('backupFilesList');
  list.innerHTML = '';
  document.getElementById('backupFilesPath').textContent = currentManagedPath || '允许访问的位置';
  document.getElementById('btnBackupFilesParent').disabled = !currentManagedParent;
  document.getElementById('btnSelectCurrentBackupFiles').disabled = !currentManagedFiles.some(item => item.type === 'file');
  if (!currentManagedFiles.length) {
    const empty = document.createElement('div');
    empty.className = 'backup-files-empty';
    empty.textContent = '当前目录没有有效的 SQL / CSV 备份文件';
    list.appendChild(empty);
    return;
  }
  for (const entry of currentManagedFiles) {
    if (entry.type === 'directory') {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'backup-directory-entry';
      button.innerHTML = '<span class="backup-entry-icon">📁</span>';
      const label = document.createElement('span');
      label.textContent = entry.name;
      button.appendChild(label);
      button.addEventListener('click', () => loadManagedDirectory(entry.path).catch(showBackupManagerError));
      list.appendChild(button);
      continue;
    }
    const label = document.createElement('label');
    label.className = 'backup-file-entry';
    label.classList.toggle('is-selected', selectedManagedFiles.has(entry.path));
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = selectedManagedFiles.has(entry.path);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selectedManagedFiles.set(entry.path, entry);
      else selectedManagedFiles.delete(entry.path);
      label.classList.toggle('is-selected', checkbox.checked);
      renderManagedSelection();
    });
    const content = document.createElement('span');
    content.className = 'backup-file-entry-content';
    const name = document.createElement('strong');
    name.textContent = entry.name;
    const meta = document.createElement('span');
    meta.className = 'muted';
    meta.textContent = managedFileMeta(entry);
    content.append(name, meta);
    label.append(checkbox, content);
    list.appendChild(label);
  }
}

function showBackupManagerError(error) {
  setHint('backupManagerHint', error.message || String(error), 'muted warn');
}

async function loadManagedRoots() {
  const data = await fetchJson('/api/backup-files/roots');
  currentManagedPath = '';
  currentManagedParent = '';
  currentManagedFiles = (data.roots || []).filter(root => root.exists).map(root => ({
    ...root,
    type: 'directory',
    name: root.name || root.path,
  }));
  renderManagedEntries();
}

async function loadManagedDirectory(path) {
  const data = await fetchJson(`/api/backup-files/entries?path=${encodeURIComponent(path)}`);
  currentManagedPath = data.current || path;
  currentManagedParent = data.parent || '';
  currentManagedFiles = data.entries || [];
  const validPaths = new Set(currentManagedFiles.filter(item => item.type === 'file').map(item => item.path));
  for (const selected of selectedManagedFileList()) {
    const slash = Math.max(selected.path.lastIndexOf('\\'), selected.path.lastIndexOf('/'));
    const selectedParent = slash >= 0 ? selected.path.slice(0, slash) : '';
    if (selectedParent.toLowerCase() === currentManagedPath.toLowerCase() && !validPaths.has(selected.path)) {
      selectedManagedFiles.delete(selected.path);
    }
  }
  renderManagedEntries();
  renderManagedSelection();
}

async function refreshManagedFiles() {
  if (currentManagedPath) await loadManagedDirectory(currentManagedPath);
  else await loadManagedRoots();
}

function defaultRemovableBackupDir(root) {
  return `${String(root || '').replace(/[\\/]+$/, '')}\\SD_SMA_Backups`;
}

async function refreshRemovableDrives({ announce = false } = {}) {
  const data = await fetchJson('/api/filesystem/roots?purpose=removable-directory');
  removableRoots = (data.roots || []).filter(root => root.exists);
  const input = document.getElementById('backupCopyDestination');
  if (!removableRoots.length) {
    input.value = '';
  } else if (!destinationIsOnAvailableDrive(input.value)) {
    input.value = removableRoots.length === 1 ? defaultRemovableBackupDir(removableRoots[0].path) : '';
  }
  if (announce) {
    setHint(
      'backupManagerHint',
      removableRoots.length ? `已检测到 ${removableRoots.length} 个外部设备` : '未检测到外部设备',
      removableRoots.length ? 'muted ok' : 'muted warn',
    );
  }
  renderManagedSelection();
  return removableRoots;
}

async function chooseBackupCopyDestination() {
  await refreshRemovableDrives();
  const input = document.getElementById('backupCopyDestination');
  const selected = await openFilesystemBrowser({
    purpose: 'removable-directory',
    initialPath: input.value,
  });
  if (selected) input.value = selected;
  await refreshRemovableDrives();
}

async function startCopyBackups() {
  const selected = selectedManagedFileList();
  const destination = document.getElementById('backupCopyDestination').value.trim();
  if (!selected.length) throw new Error('请至少选择一个 SQL 或 CSV 文件');
  if (!destination || !destinationIsOnAvailableDrive(destination)) throw new Error('请选择当前可用的外部设备目录');
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const confirmed = await showConfirmModal({
    title: '确认复制备份文件',
    message: `将 ${selected.length} 个文件（${formatBytes(totalBytes)}）复制到外部设备：${destination}。复制后将校验 SHA-256。`,
    confirmText: '开始复制',
  });
  if (!confirmed) return;
  const data = await fetchJson('/api/copy-backup-files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      paths: selected.map(item => item.path),
      destination_dir: destination,
    }),
  });
  watchJob(data.job.id);
  setHint('backupManagerHint', `已开始复制到外部设备：${destination}`, 'muted ok');
}

async function deleteSelectedBackupFiles() {
  const selected = selectedManagedFileList();
  if (!selected.length) throw new Error('请至少选择一个要删除的文件');
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const first = await showConfirmModal({
    title: '删除备份文件（1/2）',
    message: `将永久删除 ${selected.length} 个文件及其 manifest，共 ${formatBytes(totalBytes)}。此操作不可恢复，是否继续？`,
    confirmText: '继续',
  });
  if (!first) return;
  const second = await showConfirmModal({
    title: '删除备份文件（2/2）',
    message: '二次确认：所选 SQL / CSV 及校验清单将被永久删除，确定执行？',
    confirmText: '永久删除',
  });
  if (!second) return;
  const paths = selected.map(item => item.path);
  const confirmation = await fetchJson('/api/backup-files/delete-confirmation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  });
  const result = await fetchJson('/api/delete-backup-files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths, confirmation_token: confirmation.token }),
  });
  selectedManagedFiles.clear();
  await Promise.all([refreshManagedFiles(), loadServerBackups(), loadServerCsvExports()]);
  renderManagedSelection();
  setHint('backupManagerHint', `已永久删除 ${result.count} 个文件（${formatBytes(result.size_bytes)}）`, 'muted ok');
}

async function loadServerCsvExports() {
  const data = await fetchJson('/api/csv-exports');
  const select = document.getElementById('serverCsvSelect');
  const previous = select.value;
  select.innerHTML = '';
  for (const item of data.exports || []) {
    const option = document.createElement('option');
    option.value = item.filename;
    const origin = item.table ? `${item.database || ''}.${item.table}` : '外部登记';
    option.textContent = `${item.filename} [${origin}] (${formatBytes(item.size_bytes)})`;
    select.appendChild(option);
  }
  if (previous && hasOption(select, previous)) select.value = previous;
}

async function startRestoreServerBackup() {
  const database = getSelectedValue('restoreDatabaseSelect', '请先选择恢复目标数据库', { asIdent: true });
  const filename = getSelectedValue('serverBackupSelect', '没有可恢复的已完成备份');
  if (!(await requireDoubleConfirm('恢复服务器备份', `${filename} → ${database}`))) return;
  const confirmationToken = await requestConfirmationToken('restore-backup', database);
  const data = await fetchJson('/api/restore-backup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
      filename,
      confirmation_token: confirmationToken,
    }),
  });
  watchJob(data.job.id);
  setHint('importHint', `已开始校验并恢复: ${filename} → ${database}`, 'muted warn');
}

function syncForceImportTruncate() {
  const forceEl = document.getElementById('forceCsvImport');
  const truncateEl = document.getElementById('truncateBeforeImport');
  if (forceEl.checked) {
    truncateEl.checked = true;
    truncateEl.disabled = true;
  } else {
    truncateEl.disabled = false;
  }
}

async function startImportServerCsv() {
  const database = getSelectedValue('importDatabaseSelect', '请先选择导入目标数据库', { asIdent: true });
  const table = getSelectedValue('importTableSelect', '请先选择导入目标表', { asIdent: true });
  const filename = getSelectedValue('serverCsvSelect', '没有可导入的已导出 CSV');
  const force = document.getElementById('forceCsvImport').checked;
  if (force) syncForceImportTruncate();
  if (!(await requireDoubleConfirm('导入 CSV', `${filename} → ${database}.${table}`))) {
    setHint('importHint', '已取消 CSV 导入', 'muted');
    return;
  }
  const confirmationToken = await requestConfirmationToken('import-server-csv', database, table);
  const data = await fetchJson('/api/import-server-csv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
      table,
      filename,
      force,
      truncate: force || document.getElementById('truncateBeforeImport').checked,
      confirmation_token: confirmationToken,
    }),
  });
  watchJob(data.job.id);
  setHint('importHint', `已开始 CSV 导入: ${filename} → ${database}.${table}`, 'muted warn');
}

function statusClass(status) {
  if (status === 'done') return 'status-done';
  if (status === 'failed') return 'status-failed';
  if (status === 'cancelled' || status === 'canceled') return 'status-cancelled';
  return 'status-running';
}

function notifyJobOutcome(job) {
  if (!job || !job.id || job.status === 'running') return;
  const status = String(job.status || '');
  const notificationKey = `${job.id}:${status}`;
  if (notifiedJobStates.get(job.id) === notificationKey) return;
  notifiedJobStates.set(job.id, notificationKey);

  const title = job.title || job.id;
  if (status === 'done') {
    const outputPath = job.result?.path || job.result?.destination || '';
    const output = outputPath ? `：${outputPath}` : '';
    showStatusBar(`任务成功：${title}${output}`, 'ok');
  } else if (status === 'cancelled' || status === 'canceled') {
    showStatusBar(`任务已取消：${title}`, 'warn');
  } else if (status === 'failed') {
    showStatusBar(`任务失败：${title}：${job.error || '未知错误'}`, 'error');
  }
}

function formatDuration(seconds) {
  if (seconds == null) return '-';
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

function renderProgress(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  return (
    `<div class="progress-wrap" title="${progress}%">` +
    `<div class="progress-bar" style="width: ${progress}%"></div>` +
    `</div><span class="progress-label">${progress}%</span>`
  );
}

function renderJobs(jobs) {
  const table = document.getElementById('jobsTable');
  table.innerHTML = '';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>状态</th><th>进度</th><th>任务</th><th>耗时</th><th>预计剩余</th><th>输出/结果</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const job of jobs) {
    const tr = document.createElement('tr');
    const status = job.status || 'running';
    let result = job.error || job.phase || '';
    if (job.result?.path) {
      result = `${job.result.path}${job.result.size_bytes ? ` (${job.result.size_bytes} bytes)` : ''}`;
    } else if (job.result?.destination) {
      const copied = (job.result.copied || []).length;
      const skipped = (job.result.skipped || []).length;
      result = `${job.result.destination} / 已复制 ${copied} / 已跳过 ${skipped}`;
    }
    const statusCell = document.createElement('td');
    const statusButton = document.createElement('button');
    statusButton.type = 'button';
    statusButton.className = `job-link status-pill ${statusClass(status)}`;
    statusButton.dataset.jobId = String(job.id || '');
    statusButton.textContent = status;
    statusCell.appendChild(statusButton);
    if (status === 'running') {
      statusCell.appendChild(document.createTextNode(' '));
      const cancelButton = document.createElement('button');
      cancelButton.type = 'button';
      cancelButton.className = 'job-cancel';
      cancelButton.textContent = '取消';
      cancelButton.addEventListener('click', async event => {
        event.stopPropagation();
        if (!(await showConfirmModal({ title: '取消任务', message: `确定取消 ${job.title || job.id}？`, confirmText: '确认取消' }))) return;
        try {
          await fetchJson(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: 'POST' });
          showStatusBar(`已提交任务取消请求：${job.title || job.id}`, 'warn');
          await refreshJobs();
        } catch (err) {
          showStatusBar(`任务取消失败：${job.title || job.id}：${err.message}`, 'error');
        }
      });
      statusCell.appendChild(cancelButton);
    }
    tr.appendChild(statusCell);

    const progressCell = document.createElement('td');
    progressCell.innerHTML = renderProgress(job);
    tr.appendChild(progressCell);
    for (const text of [
      job.title || job.id,
      formatDuration(job.elapsed_seconds),
      formatDuration(job.eta_seconds),
    ]) {
      const cell = document.createElement('td');
      cell.textContent = String(text || '');
      tr.appendChild(cell);
    }
    const resultCell = document.createElement('td');
    const resultText = document.createElement('span');
    resultText.textContent = String(result || '');
    resultCell.appendChild(resultText);
    if (job.result && job.result.download_url) {
      resultCell.appendChild(document.createTextNode(' '));
      const downloadLink = document.createElement('a');
      downloadLink.href = job.result.download_url;
      downloadLink.textContent = '下载';
      downloadLink.setAttribute('download', job.result.filename || '');
      resultCell.appendChild(downloadLink);
    }
    tr.appendChild(resultCell);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  for (const btn of document.querySelectorAll('.job-link[data-job-id]')) {
    btn.addEventListener('click', () => watchJob(btn.getAttribute('data-job-id')));
  }
}

function renderJobLog(job) {
  const lines = job && Array.isArray(job.logs) ? job.logs : [];
  document.getElementById('jobLog').value = lines.join('\n');
  document.getElementById('jobSummary').textContent = job
    ? `当前任务: ${job.title || job.id} / ${job.status} / ${job.progress || 0}%`
    : '';
}

async function refreshJobs() {
  const data = await fetchJson('/api/jobs');
  const jobs = data.jobs || [];
  renderJobs(jobs);
  if (!activeJobId && jobs.length > 0) {
    activeJobId = jobs[0].id;
  }
  if (activeJobId) {
    const jobData = await fetchJson('/api/jobs/' + encodeURIComponent(activeJobId));
    renderJobLog(jobData.job);
    notifyJobOutcome(jobData.job);
    if (jobData.job.status !== 'running' && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
}

function watchJob(jobId) {
  activeJobId = jobId || '';
  refreshJobs().catch(err => setHint('jobSummary', err.message, 'muted warn'));
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    refreshJobs().catch(() => {});
  }, 1200);
}

function bindEvents() {
  for (const chip of document.querySelectorAll('.quick-chip')) {
    chip.addEventListener('click', () => applyConnectionShortcut(chip));
  }
  document.getElementById('btnTogglePassword').addEventListener('click', togglePasswordVisibility);
  document.getElementById('btnClearPassword').addEventListener('click', clearPassword);
  document.getElementById('btnTestConnection').addEventListener('click', () => {
    testConnection().catch(err => setHint('connectionHint', err.message, 'muted warn'));
  });
  document.getElementById('btnLoadDatabases').addEventListener('click', () => {
    loadDatabases().catch(err => setHint('objectHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRefreshTables').addEventListener('click', () => {
    loadExportTables().catch(err => setHint('objectHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRefreshImportTables').addEventListener('click', () => {
    loadImportTables().catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  for (const id of [
    'databaseSelect',
    'restoreDatabaseSelect',
    'importDatabaseSelect',
    'tableSelect',
    'importTableSelect',
  ]) {
    document.getElementById(id).addEventListener('change', () => {
      refreshSizedCatalog().catch(err => {
        const hintId = id.startsWith('import') ? 'importHint' : 'objectHint';
        setHint(hintId, err.message, 'muted warn');
      });
    });
  }
  document.getElementById('outputDir').addEventListener('change', saveState);
  for (const id of ['dbHost', 'dbPort', 'dbUsername']) {
    document.getElementById(id).addEventListener('change', () => {
      persistConnectionConfig().catch(() => saveState());
      updateQuickChipState();
    });
    document.getElementById(id).addEventListener('input', updateQuickChipState);
  }
  document.getElementById('dbPassword').addEventListener('change', () => {
    persistConnectionConfig().catch(() => saveState());
  });
  document.getElementById('dbPassword').addEventListener('input', saveState);
  document.getElementById('btnUseDefaultOutputDir').addEventListener('click', () => {
    document.getElementById('outputDir').value = defaultOutputDir;
    saveState();
  });
  document.getElementById('btnChooseOutputDir').addEventListener('click', () => {
    chooseOutputFolder().catch(err => setHint('backupHint', err.message, 'muted warn'));
  });
  document.getElementById('btnBackupSql').addEventListener('click', () => {
    startBackup().catch(err => setHint('backupHint', err.message, 'muted warn'));
  });
  document.getElementById('btnBackupTableSql').addEventListener('click', () => {
    startTableBackup().catch(err => setHint('backupHint', err.message, 'muted warn'));
  });
  document.getElementById('btnExportCsv').addEventListener('click', () => {
    startCsvExport().catch(err => setHint('backupHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRefreshBackups').addEventListener('click', () => {
    loadServerBackups().catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('btnBackupFilesRoots').addEventListener('click', () => {
    loadManagedRoots().catch(showBackupManagerError);
  });
  document.getElementById('btnBackupFilesParent').addEventListener('click', () => {
    if (currentManagedParent) loadManagedDirectory(currentManagedParent).catch(showBackupManagerError);
  });
  document.getElementById('btnRefreshBackupFiles').addEventListener('click', () => {
    refreshManagedFiles().catch(showBackupManagerError);
  });
  document.getElementById('btnSelectCurrentBackupFiles').addEventListener('click', () => {
    for (const entry of currentManagedFiles) {
      if (entry.type === 'file') selectedManagedFiles.set(entry.path, entry);
    }
    renderManagedEntries();
    renderManagedSelection();
  });
  document.getElementById('btnClearBackupSelection').addEventListener('click', () => {
    selectedManagedFiles.clear();
    renderManagedEntries();
    renderManagedSelection();
  });
  document.getElementById('btnChooseBackupDestination').addEventListener('click', () => {
    chooseBackupCopyDestination().catch(showBackupManagerError);
  });
  document.getElementById('btnRefreshRemovableDrives').addEventListener('click', () => {
    refreshRemovableDrives({ announce: true }).catch(showBackupManagerError);
  });
  document.getElementById('btnCopyBackups').addEventListener('click', () => {
    startCopyBackups().catch(showBackupManagerError);
  });
  document.getElementById('btnDeleteBackupFiles').addEventListener('click', () => {
    deleteSelectedBackupFiles().catch(showBackupManagerError);
  });
  document.getElementById('btnRegisterSql').addEventListener('click', () => {
    registerLocalFile('sql').catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRestoreServerBackup').addEventListener('click', () => {
    startRestoreServerBackup().catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRefreshCsvExports').addEventListener('click', () => {
    loadServerCsvExports().catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRegisterCsv').addEventListener('click', () => {
    registerLocalFile('csv').catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('forceCsvImport').addEventListener('change', syncForceImportTruncate);
  document.getElementById('btnImportServerCsv').addEventListener('click', () => {
    startImportServerCsv().catch(err => setHint('importHint', err.message, 'muted warn'));
  });
  document.getElementById('btnRefreshJobs').addEventListener('click', () => {
    refreshJobs().catch(err => setHint('jobSummary', err.message, 'muted warn'));
  });
  syncForceImportTruncate();
}

async function init() {
  await loadConfig();
  bindEvents();
  enableButtonClickFeedback();
  const saved = loadSavedState();
  if (saved && saved.database) {
    await loadDatabases(saved).catch(err => setHint('objectHint', err.message, 'muted warn'));
  }
  await refreshJobs();
  await loadServerBackups();
  await refreshRemovableDrives();
  await loadServerCsvExports();
  await loadManagedRoots();
  renderManagedSelection();
}

init().catch(err => showStatusBar(err.message, 'error'));
