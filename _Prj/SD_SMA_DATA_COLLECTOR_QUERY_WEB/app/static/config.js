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
let currentProfile = null;
let loadedGroup = '';
let loadedConditionGroup = '';
let loadedPluginPage = '1';
let conditionsDirty = false;
let opcuaNodes = [];

function syncOpcuaNodesFromDom() {
  const host = document.getElementById('opcuaNodeList');
  if (!host) return opcuaNodes;
  const rows = [];
  for (const wrap of host.querySelectorAll('[data-node-index]')) {
    rows.push({
      name: wrap.querySelector('[data-field="name"]').value.trim(),
      node_id: wrap.querySelector('[data-field="node_id"]').value.trim(),
    });
  }
  opcuaNodes = rows;
  return opcuaNodes;
}

const OPCUA_NODE_INPUT = 'input.opcua-node-input';

function hideOpcuaNodePicker() {
  const picker = document.getElementById('opcuaNodePicker');
  if (picker) picker.hidden = true;
}

function showOpcuaNodePicker(input, filterText) {
  const picker = document.getElementById('opcuaNodePicker');
  if (!picker || !input) return;
  const query = String(filterText || '').trim().toLowerCase();
  const rows = opcuaNodes.filter(row => {
    if (!row.node_id) return false;
    if (!query) return true;
    return row.name.toLowerCase().includes(query) || row.node_id.toLowerCase().includes(query);
  });
  picker.innerHTML = '';
  if (!rows.length) {
    picker.hidden = true;
    return;
  }
  for (const row of rows) {
    const button = document.createElement('button');
    button.type = 'button';
    const name = document.createElement('span');
    name.className = 'opcua-node-name';
    name.textContent = row.name || row.node_id;
    const nodeId = document.createElement('span');
    nodeId.className = 'opcua-node-id';
    nodeId.textContent = row.node_id;
    button.append(name, nodeId);
    button.addEventListener('mousedown', event => {
      event.preventDefault();
      input.value = row.node_id;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      hideOpcuaNodePicker();
    });
    picker.appendChild(button);
  }
  picker.hidden = false;
  const rect = input.getBoundingClientRect();
  const width = Math.max(rect.width, 320);
  let left = rect.left;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - width);
  picker.style.width = `${width}px`;
  picker.style.left = `${left}px`;
  const height = picker.offsetHeight;
  const below = window.innerHeight - rect.bottom;
  const top = below >= height + 4 || below >= rect.top
    ? rect.bottom + 2
    : Math.max(8, rect.top - height - 2);
  picker.style.top = `${top}px`;
}

function renderOpcuaNodeList() {
  const host = document.getElementById('opcuaNodeList');
  if (!host) return;
  host.innerHTML = '';
  opcuaNodes.forEach((row, index) => {
    const wrap = document.createElement('div');
    wrap.className = 'row';
    wrap.dataset.nodeIndex = String(index);
    const nameLabel = document.createElement('label');
    nameLabel.textContent = '名称';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.dataset.field = 'name';
    nameInput.value = row.name || '';
    nameInput.placeholder = '例如心跳';
    const nodeLabel = document.createElement('label');
    nodeLabel.textContent = 'NodeId';
    const nodeInput = document.createElement('input');
    nodeInput.type = 'text';
    nodeInput.className = 'field-wide';
    nodeInput.dataset.field = 'node_id';
    nodeInput.value = row.node_id || '';
    nodeInput.placeholder = 'ns=6;s=::DataRev:bHeartBeat';
    const removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.textContent = '删除';
    removeButton.addEventListener('click', () => {
      syncOpcuaNodesFromDom();
      opcuaNodes.splice(index, 1);
      renderOpcuaNodeList();
    });
    wrap.append(nameLabel, nameInput, nodeLabel, nodeInput, removeButton);
    host.appendChild(wrap);
  });
  hideOpcuaNodePicker();
}

function addOpcuaNode(name, nodeId) {
  syncOpcuaNodesFromDom();
  const nextName = String(name || '').trim();
  const nextId = String(nodeId || '').trim();
  if (nextId && opcuaNodes.some(row => row.node_id === nextId)) {
    throw new Error(`点位已存在: ${nextId}`);
  }
  if (nextName && opcuaNodes.some(row => row.name === nextName)) {
    throw new Error(`点位名称已存在: ${nextName}`);
  }
  opcuaNodes.push({ name: nextName, node_id: nextId });
  renderOpcuaNodeList();
}

function renderOpcuaBrowseNodes(items, container) {
  container.innerHTML = '';
  for (const item of items) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = `${item.display_name || item.node_id} | ${item.node_class || ''} | ${item.node_id}`;
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'node-button';
    addButton.textContent = '加入点位';
    addButton.addEventListener('click', () => {
      try {
        addOpcuaNode(item.display_name || item.node_id, item.node_id);
        addButton.disabled = true;
        addButton.textContent = '已加入';
        setHintMessage('opcuaNodeHint', `已加入 ${item.display_name || item.node_id}`, 'ok');
      } catch (err) {
        setHintMessage('opcuaNodeHint', err.message || String(err));
      }
    });
    li.append(label, addButton);
    if (item.has_children) {
      const children = document.createElement('ul');
      children.className = 'opcua-browse-list';
      children.hidden = true;
      const expandButton = document.createElement('button');
      expandButton.type = 'button';
      expandButton.className = 'node-button';
      expandButton.textContent = '展开';
      expandButton.addEventListener('click', () => {
        if (!children.hidden) {
          children.hidden = true;
          expandButton.textContent = '展开';
          return;
        }
        const load = children.dataset.loaded
          ? Promise.resolve()
          : browseOpcuaNodes(item.node_id, children).then(() => {
            children.dataset.loaded = '1';
          });
        load.then(() => {
          children.hidden = false;
          expandButton.textContent = '收起';
        }).catch(err => setHintMessage('opcuaNodeHint', err.message || String(err)));
      });
      li.append(expandButton, children);
    }
    container.appendChild(li);
  }
  if (!items.length) {
    const li = document.createElement('li');
    li.textContent = '没有子节点';
    container.appendChild(li);
  }
}

async function browseOpcuaNodes(nodeId, container) {
  const payload = getOpcuaPayload();
  const data = await fetchJson('/api/config/opcua/browse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      endpoint_url: payload.endpoint_url,
      username: payload.username,
      password: payload.password,
      node_id: nodeId || '',
    }),
  });
  renderOpcuaBrowseNodes(Array.isArray(data.items) ? data.items : [], container);
}

let preservedBatchField = '';
let queryConditionRows = [];
let conditionSourceTables = [];

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
    nodes: syncOpcuaNodesFromDom(),
  };
}

function clampSqlWritebackMaxLen(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 80;
  return Math.max(1, Math.min(4096, Math.round(n)));
}

function loadPluginSqlWritebackForm(cfg) {
  const data = cfg && typeof cfg === 'object' ? cfg : {};
  document.getElementById('pluginSqlWritebackEnabled').checked = data.enabled === true;
  document.getElementById('pluginSqlWritebackNode').value = data.node || '';
  document.getElementById('pluginSqlWritebackMaxLen').value = clampSqlWritebackMaxLen(data.max_len ?? 80);
}

function collectPluginSqlWriteback() {
  const enabled = document.getElementById('pluginSqlWritebackEnabled').checked;
  if (!enabled) return null;
  const node = document.getElementById('pluginSqlWritebackNode').value.trim();
  if (!node) return { _invalid: true };
  return {
    enabled: true,
    node,
    max_len: clampSqlWritebackMaxLen(document.getElementById('pluginSqlWritebackMaxLen').value),
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
  opcuaNodes = (Array.isArray(settings.nodes) ? settings.nodes : []).map(item => ({
    name: item.name || '',
    node_id: item.node_id || '',
  }));
  renderOpcuaNodeList();
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

function populateFieldSelectors(columns, preferredTimeField, preferredSortBy) {
  const normalizedColumns = Array.isArray(columns) ? columns : [];
  const timeField = document.getElementById('tableTimeField');
  const sortBy = document.getElementById('tableSortBy');
  timeField.innerHTML = '';
  sortBy.innerHTML = '';

  for (const column of normalizedColumns) {
    appendOption(timeField, column);
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
  refreshConditionBindOptions();
}

async function loadColumnsForTable(tableName, preferredTimeField, preferredSortBy) {
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
  populateFieldSelectors(availableColumns, preferredTimeField, preferredSortBy);
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
  await ensureConditionSourceTables();
}

function readConditionRowsFromDom() {
  const host = document.getElementById('queryConditionList');
  if (!host) return queryConditionRows;
  const rows = [];
  for (const wrap of host.querySelectorAll('[data-condition-index]')) {
    rows.push({
      method: wrap.querySelector('[data-field="method"]').value,
      name: wrap.querySelector('[data-field="name"]').value.trim(),
      source_table: wrap.querySelector('[data-field="source_table"]').value || '',
      source_field: wrap.querySelector('[data-field="source_field"]').value || '',
      bind_field: wrap.querySelector('[data-field="bind_field"]').value || '',
    });
  }
  queryConditionRows = rows;
  return rows;
}

function fillConditionSelect(select, values, preferred, emptyLabel, missingLabel) {
  select.innerHTML = '';
  appendOption(select, '', emptyLabel);
  const known = Array.isArray(values) ? values : [];
  for (const value of known) appendOption(select, value);
  if (preferred && !known.includes(preferred)) {
    appendOption(select, preferred, missingLabel ? `${preferred}（${missingLabel}）` : preferred);
  }
  select.value = preferred && Array.from(select.options).some(option => option.value === preferred) ? preferred : '';
}

function refreshConditionBindOptions() {
  const host = document.getElementById('queryConditionList');
  if (!host) return;
  for (const wrap of host.querySelectorAll('[data-condition-index]')) {
    const select = wrap.querySelector('[data-field="bind_field"]');
    const preferred = select.value || '';
    fillConditionSelect(select, availableColumns, preferred, '绑定列', '当前基准表没有此列');
  }
}

async function loadConditionSourceFields(tableName, fieldSelect, preferredField) {
  fillConditionSelect(fieldSelect, [], preferredField, '来源字段');
  if (!tableName) return;
  try {
    const meta = await fetchJson('/api/meta/columns?table=' + encodeURIComponent(tableName));
    fillConditionSelect(fieldSelect, meta.columns || [], preferredField, '来源字段', '来源表中不存在');
  } catch (_) {
    fillConditionSelect(fieldSelect, [], preferredField, '来源字段', '来源表暂不可读');
  }
}

function renderQueryConditionEditor() {
  const host = document.getElementById('queryConditionList');
  host.innerHTML = '';
  queryConditionRows.forEach((row, index) => {
    const wrap = document.createElement('div');
    wrap.className = 'row';
    wrap.dataset.conditionIndex = String(index);

    const nameLabel = document.createElement('label');
    nameLabel.textContent = '名称';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.dataset.field = 'name';
    nameInput.value = row.name || '';
    nameInput.placeholder = '例如批次号';

    const methodLabel = document.createElement('label');
    methodLabel.textContent = SdI18n.t('query.condition.method');
    const methodSelect = document.createElement('select');
    methodSelect.dataset.field = 'method';
    appendOption(methodSelect, 'eq', SdI18n.t('query.condition.exact'));
    appendOption(methodSelect, 'contains', SdI18n.t('query.condition.contains'));
    methodSelect.value = row.method || 'eq';

    const tableLabel = document.createElement('label');
    tableLabel.textContent = '来源表';
    const tableSelect = document.createElement('select');
    tableSelect.dataset.field = 'source_table';
    fillConditionSelect(
      tableSelect,
      conditionSourceTables,
      row.source_table || '',
      '来源表',
      '当前数据库不存在',
    );

    const fieldLabel = document.createElement('label');
    fieldLabel.textContent = '来源字段';
    const fieldSelect = document.createElement('select');
    fieldSelect.dataset.field = 'source_field';

    const bindLabel = document.createElement('label');
    bindLabel.textContent = '绑定列';
    const bindSelect = document.createElement('select');
    bindSelect.dataset.field = 'bind_field';
    fillConditionSelect(bindSelect, availableColumns, row.bind_field || '', '绑定列', '当前基准表没有此列');

    const removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.textContent = '删除';
    removeButton.addEventListener('click', () => {
      conditionsDirty = true;
      readConditionRowsFromDom();
      queryConditionRows.splice(index, 1);
      renderQueryConditionEditor();
    });

    tableSelect.addEventListener('change', () => {
      loadConditionSourceFields(tableSelect.value, fieldSelect, '').catch(catchHintError('queryConditionHint'));
    });

    const updateMethod = () => {
      const contains = methodSelect.value === "contains";
      tableSelect.disabled = fieldSelect.disabled = contains;
      if (contains) { tableSelect.value = ""; fieldSelect.value = ""; }
    };
    methodSelect.addEventListener("change", updateMethod);
    updateMethod();
    wrap.append(nameLabel, nameInput, methodLabel, methodSelect, tableLabel, tableSelect, fieldLabel, fieldSelect, bindLabel, bindSelect, removeButton);
    host.appendChild(wrap);
    loadConditionSourceFields(row.source_table || '', fieldSelect, row.source_field || '')
      .catch(catchHintError('queryConditionHint'));
  });
}

function cloneConditionRows(rows) {
  return (Array.isArray(rows) ? rows : []).map(item => ({
    method: item.method || 'eq',
    name: item.name || '',
    source_table: item.source_table || '',
    source_field: item.source_field || '',
    bind_field: item.bind_field || '',
  }));
}

function resolveProfileConditions(viewName, group) {
  const view = currentProfile?.query_view?.views?.[viewName] || {};
  const groupCfg = group && view.per_group ? view.per_group[group] : null;
  if (groupCfg && Array.isArray(groupCfg.query_conditions)) {
    return { conditions: cloneConditionRows(groupCfg.query_conditions), scope: 'group' };
  }
  let source = view.batch_source && typeof view.batch_source === 'object' ? view.batch_source : {};
  let bindField = String(view.batch_field || '');
  if (groupCfg && typeof groupCfg === 'object') {
    if (Object.prototype.hasOwnProperty.call(groupCfg, 'batch_source') && groupCfg.batch_source && typeof groupCfg.batch_source === 'object') {
      source = groupCfg.batch_source;
    }
    if (Object.prototype.hasOwnProperty.call(groupCfg, 'batch_field')) {
      bindField = String(groupCfg.batch_field || '');
    }
  }
  const sourceTable = String(source.table || '').trim();
  const sourceField = String(source.field || '').trim();
  if (!sourceTable || !sourceField) {
    return { conditions: [], scope: 'legacy' };
  }
  return {
    conditions: [{
      name: '批次号',
      source_table: sourceTable,
      source_field: sourceField,
      bind_field: bindField,
    }],
    scope: 'legacy',
  };
}

async function ensureConditionSourceTables() {
  if (conditionSourceTables.length > 0) return;
  const tableData = await fetchJson('/api/meta/database-tables');
  conditionSourceTables = Array.isArray(tableData.tables) ? tableData.tables : [];
}

function clearQueryConditionEditor() {
  queryConditionRows = [];
  conditionsDirty = false;
  loadedConditionGroup = '';
  const host = document.getElementById('queryConditionList');
  if (host) host.innerHTML = '';
  const hint = document.getElementById('queryConditionHint');
  if (hint) {
    hint.textContent = '';
    hint.className = 'muted';
  }
}

async function showQueryConditionsForGroup(group) {
  if (!group) {
    clearQueryConditionEditor();
    return;
  }
  await ensureConditionSourceTables();
  const viewName = document.getElementById('editViewName').value || LOCKED_GROUP_VIEW;
  const resolved = resolveProfileConditions(viewName, group);
  queryConditionRows = resolved.conditions;
  conditionsDirty = false;
  loadedConditionGroup = group;
  renderQueryConditionEditor();
  const hint = document.getElementById('queryConditionHint');
  const count = queryConditionRows.length;
  if (resolved.scope === 'group') {
    hint.textContent = `分组 ${group} 已保存 ${count} 条查询条件`;
    hint.className = 'muted ok';
  } else if (count) {
    hint.textContent = `分组 ${group} 尚未保存查询条件列表，当前显示由原批次号来源生成。保存后以本列表为准。`;
    hint.className = 'muted warn';
  } else {
    hint.textContent = `分组 ${group} 尚未保存查询条件。`;
    hint.className = 'muted warn';
  }
}

function applyConditionsToProfile() {
  if (!conditionsDirty || !currentProfile || !loadedConditionGroup) return;
  const viewName = document.getElementById('editViewName').value || LOCKED_GROUP_VIEW;
  const conditions = readConditionRowsFromDom();
  for (const [index, row] of conditions.entries()) {
    if (!row.name || !row.bind_field || (row.method !== "contains" && (!row.source_table || !row.source_field))) {
      activateConfigTab('groups');
      throw new Error(`分组 ${loadedConditionGroup} 的第 ${index + 1} 条查询条件需要名称、来源表、来源字段和绑定列`);
    }
  }
  const view = ensureProfileView(viewName);
  if (!view.per_group[loadedConditionGroup] || typeof view.per_group[loadedConditionGroup] !== 'object') {
    view.per_group[loadedConditionGroup] = {};
  }
  view.per_group[loadedConditionGroup].query_conditions = conditions;
  queryViews[viewName] = view;
}

async function loadWorkingProfile() {
  currentProfile = await fetchJson('/api/config/profile');
  currentProfile.query_view = currentProfile.query_view || { views: {}, group_baselines: {} };
  currentProfile.query_view.views = currentProfile.query_view.views || {};
  currentProfile.query_view.group_baselines = currentProfile.query_view.group_baselines || {};
  currentProfile.plugins = currentProfile.plugins || { modules: {} };
  currentProfile.plugins.modules = currentProfile.plugins.modules || {};
  stripViewLevelQueryConditions(currentProfile);
  pluginConfigData = currentProfile.plugins;
  fillAppSettingsForm(currentProfile.app_settings || {});
  fillOpcuaForm(currentProfile.opcua || {});
}

function stripViewLevelQueryConditions(profile) {
  const views = profile?.query_view?.views;
  if (!views || typeof views !== 'object') return;
  for (const view of Object.values(views)) {
    if (view && typeof view === 'object') delete view.query_conditions;
  }
}

function ensureProfileView(viewName) {
  const views = currentProfile.query_view.views;
  if (!views[viewName] || typeof views[viewName] !== 'object') {
    views[viewName] = { per_group: {} };
  }
  if (!views[viewName].per_group || typeof views[viewName].per_group !== 'object') {
    views[viewName].per_group = {};
  }
  return views[viewName];
}

function groupColumnDefsFromProfile(viewName, group) {
  const view = currentProfile?.query_view?.views?.[viewName];
  const groupCfg = view?.per_group?.[group];
  return Array.isArray(groupCfg?.columns) ? groupCfg.columns : [];
}

async function loadAppSettings() {
  if (!currentProfile) await loadWorkingProfile();
  fillAppSettingsForm(currentProfile.app_settings || {});
  fillOpcuaForm(currentProfile.opcua || {});
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
  document.getElementById('configProfileHint').textContent =
    currentConfigProfile ? `当前文件: ${currentConfigProfile}` : '未找到可用 config';
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
  currentProfile = null;
  loadedGroup = '';
  loadedConditionGroup = '';
  conditionsDirty = false;
  await loadConfigProfiles();
  await loadWorkingProfile();
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
  document.getElementById('tableSortBy').innerHTML = '';
  document.getElementById('schemaHint').textContent = '当前数据库下没有可用 group，请先检查连接设置。';
  document.getElementById('schemaHint').className = 'muted warn';
  document.getElementById('columnEditorHint').textContent = '';
  renderOrderedColumns();
  clearQueryConditionEditor();
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

function describePrune(prune) {
  const parts = [];
  if (prune?.removed_groups?.length) {
    parts.push(`已去掉当前库不存在的分组 ${prune.removed_groups.join('、')}`);
  }
  if (prune?.removed_plugin_modules?.length) {
    parts.push(`已去掉配置页不编辑的插件模块 ${prune.removed_plugin_modules.join('、')}`);
  }
  if (prune && prune.database_reached === false) {
    parts.push('数据库未连上，分组未按当前库清理');
  }
  return parts.join('；');
}

async function saveWorkingProfile(options = {}) {
  if (!currentProfile) await loadWorkingProfile();
  applyGroupEditorToProfile();
  applyPluginPageToProfile();
  applyConditionsToProfile();
  stripViewLevelQueryConditions(currentProfile);
  const result = await fetchJson('/api/config/profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      version: currentProfile.version || 1,
      name: currentProfile.name || '',
      app_settings: getAppSettingsPayload(),
      opcua: getOpcuaPayload(),
      query_view: currentProfile.query_view,
      plugins: currentProfile.plugins,
    }),
  });
  currentProfile = result.profile;
  currentProfile.query_view = currentProfile.query_view || { views: {}, group_baselines: {} };
  stripViewLevelQueryConditions(currentProfile);
  currentProfile.plugins = currentProfile.plugins || { modules: {} };
  pluginConfigData = currentProfile.plugins;
  conditionsDirty = false;
  fillAppSettingsForm(currentProfile.app_settings || {});
  fillOpcuaForm(currentProfile.opcua || {});
  if (options.refreshMetadata) {
    await refreshMetadataFromCurrentDatabase();
  }
  const pruneText = describePrune(result.prune);
  const baseMessage = options.successMessage || '整份配置已写入当前文件';
  const message = pruneText ? `${baseMessage}；${pruneText}` : baseMessage;
  const dbHint = document.getElementById('appSettingsHint');
  if (dbHint && options.refreshMetadata) dbHint.textContent = message;
  setConfigStatus(message, result.prune?.database_reached === false ? 'warn' : 'ok');
  return result;
}

async function connectDatabase() {
  document.getElementById('appSettingsHint').textContent = '正在把整份配置写入并连接数据库...';
  await saveWorkingProfile({
    refreshMetadata: true,
    successMessage: '整份配置已写入，数据库已连接，分组已按当前库刷新',
  });
  const check = await fetchJson('/api/db/check');
  document.getElementById('appSettingsHint').textContent =
    `数据库连接成功（${check.database || '-'}），分组已刷新`;
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
  if (sel.value) {
    await loadTableConfig();
  }
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

function applyGroupEditorToProfile() {
  if (!currentProfile || !loadedGroup) return;
  const viewName = document.getElementById('editViewName').value || LOCKED_GROUP_VIEW;
  const baselineTable = document.getElementById('editTableName').value;
  if (!viewName || !baselineTable) return;
  syncLabelInputsToState();
  const view = ensureProfileView(viewName);
  const previous = view.per_group[loadedGroup];
  const nextGroup = {
    columns: orderedColumns.map(name => ({
      name,
      label_en: (columnLabels[name] || {}).label_en || name,
      label_zh: (columnLabels[name] || {}).label_zh || name,
    })),
    time_field: document.getElementById('tableTimeField').value || 'collection_time',
    batch_field: preservedBatchField || '',
    sort_by: document.getElementById('tableSortBy').value || 'collection_time',
    sort_dir: document.getElementById('tableSortDir').value || 'desc',
    page_size: Number(document.getElementById('tablePageSize').value || 50),
  };
  if (previous && Array.isArray(previous.query_conditions)) {
    nextGroup.query_conditions = previous.query_conditions;
  }
  view.per_group[loadedGroup] = nextGroup;
  currentProfile.query_view.group_baselines[loadedGroup] = baselineTable;
}

async function loadTableConfig() {
  const viewName = document.getElementById('editViewName').value;
  const group = document.getElementById('editGroupName').value;
  const baselineTable = document.getElementById('editTableName').value;
  if (!viewName || !group || !baselineTable) return;

  await loadColumnsForTable(baselineTable, '', '');

  const stored = currentProfile?.query_view?.views?.[viewName]?.per_group?.[group] || {};
  const savedBaseline = currentProfile?.query_view?.group_baselines?.[group] || '';
  const cfg = {
    columns: Array.isArray(stored.columns) ? stored.columns : [],
    time_field: stored.time_field || 'collection_time',
    batch_field: stored.batch_field || '',
    sort_by: stored.sort_by || 'collection_time',
    sort_dir: stored.sort_dir || 'desc',
    page_size: stored.page_size || 50,
    baseline_table: savedBaseline,
  };

  orderedColumns = (cfg.columns || []).map(x => x.name);
  columnLabels = {};
  for (const c of cfg.columns || []) {
    columnLabels[c.name] = {
      label_en: c.label_en || c.name,
      label_zh: c.label_zh || c.name,
    };
  }

  preservedBatchField = cfg.batch_field || '';
  populateFieldSelectors(availableColumns, cfg.time_field, cfg.sort_by);
  document.getElementById('tableSortDir').value = cfg.sort_dir === 'asc' ? 'asc' : 'desc';
  document.getElementById('tablePageSize').value = Number(cfg.page_size || 50);
  renderOrderedColumns();
  if (cfg.baseline_table && availableColumns.length > 0) {
    const tableSel = document.getElementById('editTableName');
    if (Array.from(tableSel.options).some(x => x.value === cfg.baseline_table)) {
      tableSel.value = cfg.baseline_table;
    }
  }
  loadedGroup = group;
  await showQueryConditionsForGroup(group);
  document.getElementById('columnEditorHint').textContent =
    cfg.columns.length
      ? `当前编辑: view=${viewName}, group=${group}, 基准表=${baselineTable}, columns=${orderedColumns.length}`
      : `当前编辑: view=${viewName}, group=${group}，尚未选择列`;
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

  const view = ensureProfileView(viewName);
  const hadGroup = Boolean(view.per_group[group]);
  const hadBaseline = Boolean(currentProfile.query_view.group_baselines[group]);
  delete view.per_group[group];
  delete currentProfile.query_view.group_baselines[group];
  loadedGroup = '';
  await showQueryConditionsForGroup(group);
  orderedColumns = [];
  columnLabels = {};
  renderOrderedColumns();
  document.getElementById('columnEditorHint').textContent =
    hadGroup || hadBaseline
      ? `已从当前配置去掉 group=${group}，点上方保存后写入文件`
      : `当前配置里没有 group=${group}`;
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
document.getElementById('btnSaveProfile').addEventListener('click', () => {
  saveWorkingProfile().catch(err => {
    setConfigStatus(String(err.message || err), 'error');
    catchHintError('configProfileHint')(err);
  });
});
document.getElementById('btnConnectDatabase').addEventListener('click', () => {
  connectDatabase().catch(catchHintError('appSettingsHint'));
});
document.getElementById('btnTestOpcuaConnection').addEventListener('click', () => {
  testOpcuaConnection().catch(catchHintError('opcuaSettingsHint'));
});
document.getElementById('btnAddOpcuaNode').addEventListener('click', () => {
  addOpcuaNode('', '');
});
document.getElementById('opcuaNodeList').addEventListener('input', () => {
  syncOpcuaNodesFromDom();
});
document.addEventListener('focusin', event => {
  if (!event.target.matches(OPCUA_NODE_INPUT)) return;
  event.target.dataset.opcuaPickerFilter = '';
  showOpcuaNodePicker(event.target, '');
});
document.addEventListener('click', event => {
  if (!event.target.matches(OPCUA_NODE_INPUT)) return;
  event.target.dataset.opcuaPickerFilter = '';
  showOpcuaNodePicker(event.target, '');
});
document.addEventListener('input', event => {
  if (!event.target.matches(OPCUA_NODE_INPUT)) return;
  event.target.dataset.opcuaPickerFilter = event.target.value;
  showOpcuaNodePicker(event.target, event.target.value);
});
document.addEventListener('focusout', event => {
  if (event.target.matches(OPCUA_NODE_INPUT)) hideOpcuaNodePickerSoon();
});
let opcuaPickerHideTimer = 0;

function hideOpcuaNodePickerSoon() {
  clearTimeout(opcuaPickerHideTimer);
  opcuaPickerHideTimer = setTimeout(() => {
    const active = document.activeElement;
    if (active && active.matches && active.matches(OPCUA_NODE_INPUT)) return;
    hideOpcuaNodePicker();
  }, 150);
}
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') hideOpcuaNodePicker();
});
window.addEventListener('scroll', () => {
  const picker = document.getElementById('opcuaNodePicker');
  const active = document.activeElement;
  if (!picker || picker.hidden) return;
  if (active && active.matches && active.matches(OPCUA_NODE_INPUT)) {
    showOpcuaNodePicker(active, active.dataset.opcuaPickerFilter || '');
    return;
  }
  hideOpcuaNodePicker();
}, true);
window.addEventListener('resize', hideOpcuaNodePicker);
document.getElementById('btnBrowseOpcuaNodes').addEventListener('click', () => {
  const hint = document.getElementById('opcuaNodeHint');
  hint.textContent = '正在浏览 OPC UA 节点...';
  hint.className = 'muted';
  browseOpcuaNodes(
    document.getElementById('opcuaBrowseNodeId').value.trim(),
    document.getElementById('opcuaBrowseList'),
  ).then(() => {
    hint.textContent = '已加载节点，点「加入点位」后可在其它 NodeId 框中下拉选择';
    hint.className = 'muted ok';
  }).catch(err => setHintMessage('opcuaNodeHint', err.message || String(err)));
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
document.getElementById('btnAddQueryCondition').addEventListener('click', () => {
  conditionsDirty = true;
  readConditionRowsFromDom();
  queryConditionRows.push({ name: '', source_table: '', source_field: '', bind_field: '' });
  renderQueryConditionEditor();
});
document.getElementById('queryConditionList').addEventListener('input', () => {
  conditionsDirty = true;
});
document.getElementById('queryConditionList').addEventListener('change', () => {
  conditionsDirty = true;
});
document.getElementById('btnDeleteGroupConfig').addEventListener('click', () => {
  deleteGroupConfig().catch(catchHintError('columnEditorHint'));
});
document.getElementById('editGroupName').addEventListener('change', async () => {
  const select = document.getElementById('editGroupName');
  const previousGroup = loadedConditionGroup;
  try {
    applyGroupEditorToProfile();
    applyConditionsToProfile();
  } catch (err) {
    if (previousGroup && hasOption(select, previousGroup)) {
      select.value = previousGroup;
    }
    catchHintError('queryConditionHint')(err);
    return;
  }
  try {
    loadedGroup = '';
    await loadTables();
    if (!document.getElementById('editTableName').value) {
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
  ).catch(catchHintError('columnEditorHint'));
  saveConfigPageState();
});
document.getElementById('tableTimeField').addEventListener('change', saveConfigPageState);
document.getElementById('tableSortBy').addEventListener('change', saveConfigPageState);
document.getElementById('tableSortDir').addEventListener('change', saveConfigPageState);
document.getElementById('tablePageSize').addEventListener('change', saveConfigPageState);
document.getElementById('pluginModule').addEventListener('change', () => {
  saveConfigPageState();
  loadPluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('pluginPageIndex').addEventListener('change', () => {
  try {
    applyPluginPageToProfile();
  } catch (err) {
    catchHintError('pluginConfigHint')(err);
    return;
  }
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
    const columns = groupColumnDefsFromProfile(viewName, bindGroup);
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
  if (!currentProfile) await loadWorkingProfile();
  pluginConfigData = currentProfile.plugins;
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
      `<td><input type="text" class="opcua-col-nodeid opcua-node-input field-wide" value="${escapeHtmlAttr(nodeId)}" placeholder="ns=6;s=::DataRev:..." /></td>`;
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
    const columns = groupColumnDefsFromProfile(viewName, bindGroup);
    if (columns.length === 0) {
      hint.textContent = `view=${viewName}, group=${bindGroup} 尚未配置列。先在「分组与列」里选好列，再点上方保存。`;
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
  loadPluginSqlWritebackForm(pageCfg.sql_writeback);
  loadPluginTableListWritebackForm(tableListCfg);
  await refreshPluginTableListColumnOptions(tableListCfg);

  loadedPluginPage = pageIndex;
  document.getElementById('pluginConfigHint').textContent =
    `当前编辑: module=${moduleName}, page=${pageIndex}`;
  saveConfigPageState();
}

function applyPluginPageToProfile() {
  const moduleName = LOCKED_PLUGIN_MODULE;
  const pageIndex = loadedPluginPage || '1';
  if (!pluginConfigData) return;
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

  const sqlWriteback = collectPluginSqlWriteback();
  if (sqlWriteback && sqlWriteback._invalid) {
    document.getElementById('pluginPageIndex').value = pageIndex;
    activateConfigTab('plugins');
    throw new Error('启用查询 SQL 写回时需填写 SQL NodeId');
  }
  if (sqlWriteback) {
    pageCfg.sql_writeback = sqlWriteback;
  } else {
    delete pageCfg.sql_writeback;
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
      document.getElementById('pluginPageIndex').value = pageIndex;
      activateConfigTab('plugins');
      throw new Error(reason);
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
}

document.getElementById('btnLoadPluginPage').addEventListener('click', () => {
  loadPluginPageConfig().catch(catchHintError('pluginConfigHint'));
});
document.getElementById('pluginEnabled').addEventListener('change', saveConfigPageState);
document.getElementById('pluginTitle').addEventListener('input', saveConfigPageState);
document.getElementById('pluginViewName').addEventListener('change', () => {
  refreshPluginOpcuaWritebackTable().catch(() => {});
  refreshPluginTableListColumnOptions().catch(() => {});
  saveConfigPageState();
});
document.getElementById('pluginPageSize').addEventListener('change', saveConfigPageState);
document.getElementById('pluginSqlWritebackEnabled').addEventListener('change', saveConfigPageState);
document.getElementById('pluginSqlWritebackNode').addEventListener('input', saveConfigPageState);
document.getElementById('pluginSqlWritebackMaxLen').addEventListener('change', saveConfigPageState);
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

function activateConfigTab(name) {
  document.querySelectorAll('.config-editor .tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
  document.querySelectorAll('.config-editor .tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${name}`);
  });
}

async function initConfigPage() {
  initializeLockedControls();
  document.querySelectorAll('.config-editor .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateConfigTab(btn.dataset.tab));
  });
  await loadConfigProfiles();
  await loadWorkingProfile();
  await loadViews();
  await loadGroups();
  enableButtonClickFeedback();
  await loadPluginConfig().catch(() => {});
  await restoreConfigPageState();
  setConfigStatus(`已读取 ${currentConfigProfile || '配置文件'}`, 'ok');
}

initConfigPage().catch(catchHintError('configProfileHint'));
