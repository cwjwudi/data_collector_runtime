let queryViews = {};
let currentPage = 1;
let totalPages = 1;
let hasMore = false;
let currentCursor = null;
let nextCursor = null;
let cursorStack = [];
let lastQueryContext = null;
let lastResultData = null;
let pageSizeOverridden = false;
let batchCodesAvailable = false;
let currentBatchSource = {};
let timeRangePicker = null;
const QUERY_STATE_KEY = 'sd_sma_query_page_state_v1';
const quickRangePresets = {
  btnRange15Min: 'last15m',
  btnRange1H: 'last1h',
  btnRange8H: 'last8h',
  btnRangeToday: 'today',
  btnRangeYesterday: 'yesterday',
  btnRange1W: 'last1w',
  btnRange1M: 'last1m',
};
const quickRangeButtonIds = Object.keys(quickRangePresets);

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

function getQueryMode() {
  return document.querySelector('input[name="queryMode"]:checked')?.value || 'time';
}

function getConfiguredBatchField() {
  const viewName = document.getElementById('viewName').value;
  const group = document.getElementById('group').value;
  const groupConfig = queryViews[viewName]?.per_group?.[group];
  return String(groupConfig?.batch_field || '').trim();
}

function updateQueryModeControls() {
  const batchModeRadio = document.getElementById('queryModeBatch');
  const batchSupported = Boolean(getConfiguredBatchField()) && batchCodesAvailable;
  batchModeRadio.disabled = !batchSupported;
  if (!batchSupported && getQueryMode() === 'batch') {
    document.getElementById('queryModeTime').checked = true;
  }

  const batchMode = getQueryMode() === 'batch';
  document.getElementById('batchCode').disabled = !batchMode || !batchSupported;
  timeRangePicker?.setDisabled(batchMode);
  for (const id of quickRangeButtonIds) document.getElementById(id).disabled = batchMode;
  if (!batchMode) {
    document.getElementById('batchCode').value = '';
  }
}

async function loadBatchCodes() {
  const viewName = document.getElementById('viewName').value;
  const group = document.getElementById('group').value;
  const select = document.getElementById('batchCode');
  select.innerHTML = '';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '请选择批次号';
  select.appendChild(empty);
  batchCodesAvailable = false;
  currentBatchSource = {};
  if (!viewName || !group) return;

  const data = await fetchJson(
    '/api/meta/batch-codes?view_name=' + encodeURIComponent(viewName) +
      '&group=' + encodeURIComponent(group),
  );
  currentBatchSource = data.source || {};
  for (const batchCode of data.items || []) {
    const option = document.createElement('option');
    option.value = batchCode;
    option.textContent = batchCode;
    select.appendChild(option);
  }
  batchCodesAvailable = select.options.length > 1;
}

async function reloadBatchCodesForCurrentContext() {
  try {
    await loadBatchCodes();
    document.getElementById('queryWarnings').textContent = batchCodesAvailable
      ? `批次号来源：${currentBatchSource.table || '-'} . ${currentBatchSource.field || '-'}`
      : '批次号来源没有可选数据，仍可按时间查询';
  } catch (err) {
    batchCodesAvailable = false;
    currentBatchSource = {};
    document.getElementById('queryWarnings').textContent =
      `批次号列表不可用，仍可按时间查询：${err.message}`;
  }
  updateQueryModeControls();
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

function saveQueryPageState() {
  const state = {
    group: document.getElementById('group').value || '',
    table: document.getElementById('tableName').value || '',
    viewName: document.getElementById('viewName').value || '',
    labelLang: document.getElementById('labelLang').value || 'zh',
    pageSize: document.getElementById('pageSize').value || '10',
    pageSizeOverridden,
    startTime: document.getElementById('startTime').value || '',
    endTime: document.getElementById('endTime').value || '',
    quickActive: quickRangeButtonIds.find(
      id => document.getElementById(id)?.classList.contains('is-selected'),
    ) || '',
    queryMode: getQueryMode(),
    batchCode: document.getElementById('batchCode').value || '',
    pageNumber: document.getElementById('pageNumber').value || '1',
    currentPage,
    totalPages,
    hasMore,
    currentCursor,
    nextCursor,
    cursorStack,
    lastQueryContext,
    lastResultData,
  };
  safeStorageSet(QUERY_STATE_KEY, JSON.stringify(state));
}

function loadSavedQueryPageState() {
  const raw = safeStorageGet(QUERY_STATE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function applyQuickRange(preset, buttonId) {
  timeRangePicker.applyPreset(preset);
  setQuickRangeActive(buttonId);
  saveQueryPageState();
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

function setQuickRangeActive(buttonId) {
  for (const id of quickRangeButtonIds) {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('is-selected');
      el.setAttribute('aria-pressed', 'false');
    }
  }
  const active = document.getElementById(buttonId);
  if (active) {
    active.classList.add('is-selected');
    active.setAttribute('aria-pressed', 'true');
  }
}

function clearQuickRangeActive() {
  for (const id of quickRangeButtonIds) {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('is-selected');
      el.setAttribute('aria-pressed', 'false');
    }
  }
}

async function loadGroupSchemaHint() {
  const group = document.getElementById('group').value;
  if (!group) return;
  const data = await fetchJson('/api/meta/group-schema?group=' + encodeURIComponent(group));
  const hint = document.getElementById('schemaHint');
  if (data.consistent) {
    hint.textContent = `group=${group} 已配置，可查询；当前基准表=${data.baseline_table}，共 ${data.tables.length} 张表`;
    hint.className = 'muted ok';
  } else {
    hint.textContent = `group=${group} 表结构不一致，将按已配置基准表 ${data.baseline_table} 查询`;
    hint.className = 'muted warn';
  }
}

async function loadTablesForCurrentGroup(preferredTable) {
  const group = document.getElementById('group').value;
  const tableSel = document.getElementById('tableName');
  tableSel.innerHTML = '';
  if (!group) return;

  const data = await fetchJson('/api/meta/tables?group=' + encodeURIComponent(group));
  const tables = Array.isArray(data.tables) ? data.tables : [];
  for (const table of tables) {
    const op = document.createElement('option');
    op.value = table;
    const kind = data.table_kinds?.[table] || '';
    op.textContent = kind === 'fixed' ? `${table}（固定表）` : table;
    tableSel.appendChild(op);
  }
  if (preferredTable && tables.includes(preferredTable)) {
    tableSel.value = preferredTable;
  }
}

async function loadGroupsForCurrentView(preferredGroup) {
  const viewName = document.getElementById('viewName').value;
  const hint = document.getElementById('schemaHint');
  if (!viewName) {
    const sel = document.getElementById('group');
    sel.innerHTML = '';
    hint.textContent = '暂无可用 view';
    hint.className = 'muted warn';
    return;
  }
  const view = queryViews[viewName] || {};
  const perGroup = view.per_group && typeof view.per_group === 'object' ? view.per_group : {};
  const configuredGroups = Object.keys(perGroup);
  const sel = document.getElementById('group');
  sel.innerHTML = '';
  for (const g of configuredGroups) {
    const op = document.createElement('option');
    op.value = g;
    op.textContent = g;
    sel.appendChild(op);
  }
  if (configuredGroups.length === 0) {
    hint.textContent = `view=${viewName} 暂无已配置 group，请先到配置页保存 group 配置`;
    hint.className = 'muted warn';
    return;
  }
  if (preferredGroup && configuredGroups.includes(preferredGroup)) {
    sel.value = preferredGroup;
  }
  await loadGroupSchemaHint();
  await loadTablesForCurrentGroup();
  await reloadBatchCodesForCurrentContext();
}

async function loadViews() {
  const currentView = document.getElementById('viewName').value;
  const currentGroup = document.getElementById('group').value;
  const data = await fetchJson('/api/query/views');
  queryViews = data.views || {};
  const viewSel = document.getElementById('viewName');
  viewSel.innerHTML = '';
  for (const [name, view] of Object.entries(queryViews)) {
    const op = document.createElement('option');
    op.value = name;
    op.textContent = `${name} - ${view.title || name}`;
    viewSel.appendChild(op);
  }
  if (currentView && queryViews[currentView]) {
    viewSel.value = currentView;
  }
  updateViewSummary();
  await loadGroupsForCurrentView(currentGroup);
}

function updateViewSummary() {
  const name = document.getElementById('viewName').value;
  const view = queryViews[name];
  if (!view) {
    document.getElementById('viewSummary').textContent = '';
    return;
  }
  const pageSize = view.page_size || 10;
  if (!pageSizeOverridden) {
    document.getElementById('pageSize').value = pageSize;
  }
  const columns = Array.isArray(view.columns) ? view.columns.join(', ') : '';
  document.getElementById('viewSummary').textContent =
    `view=${name}, title=${view.title || ''}, default_page_size=${pageSize}, columns=${columns}`;
}

async function runQuery() {
  await runQueryAtPage(1);
}

function getCurrentPageSize() {
  const value = Number(document.getElementById('pageSize').value || 10);
  return Math.max(1, Math.min(500, value));
}

function getCurrentQueryContext() {
  const viewName = document.getElementById('viewName').value;
  const group = document.getElementById('group').value;
  const table = document.getElementById('tableName').value;
  if (!viewName) return alert('请先选择 view');
  if (!group) return alert('请先选择已配置 group');
  return { viewName, group, table };
}

function updatePageInfo(total, page, pageSize, more = false) {
  hasMore = Boolean(more);
  totalPages = total == null
    ? Math.max(1, page + (hasMore ? 1 : 0))
    : Math.max(1, Math.ceil((total || 0) / pageSize));
  currentPage = Math.max(page, 1);
  document.getElementById('pageNumber').value = String(currentPage);
  document.getElementById('btnPrevPage').disabled = currentPage <= 1;
  document.getElementById('btnNextPage').disabled = !hasMore;
  document.getElementById('pageInfo').textContent = total == null
    ? `当前第 ${currentPage} 页，每页 ${pageSize} 条${hasMore ? '，还有更多' : '，已到末页'}`
    : `总条数 ${total}，共 ${totalPages} 页，当前第 ${currentPage} 页`;
  saveQueryPageState();
}

async function runQueryAtPage(page) {
  const context = getCurrentQueryContext();
  if (!context) return;
  const { viewName, group, table } = context;
  const pageSize = getCurrentPageSize();
  const targetPage = Math.max(1, page);
  currentCursor = null;
  nextCursor = null;
  cursorStack = [];

  const payload = {
    view_name: viewName,
    group,
    page: targetPage,
    page_size: pageSize,
    pagination_mode: 'cursor',
    include_total: false,
    query_mode: getQueryMode(),
  };
  if (table) payload.table = table;

  let start = '';
  let end = '';
  let batchCode = '';
  if (payload.query_mode === 'batch') {
    batchCode = document.getElementById('batchCode').value.trim();
    if (!batchCode) return alert('按批次号查询时必须选择 BatchCode');
    payload.batch_code = batchCode;
  } else {
    start = document.getElementById('startTime').value;
    end = document.getElementById('endTime').value;
    if (!start || !end) return alert('按时间查询时必须填写开始时间和结束时间');
    if (new Date(start).getTime() > new Date(end).getTime()) {
      return alert('开始时间不能大于结束时间');
    }
    // 选择器保存本地时间字符串，直接传递可避免 toISOString() 产生时区偏移
    payload.start_time = start;
    payload.end_time = end;
  }

  const data = await fetchJson('/api/history/by-view', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  renderTable(data.columns || [], data.display_columns || [], data.rows || []);
  nextCursor = data.next_cursor || null;
  document.getElementById('querySummary').textContent =
    `page=${data.page}, page_size=${data.page_size}, has_more=${Boolean(data.has_more)}`;
  updatePageInfo(data.total, data.page || targetPage, data.page_size || pageSize, data.has_more);
  lastQueryContext = {
    ...context,
    start,
    end,
    queryMode: payload.query_mode,
    batchCode,
  };
  lastResultData = data;

  const warningParts = [];
  if (Array.isArray(data.warnings) && data.warnings.length > 0) {
    warningParts.push(data.warnings.join(' | '));
  }
  if (Array.isArray(data.missing_columns) && data.missing_columns.length > 0) {
    warningParts.push(`missing_columns=${data.missing_columns.join(', ')}`);
  }
  document.getElementById('queryWarnings').textContent = warningParts.join(' | ');
  saveQueryPageState();
}

async function runLastQueryAtPage(page) {
  if (!lastQueryContext) return;
  const { viewName, group, start, end, batchCode, queryMode } = lastQueryContext;
  const table = lastQueryContext.table || '';
  const pageSize = getCurrentPageSize();
  const targetPage = Math.max(1, page);
  let requestedCursor = currentCursor;
  const proposedStack = [...cursorStack];
  if (targetPage > currentPage) {
    if (!hasMore || !nextCursor) return;
    proposedStack.push(currentCursor);
    requestedCursor = nextCursor;
  } else if (targetPage < currentPage) {
    requestedCursor = proposedStack.length ? proposedStack.pop() : null;
  } else {
    return;
  }

  const payload = {
    view_name: viewName,
    group,
    page: targetPage,
    page_size: pageSize,
    pagination_mode: 'cursor',
    include_total: false,
    query_mode: queryMode || 'time',
  };
  if (table) payload.table = table;
  if (start) payload.start_time = start;
  if (end) payload.end_time = end;
  if (batchCode) payload.batch_code = batchCode;
  if (requestedCursor) payload.cursor = requestedCursor;

  const data = await fetchJson('/api/history/by-view', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  renderTable(data.columns || [], data.display_columns || [], data.rows || []);
  currentCursor = requestedCursor;
  cursorStack = proposedStack;
  nextCursor = data.next_cursor || null;
  document.getElementById('querySummary').textContent =
    `page=${targetPage}, page_size=${data.page_size}, has_more=${Boolean(data.has_more)}`;
  updatePageInfo(data.total, targetPage, data.page_size || pageSize, data.has_more);
  lastResultData = data;
  saveQueryPageState();
}

function resolveHeader(displayMeta, lang) {
  const zh = displayMeta.label_zh || displayMeta.name;
  const en = displayMeta.label_en || displayMeta.name;
  if (lang === 'en') return en;
  if (lang === 'both') return `${zh} (${en})`;
  return zh;
}

function renderTable(columns, displayColumns, rows) {
  const table = document.getElementById('resultTable');
  table.innerHTML = '';
  const lang = document.getElementById('labelLang').value || 'zh';
  const displayMap = {};
  for (const item of displayColumns || []) {
    if (item && item.name) displayMap[item.name] = item;
  }

  const thead = document.createElement('thead');
  const trh = document.createElement('tr');
  for (const c of columns) {
    const th = document.createElement('th');
    const meta = displayMap[c] || { name: c, label_en: c, label_zh: c };
    th.textContent = resolveHeader(meta, lang);
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const c of columns) {
      const td = document.createElement('td');
      td.textContent = row[c] == null ? '' : String(row[c]);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function restoreResultFromState(saved) {
  if (!saved || !saved.lastResultData) return;
  const data = saved.lastResultData;
  renderTable(data.columns || [], data.display_columns || [], data.rows || []);
  document.getElementById('querySummary').textContent =
    `page=${data.page || 1}, page_size=${data.page_size}, has_more=${Boolean(data.has_more)}`;
  updatePageInfo(data.total, data.page || 1, data.page_size || getCurrentPageSize(), data.has_more);
  const warningParts = [];
  if (Array.isArray(data.warnings) && data.warnings.length > 0) {
    warningParts.push(data.warnings.join(' | '));
  }
  if (Array.isArray(data.missing_columns) && data.missing_columns.length > 0) {
    warningParts.push(`missing_columns=${data.missing_columns.join(', ')}`);
  }
  document.getElementById('queryWarnings').textContent = warningParts.join(' | ');
}

async function restoreQueryPageState() {
  const saved = loadSavedQueryPageState();
  if (!saved) return;

  if (saved.labelLang) document.getElementById('labelLang').value = saved.labelLang;
  pageSizeOverridden = Boolean(saved.pageSizeOverridden);
  if (saved.pageSize) document.getElementById('pageSize').value = saved.pageSize;
  if (saved.queryMode === 'batch') document.getElementById('queryModeBatch').checked = true;
  if (saved.startTime) document.getElementById('startTime').value = saved.startTime;
  if (saved.endTime) document.getElementById('endTime').value = saved.endTime;
  timeRangePicker?.refresh();
  setQuickRangeActive(saved.quickActive || '');
  if (saved.pageNumber) document.getElementById('pageNumber').value = saved.pageNumber;
  if (saved.viewName && queryViews[saved.viewName]) {
    document.getElementById('viewName').value = saved.viewName;
  }
  updateViewSummary();
  await loadGroupsForCurrentView(saved.group || '');
  await loadTablesForCurrentGroup(saved.table || '');
  if (saved.batchCode && Array.from(document.getElementById('batchCode').options).some(o => o.value === saved.batchCode)) {
    document.getElementById('batchCode').value = saved.batchCode;
  }
  updateQueryModeControls();

  lastQueryContext = saved.lastQueryContext || null;
  lastResultData = saved.lastResultData || null;
  hasMore = Boolean(saved.hasMore);
  currentCursor = saved.currentCursor || null;
  nextCursor = saved.nextCursor || null;
  cursorStack = Array.isArray(saved.cursorStack) ? saved.cursorStack : [];
  restoreResultFromState(saved);
}

document.getElementById('btnRefreshGroups').addEventListener('click', () => {
  loadViews().catch(err => alert(err.message));
});
document.getElementById('btnRunQuery').addEventListener('click', () => {
  runQuery().catch(err => alert(err.message));
});
document.getElementById('btnPrevPage').addEventListener('click', () => {
  if (!lastQueryContext) return;
  runLastQueryAtPage(Math.max(1, currentPage - 1)).catch(err => alert(err.message));
});
document.getElementById('btnNextPage').addEventListener('click', () => {
  if (!lastQueryContext) return;
  runLastQueryAtPage(Math.min(totalPages, currentPage + 1)).catch(err => alert(err.message));
});
document.getElementById('group').addEventListener('change', () => {
  Promise.all([loadGroupSchemaHint(), loadTablesForCurrentGroup()])
    .then(() => reloadBatchCodesForCurrentContext())
    .then(() => {
      saveQueryPageState();
    })
    .catch(err => alert(err.message));
});
document.getElementById('tableName').addEventListener('change', saveQueryPageState);
document.getElementById('viewName').addEventListener('change', () => {
  const selectedGroup = document.getElementById('group').value || '';
  loadGroupsForCurrentView(selectedGroup)
    .then(() => {
      updateViewSummary();
      saveQueryPageState();
    })
    .catch(err => alert(err.message));
});
document.getElementById('startTime').addEventListener('input', clearQuickRangeActive);
document.getElementById('endTime').addEventListener('input', clearQuickRangeActive);
document.getElementById('startTime').addEventListener('change', saveQueryPageState);
document.getElementById('endTime').addEventListener('change', saveQueryPageState);
document.getElementById('batchCode').addEventListener('change', saveQueryPageState);
for (const radio of document.querySelectorAll('input[name="queryMode"]')) {
  radio.addEventListener('change', () => {
    updateQueryModeControls();
    saveQueryPageState();
  });
}
document.getElementById('labelLang').addEventListener('change', saveQueryPageState);
document.getElementById('pageSize').addEventListener('change', () => {
  pageSizeOverridden = true;
  saveQueryPageState();
});
for (const [id, preset] of Object.entries(quickRangePresets)) {
  document.getElementById(id).addEventListener('click', () => applyQuickRange(preset, id));
}

async function initQueryPage() {
  timeRangePicker = window.TouchTimeRange.attach({
    idPrefix: 'query-time',
    startInputId: 'startTime',
    endInputId: 'endTime',
    triggerId: 'btnPreciseTime',
    summaryId: 'queryTimeSummary',
  });
  await loadViews();
  enableButtonClickFeedback();
  const saved = loadSavedQueryPageState();
  if (!saved) {
    applyQuickRange('last1h', 'btnRange1H');
    updatePageInfo(null, 1, getCurrentPageSize(), false);
    saveQueryPageState();
    return;
  }
  await restoreQueryPageState();
}

initQueryPage().catch(err => alert(err.message));
