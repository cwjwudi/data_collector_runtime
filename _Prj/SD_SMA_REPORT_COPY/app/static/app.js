const STATE_KEY = 'sd_sma_report_copy_state_v2';
const PAGE = document.body.dataset.page || 'main';

let activeJobId = '';
let pollTimer = null;
let currentConfig = {};
let currentViewMode = 'flat';
let currentFolderPath = '';
const notifiedJobStates = new Map();
const selectedReportPaths = new Set();
const selectedFolderPaths = new Set();

const confirmModalOverlay = document.getElementById('confirm-modal-overlay');
const confirmModalTitle = document.getElementById('confirm-modal-title');
const confirmModalMessage = document.getElementById('confirm-modal-message');
const confirmModalCancel = document.getElementById('confirm-modal-cancel');
const confirmModalConfirm = document.getElementById('confirm-modal-confirm');

function el(id) {
  return document.getElementById(id);
}

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
  const bar = el('appStatusBar');
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
  for (const btn of document.querySelectorAll('button')) {
    btn.addEventListener('click', () => {
      btn.classList.remove('is-clicked');
      void btn.offsetWidth;
      btn.classList.add('is-clicked');
      setTimeout(() => btn.classList.remove('is-clicked'), 220);
    });
  }
}

function setHint(id, text, cls = 'muted') {
  const target = el(id);
  if (target) {
    target.textContent = text;
    target.className = cls;
  }
  const classes = String(cls).split(/\s+/);
  if (classes.includes('ok')) {
    showStatusBar(text, 'ok');
  } else if (classes.includes('warn') || classes.includes('error')) {
    showStatusBar(text, classes.includes('error') ? 'error' : 'warn');
  }
}

async function openFilesystemBrowser(initialPath = '') {
  const overlay = el('filesystem-modal-overlay');
  const pathLabel = el('filesystem-modal-path');
  const list = el('filesystem-modal-list');
  const cancel = el('filesystem-modal-cancel');
  const select = el('filesystem-modal-select');
  if (!overlay) throw new Error('文件浏览窗口不可用');
  let current = '';

  return new Promise((resolve, reject) => {
    const finish = value => { overlay.style.display = 'none'; resolve(value); };
    const fail = error => { overlay.style.display = 'none'; reject(error); };
    cancel.onclick = () => finish('');
    overlay.onclick = event => { if (event.target === overlay) finish(''); };
    select.onclick = () => finish(current);
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
    const showRoots = async () => {
      const data = await fetchJson('/api/filesystem/roots?purpose=source');
      current = '';
      select.disabled = true;
      pathLabel.textContent = '允许访问的位置';
      list.innerHTML = '';
      for (const root of data.roots || []) {
        renderButton(root.name, root.exists ? root.path : '目录不存在', () => {
          if (root.exists) showDirectory(root.path).catch(fail);
        });
      }
    };
    const showDirectory = async path => {
      const data = await fetchJson(`/api/filesystem/entries?purpose=source&path=${encodeURIComponent(path)}`);
      current = data.current;
      select.disabled = false;
      pathLabel.textContent = current;
      list.innerHTML = '';
      if (data.parent) renderButton('..', '上一级', () => showDirectory(data.parent).catch(fail));
      else renderButton('位置列表', '返回', () => showRoots().catch(fail));
      for (const entry of data.entries || []) {
        renderButton(entry.name, '文件夹', () => showDirectory(entry.path).catch(fail));
      }
    };
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

function formatBytes(value) {
  const n = Number(value || 0);
  if (n <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = n;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function normalizeExts(value) {
  return String(value || '.pdf')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => (item.startsWith('.') ? item : `.${item}`));
}

function normalizeTargets(value) {
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
}

function loadState() {
  try {
    return JSON.parse(safeStorageGet(STATE_KEY) || '{}');
  } catch {
    return {};
  }
}

function saveState() {
  const state = {
    drive: el('driveSelect')?.value || '',
    search: el('reportSearch')?.value || '',
    viewMode: currentViewMode,
    folderPath: currentFolderPath,
  };
  safeStorageSet(STATE_KEY, JSON.stringify(state));
}

function getConfigPayload() {
  return {
    report_source_dir: el('sourceDir').value.trim(),
    destination_folder: el('destFolder').value.trim() || 'SMA_Report',
    allowed_extensions: normalizeExts(el('allowedExts').value),
    copy_subdirectories: el('copySubdirectories').checked,
    overwrite_by_default: el('overwriteDefault').checked,
    allowed_target_roots: normalizeTargets(el('allowedTargets').value),
  };
}

function applyConfigToForm(cfg) {
  if (!el('sourceDir')) return;
  el('sourceDir').value = cfg.report_source_dir || '';
  el('destFolder').value = cfg.destination_folder || 'SMA_Report';
  el('allowedExts').value = (cfg.allowed_extensions || ['.pdf']).join(',');
  el('copySubdirectories').checked = Boolean(cfg.copy_subdirectories);
  el('overwriteDefault').checked = Boolean(cfg.overwrite_by_default);
  el('allowedTargets').value = (cfg.allowed_target_roots || []).join(',');
}

function renderConfigHint(cfg) {
  const existsText = cfg.report_source_exists ? '目录可用' : '目录不存在，请检查路径';
  setHint(
    'configHint',
    `当前目录: ${cfg.report_source_dir_resolved || '-'} / ${existsText} / 日志: ${cfg.log_dir_resolved || '-'}`,
    cfg.report_source_exists ? 'muted ok' : 'muted warn',
  );
}

async function loadConfig() {
  currentConfig = await fetchJson('/api/config');
  applyConfigToForm(currentConfig);
  renderConfigHint(currentConfig);
  if (el('overwriteCopy')) {
    el('overwriteCopy').checked = Boolean(currentConfig.overwrite_by_default);
  }
  return currentConfig;
}

async function saveConfig() {
  const cfg = await fetchJson('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(getConfigPayload()),
  });
  currentConfig = cfg;
  renderConfigHint(cfg);
  setHint('configHint', `设置已保存: ${cfg.report_source_dir_resolved}`, 'muted ok');
}

async function chooseSourceFolder() {
  setHint('configHint', '请选择服务允许访问的报表目录...');
  const selected = await openFilesystemBrowser(el('sourceDir').value);
  if (selected) {
    el('sourceDir').value = selected;
    setHint('configHint', `已选择报表目录: ${selected}`, 'muted ok');
  } else {
    setHint('configHint', '已取消选择报表目录');
  }
}

function setViewMode(mode) {
  currentViewMode = mode === 'folder' ? 'folder' : 'flat';
  for (const btn of document.querySelectorAll('.mode-btn[data-view-mode]')) {
    btn.classList.toggle('is-selected', btn.dataset.viewMode === currentViewMode);
  }
  saveState();
}

function normalizeFolderPath(path) {
  return String(path || '').replaceAll('\\', '/').replace(/^\/+|\/+$/g, '');
}

function parentFolderPath(path) {
  const normalized = normalizeFolderPath(path);
  const idx = normalized.lastIndexOf('/');
  return idx >= 0 ? normalized.slice(0, idx) : '';
}

function pathWithinFolder(path, folder) {
  const normalizedFolder = normalizeFolderPath(folder);
  if (!normalizedFolder) return path;
  const prefix = normalizedFolder + '/';
  return path.startsWith(prefix) ? path.slice(prefix.length) : null;
}

function reportFolder(path) {
  const idx = path.lastIndexOf('/');
  return idx >= 0 ? path.slice(0, idx) : '根目录';
}

function reportName(path) {
  const idx = path.lastIndexOf('/');
  return idx >= 0 ? path.slice(idx + 1) : path;
}

function reportRowHtml(report, options = {}) {
  const label = options.nameOnly ? reportName(report.path) : report.path;
  const checked = selectedReportPaths.has(report.path) ? ' checked' : '';
  return (
    `<tr>` +
    `<td><input type="checkbox" class="report-check" value="${escapeHtml(report.path)}"${checked} aria-label="选择报表 ${escapeHtml(label)}" /></td>` +
    `<td><span class="report-path-text" title="${escapeHtml(report.path)}">${escapeHtml(label)}</span></td>` +
    `<td>${formatBytes(report.size)}</td>` +
    `<td>${escapeHtml(report.modified_at)}</td>` +
    `</tr>`
  );
}

function renderFlatReports(tbody, reports) {
  for (const report of reports) {
    tbody.insertAdjacentHTML('beforeend', reportRowHtml(report));
  }
}

function renderFolderReports(tbody, reports) {
  const folder = normalizeFolderPath(currentFolderPath);
  const childFolders = new Map();
  const files = [];

  for (const report of reports) {
    const remainder = pathWithinFolder(report.path, folder);
    if (remainder == null) continue;
    const slash = remainder.indexOf('/');
    if (slash >= 0) {
      const childName = remainder.slice(0, slash);
      const childPath = folder ? `${folder}/${childName}` : childName;
      childFolders.set(childPath, (childFolders.get(childPath) || 0) + 1);
    } else {
      files.push(report);
    }
  }

  tbody.insertAdjacentHTML(
    'beforeend',
    `<tr class="folder-toolbar-row"><td colspan="4">` +
      `<button type="button" class="folder-nav-btn" data-folder-nav="root">全部文件</button>` +
      `<span class="folder-current">当前位置：${escapeHtml(folder || '根目录')}</span>` +
      `<button type="button" class="folder-nav-btn" data-folder-nav="up" ${folder ? '' : 'disabled'}>返回上一级</button>` +
    `</td></tr>`,
  );

  const sortedFolders = Array.from(childFolders.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [childPath, count] of sortedFolders) {
    const name = reportName(childPath);
    const checked = selectedFolderPaths.has(childPath) ? ' checked' : '';
    tbody.insertAdjacentHTML(
      'beforeend',
      `<tr class="folder-row">` +
        `<td><input type="checkbox" class="folder-check" value="${escapeHtml(childPath)}"${checked} ` +
          `aria-label="选择文件夹 ${escapeHtml(name)}" title="复制该文件夹内全部允许类型报表（含子文件夹）" /></td>` +
        `<td><button type="button" class="folder-link" data-folder-path="${escapeHtml(childPath)}">${escapeHtml(name)}</button></td>` +
        `<td>${count} 个文件</td>` +
        `<td></td>` +
      `</tr>`,
    );
  }
  for (const report of files) {
    tbody.insertAdjacentHTML('beforeend', reportRowHtml(report, { nameOnly: true }));
  }
}

function renderReports(reports, root, exists) {
  el('reportCount').textContent = `${reports.length} 个文件`;
  const table = el('reportsTable');
  table.innerHTML = '';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th style="width:42px">选</th><th>报表</th><th style="width:90px">大小</th><th style="width:150px">修改时间</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  if (currentViewMode === 'folder') {
    renderFolderReports(tbody, reports);
  } else {
    renderFlatReports(tbody, reports);
  }
  table.appendChild(tbody);
  bindFolderNavigation();
  bindReportSelection();
  updateSelectionSummary();
  setHint(
    'reportsHint',
    exists ? `报表根目录: ${root}` : `报表根目录不存在: ${root}`,
    exists ? 'muted' : 'muted warn',
  );
}

function updateSelectionSummary() {
  const target = el('selectionSummary');
  if (!target) return;
  target.textContent = `已选 ${selectedReportPaths.size} 个文件、${selectedFolderPaths.size} 个文件夹`;
  target.className = selectedReportPaths.size || selectedFolderPaths.size ? 'muted ok selection-summary' : 'muted selection-summary';
}

function bindReportSelection() {
  for (const item of document.querySelectorAll('.report-check')) {
    item.addEventListener('change', () => {
      if (item.checked) selectedReportPaths.add(item.value);
      else selectedReportPaths.delete(item.value);
      updateSelectionSummary();
    });
  }
  for (const item of document.querySelectorAll('.folder-check')) {
    item.addEventListener('change', () => {
      if (item.checked) selectedFolderPaths.add(item.value);
      else selectedFolderPaths.delete(item.value);
      updateSelectionSummary();
    });
  }
}

function bindFolderNavigation() {
  for (const btn of document.querySelectorAll('.folder-link[data-folder-path]')) {
    btn.addEventListener('click', () => {
      currentFolderPath = normalizeFolderPath(btn.dataset.folderPath);
      saveState();
      refreshReports().catch(err => setHint('reportsHint', err.message, 'muted warn'));
    });
  }
  for (const btn of document.querySelectorAll('.folder-nav-btn[data-folder-nav]')) {
    btn.addEventListener('click', () => {
      const action = btn.dataset.folderNav;
      currentFolderPath = action === 'up' ? parentFolderPath(currentFolderPath) : '';
      saveState();
      refreshReports().catch(err => setHint('reportsHint', err.message, 'muted warn'));
    });
  }
}

async function refreshReports() {
  const query = el('reportSearch').value.trim();
  const data = await fetchJson('/api/reports?q=' + encodeURIComponent(query));
  renderReports(data.reports || [], data.root || '', data.exists);
}

function selectedReports() {
  return Array.from(selectedReportPaths).sort();
}

function selectedFolders() {
  return Array.from(selectedFolderPaths).sort();
}

function selectAllReports(checked) {
  if (!checked) {
    selectedReportPaths.clear();
    selectedFolderPaths.clear();
  }
  for (const item of document.querySelectorAll('.report-check, .folder-check')) {
    item.checked = checked;
    if (checked && item.classList.contains('report-check')) selectedReportPaths.add(item.value);
    if (checked && item.classList.contains('folder-check')) selectedFolderPaths.add(item.value);
  }
  updateSelectionSummary();
}

function renderDrives(drives) {
  const allowed = drives.filter(item => item.is_allowed);
  el('driveCount').textContent = `${allowed.length} 个目标`;
  const cards = el('driveCards');
  cards.innerHTML = '';
  const select = el('driveSelect');
  const previous = select.value || loadState().drive || '';
  select.innerHTML = '';
  for (const drive of drives) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = `drive-card ${drive.is_allowed ? '' : 'is-disabled'}`;
    card.dataset.root = drive.root;
    card.disabled = !drive.is_allowed;
    card.innerHTML =
      `<div class="drive-title">${escapeHtml(drive.root)} ${escapeHtml(drive.label || '')}</div>` +
      `<div class="drive-meta">${drive.is_removable ? '可移动磁盘' : '固定磁盘'} / 可用 ${formatBytes(drive.free_bytes)}</div>` +
      `<div class="drive-meta">${drive.is_allowed ? '允许复制' : '未允许'}</div>`;
    cards.appendChild(card);
    if (drive.is_allowed) {
      const op = document.createElement('option');
      op.value = drive.root;
      op.textContent = `${drive.root} ${drive.label || ''} / 可用 ${formatBytes(drive.free_bytes)}`;
      select.appendChild(op);
    }
  }
  if (previous && Array.from(select.options).some(op => op.value === previous)) {
    select.value = previous;
  }
  updateDriveCardSelection();
  for (const card of document.querySelectorAll('.drive-card:not(.is-disabled)')) {
    card.addEventListener('click', () => {
      select.value = card.dataset.root;
      saveState();
      updateDriveCardSelection();
    });
  }
  if (!drives.length) setHint('copyHint', '未检测到磁盘。请插入 U 盘后刷新。', 'muted warn');
}

function updateDriveCardSelection() {
  const root = el('driveSelect').value;
  for (const card of document.querySelectorAll('.drive-card')) {
    card.classList.toggle('is-selected', card.dataset.root === root);
  }
}

async function refreshDrives() {
  const data = await fetchJson('/api/drives');
  renderDrives(data.drives || []);
}

async function startCopy() {
  const files = selectedReports();
  const folders = selectedFolders();
  const drive = el('driveSelect').value;
  if (!files.length && !folders.length) throw new Error('请先选择报表文件或文件夹');
  if (!drive) throw new Error('请先选择U盘目标');
  const overwrite = el('overwriteCopy').checked;
  const destination = currentConfig.destination_folder || 'SMA_Report';
  const ok = await showConfirmModal({
    title: '复制报表确认',
    message: `将 ${files.length} 个文件、${folders.length} 个文件夹（含子文件夹）复制到 ${drive}\\${destination}。` +
      `${overwrite ? '同名文件将被覆盖。' : '同名文件将跳过。'}重叠选择会自动去重。是否继续？`,
    confirmText: '开始复制',
  });
  if (!ok) return;
  const data = await fetchJson('/api/copy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      drive,
      files,
      folders,
      destination_folder: destination,
      overwrite,
    }),
  });
  watchJob(data.job.id);
  setHint('copyHint', `复制任务已开始: ${files.length} 个文件、${folders.length} 个文件夹 -> ${drive}`, 'muted ok');
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
    const failedCount = Array.isArray(job.result?.failed) ? job.result.failed.length : 0;
    if (failedCount > 0) {
      showStatusBar(`任务完成但有失败：${title}，失败 ${failedCount} 项。`, 'warn');
    } else {
      showStatusBar(`任务成功：${title}`, 'ok');
    }
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
  const table = el('jobsTable');
  table.innerHTML = '';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>状态</th><th>进度</th><th>任务</th><th>耗时</th><th>结果</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const job of jobs) {
    const status = job.status || 'running';
    const result = job.result && job.result.destination
      ? `${job.result.destination} / 成功 ${job.result.copied.length} / 跳过 ${job.result.skipped.length} / 失败 ${job.result.failed.length}`
      : (job.error || job.phase || '');
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td><button type="button" class="job-link status-pill ${statusClass(status)}" data-job-id="${job.id}">${status}</button></td>` +
      `<td>${renderProgress(job)}</td>` +
      `<td>${escapeHtml(job.title || job.id)}</td>` +
      `<td>${formatDuration(job.elapsed_seconds)}</td>` +
      `<td>${escapeHtml(result)}</td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  for (const btn of document.querySelectorAll('.job-link[data-job-id]')) {
    btn.addEventListener('click', () => watchJob(btn.getAttribute('data-job-id')));
  }
}

function renderJobLog(job) {
  const lines = job && Array.isArray(job.logs) ? job.logs : [];
  el('jobLog').value = lines.join('\n');
  el('jobSummary').textContent = job
    ? `当前任务: ${job.title || job.id} / ${job.status} / ${job.progress || 0}%`
    : '';
}

async function refreshJobs() {
  const data = await fetchJson('/api/jobs');
  const jobs = data.jobs || [];
  renderJobs(jobs);
  if (!activeJobId && jobs.length > 0) activeJobId = jobs[0].id;
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

function bindConfigEvents() {
  el('btnChooseSource').addEventListener('click', () => {
    chooseSourceFolder().catch(err => setHint('configHint', err.message, 'muted warn'));
  });
  el('btnSaveConfig').addEventListener('click', () => {
    saveConfig().catch(err => setHint('configHint', err.message, 'muted warn'));
  });
  el('btnReloadConfig').addEventListener('click', () => {
    loadConfig().catch(err => setHint('configHint', err.message, 'muted warn'));
  });
}

function bindMainEvents() {
  el('btnSearchReports').addEventListener('click', () => {
    saveState();
    refreshReports().catch(err => setHint('reportsHint', err.message, 'muted warn'));
  });
  el('reportSearch').addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      saveState();
      refreshReports().catch(err => setHint('reportsHint', err.message, 'muted warn'));
    }
  });
  el('btnSelectAll').addEventListener('click', () => selectAllReports(true));
  el('btnClearSelection').addEventListener('click', () => selectAllReports(false));
  for (const btn of document.querySelectorAll('.mode-btn[data-view-mode]')) {
    btn.addEventListener('click', () => {
      setViewMode(btn.dataset.viewMode);
      if (currentViewMode !== 'folder') currentFolderPath = '';
      refreshReports().catch(err => setHint('reportsHint', err.message, 'muted warn'));
    });
  }
  el('driveSelect').addEventListener('change', () => {
    saveState();
    updateDriveCardSelection();
  });
  el('btnCopySelected').addEventListener('click', () => {
    startCopy().catch(err => setHint('copyHint', err.message, 'muted warn'));
  });
  el('btnRefreshJobs').addEventListener('click', () => {
    refreshJobs().catch(err => setHint('jobSummary', err.message, 'muted warn'));
  });
}

async function initConfigPage() {
  await loadConfig();
  bindConfigEvents();
}

async function initMainPage() {
  const saved = loadState();
  if (saved.search) el('reportSearch').value = saved.search;
  currentFolderPath = normalizeFolderPath(saved.folderPath || '');
  setViewMode(saved.viewMode || 'flat');
  await loadConfig();
  bindMainEvents();
  await refreshReports();
  await refreshDrives();
  await refreshJobs();
  setInterval(() => refreshDrives().catch(() => {}), 5000);
}

async function init() {
  enableButtonClickFeedback();
  if (PAGE === 'config') {
    await initConfigPage();
  } else {
    await initMainPage();
  }
}

init().catch(err => showStatusBar(err.message, 'error'));
