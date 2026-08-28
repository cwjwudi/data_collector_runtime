let queryViews = {};
let availableColumns = [];
let orderedColumns = [];
let columnLabels = {};
let currentSchema = null;
let pluginConfigData = { modules: {} };
let pluginOpcuaWritebackDraft = { cursor: '', columns: {} };
let pluginTableListWritebackDraft = null;
const CONFIG_STATE_KEY = 'sd_sma_query_config_page_state_v1';
const LOCKED_GROUP_VIEW = 'table';
const LOCKED_PLUGIN_MODULE = 'general';
const LOCKED_PLUGIN_VIEW = 'table';
let currentConfigProfile = '';

function safeStorageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch (_) {
    return false;
  }
}

function safeStorageRemove(key) {
  try {
    window.localStorage.removeItem(key);
    return true;
  } catch (_) {
    return false;
  }
}

function replaceSelectOptions(select, options, selectedValue) {
  if (!select) return;
  select.innerHTML = '';
  for (const option of options) {
    appendOption(select, option.value, option.label);
  }
  if (selectedValue && hasOption(select, selectedValue)) {
    select.value = selectedValue;
  } else if (select.options.length > 0) {
    select.value = select.options[0].value;
  }
}

function lockSelect(selectId, value, label) {
  const select = document.getElementById(selectId);
  replaceSelectOptions(select, [{ value, label }], value);
  select.disabled = true;
}

function getAppSettingsPayload() {
  return {
    database: {
      type: document.getElementById('appDbType').value || 'mysql',
      name: document.getElementById('appDbName').value.trim(),
      host: document.getElementById('appDbHost').value.trim(),
      port: Number(document.getElementById('appDbPort').value || 0),
      username: document.getElementById('appDbUsername').value.trim(),
      password: document.getElementById('appDbPassword').value,
      clear_password: document.getElementById('appDbClearPassword').checked,
    },
    query_limits: {
      requests_per_minute: Number(document.getElementById('appRequestsPerMinute').value || 0),
      default_window_hours: Number(document.getElementById('appDefaultWindowHours').value || 24),
      max_window_hours: Number(document.getElementById('appMaxWindowHours').value || 168),
    },
  };
}

function fillAppSettingsForm(data) {
  const database = data.database || {};
  const queryLimits = data.query_limits || {};
  document.getElementById('appDbType').value = database.type || 'mysql';
  document.getElementById('appDbName').value = database.name || '';
  document.getElementById('appDbHost').value = database.host || '';
  document.getElementById('appDbPort').value = Number(database.port || 3306);
  document.getElementById('appDbUsername').value = database.username || '';
  const passwordInput = document.getElementById('appDbPassword');
  passwordInput.value = '';
  passwordInput.placeholder = database.password_configured ? '已配置；留空保持不变' : '输入数据库密码';
  document.getElementById('appDbClearPassword').checked = false;
  document.getElementById('appRequestsPerMinute').value = Number(queryLimits.requests_per_minute || 120);
  document.getElementById('appDefaultWindowHours').value = Number(queryLimits.default_window_hours || 24);
  document.getElementById('appMaxWindowHours').value = Number(queryLimits.max_window_hours || 168);
}

const OPCUA_DEFAULT_HOST = '127.0.0.1';
const OPCUA_DEFAULT_PORT = 4840;

function parseOpcuaEndpoint(endpointUrl) {
  const raw = String(endpointUrl || '').trim();
  if (!raw) {
    return { host: '', port: '' };
  }
  const normalized = raw.replace(/^opc\s+tcp:/i, 'opc.tcp:');
  const match = normalized.match(/^opc\.tcp:\/\/([^/:]+)(?::(\d+))?(?:\/.*)?$/i);
  if (match) {
    return {
      host: match[1],
      port: match[2] || '',
    };
  }
  const hostPort = normalized.match(/^([^/:]+):(\d+)$/);
  if (hostPort) {
    return { host: hostPort[1], port: hostPort[2] };
  }
  return { host: '', port: '' };
}

function buildOpcuaEndpointUrl(host, port) {
  const resolvedHost = String(host || '').trim() || OPCUA_DEFAULT_HOST;
  const portText = String(port ?? '').trim();
  const resolvedPort = portText ? Number(portText) : OPCUA_DEFAULT_PORT;
  const safePort = Number.isFinite(resolvedPort) && resolvedPort > 0 ? resolvedPort : OPCUA_DEFAULT_PORT;
  return `opc.tcp://${resolvedHost}:${safePort}/`;
}

function clampPollIntervalMs(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 500;
  return Math.max(50, Math.min(5000, Math.round(n)));
}

function getOpcuaPayload() {
  return {
    endpoint_url: buildOpcuaEndpointUrl(
      document.getElementById('appOpcuaHost').value,
      document.getElementById('appOpcuaPort').value,
    ),
    username: document.getElementById('appOpcuaUsername').value.trim(),
    password: document.getElementById('appOpcuaPassword').value,
    clear_password: document.getElementById('appOpcuaClearPassword').checked,
    heartbeat_node: document.getElementById('appOpcuaHeartbeatNode').value.trim(),
    poll_interval_ms: clampPollIntervalMs(document.getElementById('appOpcuaPollIntervalMs').value),
  };
}

function fillOpcuaForm(data) {
  const settings = data || {};
  const endpoint = parseOpcuaEndpoint(settings.endpoint_url || '');
  document.getElementById('appOpcuaHost').value = endpoint.host;
  document.getElementById('appOpcuaPort').value = endpoint.port;
  document.getElementById('appOpcuaUsername').value = settings.username || '';
  const passwordInput = document.getElementById('appOpcuaPassword');
  passwordInput.value = '';
  passwordInput.placeholder = settings.password_configured ? '已配置；留空保持不变' : '输入 OPC UA 密码';
  document.getElementById('appOpcuaClearPassword').checked = false;
  document.getElementById('appOpcuaHeartbeatNode').value = settings.heartbeat_node || '';
  document.getElementById('appOpcuaPollIntervalMs').value = clampPollIntervalMs(
    settings.poll_interval_ms ?? 500,
  );
}


async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  let data = {};
  try {
    data = await resp.json();
  } catch {
    data = {};
  }
  if (!resp.ok) {
    const detail = data.detail || data.message || JSON.stringify(data);
    if (resp.status === 404) {
      throw new Error(`${detail}（接口不存在，请重启 Query Web 服务后再试）`);
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function setHintMessage(elementId, message, tone = 'warn') {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message;
  el.className = tone === 'ok' ? 'muted ok' : 'muted warn';
}

function setConfigStatus(message, tone = 'ok') {
  const bar = document.getElementById('configStatusBar');
  const messageEl = document.getElementById('configStatusMessage');
  const timeEl = document.getElementById('configStatusTime');
  if (!bar || !messageEl || !timeEl) return;
  bar.dataset.tone = tone;
  messageEl.textContent = message;
  timeEl.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function catchHintError(hintId) {
  return (err) => {
    const message = err?.message || String(err);
    setHintMessage(hintId, message);
    setConfigStatus(`操作失败：${message}`, 'error');
  };
}

function saveConfigPageState() {
  const state = {
    viewName: document.getElementById('editViewName').value || '',
    group: document.getElementById('editGroupName').value || '',
    baselineTable: document.getElementById('editTableName').value || '',
    timeField: document.getElementById('tableTimeField').value || '',
    batchField: document.getElementById('tableBatchField').value || '',
    batchSourceTable: document.getElementById('batchSourceTable').value || '',
    batchSourceField: document.getElementById('batchSourceField').value || '',
    sortBy: document.getElementById('tableSortBy').value || '',
    sortDir: document.getElementById('tableSortDir').value || 'desc',
    pageSize: document.getElementById('tablePageSize').value || '50',
    orderedColumns,
    columnLabels,
    pluginModule: document.getElementById('pluginModule').value || '',
    pluginPageIndex: document.getElementById('pluginPageIndex').value || '1',
    configProfile: document.getElementById('configProfileSelect').value || currentConfigProfile || '',
  };
  safeStorageSet(CONFIG_STATE_KEY, JSON.stringify(state));
}

function loadSavedConfigPageState() {
  const raw = safeStorageGet(CONFIG_STATE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
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

function appendOption(select, value, label) {
  const op = document.createElement('option');
  op.value = value;
  op.textContent = label || value;
  select.appendChild(op);
}

function getSelectedValues(selectId) {
  const sel = document.getElementById(selectId);
  return Array.from(sel.selectedOptions).map(x => x.value);
}

function populateFieldSelectors(columns, preferredTimeField, preferredSortBy, preferredBatchField = '') {
  const normalizedColumns = Array.isArray(columns) ? columns : [];
  const timeField = document.getElementById('tableTimeField');
  const batchField = document.getElementById('tableBatchField');
  const sortBy = document.getElementById('tableSortBy');
  timeField.innerHTML = '';
  batchField.innerHTML = '';
  sortBy.innerHTML = '';

  appendOption(batchField, '', '不支持批次号查询');

  for (const column of normalizedColumns) {
    appendOption(timeField, column);
    appendOption(batchField, column);
    appendOption(sortBy, column);
  }

  if (normalizedColumns.includes(preferredTimeField)) {
    timeField.value = preferredTimeField;
  } else if (normalizedColumns.includes('collection_time')) {
    timeField.value = 'collection_time';
  } else if (normalizedColumns.length > 0) {
    timeField.value = normalizedColumns[0];
  }

  if (normalizedColumns.includes(preferredSortBy)) {
    sortBy.value = preferredSortBy;
  } else if (normalizedColumns.includes(timeField.value)) {
    sortBy.value = timeField.value;
  } else if (normalizedColumns.length > 0) {
    sortBy.value = normalizedColumns[0];
  }

  batchField.value = normalizedColumns.includes(preferredBatchField) ? preferredBatchField : '';
}

async function loadColumnsForTable(tableName, preferredTimeField, preferredSortBy, preferredBatchField = '') {
  if (!tableName) {
    availableColumns = [];
    document.getElementById('availableColumns').innerHTML = '';
    populateFieldSelectors([], '', '');
    return;
  }

  const meta = await fetchJson('/api/meta/columns?table=' + encodeURIComponent(tableName));
  availableColumns = meta.columns || [];
  const available = document.getElementById('availableColumns');
  available.innerHTML = '';
  for (const c of availableColumns) appendOption(available, c);
  populateFieldSelectors(availableColumns, preferredTimeField, preferredSortBy, preferredBatchField);
}

async function loadViews() {
  const data = await fetchJson('/api/query/views');
  queryViews = data.views || {};
  const lockedView = queryViews[LOCKED_GROUP_VIEW] || {};
  lockSelect(
    'editViewName',
    LOCKED_GROUP_VIEW,
    `${LOCKED_GROUP_VIEW} - ${lockedView.title || LOCKED_GROUP_VIEW}`,
  );
  await loadBatchSourceConfig();
}

async function loadBatchSourceFields(tableName, preferredField = '') {
  const fieldSelect = document.getElementById('batchSourceField');
  fieldSelect.innerHTML = '';
  appendOption(fieldSelect, '', '不启用批次号来源');
  if (!tableName) return;

  const meta = await fetchJson('/api/meta/columns?table=' + encodeURIComponent(tableName));
  const fields = Array.isArray(meta.columns) ? meta.columns : [];
  for (const field of fields) appendOption(fieldSelect, field);
  if (preferredField && fields.includes(preferredField)) {
    fieldSelect.value = preferredField;
  }
}

async function loadBatchSourceConfig() {
  const viewName = document.getElementById('editViewName').value || LOCKED_GROUP_VIEW;
  const [source, tableData] = await Promise.all([
    fetchJson('/api/config/query-batch-source?view_name=' + encodeURIComponent(viewName)),
    fetchJson('/api/meta/database-tables'),
  ]);
  const tableSelect = document.getElementById('batchSourceTable');
  tableSelect.innerHTML = '';
  appendOption(tableSelect, '', '不启用批次号来源');
  const tables = Array.isArray(tableData.tables) ? tableData.tables : [];
  for (const table of tables) appendOption(tableSelect, table);
  if (source.table && !tables.includes(source.table)) {
    appendOption(tableSelect, source.table, `${source.table}（当前数据库不存在）`);
  }
  tableSelect.value = source.table || '';
  if (source.table && !tables.includes(source.table)) {
    const fieldSelect = document.getElementById('batchSourceField');
    fieldSelect.innerHTML = '';
    appendOption(fieldSelect, '', '不启用批次号来源');
    if (source.field) appendOption(fieldSelect, source.field, `${source.field}（来源表不存在，无法校验）`);
    fieldSelect.value = source.field || '';
  } else {
    await loadBatchSourceFields(source.table || '', source.field || '');
  }
  const hint = document.getElementById('batchSourceHint');
  if (source.table && source.field) {
    hint.textContent = `当前来源：${source.table}.${source.field}`;
    hint.className = tables.includes(source.table) ? 'muted ok' : 'muted warn';
  } else {
    hint.textContent = '当前 View 未配置批次号来源，查询页将禁用按批次号查询';
    hint.className = 'muted warn';
  }
}

async function saveBatchSourceConfig() {
  const viewName = document.getElementById('editViewName').value || LOCKED_GROUP_VIEW;
  const table = document.getElementById('batchSourceTable').value || '';
  const field = document.getElementById('batchSourceField').value || '';
  if (table && !field) {
    setHintMessage('batchSourceHint', '选择来源表后必须选择来源字段');
    return;
  }
  const result = await fetchJson('/api/config/query-batch-source', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ view_name: viewName, table, field }),
  });
  queryViews[viewName] = queryViews[viewName] || {};
  queryViews[viewName].batch_source = { table: result.table || '', field: result.field || '' };
  setHintMessage(
    'batchSourceHint',
    table ? `已保存批次号来源：${table}.${field}` : '已清空批次号来源，查询页将禁用按批次号查询',
    'ok',
  );
  setConfigStatus(
    table ? `保存成功：批次号来源=${table}.${field}` : '保存成功：已清空批次号来源',
    'ok',
  );
  saveConfigPageState();
}

async function loadAppSettings() {
  const [data, opcuaData] = await Promise.all([
    fetchJson('/api/config/app-settings'),
    fetchJson('/api/config/opcua').catch(() => ({
      endpoint_url: '',
      username: '',
      password_configured: false,
      heartbeat_node: '',
      poll_interval_ms: 500,
    })),
  ]);
  fillAppSettingsForm(data || {});
  fillOpcuaForm(opcuaData || {});
  document.getElementById('appSettingsHint').textContent = '已加载数据库设定（数据库与查询限制）';
  const opcuaHint = document.getElementById('opcuaSettingsHint');
  if (opcuaHint) {
    opcuaHint.textContent = '已加载 OPCUA设定（连接、轮询间隔与心跳）';
  }
}

function fillConfigProfileSelect(select, profiles, activeName) {
  if (!select) return;
  select.innerHTML = '';
  for (const profile of profiles || []) {
    const op = document.createElement('option');
    op.value = profile.filename;
    op.textContent = profile.name && profile.name !== profile.filename
      ? `${profile.name} (${profile.filename})`
      : profile.filename;
    select.appendChild(op);
  }
  if (activeName && hasOption(select, activeName)) {
    select.value = activeName;
  }
}

async function loadConfigProfiles() {
  const data = await fetchJson('/api/config/profiles');
  currentConfigProfile = data.active || '';
  const profiles = data.profiles || [];
  fillConfigProfileSelect(document.getElementById('configProfileSelect'), profiles, currentConfigProfile);
  fillConfigProfileSelect(document.getElementById('activeConfigSelect'), profiles, currentConfigProfile);
  document.getElementById('configProfileHint').textContent =
    currentConfigProfile ? `当前加载: ${currentConfigProfile}` : '未找到可用 config';
  const activeHint = document.getElementById('activeConfigHint');
  if (activeHint) {
    activeHint.textContent = currentConfigProfile
      ? `服务启动与 OPC 回写均使用此配置：${currentConfigProfile}`
      : '未找到可用 config';
  }
}

async function switchConfigProfile(sourceSelectId) {
  const selectId = sourceSelectId || 'configProfileSelect';
  const filename = document.getElementById(selectId).value;
  if (!filename) return;
  const result = await fetchJson('/api/config/profiles/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  });
  currentConfigProfile = result.active || filename;
  await reloadActiveConfigData(`已激活并加载 config: ${currentConfigProfile}`);
}

async function refreshConfigProfiles() {
  await loadConfigProfiles();
  document.getElementById('configProfileHint').textContent =
    currentConfigProfile ? `config 列表已刷新，当前加载: ${currentConfigProfile}` : '未找到可用 config';
}

async function createConfigProfile() {
  const filename = await showNameModal(suggestConfigBaseName());
  const hint = document.getElementById('configProfileHint');
  if (!filename) {
    hint.textContent = '已取消新建';
    return;
  }

  const result = await fetchJson('/api/config/profiles/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  });
  currentConfigProfile = result.active || filename;
  await reloadActiveConfigData(`已新建配置文件: ${currentConfigProfile}`);
}

function suggestConfigBaseName() {
  const existing = new Set(
    Array.from(document.getElementById('configProfileSelect').options).map(option =>
      option.value.replace(/\.json$/i, ''),
    ),
  );
  let base = 'new_config';
  let idx = 2;
  while (existing.has(base)) {
    base = `new_config_${idx}`;
    idx += 1;
  }
  return base;
}

async function deleteCurrentConfigProfile() {
  const filename = (document.getElementById('configProfileSelect').value || currentConfigProfile || '').trim();
  const hint = document.getElementById('configProfileHint');
  if (!filename) {
    hint.textContent = '没有可删除的 config';
    return;
  }

  const confirmed = await showConfirmModal({
    title: '删除确认（1/2）',
    message: `确认删除配置文件 ${filename} 吗？`,
  });
  if (!confirmed) {
    hint.textContent = '已取消删除';
    return;
  }

  const confirmedAgain = await showConfirmModal({
    title: '删除确认（2/2）',
    message: `二次确认：删除后不可恢复，是否继续删除 ${filename}？`,
  });
  if (!confirmedAgain) {
    hint.textContent = '已取消删除';
    return;
  }

  const result = await fetchJson('/api/config/profiles/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  });
  currentConfigProfile = result.active || '';
  await reloadActiveConfigData(`已删除 config: ${filename}；当前加载: ${currentConfigProfile || '-'}`);
}

async function reloadActiveConfigData(message) {
  setConfigStatus('正在读取配置文件及其全部设置…', 'working');
  safeStorageRemove(CONFIG_STATE_KEY);
  queryViews = {};
  availableColumns = [];
  orderedColumns = [];
  columnLabels = {};
  currentSchema = null;
  pluginConfigData = { modules: {} };
  await loadConfigProfiles();
  await loadAppSettings();
  await loadViews();
  await loadGroups();
  await refreshMetadataFromCurrentDatabase();
  await loadPluginConfig();
  document.getElementById('configProfileHint').textContent = message;
  setConfigStatus(`读取成功：${message}`, 'ok');
}

function showNameModal(defaultBaseName = 'new_config') {
  return new Promise(resolve => {
    const overlay = document.getElementById('name-modal-overlay');
    const input = document.getElementById('name-modal-input');
    const hint = document.getElementById('name-modal-hint');
    const cancelBtn = document.getElementById('name-modal-cancel');
    const confirmBtn = document.getElementById('name-modal-confirm');

    hint.textContent = '文件后缀固定为 .json';
    input.value = defaultBaseName;
    overlay.style.display = 'flex';
    input.focus();
    input.select();

    const cleanup = () => {
      overlay.style.display = 'none';
      cancelBtn.removeEventListener('click', onCancel);
      confirmBtn.removeEventListener('click', onConfirm);
      overlay.removeEventListener('click', onOverlayClick);
      input.removeEventListener('keydown', onKeyDown);
    };

    const onCancel = () => {
      cleanup();
      resolve('');
    };

    const onConfirm = () => {
      const raw = input.value.trim().replace(/\.json$/i, '');
      if (!raw) {
        hint.textContent = '文件名不能为空';
        return;
      }
      if (/[\\/:*?"<>|]/.test(raw)) {
        hint.textContent = '文件名包含非法字符 \\ / : * ? " < > |';
        return;
      }
      cleanup();
      resolve(`${raw}.json`);
    };

    const onOverlayClick = event => {
      if (event.target === overlay) {
        onCancel();
      }
    };

    const onKeyDown = event => {
      if (event.key === 'Enter') {
        onConfirm();
      } else if (event.key === 'Escape') {
        onCancel();
      }
    };

    cancelBtn.addEventListener('click', onCancel);
    confirmBtn.addEventListener('click', onConfirm);
    overlay.addEventListener('click', onOverlayClick);
    input.addEventListener('keydown', onKeyDown);
  });
}

function showConfirmModal({ title, message }) {
  return new Promise(resolve => {
    const overlay = document.getElementById('confirm-modal-overlay');
    const modalTitle = document.getElementById('confirm-modal-title');
    const modalMessage = document.getElementById('confirm-modal-message');
    const cancelBtn = document.getElementById('confirm-modal-cancel');
    const confirmBtn = document.getElementById('confirm-modal-confirm');

    modalTitle.textContent = title || '确认';
    modalMessage.textContent = message || '';
    overlay.style.display = 'flex';

    const cleanup = () => {
      overlay.style.display = 'none';
      cancelBtn.removeEventListener('click', onCancel);
      confirmBtn.removeEventListener('click', onConfirm);
      overlay.removeEventListener('click', onOverlayClick);
    };

    const onCancel = () => {
      cleanup();
      resolve(false);
    };

    const onConfirm = () => {
      cleanup();
      resolve(true);
    };

    const onOverlayClick = event => {
      if (event.target === overlay) {
        onCancel();
      }
    };

    cancelBtn.addEventListener('click', onCancel);
    confirmBtn.addEventListener('click', onConfirm);
    overlay.addEventListener('click', onOverlayClick);
  });
}

function hasOption(select, value) {
  return Array.from(select.options).some(option => option.value === value);
}

function initializeLockedControls() {
  const groupLoadBtn = document.getElementById('btnLoadTableConfig');
  const pluginLoadBtn = document.getElementById('btnLoadPluginPage');
  if (groupLoadBtn) groupLoadBtn.hidden = true;
  if (pluginLoadBtn) pluginLoadBtn.hidden = true;
  lockSelect('pluginViewName', LOCKED_PLUGIN_VIEW, LOCKED_PLUGIN_VIEW);
}

function clearGroupConfigEditor() {
  currentSchema = null;
  availableColumns = [];
  orderedColumns = [];
  columnLabels = {};
  document.getElementById('editTableName').innerHTML = '';
  document.getElementById('availableColumns').innerHTML = '';
  document.getElementById('tableTimeField').innerHTML = '';
  document.getElementById('tableBatchField').innerHTML = '';
  document.getElementById('tableSortBy').innerHTML = '';
  document.getElementById('schemaHint').textContent = '当前数据库下没有可用 group，请先检查连接设置。';
  document.getElementById('schemaHint').className = 'muted warn';
  document.getElementById('columnEditorHint').textContent = '';
  renderOrderedColumns();
}

async function refreshMetadataFromCurrentDatabase() {
  const viewSel = document.getElementById('editViewName');
  const groupSel = document.getElementById('editGroupName');
  const tableSel = document.getElementById('editTableName');
  const pluginGroupSel = document.getElementById('pluginBindGroup');
  const previousView = viewSel.value;
  const previousGroup = groupSel.value;
  const previousTable = tableSel.value;
  const previousPluginGroup = pluginGroupSel.value;

  await loadViews();
  if (previousView && hasOption(viewSel, previousView)) {
    viewSel.value = previousView;
  }

  await loadGroups();

  if ((groupSel.options || []).length === 0) {
    clearGroupConfigEditor();
    pluginGroupSel.value = '';
    await updatePluginGroupHint('');
    saveConfigPageState();
    return;
  }

  if (previousGroup && hasOption(groupSel, previousGroup)) {
    groupSel.value = previousGroup;
    await loadTables();
    if (previousTable && hasOption(tableSel, previousTable)) {
      tableSel.value = previousTable;
      await loadColumnsForTable(
        previousTable,
        document.getElementById('tableTimeField').value,
        document.getElementById('tableSortBy').value,
      );
    }
  }

  if (groupSel.value && tableSel.value) {
    await loadTableConfig();
  } else {
    clearGroupConfigEditor();
  }

  if (previousPluginGroup && hasOption(pluginGroupSel, previousPluginGroup)) {
    pluginGroupSel.value = previousPluginGroup;
  } else {
    pluginGroupSel.value = pluginGroupSel.options.length > 0 ? pluginGroupSel.options[0].value : '';
  }
  await updatePluginGroupHint(pluginGroupSel.value || '');
  saveConfigPageState();
}

async function saveAppSettings(options = {}) {
  const { refreshMetadata = false, successMessage } = options;
  const payload = getAppSettingsPayload();
  const result = await fetchJson('/api/config/app-settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  fillAppSettingsForm((result && result.settings) || payload);
  if (refreshMetadata) {
    await refreshMetadataFromCurrentDatabase();
  }
  const baseMessage =
    successMessage ||
    (refreshMetadata
      ? '数据库设定已保存，数据库已重连，Group 与列已按当前数据库刷新'
      : '数据库设定已保存；如需刷新 Group 与列，请点击“连接数据库”');
  document.getElementById('appSettingsHint').textContent = baseMessage;
  setConfigStatus(`保存成功：${baseMessage}`, 'ok');
  return result;
}

async function saveOpcuaSettings(options = {}) {
  const { successMessage } = options;
  const opcuaPayload = getOpcuaPayload();
  const opcuaResult = await fetchJson('/api/config/opcua', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opcuaPayload),
  });
  fillOpcuaForm(opcuaResult || opcuaPayload);
  const hint = document.getElementById('opcuaSettingsHint');
  if (hint) {
    hint.textContent =
      successMessage ||
      `OPCUA设定已保存（轮询 ${opcuaResult?.poll_interval_ms || opcuaPayload.poll_interval_ms} ms` +
        `${opcuaResult?.heartbeat_node || opcuaPayload.heartbeat_node ? '，已配置心跳' : '，未配置心跳'}）`;
    hint.className = 'muted ok';
  }
  setConfigStatus('保存成功：OPC UA 连接、轮询与心跳设置已写入', 'ok');
  return opcuaResult;
}

async function connectDatabase() {
  document.getElementById('appSettingsHint').textContent = '正在保存数据库设定并连接数据库...';
  await saveAppSettings({
    refreshMetadata: false,
    successMessage: '数据库设定已保存，正在验证数据库连接...',
  });
  const check = await fetchJson('/api/db/check');
  await refreshMetadataFromCurrentDatabase();
  document.getElementById('appSettingsHint').textContent =
    `数据库连接成功（${check.database || '-'}），Group 与列已刷新`;
  setConfigStatus(`读取成功：数据库 ${check.database || '-'} 已连接，Group 与列已刷新`, 'ok');
}

async function testOpcuaConnection() {
  const hint = document.getElementById('opcuaSettingsHint') || document.getElementById('appSettingsHint');
  const payload = getOpcuaPayload();
  hint.textContent = '正在测试 OPC UA 连接...';
  hint.className = 'muted';
  try {
    const result = await fetchJson('/api/config/opcua', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, test_only: true }),
    });
    const parts = [
      result.message || 'OPC UA 连接成功',
      result.endpoint_url ? `endpoint=${result.endpoint_url}` : '',
      result.product_name ? `product=${result.product_name}` : '',
      result.namespace_count != null ? `namespaces=${result.namespace_count}` : '',
    ].filter(Boolean);
    hint.textContent = parts.join('；');
    hint.className = 'muted ok';
  } catch (err) {
    hint.textContent = String(err?.message || err);
    hint.className = 'muted warn';
  }
}

async function loadGroups() {
  const data = await fetchJson('/api/meta/groups');
  const sel = document.getElementById('editGroupName');
  sel.innerHTML = '';
  for (const g of data.groups || []) appendOption(sel, g);
  const pluginGroupSel = document.getElementById('pluginBindGroup');
  pluginGroupSel.innerHTML = '';
  appendOption(pluginGroupSel, '');
  for (const g of data.groups || []) appendOption(pluginGroupSel, g);
  if ((data.groups || []).length > 0) {
    await loadTables();
  }
}

async function loadTables() {
  const group = document.getElementById('editGroupName').value;
  if (!group) return;
  const data = await fetchJson('/api/meta/group-schema?group=' + encodeURIComponent(group));
  currentSchema = data;
  const sel = document.getElementById('editTableName');
  sel.innerHTML = '';
  for (const t of data.tables || []) {
    const kind = data.table_kinds?.[t] || '';
    const label = kind === 'fixed' ? `${t}（固定表）` : t;
    appendOption(sel, t, label);
  }
  if (data.baseline_table && (data.tables || []).includes(data.baseline_table)) {
    sel.value = data.baseline_table;
  }
  const hint = document.getElementById('schemaHint');
  const fixedCount = (data.tables || []).filter(t => data.table_kinds?.[t] === 'fixed').length;
  const partitionedCount = (data.tables || []).filter(t => data.table_kinds?.[t] === 'partitioned').length;
  const legacyCount = (data.tables || []).filter(t => data.table_kinds?.[t] === 'legacy_date').length;
  const kindSummary = [
    fixedCount ? `固定表 ${fixedCount}` : '',
    partitionedCount ? `年份分表 ${partitionedCount}` : '',
    legacyCount ? `日表 ${legacyCount}` : '',
  ].filter(Boolean).join('，');
  if (data.consistent) {
    hint.textContent = `group=${group} 结构一致，共 ${data.tables.length} 张表${kindSummary ? `（${kindSummary}）` : ''}`;
    hint.className = 'muted ok';
  } else {
    hint.textContent = `group=${group} 检测到结构不一致，请选择基准表。当前基准：${data.baseline_table}${kindSummary ? `；${kindSummary}` : ''}`;
    hint.className = 'muted warn';
  }
  await loadColumnsForTable(sel.value, document.getElementById('tableTimeField').value, document.getElementById('tableSortBy').value);
  saveConfigPageState();
}

function renderOrderedColumns() {
  const ordered = document.getElementById('orderedColumns');
  ordered.innerHTML = '';
  for (const c of orderedColumns) appendOption(ordered, c);
  renderLabelTable();
  saveConfigPageState();
}

function renderLabelTable() {
  const table = document.getElementById('columnLabelTable');
  table.innerHTML = '';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>字段名</th><th>English</th><th>中文</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const name of orderedColumns) {
    const labels = columnLabels[name] || { label_en: name, label_zh: name };
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td>${name}</td>` +
      `<td><input data-col="${name}" data-type="en" value="${labels.label_en || name}" /></td>` +
      `<td><input data-col="${name}" data-type="zh" value="${labels.label_zh || name}" /></td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function syncLabelInputsToState() {
  const inputs = document.querySelectorAll('#columnLabelTable input[data-col]');
  for (const input of inputs) {
    const name = input.getAttribute('data-col');
    const type = input.getAttribute('data-type');
    if (!columnLabels[name]) {
      columnLabels[name] = { label_en: name, label_zh: name };
    }
    if (type === 'en') columnLabels[name].label_en = input.value || name;
    if (type === 'zh') columnLabels[name].label_zh = input.value || name;
  }
  saveConfigPageState();
}

async function loadTableConfig() {
  const viewName = document.getElementById('editViewName').value;
  const group = document.getElementById('editGroupName').value;
  const baselineTable = document.getElementById('editTableName').value;
  if (!viewName || !group || !baselineTable) return;

  await loadColumnsForTable(baselineTable, '', '');

  const cfg = await fetchJson(
    '/api/config/query-group?view_name=' +
      encodeURIComponent(viewName) +
      '&group=' +
      encodeURIComponent(group),
  );

  orderedColumns = (cfg.columns || []).map(x => x.name);
  columnLabels = {};
  for (const c of cfg.columns || []) {
    columnLabels[c.name] = {
      label_en: c.label_en || c.name,
      label_zh: c.label_zh || c.name,
    };
  }

  populateFieldSelectors(availableColumns, cfg.time_field, cfg.sort_by, cfg.batch_field);
  document.getElementById('tableSortDir').value = cfg.sort_dir === 'asc' ? 'asc' : 'desc';
  document.getElementById('tablePageSize').value = Number(cfg.page_size || 50);
  renderOrderedColumns();
  if (cfg.baseline_table && availableColumns.length > 0) {
    const tableSel = document.getElementById('editTableName');
    if (Array.from(tableSel.options).some(x => x.value === cfg.baseline_table)) {
      tableSel.value = cfg.baseline_table;
    }
  }
  document.getElementById('columnEditorHint').textContent =
    `已加载: view=${viewName}, group=${group}, 基准表=${baselineTable}, columns=${orderedColumns.length}（当前为 group 级配置）`;
  setConfigStatus(
    `读取成功：view=${viewName}，group=${group}，columns=${orderedColumns.length}`,
    'ok',
  );
  saveConfigPageState();
}

function addColumns() {
  syncLabelInputsToState();
  const selected = getSelectedValues('availableColumns');
  for (const c of selected) {
    if (!orderedColumns.includes(c)) {
      orderedColumns.push(c);
      if (!columnLabels[c]) columnLabels[c] = { label_en: c, label_zh: c };
    }
  }
  renderOrderedColumns();
}

function removeColumns() {
  syncLabelInputsToState();
  const selected = new Set(getSelectedValues('orderedColumns'));
  orderedColumns = orderedColumns.filter(c => !selected.has(c));
  renderOrderedColumns();
}

function moveSelected(up) {
  syncLabelInputsToState();
  const selected = getSelectedValues('orderedColumns');
  if (selected.length === 0) return;
  const idxs = selected.map(v => orderedColumns.indexOf(v)).filter(i => i >= 0);
  if (up) {
    idxs.sort((a, b) => a - b);
    for (const idx of idxs) {
      if (idx > 0) {
        const tmp = orderedColumns[idx - 1];
        orderedColumns[idx - 1] = orderedColumns[idx];
        orderedColumns[idx] = tmp;
      }
    }
  } else {
    idxs.sort((a, b) => b - a);
    for (const idx of idxs) {
      if (idx < orderedColumns.length - 1) {
        const tmp = orderedColumns[idx + 1];
        orderedColumns[idx + 1] = orderedColumns[idx];
        orderedColumns[idx] = tmp;
      }
    }
  }
  renderOrderedColumns();
  const orderedSelect = document.getElementById('orderedColumns');
  for (const opt of orderedSelect.options) {
    opt.selected = selected.includes(opt.value);
  }
}

async function saveTableConfig() {
  syncLabelInputsToState();
  const viewName = document.getElementById('editViewName').value;
  const group = document.getElementById('editGroupName').value;
  const baselineTable = document.getElementById('editTableName').value;
  if (!viewName || !group || !baselineTable) {
    setHintMessage('columnEditorHint', '请先选择 view、group、基准表');
    return;
  }

  const payload = {
    view_name: viewName,
    group,
    baseline_table: baselineTable,
    time_field: document.getElementById('tableTimeField').value || 'collection_time',
    batch_field: document.getElementById('tableBatchField').value || '',
    sort_by: document.getElementById('tableSortBy').value || 'collection_time',
    sort_dir: document.getElementById('tableSortDir').value || 'desc',
    page_size: Number(document.getElementById('tablePageSize').value || 50),
    columns: orderedColumns.map(name => ({
      name,
      label_en: (columnLabels[name] || {}).label_en || name,
      label_zh: (columnLabels[name] || {}).label_zh || name,
    })),
  };

  await fetchJson('/api/config/query-group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  await fetchJson('/api/config/group-baseline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group, baseline_table: baselineTable }),
  });
  document.getElementById('columnEditorHint').textContent =
    `已保存: view=${viewName}, group=${group}, 基准表=${baselineTable}, columns=${orderedColumns.length}（已写入 views.${viewName}.per_group.${group}）`;
  setConfigStatus(
    `保存成功：view=${viewName}，group=${group}，columns=${orderedColumns.length}`,
    'ok',
  );
  saveConfigPageState();
}

async function deleteGroupConfig() {
  const viewName = document.getElementById('editViewName').value;
  const group = document.getElementById('editGroupName').value;
  const hint = document.getElementById('columnEditorHint');
  if (!viewName || !group) {
    hint.textContent = '请先选择 view、group';
    hint.className = 'muted warn';
    return;
  }

  const confirmed = await showConfirmModal({
    title: '删除确认（1/2）',
    message: `确认删除当前 group 配置 view=${viewName}, group=${group} 吗？`,
  });
  if (!confirmed) {
    hint.textContent = '已取消删除';
    return;
  }

  const confirmedAgain = await showConfirmModal({
    title: '删除确认（2/2）',
    message: `二次确认：删除后不可恢复，是否继续删除 view=${viewName}, group=${group}？`,
  });
  if (!confirmedAgain) {
    hint.textContent = '已取消删除';
    return;
  }

  const result = await fetchJson(
    '/api/config/query-group?view_name=' +
      encodeURIComponent(viewName) +
      '&group=' +
      encodeURIComponent(group),
    { method: 'DELETE' },
  );
  orderedColumns = [];
  columnLabels = {};
  renderOrderedColumns();
  document.getElementById('columnEditorHint').textContent =
    result.status === 'deleted'
      ? `已删除: view=${viewName}, group=${group}（views.${viewName}.per_group.${group}）`
      : `未找到配置: view=${viewName}, group=${group}`;
  saveConfigPageState();
}

async function restoreConfigPageState() {
  const saved = loadSavedConfigPageState();
  if (!saved) return;
  if (saved.viewName && queryViews[saved.viewName]) {
    document.getElementById('editViewName').value = saved.viewName;
  }
  if (saved.group) {
    const groupSel = document.getElementById('editGroupName');
    if (Array.from(groupSel.options).some(o => o.value === saved.group)) {
      groupSel.value = saved.group;
      await loadTables();
      if (saved.baselineTable) {
        const tableSel = document.getElementById('editTableName');
        if (Array.from(tableSel.options).some(o => o.value === saved.baselineTable)) {
          tableSel.value = saved.baselineTable;
        }
      }
      await loadTableConfig();
      if (saved.timeField) {
        const timeFieldSel = document.getElementById('tableTimeField');
        if (Array.from(timeFieldSel.options).some(o => o.value === saved.timeField)) {
          timeFieldSel.value = saved.timeField;
        }
      }
      if (saved.batchField) {
        const batchFieldSel = document.getElementById('tableBatchField');
        if (Array.from(batchFieldSel.options).some(o => o.value === saved.batchField)) {
          batchFieldSel.value = saved.batchField;
        }
      }
      if (saved.sortBy) {
        const sortBySel = document.getElementById('tableSortBy');
        if (Array.from(sortBySel.options).some(o => o.value === saved.sortBy)) {
          sortBySel.value = saved.sortBy;
        }
      }
      if (saved.sortDir) {
        document.getElementById('tableSortDir').value = saved.sortDir === 'asc' ? 'asc' : 'desc';
      }
      if (saved.pageSize) {
        document.getElementById('tablePageSize').value = saved.pageSize;
      }
      if (Array.isArray(saved.orderedColumns)) {
        orderedColumns = saved.orderedColumns.filter(c => availableColumns.includes(c));
      }
      if (saved.columnLabels && typeof saved.columnLabels === 'object') {
        columnLabels = saved.columnLabels;
      }
      renderOrderedColumns();
    }
  }
  if (saved.pluginModule) {
    const sel = document.getElementById('pluginModule');
    if (Array.from(sel.options).some(o => o.value === saved.pluginModule)) {
      sel.value = saved.pluginModule;
    }
  }
  if (saved.pluginPageIndex) {
    document.getElementById('pluginPageIndex').value = saved.pluginPageIndex;
  }
  await loadPluginPageConfig();
}

document.getElementById('btnLoadTableConfig').addEventListener('click', () => {
  loadTableConfig().catch(catchHintError('columnEditorHint'));
});
document.getElementById('btnReloadConfigProfile').addEventListener('click', () => {
  switchConfigProfile('configProfileSelect').catch(catchHintError('configProfileHint'));
});
document.getElementById('btnRefreshConfigProfiles').addEventListener('click', () => {
  refreshConfigProfiles().catch(catchHintError('configProfileHint'));
});
document.getElementById('btnCreateConfigProfile').addEventListener('click', () => {
  createConfigProfile().catch(catchHintError('configProfileHint'));
});
document.getElementById('btnDeleteConfigProfile').addEventListener('click', () => {
  deleteCurrentConfigProfile().catch(catchHintError('configProfileHint'));
});
document.getElementById('configProfileSelect').addEventListener('change', () => {
  switchConfigProfile('configProfileSelect').catch(catchHintError('configProfileHint'));
});
document.getElementById('activeConfigSelect').addEventListener('change', () => {
  switchConfigProfile('activeConfigSelect').catch(err => {
    const hint = document.getElementById('activeConfigHint');
    if (hint) hint.textContent = String(err.message || err);
    catchHintError('configProfileHint')(err);
  });
});
document.getElementById('btnSaveAppSettings').addEventListener('click', () => {
  saveAppSettings().catch(catchHintError('appSettingsHint'));
});
document.getElementById('btnSaveOpcuaSettings').addEventListener('click', () => {
  saveOpcuaSettings().catch(catchHintError('opcuaSettingsHint'));
});
document.getElementById('btnConnectDatabase').addEventListener('click', () => {
  connectDatabase().catch(catchHintError('appSettingsHint'));
});
document.getElementById('btnTestOpcuaConnection').addEventListener('click', () => {
  testOpcuaConnection().catch(catchHintError('opcuaSettingsHint'));
});
document.getElementById('appDbPassword').addEventListener('input', event => {
  if (event.target.value) document.getElementById('appDbClearPassword').checked = false;
});
document.getElementById('appOpcuaPassword').addEventListener('input', event => {
  if (event.target.value) document.getElementById('appOpcuaClearPassword').checked = false;
});
document.getElementById('btnAddColumns').addEventListener('click', addColumns);
document.getElementById('btnRemoveColumns').addEventListener('click', removeColumns);
document.getElementById('btnMoveUp').addEventListener('click', () => moveSelected(true));
document.getElementById('btnMoveDown').addEventListener('click', () => moveSelected(false));
document.getElementById('btnSaveTableConfig').addEventListener('click', () => {
  saveTableConfig().catch(catchHintError('columnEditorHint'));
});
document.getElementById('btnSaveBatchSource').addEventListener('click', () => {
  saveBatchSourceConfig().catch(catchHintError('batchSourceHint'));
});
document.getElementById('btnDeleteGroupConfig').addEventListener('click', () => {
  deleteGroupConfig().catch(catchHintError('columnEditorHint'));
});
document.getElementById('editGroupName').addEventListener('change', async () => {
  try {
    await loadTables();
    if (document.getElementById('editTableName').value) {
      await loadTableConfig();
    } else {
      clearGroupConfigEditor();
    }
    saveConfigPageState();
  } catch (err) {
    catchHintError('schemaHint')(err);
  }
});
document.getElementById('editViewName').addEventListener('change', saveConfigPageState);
document.getElementById('editTableName').addEventListener('change', () => {
  loadColumnsForTable(
    document.getElementById('editTableName').value,
    document.getElementById('tableTimeField').value,
    document.getElementById('tableSortBy').value,
    document.getElementById('tableBatchField').value,
  ).catch(catchHintError('columnEditorHint'));
  saveConfigPageState();
});
document.getElementById('tableTimeField').addEventListener('change', saveConfigPageState);
document.getElementById('tableBatchField').addEventListener('change', saveConfigPageState);
document.getElementById('batchSourceTable').addEventListener('change', () => {
  loadBatchSourceFields(document.getElementById('batchSourceTable').value, '')
    .then(saveConfigPageState)
    .catch(catchHintError('batchSourceHint'));
});
document.getElementById('batchSourceField').addEventListener('change', saveConfigPageState);
document.getElementById('tableSortBy').addEventListener('change', saveConfigPageState);
document.getElementById('tableSortDir').addEventListener('change', saveConfigPageState);
document.getElementById('tablePageSize').addEventListener('change', saveConfigPageState);
document.getElementById('pluginModule').addEventListener('change', () => {
  saveConfigPageState();
  loadPluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('pluginPageIndex').addEventListener('change', () => {
  saveConfigPageState();
  loadPluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('pluginBindGroup').addEventListener('change', () => {
  const group = document.getElementById('pluginBindGroup').value || '';
  saveConfigPageState();
  loadPluginWritebackSettingsForGroup(group).catch(catchHintError('pluginConfigHint'));
});

function buildTableListWritebackDefaults() {
  return {
    enabled: false,
    mode: 'cursor',
    batch_column: '',
    start_time_column: '',
    buffer_node: '',
    max_tables: 50,
    string_max_len: 80,
    advanced: {
      query_node: '',
      prev_page_node: '',
      next_page_node: '',
      batch_no_node: '',
      trigger_node: '',
    },
  };
}

function buildTableListAdvancedDefaults() {
  return {
    query_node: '',
    prev_page_node: '',
    next_page_node: '',
    batch_no_node: '',
    trigger_node: '',
  };
}

function updatePluginTableListModeUi() {
  const mode = document.getElementById('pluginTableListMode').value || 'cursor';
  const isAdvanced = mode === 'advanced';
  const advancedPanel = document.getElementById('pluginTableListAdvancedFields');
  const cursorFields = document.getElementById('pluginTableListCursorFields');
  const batchSel = document.getElementById('pluginTableListBatchColumn');
  const startSel = document.getElementById('pluginTableListStartTimeColumn');
  const cursorHint = document.getElementById('pluginTableListCursorHint');
  if (advancedPanel) {
    advancedPanel.hidden = !isAdvanced;
  }
  if (cursorFields) {
    cursorFields.classList.toggle('fields-locked', isAdvanced);
  }
  for (const el of [batchSel, startSel]) {
    if (el) {
      el.disabled = isAdvanced;
    }
  }
  if (cursorHint) {
    cursorHint.textContent = isAdvanced
      ? 'OPC UA 模式已锁定，不可编辑'
      : '可选；不选则按批次号反查主表';
  }
  const hint = document.getElementById('pluginTableListWritebackHint');
  if (hint && isAdvanced) {
    hint.textContent = 'OPC UA 模式：批次号来自 PLC 节点，开批时间由主表反查。';
    hint.className = 'muted ok';
  }
}

function resolveBatchColumnForAdvancedMode(batchColumnFromUi) {
  const fromUi = String(batchColumnFromUi || '').trim();
  if (fromUi) {
    return fromUi;
  }
  const cached = window.__pluginTableListAdvanced?.batch_column;
  if (cached) {
    return String(cached).trim();
  }
  const writeback = collectPluginOpcuaWriteback();
  const preferred = ['strBatchCode', 'Batch', 'batch_code', 'code'];
  if (writeback?.columns) {
    for (const name of preferred) {
      if (writeback.columns[name]) {
        return name;
      }
    }
    const keys = Object.keys(writeback.columns);
    if (keys.length === 1) {
      return keys[0];
    }
  }
  return '';
}

function mergeTableListAdvancedFields(baseCfg, existingCfg) {
  const merged = { ...baseCfg };
  if (!existingCfg || typeof existingCfg !== 'object') {
    return merged;
  }
  // mode / advanced 以表单为准，不得从缓存覆盖（否则切换高级模式后仍会保存为 cursor）
  for (const key of ['max_tables', 'string_max_len', 'lookup_start_time_column', 'batch_master_table']) {
    if (existingCfg[key] !== undefined && existingCfg[key] !== null && existingCfg[key] !== '') {
      merged[key] = existingCfg[key];
    }
  }
  return merged;
}

function inferTableListWritebackMode(cfg) {
  if (!cfg || typeof cfg !== 'object') {
    return 'cursor';
  }
  const mode = String(cfg.mode || '').trim().toLowerCase();
  if (mode === 'advanced' || mode === 'opcua') {
    return 'advanced';
  }
  const advanced = cfg.advanced && typeof cfg.advanced === 'object' ? cfg.advanced : {};
  if (
    String(advanced.query_node || '').trim() ||
    String(advanced.prev_page_node || '').trim() ||
    String(advanced.next_page_node || '').trim() ||
    (String(advanced.trigger_node || '').trim() && String(advanced.batch_no_node || '').trim())
  ) {
    return 'advanced';
  }
  return 'cursor';
}

function syncPluginTableListAdvancedCache() {
  const payload = collectPluginTableListWriteback();
  if (!payload) {
    window.__pluginTableListAdvanced = null;
    pluginTableListWritebackDraft = null;
    return;
  }
  const cached = { ...payload };
  delete cached._invalid;
  delete cached._invalidReason;
  window.__pluginTableListAdvanced = cached;
  pluginTableListWritebackDraft = JSON.parse(JSON.stringify(cached));
}

function renderPluginTableListColumnOptions(columns, tableListCfg) {
  const batchSel = document.getElementById('pluginTableListBatchColumn');
  const startSel = document.getElementById('pluginTableListStartTimeColumn');
  const previousBatch = batchSel.value;
  const previousStart = startSel.value;
  batchSel.innerHTML = '';
  startSel.innerHTML = '';
  appendOption(batchSel, '');
  appendOption(startSel, '');
  for (const col of columns) {
    appendOption(batchSel, col.name);
    appendOption(startSel, col.name);
  }
  if (tableListCfg?.batch_column && hasOption(batchSel, tableListCfg.batch_column)) {
    batchSel.value = tableListCfg.batch_column;
  } else if (previousBatch && hasOption(batchSel, previousBatch)) {
    batchSel.value = previousBatch;
  }
  if (tableListCfg?.start_time_column && hasOption(startSel, tableListCfg.start_time_column)) {
    startSel.value = tableListCfg.start_time_column;
  } else if (previousStart && hasOption(startSel, previousStart)) {
    startSel.value = previousStart;
  }
}

async function refreshPluginTableListColumnOptions(tableListCfg) {
  const hint = document.getElementById('pluginTableListWritebackHint');
  const mode = document.getElementById('pluginTableListMode').value || 'cursor';
  const isAdvanced = mode === 'advanced';
  const viewName = document.getElementById('pluginViewName').value || 'table';
  const bindGroup = document.getElementById('pluginBindGroup').value || '';
  if (!bindGroup) {
    renderPluginTableListColumnOptions([], tableListCfg || {});
    hint.textContent = '请先选择 bind_group；批次列来自该 group 在「Group 与列」中的配置。';
    hint.className = 'muted warn';
    updatePluginTableListModeUi();
    return [];
  }
  try {
    const cfg = await fetchJson(
      '/api/config/query-group?view_name=' +
        encodeURIComponent(viewName) +
        '&group=' +
        encodeURIComponent(bindGroup),
    );
    const columns = cfg.columns || [];
    renderPluginTableListColumnOptions(columns, tableListCfg || collectPluginTableListWriteback());
    if (columns.length === 0) {
      hint.textContent = `view=${viewName}, group=${bindGroup} 尚未配置列。`;
      hint.className = 'muted warn';
    } else if (isAdvanced) {
      hint.textContent = 'OPC UA 模式：批次号来自 PLC 节点，开批时间由主表反查。';
      hint.className = 'muted ok';
    } else {
      hint.textContent = `已加载 ${columns.length} 列，可配置批次列与开批时间列。`;
      hint.className = 'muted ok';
    }
    updatePluginTableListModeUi();
    return columns;
  } catch (e) {
    hint.textContent = `无法加载 group 列配置：${e.message || e}`;
    hint.className = 'muted warn';
    return [];
  }
}

function collectPluginTableListWriteback() {
  const enabled = document.getElementById('pluginTableListEnabled').checked;
  const mode = document.getElementById('pluginTableListMode').value || 'cursor';
  const bufferNode = document.getElementById('pluginTableListBufferNode').value.trim();
  let batchColumn = '';
  let startTimeColumn = '';
  if (mode === 'advanced') {
    batchColumn = resolveBatchColumnForAdvancedMode('');
    startTimeColumn = '';
  } else {
    batchColumn = document.getElementById('pluginTableListBatchColumn').value.trim();
    startTimeColumn = document.getElementById('pluginTableListStartTimeColumn').value.trim();
  }
  if (!enabled) {
    return null;
  }
  const payload = mergeTableListAdvancedFields(
    {
      enabled: true,
      mode,
      batch_column: batchColumn,
      start_time_column: startTimeColumn,
      buffer_node: bufferNode,
      max_tables: 50,
      string_max_len: 80,
    },
    window.__pluginTableListAdvanced || null,
  );
  if (!payload.start_time_column) {
    delete payload.start_time_column;
  }
  payload.mode = mode;
  if (mode === 'advanced') {
    const advanced = {
      query_node: document.getElementById('pluginTableListQueryNode').value.trim(),
      prev_page_node: document.getElementById('pluginTableListPrevPageNode').value.trim(),
      next_page_node: document.getElementById('pluginTableListNextPageNode').value.trim(),
      batch_no_node: document.getElementById('pluginTableListBatchNoNode').value.trim(),
      trigger_node: document.getElementById('pluginTableListTriggerNode').value.trim(),
    };
    payload.advanced = advanced;
    const hasSnapshotQueryNode = Boolean(advanced.query_node);
    const hasPaginationNode = Boolean(advanced.prev_page_node || advanced.next_page_node);
    const hasBatchNode = Boolean(advanced.batch_no_node);
    const hasTriggerNode = Boolean(advanced.trigger_node);
    if (!hasSnapshotQueryNode && !hasPaginationNode && !hasBatchNode && !hasTriggerNode) {
      return { ...payload, _invalid: true, _invalidReason: 'advanced_empty' };
    }
    if (hasBatchNode !== hasTriggerNode) {
      return { ...payload, _invalid: true, _invalidReason: 'advanced_batch_pair' };
    }
    if (hasBatchNode && (!payload.batch_column || !payload.buffer_node)) {
      return {
        ...payload,
        _invalid: true,
        _invalidReason: !payload.batch_column ? 'advanced_batch_column' : 'advanced_buffer',
      };
    }
  } else {
    delete payload.advanced;
    if (!payload.batch_column || !payload.buffer_node) {
      return { ...payload, _invalid: true, _invalidReason: 'basic_fields' };
    }
  }
  return payload;
}

function renderPluginTableListWritebackEditor(existingCfg) {
  const payload = collectPluginTableListWriteback();
  const preview = payload ? { ...payload } : buildTableListWritebackDefaults();
  delete preview._invalid;
  document.getElementById('pluginTableListJson').value = JSON.stringify(preview, null, 2);
}

function loadPluginTableListWritebackForm(tableListCfg) {
  const cfg = tableListCfg && typeof tableListCfg === 'object' ? tableListCfg : {};
  const resolvedMode = inferTableListWritebackMode(cfg);
  window.__pluginTableListAdvanced = { ...cfg, mode: resolvedMode };
  pluginTableListWritebackDraft = JSON.parse(JSON.stringify({ ...cfg, mode: resolvedMode }));
  document.getElementById('pluginTableListEnabled').checked = cfg.enabled === true;
  document.getElementById('pluginTableListMode').value = resolvedMode;
  document.getElementById('pluginTableListBufferNode').value = cfg.buffer_node || '';
  const advanced = cfg.advanced && typeof cfg.advanced === 'object' ? cfg.advanced : {};
  document.getElementById('pluginTableListQueryNode').value = advanced.query_node || '';
  document.getElementById('pluginTableListPrevPageNode').value = advanced.prev_page_node || '';
  document.getElementById('pluginTableListNextPageNode').value = advanced.next_page_node || '';
  document.getElementById('pluginTableListBatchNoNode').value = advanced.batch_no_node || '';
  document.getElementById('pluginTableListTriggerNode').value = advanced.trigger_node || '';
  updatePluginTableListModeUi();
  renderPluginTableListWritebackEditor(cfg);
}

async function loadPluginConfig() {
  pluginConfigData = await fetchJson('/api/config/plugins');
  ensurePluginModuleAndPage(LOCKED_PLUGIN_MODULE, '1');
  lockSelect('pluginModule', LOCKED_PLUGIN_MODULE, LOCKED_PLUGIN_MODULE);
  await loadPluginPageConfig();
  saveConfigPageState();
}

function ensurePluginModuleAndPage(moduleName, pageIndex) {
  pluginConfigData.modules = pluginConfigData.modules || {};
  if (!pluginConfigData.modules[moduleName]) {
    pluginConfigData.modules[moduleName] = {
      title: moduleName,
      view_name: LOCKED_PLUGIN_VIEW,
      bind_group: '',
      page_size: 10,
      pages: { '1': {}, '2': {}, '3': {}, '4': {}, '5': {} },
    };
  }
  const moduleCfg = pluginConfigData.modules[moduleName];
  moduleCfg.pages = moduleCfg.pages || {};
  for (const idx of ['1', '2', '3', '4', '5']) {
    if (!moduleCfg.pages[idx] || typeof moduleCfg.pages[idx] !== 'object') {
      moduleCfg.pages[idx] = {};
    }
  }
  if (!moduleCfg.pages[pageIndex]) moduleCfg.pages[pageIndex] = {};
}

async function updatePluginGroupHint(group) {
  const hint = document.getElementById('pluginGroupHint');
  if (!group) {
    hint.textContent = '未选择 bind_group';
    hint.className = 'muted';
    return;
  }
  try {
    const data = await fetchJson('/api/meta/group-schema?group=' + encodeURIComponent(group));
    const tableCount = Array.isArray(data.tables) ? data.tables.length : 0;
    const baseline = data.baseline_table || '-';
    const fixedTables = (data.tables || []).filter(t => data.table_kinds?.[t] === 'fixed');
    const fixedHint = fixedTables.length > 0 ? `，含固定表 ${fixedTables.join('、')}` : '';
    if (data.consistent) {
      hint.textContent = `group=${group}，表数量=${tableCount}，基准表=${baseline}，结构一致${fixedHint}`;
      hint.className = 'muted ok';
    } else {
      hint.textContent = `group=${group}，表数量=${tableCount}，基准表=${baseline}，检测到结构不一致${fixedHint}`;
      hint.className = 'muted warn';
    }
  } catch (e) {
    hint.textContent = `group=${group} 信息读取失败：${e.message || e}`;
    hint.className = 'muted warn';
  }
}

function buildOpcuaWritebackPreview(writebackCfg) {
  return {
    cursor: writebackCfg?.cursor || '',
    columns: writebackCfg?.columns && typeof writebackCfg.columns === 'object' ? writebackCfg.columns : {},
  };
}

function renderPluginOpcuaColumnTable(columns, writebackCfg) {
  const table = document.getElementById('pluginOpcuaColumnTable');
  table.innerHTML = '';
  const boundColumns = writebackCfg?.columns && typeof writebackCfg.columns === 'object' ? writebackCfg.columns : {};
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>回写</th><th>字段名</th><th>显示名</th><th>OPC NodeId</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const col of columns) {
    const name = col.name;
    const labelZh = col.label_zh || col.label_en || name;
    const enabled = Object.prototype.hasOwnProperty.call(boundColumns, name);
    const nodeId = enabled ? boundColumns[name] : '';
    const tr = document.createElement('tr');
    tr.setAttribute('data-col', name);
    tr.innerHTML =
      `<td><input type="checkbox" class="opcua-col-enabled"${enabled ? ' checked' : ''} /></td>` +
      `<td>${name}</td>` +
      `<td>${labelZh}</td>` +
      `<td><input type="text" class="opcua-col-nodeid field-wide" value="${escapeHtmlAttr(nodeId)}" placeholder="ns=6;s=::DataRev:..." /></td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function escapeHtmlAttr(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function capturePluginOpcuaMappings() {
  const cursorEl = document.getElementById('pluginOpcuaCursor');
  const cursor = cursorEl ? cursorEl.value.trim() : '';
  const columns = {};
  const rows = document.querySelectorAll('#pluginOpcuaColumnTable tbody tr[data-col]');
  for (const tr of rows) {
    const col = tr.getAttribute('data-col');
    const enabled = tr.querySelector('.opcua-col-enabled')?.checked;
    const nodeId = tr.querySelector('.opcua-col-nodeid')?.value.trim() || '';
    columns[col] = { enabled: !!enabled, nodeId };
  }
  return { cursor, columns };
}

function updatePluginOpcuaWritebackDraft() {
  const captured = capturePluginOpcuaMappings();
  const draft = pluginOpcuaWritebackDraft && typeof pluginOpcuaWritebackDraft === 'object'
    ? pluginOpcuaWritebackDraft
    : { cursor: '', columns: {} };
  draft.cursor = captured.cursor || '';
  draft.columns = draft.columns && typeof draft.columns === 'object' ? draft.columns : {};
  for (const [name, info] of Object.entries(captured.columns || {})) {
    if (info.enabled && info.nodeId) {
      draft.columns[name] = info.nodeId;
    } else {
      delete draft.columns[name];
    }
  }
  pluginOpcuaWritebackDraft = draft;
  return JSON.parse(JSON.stringify(draft));
}

function mergeCapturedMappingsToWriteback(captured) {
  const columns = {};
  for (const [name, info] of Object.entries(captured.columns || {})) {
    if (info.enabled && info.nodeId) {
      columns[name] = info.nodeId;
    }
  }
  return {
    cursor: captured.cursor || '',
    columns,
  };
}

function collectPluginOpcuaWriteback() {
  const captured = capturePluginOpcuaMappings();
  const writeback = mergeCapturedMappingsToWriteback(captured);
  if (Object.keys(writeback.columns).length === 0) {
    return null;
  }
  if (!writeback.cursor) {
    delete writeback.cursor;
  }
  return writeback;
}

async function loadPluginOpcuaWritebackTable(viewName, bindGroup, writebackCfg) {
  const hint = document.getElementById('pluginOpcuaWritebackHint');
  const table = document.getElementById('pluginOpcuaColumnTable');
  const cursorEl = document.getElementById('pluginOpcuaCursor');
  table.innerHTML = '';
  cursorEl.value = writebackCfg?.cursor || '';

  if (!bindGroup) {
    hint.textContent = '请先选择 bind_group；列清单来自该 group 在「Group 与列」中的配置。';
    hint.className = 'muted warn';
    return;
  }

  try {
    const cfg = await fetchJson(
      '/api/config/query-group?view_name=' +
        encodeURIComponent(viewName) +
        '&group=' +
        encodeURIComponent(bindGroup),
    );
    const columns = cfg.columns || [];
    if (columns.length === 0) {
      hint.textContent = `view=${viewName}, group=${bindGroup} 尚未配置列，请先在「Group 与列」中保存该 group。`;
      hint.className = 'muted warn';
      return;
    }
    renderPluginOpcuaColumnTable(columns, writebackCfg);
    const boundCount = Object.keys(writebackCfg?.columns || {}).length;
    hint.textContent = `已加载 ${columns.length} 列，当前绑定 ${boundCount} 列回写。`;
    hint.className = 'muted ok';
  } catch (e) {
    hint.textContent = `无法加载 group 列配置：${e.message || e}`;
    hint.className = 'muted warn';
  }
}

async function refreshPluginOpcuaWritebackTable() {
  const writebackCfg = updatePluginOpcuaWritebackDraft();
  const viewName = document.getElementById('pluginViewName').value || 'table';
  const bindGroup = document.getElementById('pluginBindGroup').value || '';
  await loadPluginOpcuaWritebackTable(viewName, bindGroup, writebackCfg);
  renderPluginOpcuaFeedbackEditor();
}

async function loadPluginWritebackSettingsForGroup(bindGroup) {
  setConfigStatus(`正在读取 bind_group=${bindGroup || '-'} 的插件页设置…`, 'working');
  const opcuaDraft = updatePluginOpcuaWritebackDraft();
  syncPluginTableListAdvancedCache();
  const tableListDraft = pluginTableListWritebackDraft || buildTableListWritebackDefaults();
  const viewName = document.getElementById('pluginViewName').value || LOCKED_PLUGIN_VIEW;

  await updatePluginGroupHint(bindGroup);
  await loadPluginOpcuaWritebackTable(viewName, bindGroup, opcuaDraft);
  renderPluginOpcuaFeedbackEditor();
  loadPluginTableListWritebackForm(tableListDraft);
  await refreshPluginTableListColumnOptions(tableListDraft);

  const moduleName = document.getElementById('pluginModule').value || LOCKED_PLUGIN_MODULE;
  const pageIndex = document.getElementById('pluginPageIndex').value || '1';
  setConfigStatus(
    `读取成功：module=${moduleName}，page=${pageIndex}，bind_group=${bindGroup || '-'}；高级回写设置已完整载入`,
    'ok',
  );
}

function renderPluginOpcuaFeedbackEditor() {
  const writeback = collectPluginOpcuaWriteback();
  const payload = buildOpcuaWritebackPreview(writeback || { cursor: document.getElementById('pluginOpcuaCursor').value.trim(), columns: {} });
  document.getElementById('pluginOpcuaFeedbackJson').value = JSON.stringify(payload, null, 2);
  document.getElementById('pluginOpcuaFeedbackHint').textContent =
    '只读预览；保存当前页配置时以上方表单为准。';
}

async function loadPluginPageConfig() {
  const moduleName = document.getElementById('pluginModule').value || LOCKED_PLUGIN_MODULE;
  const pageIndex = document.getElementById('pluginPageIndex').value || '1';
  if (!moduleName) return;
  ensurePluginModuleAndPage(moduleName, pageIndex);
  const moduleCfg = pluginConfigData.modules[moduleName];
  const pageCfg = moduleCfg.pages[pageIndex] || {};

  const bindGroup = pageCfg.bind_group ?? moduleCfg.bind_group ?? '';
  document.getElementById('pluginEnabled').checked = pageCfg.enabled !== false;
  document.getElementById('pluginTitle').value = pageCfg.title ?? moduleCfg.title ?? `${moduleName}_${pageIndex}`;
  lockSelect('pluginViewName', LOCKED_PLUGIN_VIEW, LOCKED_PLUGIN_VIEW);
  document.getElementById('pluginPageSize').value = Number(pageCfg.page_size ?? moduleCfg.page_size ?? 10);
  document.getElementById('pluginBindGroup').value = bindGroup;
  await updatePluginGroupHint(bindGroup);

  const viewName = LOCKED_PLUGIN_VIEW;
  const writebackCfg = pageCfg.opcua_writeback || { cursor: '', columns: {} };
  pluginOpcuaWritebackDraft = JSON.parse(JSON.stringify(writebackCfg));
  await loadPluginOpcuaWritebackTable(viewName, bindGroup, writebackCfg);
  renderPluginOpcuaFeedbackEditor();

  const tableListCfg = pageCfg.table_list_writeback || buildTableListWritebackDefaults();
  loadPluginTableListWritebackForm(tableListCfg);
  await refreshPluginTableListColumnOptions(tableListCfg);

  document.getElementById('pluginConfigHint').textContent =
    `当前编辑: module=${moduleName}, page=${pageIndex}`;
  setConfigStatus(
    `读取成功：module=${moduleName}，page=${pageIndex}，bind_group=${bindGroup || '-'}；插件页配置已完整载入`,
    'ok',
  );
}

async function savePluginPageConfig() {
  const moduleName = document.getElementById('pluginModule').value || LOCKED_PLUGIN_MODULE;
  const pageIndex = document.getElementById('pluginPageIndex').value || '1';
  if (!moduleName) {
    setHintMessage('pluginConfigHint', '请先选择模块');
    return;
  }
  ensurePluginModuleAndPage(moduleName, pageIndex);
  const moduleCfg = pluginConfigData.modules[moduleName];
  const pageCfg = moduleCfg.pages[pageIndex];

  pageCfg.enabled = document.getElementById('pluginEnabled').checked;
  pageCfg.title = document.getElementById('pluginTitle').value || `${moduleName}_${pageIndex}`;
  pageCfg.view_name = LOCKED_PLUGIN_VIEW;
  pageCfg.page_size = Number(document.getElementById('pluginPageSize').value || 10);
  pageCfg.bind_group = document.getElementById('pluginBindGroup').value || '';
  delete pageCfg.bind_table;

  const writeback = collectPluginOpcuaWriteback();
  if (writeback && Object.keys(writeback.columns).length > 0) {
    pageCfg.opcua_writeback = writeback;
  } else {
    delete pageCfg.opcua_writeback;
  }

  syncPluginTableListAdvancedCache();
  const tableListWriteback = collectPluginTableListWriteback();
  if (tableListWriteback && tableListWriteback.enabled) {
    if (tableListWriteback._invalid) {
      const reason =
        tableListWriteback._invalidReason === 'advanced_empty'
          ? '高级模式请至少填写上一页、下一页，或一组批次号/触发 NodeId'
          : tableListWriteback._invalidReason === 'advanced_batch_pair'
            ? '批次表名回写需同时填写批次号 NodeId 与触发 NodeId；仅使用翻页时两项均留空'
          : tableListWriteback._invalidReason === 'advanced_batch_column'
            ? '启用批次触发回写时需绑定批次字段；仅使用翻页时可将批次号与触发 NodeId 均留空'
            : tableListWriteback._invalidReason === 'advanced_buffer'
              ? '启用批次触发回写时需填写 Buffer NodeId；仅使用翻页时可留空'
              : '基础批次表名回写需填写批次列与 Buffer NodeId';
      setHintMessage('pluginConfigHint', reason);
      setConfigStatus(`保存未执行：${reason}`, 'warn');
      return;
    }
    delete tableListWriteback._invalid;
    delete tableListWriteback._invalidReason;
    pageCfg.table_list_writeback = tableListWriteback;
  } else {
    delete pageCfg.table_list_writeback;
  }

  moduleCfg.title = moduleCfg.title || moduleName;
  moduleCfg.view_name = LOCKED_PLUGIN_VIEW;
  moduleCfg.page_size = moduleCfg.page_size || pageCfg.page_size;
  moduleCfg.bind_group = moduleCfg.bind_group || pageCfg.bind_group;
  delete moduleCfg.bind_table;

  await fetchJson('/api/config/plugins', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pluginConfigData),
  });
  await updatePluginGroupHint(pageCfg.bind_group || '');
  document.getElementById('pluginConfigHint').textContent =
    `已保存: module=${moduleName}, page=${pageIndex}, bind_group=${pageCfg.bind_group || '-'}`;
  pluginOpcuaWritebackDraft = JSON.parse(JSON.stringify(pageCfg.opcua_writeback || { cursor: '', columns: {} }));
  pluginTableListWritebackDraft = pageCfg.table_list_writeback
    ? JSON.parse(JSON.stringify(pageCfg.table_list_writeback))
    : null;
  setConfigStatus(
    `保存成功：module=${moduleName}，page=${pageIndex}，bind_group=${pageCfg.bind_group || '-'}；OPC UA 翻页与可选批次回写设置已写入`,
    'ok',
  );
  saveConfigPageState();
}

document.getElementById('btnLoadPluginPage').addEventListener('click', () => {
  loadPluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('btnSavePluginPage').addEventListener('click', () => {
  savePluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('pluginEnabled').addEventListener('change', saveConfigPageState);
document.getElementById('pluginTitle').addEventListener('input', saveConfigPageState);
document.getElementById('pluginViewName').addEventListener('change', () => {
  refreshPluginOpcuaWritebackTable().catch(() => {});
  refreshPluginTableListColumnOptions().catch(() => {});
  saveConfigPageState();
});
document.getElementById('pluginPageSize').addEventListener('change', saveConfigPageState);
document.getElementById('pluginTableListEnabled').addEventListener('change', () => {
  renderPluginTableListWritebackEditor();
  saveConfigPageState();
});
document.getElementById('pluginTableListBatchColumn').addEventListener('change', () => {
  renderPluginTableListWritebackEditor();
  saveConfigPageState();
});
document.getElementById('pluginTableListStartTimeColumn').addEventListener('change', () => {
  renderPluginTableListWritebackEditor();
  saveConfigPageState();
});
document.getElementById('pluginTableListBufferNode').addEventListener('input', () => {
  renderPluginTableListWritebackEditor();
  saveConfigPageState();
});
document.getElementById('pluginTableListMode').addEventListener('change', () => {
  updatePluginTableListModeUi();
  syncPluginTableListAdvancedCache();
  refreshPluginTableListColumnOptions().catch(() => {});
  renderPluginTableListWritebackEditor();
  saveConfigPageState();
});
for (const id of [
  'pluginTableListPrevPageNode',
  'pluginTableListNextPageNode',
  'pluginTableListBatchNoNode',
  'pluginTableListTriggerNode',
]) {
  document.getElementById(id).addEventListener('input', () => {
    renderPluginTableListWritebackEditor();
    saveConfigPageState();
  });
}
document.getElementById('pluginTableListAdvanced').addEventListener('toggle', event => {
  if (event.target.open) {
    renderPluginTableListWritebackEditor();
  }
});
document.getElementById('pluginOpcuaCursor').addEventListener('input', () => {
  renderPluginOpcuaFeedbackEditor();
  saveConfigPageState();
});
document.getElementById('pluginOpcuaAdvanced').addEventListener('toggle', event => {
  if (event.target.open) {
    renderPluginOpcuaFeedbackEditor();
  }
});
document.getElementById('pluginOpcuaColumnTable').addEventListener('change', event => {
  if (event.target.matches('.opcua-col-enabled, .opcua-col-nodeid')) {
    renderPluginOpcuaFeedbackEditor();
    saveConfigPageState();
  }
});
document.getElementById('pluginOpcuaColumnTable').addEventListener('input', event => {
  if (event.target.matches('.opcua-col-nodeid')) {
    renderPluginOpcuaFeedbackEditor();
    saveConfigPageState();
  }
});

async function initConfigPage() {
  initializeLockedControls();
  await loadConfigProfiles();
  await loadAppSettings();
  await loadViews();
  await loadGroups();
  enableButtonClickFeedback();
  await loadPluginConfig().catch(() => {});
  await restoreConfigPageState();
}

initConfigPage().catch(catchHintError('configProfileHint'));
