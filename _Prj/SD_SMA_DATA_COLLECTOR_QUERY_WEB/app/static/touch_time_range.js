(function (global) {
  "use strict";

  const pad = (value) => String(value).padStart(2, "0");

  function parseLocalValue(value) {
    const match = String(value || "").match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
    );
    if (!match) return null;
    const date = new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] || 0),
      0,
    );
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatLocalValue(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
      `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function formatDisplay(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
      `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function formatDuration(milliseconds) {
    const totalMinutes = Math.max(0, Math.round(milliseconds / 60000));
    const days = Math.floor(totalMinutes / 1440);
    const hours = Math.floor((totalMinutes % 1440) / 60);
    const minutes = totalMinutes % 60;
    const parts = [];
    if (days) parts.push(`${days}天`);
    if (hours) parts.push(`${hours}小时`);
    if (minutes || !parts.length) parts.push(`${minutes}分钟`);
    return parts.join("");
  }

  function presetRange(preset, now = new Date()) {
    const end = new Date(now.getTime());
    let start = new Date(now.getTime());
    if (preset === "last15m") start = new Date(end.getTime() - 15 * 60 * 1000);
    else if (preset === "last1h") start = new Date(end.getTime() - 60 * 60 * 1000);
    else if (preset === "last8h") start = new Date(end.getTime() - 8 * 60 * 60 * 1000);
    else if (preset === "today") start.setHours(0, 0, 0, 0);
    else if (preset === "yesterday") {
      start.setDate(start.getDate() - 1);
      start.setHours(0, 0, 0, 0);
      end.setDate(end.getDate() - 1);
      end.setHours(23, 59, 59, 0);
    } else if (preset === "last1w") start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
    else if (preset === "last1m") start = new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000);
    else throw new Error(`未知时间范围: ${preset}`);
    return { start, end };
  }

  function editorMarkup(prefix, side, label) {
    return (
      `<section class="touch-time-editor" data-side="${side}">` +
        `<div class="touch-time-editor-title">${label}</div>` +
        `<label class="touch-time-date-label" for="${prefix}-${side}-date">日期</label>` +
        `<input id="${prefix}-${side}-date" class="touch-time-date" type="date" />` +
        `<div class="touch-time-segments">` +
          `<label>时<input id="${prefix}-${side}-hour" type="number" inputmode="numeric" min="0" max="23" /></label>` +
          `<span aria-hidden="true">:</span>` +
          `<label>分<input id="${prefix}-${side}-minute" type="number" inputmode="numeric" min="0" max="59" /></label>` +
          `<span aria-hidden="true">:</span>` +
          `<label>秒<input id="${prefix}-${side}-second" type="number" inputmode="numeric" min="0" max="59" /></label>` +
        `</div>` +
        `<div class="touch-time-adjust" aria-label="${label}微调">` +
          `<button type="button" data-adjust-side="${side}" data-adjust-seconds="-600">−10分</button>` +
          `<button type="button" data-adjust-side="${side}" data-adjust-seconds="-60">−1分</button>` +
          `<button type="button" data-adjust-side="${side}" data-adjust-seconds="60">+1分</button>` +
          `<button type="button" data-adjust-side="${side}" data-adjust-seconds="600">+10分</button>` +
        `</div>` +
        `<button type="button" class="touch-time-now" data-now-side="${side}">设为当前时间</button>` +
      `</section>`
    );
  }

  function attach(config) {
    const prefix = String(config.idPrefix || "touch-time");
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(prefix)) throw new Error("时间选择器 ID 前缀无效");
    const startInput = document.getElementById(config.startInputId);
    const endInput = document.getElementById(config.endInputId);
    const trigger = document.getElementById(config.triggerId);
    const summary = document.getElementById(config.summaryId);
    if (!startInput || !endInput || !trigger || !summary) {
      throw new Error("时间选择器缺少必要页面元素");
    }

    const overlay = document.createElement("div");
    overlay.className = "touch-time-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
      `<section class="touch-time-dialog" role="dialog" aria-modal="true" aria-labelledby="${prefix}-title">` +
        `<div class="touch-time-dialog-header">` +
          `<h3 id="${prefix}-title">精确时间范围</h3>` +
          `<button type="button" class="touch-time-close" aria-label="关闭精确时间选择">×</button>` +
        `</div>` +
        `<p class="touch-time-dialog-hint">点击日期后选择年月日；时、分、秒可直接输入，也可用大按钮微调。</p>` +
        `<div class="touch-time-editors">` +
          editorMarkup(prefix, "start", "开始时间") +
          editorMarkup(prefix, "end", "结束时间") +
        `</div>` +
        `<div class="touch-time-dialog-tools">` +
          `<button type="button" data-copy-start>结束时间 = 开始时间</button>` +
        `</div>` +
        `<div class="touch-time-error" role="alert" aria-live="assertive"></div>` +
        `<div class="touch-time-dialog-footer">` +
          `<button type="button" data-cancel>取消</button>` +
          `<button type="button" class="touch-time-apply" data-apply>应用时间范围</button>` +
        `</div>` +
      `</section>`;
    document.body.appendChild(overlay);

    const field = (side, part) => document.getElementById(`${prefix}-${side}-${part}`);
    const error = overlay.querySelector(".touch-time-error");
    const closeButton = overlay.querySelector(".touch-time-close");
    const cancelButton = overlay.querySelector("[data-cancel]");
    const applyButton = overlay.querySelector("[data-apply]");

    function writeEditor(side, date) {
      field(side, "date").value =
        `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
      field(side, "hour").value = String(date.getHours());
      field(side, "minute").value = String(date.getMinutes());
      field(side, "second").value = String(date.getSeconds());
    }

    function readEditor(side, label) {
      const dateValue = field(side, "date").value;
      const hour = Number(field(side, "hour").value);
      const minute = Number(field(side, "minute").value);
      const second = Number(field(side, "second").value);
      if (!dateValue) throw new Error(`请选择${label}日期`);
      if (!Number.isInteger(hour) || hour < 0 || hour > 23) throw new Error(`${label}小时应为 0–23`);
      if (!Number.isInteger(minute) || minute < 0 || minute > 59) throw new Error(`${label}分钟应为 0–59`);
      if (!Number.isInteger(second) || second < 0 || second > 59) throw new Error(`${label}秒应为 0–59`);
      const [year, month, day] = dateValue.split("-").map(Number);
      return new Date(year, month - 1, day, hour, minute, second, 0);
    }

    function setError(message) {
      error.textContent = String(message || "");
    }

    function refresh() {
      const start = parseLocalValue(startInput.value);
      const end = parseLocalValue(endInput.value);
      if (!start || !end) {
        summary.textContent = "尚未设置时间范围";
        summary.classList.add("is-empty");
        return;
      }
      summary.classList.remove("is-empty");
      summary.textContent =
        `${formatDisplay(start)} → ${formatDisplay(end)} · 共 ${formatDuration(end.getTime() - start.getTime())}`;
    }

    function dispatchValueEvents(input) {
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function setRange(start, end) {
      startInput.value = formatLocalValue(start);
      endInput.value = formatLocalValue(end);
      dispatchValueEvents(startInput);
      dispatchValueEvents(endInput);
      refresh();
    }

    function open() {
      let start = parseLocalValue(startInput.value);
      let end = parseLocalValue(endInput.value);
      if (!start || !end) {
        end = new Date();
        start = new Date(end.getTime() - 60 * 60 * 1000);
      }
      writeEditor("start", start);
      writeEditor("end", end);
      setError("");
      overlay.hidden = false;
      field("start", "date").focus();
    }

    function close() {
      overlay.hidden = true;
      setError("");
      trigger.focus();
    }

    trigger.addEventListener("click", open);
    closeButton.addEventListener("click", close);
    cancelButton.addEventListener("click", close);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !overlay.hidden) close();
    });

    for (const button of overlay.querySelectorAll("[data-adjust-side]")) {
      button.addEventListener("click", () => {
        const side = button.dataset.adjustSide;
        try {
          const date = readEditor(side, side === "start" ? "开始" : "结束");
          date.setSeconds(date.getSeconds() + Number(button.dataset.adjustSeconds || 0));
          writeEditor(side, date);
          setError("");
        } catch (err) {
          setError(err.message);
        }
      });
    }
    for (const button of overlay.querySelectorAll("[data-now-side]")) {
      button.addEventListener("click", () => {
        writeEditor(button.dataset.nowSide, new Date());
        setError("");
      });
    }
    overlay.querySelector("[data-copy-start]").addEventListener("click", () => {
      try {
        writeEditor("end", readEditor("start", "开始"));
        setError("");
      } catch (err) {
        setError(err.message);
      }
    });
    applyButton.addEventListener("click", () => {
      try {
        const start = readEditor("start", "开始");
        const end = readEditor("end", "结束");
        if (start.getTime() > end.getTime()) throw new Error("结束时间不能早于开始时间");
        setRange(start, end);
        close();
      } catch (err) {
        setError(err.message);
      }
    });

    startInput.addEventListener("input", refresh);
    startInput.addEventListener("change", refresh);
    endInput.addEventListener("input", refresh);
    endInput.addEventListener("change", refresh);
    refresh();

    return {
      applyPreset(preset) {
        const range = presetRange(preset);
        setRange(range.start, range.end);
      },
      refresh,
      setDisabled(disabled) {
        trigger.disabled = Boolean(disabled);
      },
    };
  }

  global.TouchTimeRange = { attach, presetRange };
})(window);
