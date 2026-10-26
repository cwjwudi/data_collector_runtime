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
let currentQueryConditions = [];
let timeRangePicker = null;
const QUERY_STATE_KEY = 'sd_sma_query_page_state_v1';
const t = (key, params) => window.SdI18n.t(key, params);
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

function showQueryError(message) {
  document.getElementById('queryWarnings').textContent = message;
}

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

function appendSelectOption(select, value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

function selectedCondition() {
  const name = document.getElementById('queryCondition').value || '';
  return currentQueryConditions.find(item => item.name === name) || null;
}

async function loadConditionValues(preferredValue) {
  const valueSelect = document.getElementById('queryConditionValue');
  const condition = selectedCondition();
  valueSelect.innerHTML = '';
  appendSelectOption(valueSelect, '', t('query.condition.valuePlaceholder'));
  valueSelect.disabled = !condition;
  if (!condition) return;

  const viewName = document.getElementById('viewName').value;
  const group = document.getElementById('group').value;
  const data = await fetchJson(
    '/api/meta/condition-values?view_name=' + encodeURIComponent(viewName) +
      '&group=' + encodeURIComponent(group) +
      '&name=' + encodeURIComponent(condition.name),
  );
  for (const item of data.items || []) appendSelectOption(valueSelect, item, item);
  if (preferredValue && Array.from(valueSelect.options).some(option => option.value === preferredValue)) {
    valueSelect.value = preferredValue;
  }
  const source = data.source || {};
  document.getElementById('queryWarnings').textContent = (data.items || []).length
    ? t('query.js.conditionSource', { name: condition.name, src: `${source.table || '-'} . ${source.field || '-'}` })
    : t('query.js.conditionEmpty', { name: condition.name });
}

async function reloadQueryConditions(preferredName, preferredValue) {
  const select = document.getElementById('queryCondition');
  const viewName = document.getElementById('viewName').value;
  const group = document.getElementById('group').value;
  select.innerHTML = '';
  appendSelectOption(select, '', t('query.condition.none'));
  currentQueryConditions = [];
  if (!viewName || !group) {
    document.getElementById('queryConditionValue').disabled = true;
    return;
  }
  try {
    const data = await fetchJson(
      '/api/meta/query-conditions?view_name=' + encodeURIComponent(viewName) +
        '&group=' + encodeURIComponent(group),
    );
    currentQueryConditions = Array.isArray(data.conditions) ? data.conditions : [];
    for (const item of currentQueryConditions) appendSelectOption(select, item.name, item.name);
    if (preferredName && currentQueryConditions.some(item => item.name === preferredName)) {
      select.value = preferredName;
    }
    await loadConditionValues(preferredValue);
  } catch (err) {
    document.getElementById('queryConditionValue').disabled = true;
    document.getElementById('queryWarnings').textContent = t('query.js.conditionError', { msg: err.message });
  }
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
    conditionName: document.getElementById('queryCondition').value || '',
    conditionValue: document.getElementById('queryConditionValue').value || '',
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
    hint.textContent = t('query.js.groupReady', { group, table: data.baseline_table, count: data.tables.length });
    hint.className = 'muted ok';
  } else {
    hint.textContent = t('query.js.groupSchemaMismatch', { group, table: data.baseline_table });
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
    op.textContent = kind === 'fixed' ? t('query.js.fixedTable', { table }) : table;
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
    hint.textContent = t('query.js.noView');
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
    hint.textContent = t('query.js.noGroup', { view: viewName });
    hint.className = 'muted warn';
    return;
  }
  if (preferredGroup && configuredGroups.includes(preferredGroup)) {
    sel.value = preferredGroup;
  }
  await loadGroupSchemaHint();
  await loadTablesForCurrentGroup();
  await reloadQueryConditions();
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
  if (!viewName) return showQueryError(t('query.js.selectView'));
  if (!group) return showQueryError(t('query.js.selectGroup'));
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
    ? (hasMore
      ? t('query.js.pageInfoMore', { page: currentPage, size: pageSize })
      : t('query.js.pageInfoEnd', { page: currentPage, size: pageSize }))
    : t('query.js.pageInfoTotal', { total, pages: totalPages, page: currentPage });
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
  };
  if (table) payload.table = table;

  const start = document.getElementById('startTime').value;
  const end = document.getElementById('endTime').value;
  if (Boolean(start) !== Boolean(end)) return showQueryError(t('query.js.timePartial'));
  if (start && end && new Date(start).getTime() > new Date(end).getTime()) {
    return showQueryError(t('query.js.timeOrder'));
  }
  if (start && end) {
    payload.start_time = start;
    payload.end_time = end;
  }
  const conditionName = document.getElementById('queryCondition').value || '';
  const conditionValue = document.getElementById('queryConditionValue').value || '';
  if (conditionName && conditionValue) {
    payload.condition_name = conditionName;
    payload.condition_value = conditionValue;
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
    conditionName,
    conditionValue,
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
  const { viewName, group, start, end, conditionName, conditionValue } = lastQueryContext;
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
  };
  if (table) payload.table = table;
  if (start) payload.start_time = start;
  if (end) payload.end_time = end;
  if (conditionName && conditionValue) {
    payload.condition_name = conditionName;
    payload.condition_value = conditionValue;
  }
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
  await reloadQueryConditions(saved.conditionName || '', saved.conditionValue || '');

  lastQueryContext = saved.lastQueryContext || null;
  lastResultData = saved.lastResultData || null;
  hasMore = Boolean(saved.hasMore);
  currentCursor = saved.currentCursor || null;
  nextCursor = saved.nextCursor || null;
  cursorStack = Array.isArray(saved.cursorStack) ? saved.cursorStack : [];
  restoreResultFromState(saved);
}

document.getElementById('btnRefreshGroups').addEventListener('click', () => {
  loadViews().catch(err => showQueryError(err.message));
});
document.getElementById('btnRunQuery').addEventListener('click', () => {
  runQuery().catch(err => showQueryError(err.message));
});
document.getElementById('btnPrevPage').addEventListener('click', () => {
  if (!lastQueryContext) return;
  runLastQueryAtPage(Math.max(1, currentPage - 1)).catch(err => showQueryError(err.message));
});
document.getElementById('btnNextPage').addEventListener('click', () => {
  if (!lastQueryContext) return;
  runLastQueryAtPage(Math.min(totalPages, currentPage + 1)).catch(err => showQueryError(err.message));
});
document.getElementById('group').addEventListener('change', () => {
  Promise.all([loadGroupSchemaHint(), loadTablesForCurrentGroup()])
    .then(() => reloadQueryConditions())
    .then(() => {
      saveQueryPageState();
    })
    .catch(err => showQueryError(err.message));
});
document.getElementById('tableName').addEventListener('change', saveQueryPageState);
document.getElementById('viewName').addEventListener('change', () => {
  const selectedGroup = document.getElementById('group').value || '';
  loadGroupsForCurrentView(selectedGroup)
    .then(() => {
      updateViewSummary();
      saveQueryPageState();
    })
    .catch(err => showQueryError(err.message));
});
document.getElementById('startTime').addEventListener('input', clearQuickRangeActive);
document.getElementById('endTime').addEventListener('input', clearQuickRangeActive);
document.getElementById('startTime').addEventListener('change', saveQueryPageState);
document.getElementById('endTime').addEventListener('change', saveQueryPageState);
document.getElementById('queryCondition').addEventListener('change', () => {
  loadConditionValues().then(saveQueryPageState).catch(err => showQueryError(err.message));
});
document.getElementById('queryConditionValue').addEventListener('change', saveQueryPageState);
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

initQueryPage().catch(err => showQueryError(err.message));
