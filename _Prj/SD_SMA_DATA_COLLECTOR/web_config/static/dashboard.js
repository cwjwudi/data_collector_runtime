const monitorConfigSelect = document.getElementById("monitor-config-select");
const monitorStatusEl = document.getElementById("monitor-status");
const monitorLogEl = document.getElementById("monitor-log");
const startupAutoEnabled = document.getElementById("startup-auto-enabled");
const startupDelaySeconds = document.getElementById("startup-delay-seconds");
const startupSettingsHint = document.getElementById("startup-settings-hint");

const MONITOR_CONFIG_KEY = "sd_sma_monitor_config_v1";
let monitorPollInFlight = false;
let lastStatusPayload = null;
let lastLogLines = [];
let startupSettings = {
  auto_start_enabled: false,
  auto_start_config: "",
  auto_start_delay_seconds: 3,
};
let logCursor = 0;
let logPaused = false;
let pausedBufferedCount = 0;
let pausedBufferedLines = [];
const LOG_FETCH_LIMIT = 200;
const LOG_FETCH_PAGES_PER_POLL = 3;
const LOG_RENDER_LIMIT = 500;
const LOG_PAUSED_BUFFER_LIMIT = 2000;

function appendLogLines(lines) {
  if (!Array.isArray(lines) || !lines.length) {
    return;
  }
  lastLogLines.push(...lines);
  if (lastLogLines.length > LOG_RENDER_LIMIT) {
    lastLogLines = lastLogLines.slice(-LOG_RENDER_LIMIT);
  }
}

function updatePauseButtonLabel() {
  const btn = document.getElementById("btn-log-pause");
  if (!btn) {
    return;
  }
  if (logPaused) {
    const suffix = pausedBufferedCount > 0 ? ` (${pausedBufferedCount})` : "";
    btn.textContent = `继续日志${suffix}`;
  } else {
    btn.textContent = "暂停日志";
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `请求失败: ${response.status}`);
  }
  return data;
}

function syncMonitorConfigSelect(files) {
  if (!monitorConfigSelect) {
    return;
  }
  if (!files.length) {
    monitorConfigSelect.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(无配置文件)";
    monitorConfigSelect.appendChild(opt);
    return;
  }
  let saved = "";
  try {
    saved = localStorage.getItem(MONITOR_CONFIG_KEY) || "";
  } catch (_) {
    saved = "";
  }
  monitorConfigSelect.innerHTML = "";
  files.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    monitorConfigSelect.appendChild(opt);
  });
  const startupConfig = (startupSettings.auto_start_config || "").trim();
  if (startupConfig && files.includes(startupConfig)) {
    monitorConfigSelect.value = startupConfig;
  } else if (saved && files.includes(saved)) {
    monitorConfigSelect.value = saved;
  } else {
    const preferred = files.find((f) => f === "sample_config.json") || files[0];
    monitorConfigSelect.value = preferred;
  }
  try {
    localStorage.setItem(MONITOR_CONFIG_KEY, monitorConfigSelect.value);
  } catch (_) {
    // ignore
  }
}

function renderStartupSettings() {
  if (startupAutoEnabled) {
    startupAutoEnabled.checked = Boolean(startupSettings.auto_start_enabled);
  }
  if (startupDelaySeconds) {
    startupDelaySeconds.value = String(startupSettings.auto_start_delay_seconds ?? 3);
  }
  if (startupSettingsHint) {
    const filename = startupSettings.auto_start_config || "未设置";
    startupSettingsHint.textContent = startupSettings.auto_start_enabled
      ? `已启用：${filename}`
      : "未启用自动启动";
  }
}

async function loadStartupSettings() {
  try {
    startupSettings = await api("/api/collector/startup-settings");
  } catch (err) {
    if (startupSettingsHint) {
      startupSettingsHint.textContent = `启动设置加载失败: ${err.message || err}`;
    }
  }
  renderStartupSettings();
}

async function saveStartupSettings() {
  const filename = (monitorConfigSelect?.value || "").trim();
  const enabled = Boolean(startupAutoEnabled?.checked);
  if (enabled && !filename) {
    alert("请先选择配置文件");
    return;
  }
  const delay = Number(startupDelaySeconds?.value || 0);
  const result = await api("/api/collector/startup-settings", {
    method: "POST",
    body: JSON.stringify({
      auto_start_enabled: enabled,
      auto_start_config: filename,
      auto_start_delay_seconds: Number.isFinite(delay) ? delay : 0,
    }),
  });
  startupSettings = result.settings || startupSettings;
  renderStartupSettings();
  if (startupSettingsHint) {
    startupSettingsHint.textContent = enabled
      ? `已保存：启动 Web 后自动采集 ${startupSettings.auto_start_config}`
      : "已保存：不自动启动采集";
  }
}

async function refreshMonitorFileList() {
  try {
    const data = await api("/api/config/files");
    syncMonitorConfigSelect(data.files || []);
  } catch (err) {
    if (monitorConfigSelect) {
      monitorConfigSelect.innerHTML = "";
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = `(文件列表加载失败: ${err.message || err})`;
      monitorConfigSelect.appendChild(opt);
    }
  }
}

function renderMonitor(statusPayload, logLines) {
  if (!monitorStatusEl) {
    return;
  }
  const st = statusPayload || {
    phase: "unknown",
    task_active: false,
    snapshot: null,
    last_error: "状态暂不可用",
  };
  const snap = st.snapshot;
  let text = `阶段: ${st.phase}`;
  if (st.task_active) {
    text += " (后台任务活跃)";
  }
  text += "\n";
  if (st.last_error) {
    text += `错误: ${st.last_error}\n`;
  }
  if (snap && !snap.error) {
    text += `配置文件: ${snap.config_path || ""}\n`;
    text += `采集循环运行中: ${snap.collector_running ? "是" : "否"}\n`;
    text += `初始化完成: ${snap.initialized ? "是" : "否"}\n`;
    text += `数据库: ${snap.database_connected ? "连通" : "未连通"}\n`;
    const opc = snap.opcua || {};
    text += `OPC UA 全部连通: ${opc.all_connected ? "是" : "否"}\n`;
    if (opc.by_name && typeof opc.by_name === "object") {
      text += Object.entries(opc.by_name)
        .map(([k, v]) => `  - ${k}: ${v ? "连通" : "断开"}`)
        .join("\n");
      text += "\n";
    }
  } else if (snap && snap.error) {
    text += `状态快照异常: ${snap.error}\n`;
  } else {
    text += "（采集未启动，或正在初始化 — 无快照）\n";
  }
  monitorStatusEl.textContent = text.trimEnd();
  if (monitorLogEl) {
    const lines = Array.isArray(logLines) ? logLines : [];
    // 仅当用户当前在底部附近时，才自动跟随最新日志。
    // 若用户手动向上查看历史日志，则保持当前位置不被“抢焦点”。
    const distanceToBottom =
      monitorLogEl.scrollHeight - monitorLogEl.scrollTop - monitorLogEl.clientHeight;
    const shouldStickToBottom = distanceToBottom <= 24;
    monitorLogEl.textContent = lines.slice(-LOG_RENDER_LIMIT).join("\n");
    if (shouldStickToBottom) {
      monitorLogEl.scrollTop = monitorLogEl.scrollHeight;
    }
  }
}

async function pullLogsIncremental() {
  let pageCount = 0;
  let hasMore = true;
  while (hasMore && pageCount < LOG_FETCH_PAGES_PER_POLL) {
    const logs = await api(`/api/collector/logs?cursor=${encodeURIComponent(logCursor)}&limit=${LOG_FETCH_LIMIT}`);
    const lines = Array.isArray(logs.lines) ? logs.lines : [];
    const nextCursor = Number(logs.cursor || 0);
    const reset = Boolean(logs.reset);
    hasMore = Boolean(logs.has_more);

    if (reset) {
      lastLogLines = [];
      pausedBufferedCount = 0;
      pausedBufferedLines = [];
    }
    if (nextCursor >= logCursor) {
      logCursor = nextCursor;
    }
    if (lines.length) {
      if (logPaused) {
        pausedBufferedCount += lines.length;
        pausedBufferedLines.push(...lines);
        if (pausedBufferedLines.length > LOG_PAUSED_BUFFER_LIMIT) {
          pausedBufferedLines = pausedBufferedLines.slice(-LOG_PAUSED_BUFFER_LIMIT);
        }
      } else {
        appendLogLines(lines);
      }
    }
    pageCount += 1;
  }
}

async function pollMonitor() {
  if (!monitorStatusEl) {
    return;
  }
  if (monitorPollInFlight) {
    return;
  }
  monitorPollInFlight = true;
  try {
    const statusPromise = api("/api/collector/status")
      .then((st) => {
        lastStatusPayload = st;
        return st;
      })
      .catch((err) => {
        monitorStatusEl.textContent = `状态获取失败: ${err.message || err}`;
        return null;
      });

    const logsPromise = pullLogsIncremental()
      .catch((err) => {
        if (monitorLogEl) {
          monitorLogEl.textContent = `日志获取失败: ${err.message || err}`;
        }
        return null;
      });

    // 先渲染日志，避免被慢状态接口拖慢
    await logsPromise;
    updatePauseButtonLabel();
    renderMonitor(lastStatusPayload, lastLogLines);

    // 状态返回后再补一帧，更新顶部状态
    await statusPromise;
    renderMonitor(lastStatusPayload, lastLogLines);
  } catch (err) {
    monitorStatusEl.textContent = `状态获取失败: ${err.message || err}`;
  } finally {
    monitorPollInFlight = false;
  }
}

monitorConfigSelect?.addEventListener("change", () => {
  try {
    localStorage.setItem(MONITOR_CONFIG_KEY, monitorConfigSelect.value);
  } catch (_) {
    // ignore
  }
});

document.getElementById("btn-collector-start")?.addEventListener("click", () => {
  const fn = (monitorConfigSelect?.value || "").trim();
  if (!fn) {
    alert("请选择配置文件");
    return;
  }
  api("/api/collector/start", {
    method: "POST",
    body: JSON.stringify({ filename: fn }),
  })
    .then(() => pollMonitor())
    .catch((error) => alert(error.message));
});

document.getElementById("btn-collector-stop")?.addEventListener("click", () => {
  api("/api/collector/stop", { method: "POST", body: JSON.stringify({}) })
    .then(() => pollMonitor())
    .catch((error) => alert(error.message));
});

document.getElementById("btn-save-startup-settings")?.addEventListener("click", () => {
  saveStartupSettings().catch((error) => alert(error.message));
});

document.getElementById("btn-log-pause")?.addEventListener("click", () => {
  logPaused = !logPaused;
  if (!logPaused) {
    // 恢复后把暂停期间累计的增量显示出来（按游标已消费，不会重复拉取）。
    appendLogLines(pausedBufferedLines);
    pausedBufferedLines = [];
    pausedBufferedCount = 0;
    renderMonitor(lastStatusPayload, lastLogLines);
  }
  updatePauseButtonLabel();
});

async function initializeDashboard() {
  await loadStartupSettings();
  await refreshMonitorFileList();
  setInterval(pollMonitor, 1000);
  updatePauseButtonLabel();
  pollMonitor();
}

initializeDashboard().catch((error) => {
  if (monitorStatusEl) {
    monitorStatusEl.textContent = `初始化失败: ${error.message || error}`;
  }
});
