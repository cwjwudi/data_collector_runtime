(function (global) {
  "use strict";

  const t = (key, params) => global.SdI18n.t(key, params);
  const pad = (value) => String(value).padStart(2, "0");
  const widths = [4, 2, 2, 2, 2, 2];
  const minimums = [1, 1, 1, 0, 0, 0];
  const maximums = [9999, 12, 31, 23, 59, 59];
  const labels = () => [t('query.time.year'), t('query.time.month'), t('query.time.day'),
    t('query.time.hour'), t('query.time.minute'), t('query.time.second')];
  const sideLabel = (side) => side === "start" ? t('query.time.start') : t('query.time.end');

  function dateFromParts(parts) {
    if (parts.some((value, index) => !/^\d+$/.test(value) ||
      Number(value) < minimums[index] || Number(value) > maximums[index])) return null;
    if (parts[0].length !== 4) return null;
    const [year, month, day, hour, minute, second] = parts.map(Number);
    const date = new Date(0);
    date.setFullYear(year, month - 1, day);
    date.setHours(hour, minute, second, 0);
    return date.getFullYear() === year && date.getMonth() === month - 1 &&
      date.getDate() === day ? date : null;
  }

  function parseLocalValue(value) {
    const match = String(value || "").match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
    );
    return match ? dateFromParts([...match.slice(1, 6), match[6] || "00"]) : null;
  }

  function dateParts(date) {
    return [String(date.getFullYear()).padStart(4, "0"), pad(date.getMonth() + 1),
      pad(date.getDate()), pad(date.getHours()), pad(date.getMinutes()), pad(date.getSeconds())];
  }

  function formatParts(parts, separator = " ") {
    return `${parts.slice(0, 3).join("-")}${separator}${parts.slice(3).join(":")}`;
  }

  function formatDuration(milliseconds) {
    const total = Math.max(0, Math.floor(milliseconds / 1000));
    return t('query.time.duration', { days: Math.floor(total / 86400),
      hours: Math.floor(total % 86400 / 3600), minutes: Math.floor(total % 3600 / 60), seconds: total % 60 });
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
    else throw new Error(t('query.time.unknownPreset', { preset }));
    return { start, end };
  }

  function segmentsMarkup(offset) {
    return `<div class="touch-time-segments" role="group" aria-label="${offset === 0 ? t('query.time.date') : t('query.time.clock')}">` +
      labels().slice(offset, offset + 3).map((label, index) =>
        (index ? `<span aria-hidden="true">${offset === 0 ? "/" : ":"}</span>` : "") +
        `<button type="button" data-part="${offset + index}" aria-pressed="false"><span>${label}</span><b></b></button>`
      ).join("") + `</div>`;
  }

  function attach(config) {
    const prefix = String(config.idPrefix || "touch-time");
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(prefix)) throw new Error(t('query.time.invalidPrefix'));
    const startInput = document.getElementById(config.startInputId);
    const endInput = document.getElementById(config.endInputId);
    const trigger = document.getElementById(config.triggerId);
    const summary = document.getElementById(config.summaryId);
    if (!startInput || !endInput || !trigger || !summary) throw new Error(t('query.time.missingElements'));

    const overlay = document.createElement("div");
    overlay.className = "touch-time-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
      `<section class="touch-time-dialog" role="dialog" aria-modal="true" aria-labelledby="${prefix}-title">` +
        `<div class="touch-time-dialog-header"><h3 id="${prefix}-title">${t('query.time.title')}</h3>` +
          `<button type="button" class="touch-time-close" aria-label="${t('query.time.close')}">×</button></div>` +
        `<div class="touch-time-endpoints">` +
          ["start", "end"].map(side => `<button type="button" data-side="${side}" aria-pressed="false">` +
            `<span>${sideLabel(side)}</span><b data-summary="${side}"></b></button>`).join("") + `</div>` +
        segmentsMarkup(0) + segmentsMarkup(3) +
        `<div class="touch-time-editing" aria-live="polite"><span data-editing></span><span data-width></span></div>` +
        `<div class="touch-time-keypad" role="group" aria-label="${t('query.time.keypad')}">` +
          [1, 2, 3, 4, 5, 6, 7, 8, 9].map(digit => `<button type="button" data-digit="${digit}">${digit}</button>`).join("") +
          `<button type="button" data-back aria-label="${t('query.time.backAria')}">${t('query.time.back')}</button>` +
          `<button type="button" data-digit="0">0</button><button type="button" data-next></button></div>` +
        `<p class="touch-time-dialog-hint">${t('query.time.hint')}</p>` +
        `<div class="touch-time-duration" aria-live="polite"></div>` +
        `<div class="touch-time-error" role="alert"></div>` +
        `<div class="touch-time-dialog-footer"><button type="button" data-cancel>${t('query.time.cancel')}</button>` +
          `<button type="button" class="touch-time-apply" data-apply>${t('query.time.apply')}</button></div>` +
      `</section>`;
    document.body.appendChild(overlay);

    const error = overlay.querySelector(".touch-time-error");
    const partButtons = [...overlay.querySelectorAll("[data-part]")];
    const draft = { start: [], end: [] };
    let side = "start", part = 0, typing = false;

    function renderEditor() {
      for (const button of overlay.querySelectorAll("[data-side]")) {
        button.setAttribute("aria-pressed", String(button.dataset.side === side));
      }
      for (const value of overlay.querySelectorAll("[data-summary]")) {
        value.textContent = formatParts(draft[value.dataset.summary]);
      }
      for (const button of partButtons) {
        const index = Number(button.dataset.part);
        const value = draft[side][index];
        const active = index === part;
        button.setAttribute("aria-pressed", String(active));
        button.setAttribute("aria-label", `${labels()[index]} ${value}`);
        button.querySelector("b").textContent = active && typing ? value.padEnd(widths[index], "_") : value;
      }
      overlay.querySelector("[data-editing]").textContent = t('query.time.editing', { side: sideLabel(side), field: labels()[part] });
      overlay.querySelector("[data-width]").textContent = t('query.time.digits', { count: widths[part] });
      overlay.querySelector("[data-next]").textContent = side === "end" && part === 5 ? t('query.time.done') : t('query.time.next');
      const start = dateFromParts(draft.start), end = dateFromParts(draft.end);
      overlay.querySelector(".touch-time-duration").textContent = start && end && end >= start ?
        formatDuration(end - start) : t('query.time.pending');
    }

    function commitField() {
      const value = draft[side][part];
      if (!/^\d+$/.test(value) || Number(value) < minimums[part] || Number(value) > maximums[part] ||
        (part === 0 && value.length !== 4)) {
        error.textContent = t('query.time.invalidField', { field: labels()[part], min: minimums[part], max: maximums[part], count: widths[part] });
        return false;
      }
      draft[side][part] = value.padStart(widths[part], "0");
      typing = false;
      error.textContent = "";
      return true;
    }

    function advance() {
      if (!commitField()) return;
      if (part < 5) part++;
      else if (side === "start") { side = "end"; part = 0; }
      partButtons[part].focus();
    }

    function enterDigit(digit) {
      error.textContent = "";
      if (!typing || draft[side][part].length >= widths[part]) draft[side][part] = "";
      draft[side][part] += digit;
      typing = true;
      if (draft[side][part].length === widths[part]) advance();
      renderEditor();
    }

    function backspace() {
      error.textContent = "";
      draft[side][part] = draft[side][part].slice(0, -1);
      typing = true;
      renderEditor();
    }

    function refresh() {
      const start = parseLocalValue(startInput.value), end = parseLocalValue(endInput.value);
      summary.classList.toggle("is-empty", !start || !end);
      summary.textContent = start && end ? t('query.time.summary', {
        start: formatParts(dateParts(start)), end: formatParts(dateParts(end)), duration: formatDuration(end - start),
      }) : t('query.time.empty');
    }

    function setRange(start, end) {
      startInput.value = formatParts(dateParts(start), "T");
      endInput.value = formatParts(dateParts(end), "T");
      for (const input of [startInput, endInput]) {
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
      refresh();
    }

    function open() {
      const end = parseLocalValue(endInput.value) || new Date();
      const start = parseLocalValue(startInput.value) || new Date(end.getTime() - 60 * 60 * 1000);
      draft.start = dateParts(start);
      draft.end = dateParts(end);
      side = "start"; part = 0; typing = false;
      error.textContent = "";
      renderEditor();
      overlay.hidden = false;
      partButtons[0].focus();
    }

    function close() {
      overlay.hidden = true;
      error.textContent = "";
      trigger.focus();
    }

    trigger.addEventListener("click", open);
    overlay.querySelector(".touch-time-close").addEventListener("click", close);
    overlay.querySelector("[data-cancel]").addEventListener("click", close);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    for (const button of overlay.querySelectorAll("[data-side], [data-part]")) {
      button.addEventListener("click", () => {
        if (commitField()) {
          if (button.dataset.side) { side = button.dataset.side; part = 0; }
          else part = Number(button.dataset.part);
        }
        renderEditor();
      });
    }
    for (const button of overlay.querySelectorAll("[data-digit]")) {
      button.addEventListener("click", () => enterDigit(button.dataset.digit));
    }
    overlay.querySelector("[data-back]").addEventListener("click", backspace);
    overlay.querySelector("[data-next]").addEventListener("click", () => { advance(); renderEditor(); });
    overlay.querySelector("[data-apply]").addEventListener("click", () => {
      if (!commitField()) return;
      const start = dateFromParts(draft.start), end = dateFromParts(draft.end);
      if (!start || !end) {
        error.textContent = t('query.time.invalidDate', { side: sideLabel(!start ? "start" : "end") });
      } else if (start > end) error.textContent = t('query.time.order');
      else { setRange(start, end); close(); }
    });
    document.addEventListener("keydown", event => {
      if (overlay.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (!event.ctrlKey && !event.metaKey && !event.altKey && /^\d$/.test(event.key)) {
        event.preventDefault(); enterDigit(event.key);
      } else if (event.key === "Backspace") { event.preventDefault(); backspace(); }
      else if (event.key === "Tab") {
        const buttons = [...overlay.querySelectorAll("button")];
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    for (const input of [startInput, endInput]) {
      input.addEventListener("input", refresh);
      input.addEventListener("change", refresh);
    }
    refresh();
    return {
      applyPreset(preset) { const range = presetRange(preset); setRange(range.start, range.end); },
      refresh,
      setDisabled(disabled) { trigger.disabled = Boolean(disabled); },
    };
  }

  global.TouchTimeRange = { attach, presetRange };
})(window);
