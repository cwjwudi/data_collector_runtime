const STATE_KEY = 'sd_sma_db_admin_state_v2';
let activeJobId = '';
let pollTimer = null;
let configuredOutputDir = '';
let savedConnection = {};
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

function showConfirmModal({ title, message, confirmText = SdI18n.t('dbAdmin.showConfirmModal.text1') }) {
  if (
    !confirmModalOverlay ||
    !confirmModalTitle ||
    !confirmModalMessage ||
    !confirmModalCancel ||
    !confirmModalConfirm
  ) {
    showStatusBar(SdI18n.t('dbAdmin.showConfirmModal.text2'), 'error');
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

function getConnectionPayload() {
  return { ...savedConnection, database: coerceIdentName(document.getElementById('databaseSelect').value) || '' };
}

function getSelectedValue(id, message, { asIdent = false } = {}) {
  const raw = document.getElementById(id).value;
  const value = asIdent ? coerceIdentName(raw) : String(raw || '').trim();
  if (!value || value === '[object Object]') throw new Error(message);
  // SQL identifiers only — do not apply to backup/CSV filenames (they contain '.').
  if (asIdent && !/^[A-Za-z0-9_]+$/.test(value)) {
    throw new Error(SdI18n.t('dbAdmin.getSelectedValue.text1', { value1: message, value2: value }));
  }
  return value;
}

function getExportDatabase() {
  return getSelectedValue('databaseSelect', SdI18n.t('dbAdmin.getExportDatabase.text1'), { asIdent: true });
}

function getExportTable() {
  return getSelectedValue('tableSelect', SdI18n.t('dbAdmin.getExportTable.text1'), { asIdent: true });
}

function saveState() {
  const state = {
    database: document.getElementById('databaseSelect').value,
    table: document.getElementById('tableSelect').value,
    restoreDatabase: document.getElementById('restoreDatabaseSelect').value,
    importDatabase: document.getElementById('importDatabaseSelect').value,
    importTable: document.getElementById('importTableSelect').value,
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
    for (const key of ['host', 'port', 'username', 'password', 'outputDir']) delete state[key];
    safeStorageSet(STATE_KEY, JSON.stringify(state));
    return state;
  } catch {
    return null;
  }
}

async function loadConfig() {
  const config = await fetchJson('/api/config');
  loadSavedState();
  savedConnection = config.default_connection || {};
  configuredOutputDir = config.output_dir || config.backup_dir || '';
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
  setHint('objectHint', SdI18n.t('dbAdmin.loadDatabases.text1', { value1: databases.length }));
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
  setHint('objectHint', SdI18n.t('dbAdmin.loadExportTables.text1', { value1: database, value2: tables.length }));
  saveState();
}

async function loadImportTables(preferredTable = '') {
  const database = getSelectedValue('importDatabaseSelect', SdI18n.t('dbAdmin.loadImportTables.text1'), { asIdent: true });
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

async function startBackup() {
  const database = getExportDatabase();
  const data = await fetchJson('/api/backup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      connection: getConnectionPayload(),
      database,
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', SdI18n.t('dbAdmin.startBackup.text1', { value1: configuredOutputDir }), 'muted ok');
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
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', SdI18n.t('dbAdmin.startTableBackup.text1', { value1: database, value2: table }), 'muted ok');
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
    }),
  });
  watchJob(data.job.id);
  setHint('backupHint', SdI18n.t('dbAdmin.startCsvExport.text1', { value1: database, value2: table }), 'muted ok');
}

async function requireDoubleConfirm(kind, target) {
  const first = await showConfirmModal({
    title: SdI18n.t('dbAdmin.requireDoubleConfirm.text1', { value1: kind }),
    message: SdI18n.t('dbAdmin.requireDoubleConfirm.text2', { value1: kind, value2: target }),
    confirmText: SdI18n.t('dbAdmin.requireDoubleConfirm.text3'),
  });
  if (!first) return false;
  return showConfirmModal({
    title: SdI18n.t('dbAdmin.requireDoubleConfirm.text4', { value1: kind }),
    message: SdI18n.t('dbAdmin.requireDoubleConfirm.text5', { value1: kind }),
    confirmText: SdI18n.t('dbAdmin.requireDoubleConfirm.text6'),
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
  setHint('importHint', SdI18n.t('dbAdmin.registerLocalFile.text1', { value1: kind.toUpperCase() }));
  const selected = await openFilesystemBrowser({ purpose: kind, initialPath: configuredOutputDir, allowFiles: true });
  if (!selected) {
    setHint('importHint', SdI18n.t('dbAdmin.registerLocalFile.text2'), 'muted');
    return;
  }
  const data = await fetchJson('/api/register-file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, path: selected }),
  });
  if (kind === 'csv') {
    await loadServerCsvExports();
    setHint('importHint', SdI18n.t('dbAdmin.registerLocalFile.text3', { value1: data.filename }), 'muted ok');
  } else {
    await loadServerBackups();
    setHint('importHint', SdI18n.t('dbAdmin.registerLocalFile.text4', { value1: data.filename }), 'muted ok');
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
      ? SdI18n.t('dbAdmin.loadServerBackups.text1', { value1: backup.database || '', value2: backup.table })
      : (backup.scope === 'external' ? SdI18n.t('dbAdmin.loadServerBackups.text2') : SdI18n.t('dbAdmin.loadServerBackups.text3'));
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
    ? SdI18n.t('dbAdmin.managedFileMeta.text1', { value1: item.database || '', value2: item.table })
    : (item.database ? SdI18n.t('dbAdmin.managedFileMeta.text2', { value1: item.database }) : (item.scope === 'external' ? SdI18n.t('dbAdmin.managedFileMeta.text3') : SdI18n.t('dbAdmin.managedFileMeta.text4')));
  return SdI18n.t('dbAdmin.managedFileMeta.text6', { value1: String(item.kind || '').toUpperCase(), value2: origin, value3: formatBytes(item.size || 0), value4: item.completed_at || item.modified_at || SdI18n.t('dbAdmin.managedFileMeta.text5') });
}

function selectedManagedFileList() {
  return [...selectedManagedFiles.values()];
}

function renderManagedSelection() {
  const selected = selectedManagedFileList();
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  document.getElementById('backupSelectionSummary').textContent = SdI18n.t('dbAdmin.renderManagedSelection.text1', { value1: selected.length, value2: formatBytes(totalBytes) });
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
    remove.textContent = SdI18n.t('dbAdmin.renderManagedSelection.text2');
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
  const driveText = removableRoots.length ? SdI18n.t('dbAdmin.renderManagedSelection.text3', { value1: removableRoots.length }) : SdI18n.t('dbAdmin.renderManagedSelection.text4');
  setHint(
    'backupManagerHint',
    SdI18n.t('dbAdmin.renderManagedSelection.text5', { value1: driveText, value2: selected.length, value3: formatBytes(totalBytes) }),
    removableRoots.length ? 'muted' : 'muted warn',
  );
}

function renderManagedEntries() {
  const list = document.getElementById('backupFilesList');
  list.innerHTML = '';
  document.getElementById('backupFilesPath').textContent = currentManagedPath || SdI18n.t('dbAdmin.renderManagedEntries.text1');
  document.getElementById('btnBackupFilesParent').disabled = !currentManagedParent;
  document.getElementById('btnSelectCurrentBackupFiles').disabled = !currentManagedFiles.some(item => item.type === 'file');
  if (!currentManagedFiles.length) {
    const empty = document.createElement('div');
    empty.className = 'backup-files-empty';
    empty.textContent = SdI18n.t('dbAdmin.renderManagedEntries.text2');
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
      removableRoots.length ? SdI18n.t('dbAdmin.refreshRemovableDrives.text1', { value1: removableRoots.length }) : SdI18n.t('dbAdmin.refreshRemovableDrives.text2'),
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
  if (!selected.length) throw new Error(SdI18n.t('dbAdmin.startCopyBackups.text1'));
  if (!destination || !destinationIsOnAvailableDrive(destination)) throw new Error(SdI18n.t('dbAdmin.startCopyBackups.text2'));
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const confirmed = await showConfirmModal({
    title: SdI18n.t('dbAdmin.startCopyBackups.text3'),
    message: SdI18n.t('dbAdmin.startCopyBackups.text4', { value1: selected.length, value2: formatBytes(totalBytes), value3: destination }),
    confirmText: SdI18n.t('dbAdmin.startCopyBackups.text5'),
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
  setHint('backupManagerHint', SdI18n.t('dbAdmin.startCopyBackups.text6', { value1: destination }), 'muted ok');
}

async function deleteSelectedBackupFiles() {
  const selected = selectedManagedFileList();
  if (!selected.length) throw new Error(SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text1'));
  const totalBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const first = await showConfirmModal({
    title: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text2'),
    message: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text3', { value1: selected.length, value2: formatBytes(totalBytes) }),
    confirmText: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text4'),
  });
  if (!first) return;
  const second = await showConfirmModal({
    title: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text5'),
    message: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text6'),
    confirmText: SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text7'),
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
  setHint('backupManagerHint', SdI18n.t('dbAdmin.deleteSelectedBackupFiles.text8', { value1: result.count, value2: formatBytes(result.size_bytes) }), 'muted ok');
}

async function loadServerCsvExports() {
  const data = await fetchJson('/api/csv-exports');
  const select = document.getElementById('serverCsvSelect');
  const previous = select.value;
  select.innerHTML = '';
  for (const item of data.exports || []) {
    const option = document.createElement('option');
    option.value = item.filename;
    const origin = item.table ? `${item.database || ''}.${item.table}` : SdI18n.t('dbAdmin.loadServerCsvExports.text1');
    option.textContent = `${item.filename} [${origin}] (${formatBytes(item.size_bytes)})`;
    select.appendChild(option);
  }
  if (previous && hasOption(select, previous)) select.value = previous;
}

async function startRestoreServerBackup() {
  const database = getSelectedValue('restoreDatabaseSelect', SdI18n.t('dbAdmin.startRestoreServerBackup.text1'), { asIdent: true });
  const filename = getSelectedValue('serverBackupSelect', SdI18n.t('dbAdmin.startRestoreServerBackup.text2'));
  if (!(await requireDoubleConfirm(SdI18n.t('dbAdmin.startRestoreServerBackup.text3'), `${filename} → ${database}`))) return;
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
  setHint('importHint', SdI18n.t('dbAdmin.startRestoreServerBackup.text4', { value1: filename, value2: database }), 'muted warn');
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
  const database = getSelectedValue('importDatabaseSelect', SdI18n.t('dbAdmin.startImportServerCsv.text1'), { asIdent: true });
  const table = getSelectedValue('importTableSelect', SdI18n.t('dbAdmin.startImportServerCsv.text2'), { asIdent: true });
  const filename = getSelectedValue('serverCsvSelect', SdI18n.t('dbAdmin.startImportServerCsv.text3'));
  const force = document.getElementById('forceCsvImport').checked;
  if (force) syncForceImportTruncate();
  if (!(await requireDoubleConfirm(SdI18n.t('dbAdmin.startImportServerCsv.text4'), `${filename} → ${database}.${table}`))) {
    setHint('importHint', SdI18n.t('dbAdmin.startImportServerCsv.text5'), 'muted');
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
  setHint('importHint', SdI18n.t('dbAdmin.startImportServerCsv.text6', { value1: filename, value2: database, value3: table }), 'muted warn');
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
    showStatusBar(SdI18n.t('dbAdmin.notifyJobOutcome.text1', { value1: title, value2: output }), 'ok');
  } else if (status === 'cancelled' || status === 'canceled') {
    showStatusBar(SdI18n.t('dbAdmin.notifyJobOutcome.text2', { value1: title }), 'warn');
  } else if (status === 'failed') {
    showStatusBar(SdI18n.t('dbAdmin.notifyJobOutcome.text4', { value1: title, value2: job.error || SdI18n.t('dbAdmin.notifyJobOutcome.text3') }), 'error');
  }
}

function formatDuration(seconds) {
  if (seconds == null) return '-';
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return m > 0 ? SdI18n.t('dbAdmin.formatDuration.text1', { value1: m, value2: s }) : SdI18n.t('dbAdmin.formatDuration.text2', { value1: s });
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
  thead.innerHTML = SdI18n.t('dbAdmin.renderJobs.text1');
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
      result = SdI18n.t('dbAdmin.renderJobs.text2', { value1: job.result.destination, value2: copied, value3: skipped });
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
      cancelButton.textContent = SdI18n.t('dbAdmin.renderJobs.text3');
      cancelButton.addEventListener('click', async event => {
        event.stopPropagation();
        if (!(await showConfirmModal({ title: SdI18n.t('dbAdmin.renderJobs.text4'), message: SdI18n.t('dbAdmin.renderJobs.text5', { value1: job.title || job.id }), confirmText: SdI18n.t('dbAdmin.renderJobs.text6') }))) return;
        try {
          await fetchJson(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: 'POST' });
          showStatusBar(SdI18n.t('dbAdmin.renderJobs.text7', { value1: job.title || job.id }), 'warn');
          await refreshJobs();
        } catch (err) {
          showStatusBar(SdI18n.t('dbAdmin.renderJobs.text8', { value1: job.title || job.id, value2: err.message }), 'error');
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
      downloadLink.textContent = SdI18n.t('dbAdmin.renderJobs.text9');
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
    ? SdI18n.t('dbAdmin.renderJobLog.text1', { value1: job.title || job.id, value2: job.status, value3: job.progress || 0 })
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
  await loadDatabases(saved || {}).catch(err => setHint('objectHint', err.message, 'muted warn'));
  await refreshJobs();
  await loadServerBackups();
  await refreshRemovableDrives();
  await loadServerCsvExports();
  await loadManagedRoots();
  renderManagedSelection();
}

init().catch(err => showStatusBar(err.message, 'error'));
