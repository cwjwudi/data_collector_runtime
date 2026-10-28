(function () {
  let currentPage = 1;
  let totalPages = 1;
  let totalRecords = null;
  let hasMore = false;
  let currentPageCursor = null;
  let nextPageCursor = null;
  let pageCursorStack = [];
  let currentBinding = null;
  let lastQueryContext = null;
  let selectedCursor = -1;
  let activePluginKey = "";
  let advancedOpcuaMode = false;
  let runtimeRevision = 0;
  let runtimePollTimer = null;
  let runtimePollGeneration = 0;
  let pluginStateKey = null;
  let currentQueryConditions = [];
  let timeRangePicker = null;

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
  const quickPresets = {
    btnRange15Min: "last15m",
    btnRange1H: "last1h",
    btnRange8H: "last8h",
    btnRangeToday: "today",
    btnRangeYesterday: "yesterday",
    btnRange1W: "last1w",
  };
  const quickButtons = Object.keys(quickPresets);

  async function fetchJson(url, opts) {
    const resp = await fetch(url, opts);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));
    return data;
  }

  function getPluginKeyFromPath() {
    const match = window.location.pathname.match(/\/plugins\/([^/]+)\.html$/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function enableButtonClickFeedback() {
    for (const btn of document.querySelectorAll("button")) {
      btn.addEventListener("click", () => {
        btn.classList.remove("is-clicked");
        void btn.offsetWidth;
        btn.classList.add("is-clicked");
        setTimeout(() => btn.classList.remove("is-clicked"), 220);
      });
    }
  }

  function setQuickActive(id) {
    for (const btnId of quickButtons) {
      const button = document.getElementById(btnId);
      button?.classList.remove("active");
      button?.setAttribute("aria-pressed", "false");
    }
    const activeButton = document.getElementById(id);
    activeButton?.classList.add("active");
    activeButton?.setAttribute("aria-pressed", "true");
  }

  function applyQuickRange(preset, buttonId) {
    timeRangePicker.applyPreset(preset);
    setQuickActive(buttonId);
    savePluginState();
  }

  function getActiveQuickButtonId() {
    return quickButtons.find((id) => document.getElementById(id)?.classList.contains("active")) || "";
  }

  function formatClock(value) {
    const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
    if (!match) return "";
    return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6] || "00"}`;
  }

  function syncTimeDisplays() {
    for (const [inputId, displayId] of [["startDate", "startTimeDisplay"], ["endDate", "endTimeDisplay"]]) {
      const text = formatClock(document.getElementById(inputId).value);
      const display = document.getElementById(displayId);
      display.textContent = text || "未设置";
      display.classList.toggle("is-empty", !text);
    }
  }

  function updateConditionControls() {
    const locked = advancedOpcuaMode;
    document.getElementById("queryCondition").disabled = locked;
    document.getElementById("queryConditionValue").disabled = locked || !document.getElementById("queryCondition").value;
    timeRangePicker?.setDisabled(locked);
    for (const id of ["startTimeDisplay", "endTimeDisplay", ...quickButtons]) {
      document.getElementById(id).disabled = locked;
    }
  }

  async function loadConditionValues(preferredValue) {
    const valueSelect = document.getElementById("queryConditionValue");
    const name = document.getElementById("queryCondition").value || "";
    valueSelect.innerHTML = '<option value="">请选择</option>';
    valueSelect.disabled = advancedOpcuaMode || !name;
    if (!name || advancedOpcuaMode) return;
    const data = await fetchJson(
      `/api/meta/condition-values?view_name=${encodeURIComponent(currentBinding.view_name)}` +
        `&group=${encodeURIComponent(currentBinding.resolved_group)}` +
        `&name=${encodeURIComponent(name)}`,
    );
    for (const item of data.items || []) {
      const option = document.createElement("option");
      option.value = item;
      option.textContent = item;
      valueSelect.appendChild(option);
    }
    if (preferredValue && Array.from(valueSelect.options).some((option) => option.value === preferredValue)) {
      valueSelect.value = preferredValue;
    }
  }

  async function loadQueryConditions(preferredName, preferredValue) {
    const select = document.getElementById("queryCondition");
    select.innerHTML = '<option value="">不附加</option>';
    currentQueryConditions = [];
    if (advancedOpcuaMode || !currentBinding?.view_name || !currentBinding?.resolved_group) {
      updateConditionControls();
      return;
    }
    try {
      const data = await fetchJson(
        `/api/meta/query-conditions?view_name=${encodeURIComponent(currentBinding.view_name)}` +
          `&group=${encodeURIComponent(currentBinding.resolved_group)}`,
      );
      currentQueryConditions = Array.isArray(data.conditions) ? data.conditions : [];
      for (const item of currentQueryConditions) {
        const option = document.createElement("option");
        option.value = item.name;
        option.textContent = item.name;
        select.appendChild(option);
      }
      if (preferredName && currentQueryConditions.some((item) => item.name === preferredName)) {
        select.value = preferredName;
      }
      await loadConditionValues(preferredValue);
    } catch (error) {
      document.getElementById("meta").textContent = error.message;
    }
    updateConditionControls();
  }

  function savePluginState() {
    if (!pluginStateKey) return;
    safeStorageSet(pluginStateKey, JSON.stringify({
      startDate: document.getElementById("startDate").value || "",
      endDate: document.getElementById("endDate").value || "",
      conditionName: document.getElementById("queryCondition").value || "",
      conditionValue: document.getElementById("queryConditionValue").value || "",
      table: document.getElementById("tableSelector").value || "",
      currentPage,
      totalPages,
      totalRecords,
      hasMore,
      currentPageCursor,
      nextPageCursor,
      pageCursorStack,
      lastQueryContext,
      quickActive: getActiveQuickButtonId(),
    }));
  }

  function loadPluginState() {
    if (!pluginStateKey) return null;
    try {
      return JSON.parse(safeStorageGet(pluginStateKey) || "null");
    } catch {
      return null;
    }
  }

  function updatePagerMeta(binding, warnings) {
    const totalKnown = totalRecords !== null && Number.isFinite(totalRecords);
    const infoParts = [
      `plugin=${binding.plugin_key}`,
      `group=${binding.resolved_group || "-"}`,
      `table=${document.getElementById("tableSelector").value || binding.resolved_table || "-"}`,
      `current=${currentPage}`,
      `has_more=${hasMore}`,
    ];
    if (totalKnown) {
      infoParts.push(`total=${totalRecords}`, `pages=${totalPages}`);
    }
    if (!advancedOpcuaMode) {
      const condition = currentQueryConditions.find((item) => item.name === document.getElementById("queryCondition").value);
      if (condition) infoParts.push(`condition=${condition.name}:${condition.source_table}.${condition.source_field}`);
    }
    if (Array.isArray(warnings) && warnings.length) infoParts.push(`warnings=${warnings.join(" | ")}`);
    document.getElementById("meta").textContent = infoParts.join(" ; ");
    document.getElementById("pageNumber").value = String(currentPage);
    document.getElementById("totalPageCount").textContent = totalKnown ? String(totalPages) : "—";
    document.getElementById("recordSummary").textContent = totalKnown
      ? `共 ${totalRecords} 条`
      : "总数统计中";
    document.getElementById("btnPrevPage").disabled = currentPage <= 1;
    document.getElementById("btnNextPage").disabled = advancedOpcuaMode
      ? currentPage >= totalPages
      : !hasMore;
  }

  function renderTable(columns, displayColumns, rows) {
    const table = document.getElementById("resultTable");
    table.innerHTML = "";
    const displayMap = {};
    for (const item of displayColumns || []) if (item?.name) displayMap[item.name] = item;
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    for (const column of columns || []) {
      const th = document.createElement("th");
      const meta = displayMap[column] || { label_zh: column, label_en: column };
      th.textContent = `${meta.label_zh || column} (${meta.label_en || column})`;
      headerRow.appendChild(th);
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (let rowIndex = 0; rowIndex < (rows || []).length; rowIndex += 1) {
      const row = rows[rowIndex];
      const tr = document.createElement("tr");
      if (rowIndex === selectedCursor) tr.classList.add("row-selected");
      tr.addEventListener("click", () => {
        selectedCursor = rowIndex;
        renderTable(columns, displayColumns, rows);
        fetch(`/api/plugins/cursor/${encodeURIComponent(activePluginKey)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cursor: rowIndex }),
        }).catch(() => {});
      });
      for (const column of columns || []) {
        const td = document.createElement("td");
        td.textContent = row[column] == null ? "" : String(row[column]);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
  }

  function isAdvancedTableListWriteback(binding) {
    const config = binding?.table_list_writeback;
    if (!config?.enabled) return false;
    const mode = String(config.mode || "").trim().toLowerCase();
    if (mode === "advanced" || mode === "opcua") return true;
    const advanced = typeof config.advanced === "object" ? config.advanced : {};
    return Boolean(String(advanced.trigger_node || "").trim() && String(advanced.batch_no_node || "").trim());
  }

  function applyQueryResult(data, targetPage, requestedCursor, proposedStack) {
    renderTable(data.columns || [], data.display_columns || [], data.rows || []);
    selectedCursor = -1;
    if (data.total != null) totalRecords = Math.max(0, Number(data.total) || 0);
    hasMore = Boolean(data.has_more);
    currentPage = Math.max(1, Number(data.page || targetPage));
    const pageSize = Math.max(1, Number(data.page_size || currentBinding.page_size || 10));
    if (advancedOpcuaMode) {
      totalPages = Math.max(1, Math.ceil((totalRecords || 0) / pageSize));
    } else {
      currentPageCursor = requestedCursor;
      nextPageCursor = data.next_cursor || null;
      pageCursorStack = proposedStack;
      totalPages = totalRecords !== null
        ? Math.max(1, Math.ceil(totalRecords / pageSize))
        : currentPage + (hasMore ? 1 : 0);
    }
    updatePagerMeta(currentBinding, data.warnings || []);
    savePluginState();
  }

  function buildQueryContext() {
    const context = {
      table: document.getElementById("tableSelector").value || undefined,
      startTime: null,
      endTime: null,
      conditionName: "",
      conditionValue: "",
    };
    if (advancedOpcuaMode) return context;
    context.startTime = document.getElementById("startDate").value;
    context.endTime = document.getElementById("endDate").value;
    if (Boolean(context.startTime) !== Boolean(context.endTime)) {
      throw new Error("开始时间和结束时间需要同时填写，或同时留空");
    }
    if (context.startTime && context.endTime && new Date(context.startTime).getTime() > new Date(context.endTime).getTime()) {
      throw new Error("开始时间不能大于结束时间");
    }
    context.conditionName = document.getElementById("queryCondition").value || "";
    context.conditionValue = document.getElementById("queryConditionValue").value || "";
    return context;
  }

  function buildPayload(context, page, pageCursor, includeTotal = false) {
    const payload = {
      page,
      page_size: currentBinding.page_size || 10,
      table: context.table,
      cursor: -1,
      pagination_mode: advancedOpcuaMode ? "offset" : "cursor",
      include_total: advancedOpcuaMode || Boolean(includeTotal),
    };
    if (context.startTime) payload.start_time = context.startTime;
    if (context.endTime) payload.end_time = context.endTime;
    if (context.conditionName && context.conditionValue) {
      payload.condition_name = context.conditionName;
      payload.condition_value = context.conditionValue;
    }
    if (pageCursor) payload.page_cursor = pageCursor;
    return payload;
  }

  async function runFreshQuery(page = 1) {
    lastQueryContext = buildQueryContext();
    const targetPage = advancedOpcuaMode ? Math.max(1, page) : 1;
    const data = await fetchJson(`/api/plugins/query/${encodeURIComponent(activePluginKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload(lastQueryContext, targetPage, null, true)),
    });
    if (!advancedOpcuaMode) totalRecords = null;
    applyQueryResult(data, targetPage, null, []);
  }

  async function runAdjacentPage(direction) {
    if (!lastQueryContext && !advancedOpcuaMode) return;
    if (advancedOpcuaMode) {
      const targetPage = Math.min(Math.max(currentPage + direction, 1), totalPages);
      const data = await fetchJson(`/api/plugins/snapshot-page/${encodeURIComponent(activePluginKey)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page: targetPage }),
      });
      applyQueryResult(data, targetPage, null, []);
      return;
    }

    let requestedCursor = currentPageCursor;
    const proposedStack = [...pageCursorStack];
    let targetPage = currentPage;
    if (direction > 0) {
      if (!hasMore || !nextPageCursor) return;
      proposedStack.push(currentPageCursor);
      requestedCursor = nextPageCursor;
      targetPage += 1;
    } else {
      if (currentPage <= 1) return;
      requestedCursor = proposedStack.length ? proposedStack.pop() : null;
      targetPage -= 1;
    }
    const data = await fetchJson(`/api/plugins/query/${encodeURIComponent(activePluginKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...buildPayload(lastQueryContext, targetPage, requestedCursor), write_sql: false }),
    });
    applyQueryResult(data, targetPage, requestedCursor, proposedStack);
  }

  function applyRuntimeState(state) {
    if (!state) return;
    renderTable(state.columns || [], state.display_columns || [], state.rows || []);
    selectedCursor = -1;
    totalRecords = Number(state.total_records || 0);
    totalPages = Math.max(1, Number(state.total_pages || 1));
    currentPage = Math.min(Math.max(Number(state.page || 1), 1), totalPages);
    const warnings = [...(state.warnings || [])];
    if (advancedOpcuaMode && state.has_snapshot === false) {
      warnings.push("尚未建立查询快照，请先点击查询");
    }
    updatePagerMeta(currentBinding, warnings);
    savePluginState();
  }

  function stopRuntimeStatePolling() {
    runtimePollGeneration += 1;
    if (runtimePollTimer) clearTimeout(runtimePollTimer);
    runtimePollTimer = null;
  }

  function scheduleRuntimeStatePoll(pluginKey, generation, delayMs = 1000) {
    if (generation !== runtimePollGeneration || document.hidden) return;
    runtimePollTimer = setTimeout(() => {
      pollRuntimeState(pluginKey, generation).catch(() => {});
    }, delayMs);
  }

  async function pollRuntimeState(pluginKey, generation) {
    if (generation !== runtimePollGeneration || document.hidden) return;
    try {
      const state = await fetchJson(
        `/api/plugins/runtime-state/${encodeURIComponent(pluginKey)}?since_revision=${runtimeRevision}`,
      );
      if (generation !== runtimePollGeneration) return;
      if (state.changed !== false) {
        runtimeRevision = Number(state.revision || 0);
        applyRuntimeState(state);
      }
    } finally {
      scheduleRuntimeStatePoll(pluginKey, generation);
    }
  }

  function startRuntimeStatePolling(pluginKey) {
    stopRuntimeStatePolling();
    const generation = runtimePollGeneration;
    scheduleRuntimeStatePoll(pluginKey, generation);
  }

  function populateTables(savedTable) {
    const selector = document.getElementById("tableSelector");
    selector.innerHTML = "";
    const schema = currentBinding.schema_report || {};
    const tables = Array.isArray(schema.tables) ? schema.tables : [];
    const defaultTable = currentBinding.resolved_table || "";
    for (const table of tables) {
      const option = document.createElement("option");
      option.value = table;
      option.textContent = schema.table_kinds?.[table] === "fixed" ? `${table}（固定表）` : table;
      selector.appendChild(option);
    }
    if (!tables.length && defaultTable) {
      const option = document.createElement("option");
      option.value = defaultTable;
      option.textContent = defaultTable;
      selector.appendChild(option);
    }
    const preferred = savedTable && Array.from(selector.options).some((item) => item.value === savedTable)
      ? savedTable
      : defaultTable;
    if (preferred) selector.value = preferred;
  }

  async function run() {
    activePluginKey = getPluginKeyFromPath();
    if (!activePluginKey) throw new Error("无法识别插件路径");
    pluginStateKey = `sd_sma_plugin_state_${activePluginKey}`;
    timeRangePicker = window.TouchTimeRange.attach({
      idPrefix: "plugin-time",
      startInputId: "startDate",
      endInputId: "endDate",
      triggerId: "btnPreciseTime",
      summaryId: "pluginTimeSummary",
    });
    currentBinding = await fetchJson(`/api/plugins/resolve/${encodeURIComponent(activePluginKey)}`);
    advancedOpcuaMode = isAdvancedTableListWriteback(currentBinding);
    const saved = loadPluginState();
    populateTables(saved?.table);
    if (saved?.startDate) document.getElementById("startDate").value = saved.startDate;
    if (saved?.endDate) document.getElementById("endDate").value = saved.endDate;
    timeRangePicker.refresh();
    if (!saved) {
      if (!advancedOpcuaMode) applyQuickRange("last1h", "btnRange1H");
      else setQuickActive("");
    } else {
      setQuickActive(saved.quickActive || "");
    }
    syncTimeDisplays();
    await loadQueryConditions(saved?.conditionName, saved?.conditionValue);
    updateConditionControls();

    document.getElementById("btnGo").addEventListener("click", () => {
      runFreshQuery(1).catch((error) => (document.getElementById("meta").textContent = error.message));
    });
    document.getElementById("tableSelector").addEventListener("change", () => {
      savePluginState();
      if (!advancedOpcuaMode) {
        runFreshQuery(1).catch((error) => (document.getElementById("meta").textContent = error.message));
      }
    });
    document.getElementById("btnPrevPage").addEventListener("click", () => {
      runAdjacentPage(-1).catch((error) => (document.getElementById("meta").textContent = error.message));
    });
    document.getElementById("btnNextPage").addEventListener("click", () => {
      runAdjacentPage(1).catch((error) => (document.getElementById("meta").textContent = error.message));
    });
    document.getElementById("queryCondition").addEventListener("change", () => {
      loadConditionValues()
        .then(() => {
          updateConditionControls();
          savePluginState();
        })
        .catch((error) => {
          document.getElementById("meta").textContent = error.message;
        });
    });
    document.getElementById("queryConditionValue").addEventListener("change", savePluginState);
    for (const [id, preset] of Object.entries(quickPresets)) {
      document.getElementById(id).addEventListener("click", () => applyQuickRange(preset, id));
    }
    for (const id of ["startTimeDisplay", "endTimeDisplay"]) {
      document.getElementById(id).addEventListener("click", () => {
        if (document.getElementById(id).disabled) return;
        document.getElementById("btnPreciseTime").click();
      });
    }
    for (const id of ["startDate", "endDate"]) {
      document.getElementById(id).addEventListener("input", () => {
        setQuickActive("");
        syncTimeDisplays();
        savePluginState();
      });
    }

    enableButtonClickFeedback();
    if (advancedOpcuaMode) {
      const state = await fetchJson(`/api/plugins/runtime-state/${encodeURIComponent(activePluginKey)}`);
      runtimeRevision = Number(state.revision || 0);
      applyRuntimeState(state);
      startRuntimeStatePolling(activePluginKey);
    } else {
      await runFreshQuery(1);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!advancedOpcuaMode || !activePluginKey) return;
    if (document.hidden) {
      stopRuntimeStatePolling();
    } else {
      startRuntimeStatePolling(activePluginKey);
      const generation = runtimePollGeneration;
      if (runtimePollTimer) clearTimeout(runtimePollTimer);
      runtimePollTimer = null;
      pollRuntimeState(activePluginKey, generation).catch(() => {});
    }
  });
  window.addEventListener("pagehide", stopRuntimeStatePolling);
  window.addEventListener("beforeunload", stopRuntimeStatePolling);

  run().catch((error) => {
    document.getElementById("meta").textContent = error.message;
  });
})();
