"""共享时间键盘回归自检：Node 执行真实组件、字典和语言引擎，DOM 仅提供事件宿主。"""
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).parent / "app/static/touch_time_range.js"
    common = root / "_Prj/SD_SMA_COMMON/sd_sma_common/web"
    check = r"""
(async () => {
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const document = {elements: new Map(), activeElement: null, readyState: 'loading', events: {}};
class Element {
  constructor(tag = 'div', attrs = '') {
    this.tag = tag; this.attrs = {}; this.dataset = {}; this.events = {}; this.children = [];
    this.value = ''; this.textContent = ''; this.hidden = false;
    for (const match of attrs.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      this.attrs[match[1]] = match[2] || '';
      if (match[1].startsWith('data-')) this.dataset[match[1].slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = match[2] || '';
    }
    const classes = new Set((this.attrs.class || '').split(' '));
    this.classList = {toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }};
  }
  set innerHTML(html) {
    this.html = html;
    this.children = [...html.matchAll(/<(button|div|span|b)\b([^>]*)>/g)].map(m => new Element(m[1], m[2]));
  }
  querySelectorAll(selector) {
    return this.children.filter(el => selector.split(',').some(item => {
      item = item.trim();
      if (item.startsWith('.')) return (el.attrs.class || '').split(' ').includes(item.slice(1));
      if (item.startsWith('[')) return item.slice(1, -1) in el.attrs;
      return el.tag === item;
    }));
  }
  querySelector(selector) {
    if (selector === 'b' && this.tag === 'button') return this.bold || (this.bold = new Element('b'));
    return this.querySelectorAll(selector)[0];
  }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, handler) { (this.events[name] ||= []).push(handler); }
  dispatchEvent(event) { return Promise.all((this.events[event.type] || []).map(fn => fn(event))); }
  click() { return this.dispatchEvent({type: 'click', target: this}); }
  focus() { document.activeElement = this; }
}
document.getElementById = id => document.elements.get(id);
document.createElement = tag => new Element(tag);
document.addEventListener = (name, handler) => (document.events[name] ||= []).push(handler);
document.body = {appendChild(el) { document.overlay = el; }};
for (const id of ['start', 'end', 'trigger', 'summary']) document.elements.set(id, new Element());
let maxWindowHours = 168, fetchFails = false;
const window = {fetch: async (url, options) => {
  assert.equal(url, '/api/config/app-settings'); assert.equal(options.cache, 'no-store');
  if (fetchFails) throw new Error('offline');
  return {ok: true, json: async () => ({query_limits: {max_window_hours: maxWindowHours}})};
}, document, localStorage: {getItem() { return null; }}};
const context = vm.createContext({window, document, Event, Date});
for (const path of process.argv.slice(1)) vm.runInContext(fs.readFileSync(path, 'utf8'), context);
const el = id => document.elements.get(id);
el('start').value = '2026-10-28T19:53:11'; el('end').value = '2026-10-28T20:53:11';
const picker = window.TouchTimeRange.attach({idPrefix: 'check', startInputId: 'start', endInputId: 'end', triggerId: 'trigger', summaryId: 'summary'});
const overlay = document.overlay;
const find = selector => overlay.querySelector(selector);
const fields = overlay.querySelectorAll('[data-part]');
const digit = n => overlay.querySelectorAll('[data-digit]').find(btn => btn.dataset.digit === n).click();
const digits = text => [...text].forEach(digit);
const side = name => overlay.querySelectorAll('[data-side]').find(btn => btn.dataset.side === name).click();
const error = () => find('.touch-time-error').textContent;
const key = (key, extra = {}) => {
  const event = {key, preventDefault() { this.prevented = true; }, ...extra};
  for (const fn of document.events.keydown) fn(event);
  return event;
};
assert.equal(fields.length, 6);
assert.equal(overlay.querySelectorAll('[data-digit]').length, 10);
assert.doesNotMatch(overlay.html, /<input\b/);
assert.doesNotMatch(overlay.html, /touch-time-dialog-header|touch-time-close|<h3\b/);
assert.match(overlay.html, /touch-time-dialog-footer"><div class="touch-time-duration"/);
assert.match(overlay.html, /data-apply>应用<\/button>/);
assert.doesNotMatch(overlay.html, /query\.time\./);
picker.setDisabled(true); assert.equal(el('trigger').disabled, true);
picker.setDisabled(false); assert.equal(el('trigger').disabled, false);
el('trigger').click();
assert.equal(overlay.hidden, false); assert.equal(document.activeElement, fields[0]);
digits('20280229123045');
assert.equal(overlay.querySelectorAll('[data-side]')[1].attrs['aria-pressed'], 'true');
assert.doesNotMatch(overlay.html, /touch-time-editing|touch-time-dialog-hint/);
digits('20280229133046');
assert.equal(find('.touch-time-duration').textContent, '共 0天 1小时 0分 1秒');
assert.equal(el('start').value, '2026-10-28T19:53:11');
let changes = 0;
for (const id of ['start', 'end']) for (const type of ['input', 'change']) el(id).addEventListener(type, () => changes++);
await find('[data-apply]').click();
assert.equal(overlay.hidden, true); assert.equal(document.activeElement, el('trigger'));
assert.equal(el('start').value, '2028-02-29T12:30:45');
assert.equal(el('end').value, '2028-02-29T13:30:46'); assert.equal(changes, 4);
assert.match(el('summary').textContent, /2028-02-29 12:30:45/);

// 不存在的日期和倒序范围都不写回页面。
el('trigger').click(); digits('20270229'); await find('[data-apply]').click();
assert.match(error(), /开始时间日期无效/); assert.equal(overlay.hidden, false);
assert.equal(el('start').value, '2028-02-29T12:30:45');
find('[data-cancel]').click(); el('trigger').click();
fields[3].click(); digits('14'); await find('[data-apply]').click();
assert.equal(error(), '结束时间不能早于开始时间');
find('[data-cancel]').click();

// 各字段范围、退格恢复、下一项补零、完整年份与键盘输入。
el('trigger').click(); fields[1].click(); digits('13');
assert.match(error(), /月请输入/); assert.equal(fields[1].attrs['aria-pressed'], 'true');
digits('01'); assert.equal(fields[2].attrs['aria-pressed'], 'true');
digits('32'); assert.match(error(), /日请输入/); digits('28');
digits('24'); assert.match(error(), /时请输入/); digits('23');
digits('60'); assert.match(error(), /分请输入/); digits('59');
digits('60'); assert.match(error(), /秒请输入/); digits('58');
side('start'); fields[4].click(); digit('7'); find('[data-next]').click();
assert.equal(fields[4].querySelector('b').textContent, '07');
fields[3].click(); digit('2'); find('[data-back]').click(); find('[data-next]').click();
assert.match(error(), /时请输入/); digits('12');
side('start'); digits('20'); find('[data-next]').click(); assert.match(error(), /年请输入/);
key('2'); key('8'); assert.equal(fields[1].attrs['aria-pressed'], 'true');
assert.equal(key('1', {ctrlKey: true}).prevented, undefined);
assert.equal(key('Backspace').prevented, true); digits('02');
const unchanged = el('start').value;
key('Escape'); assert.equal(overlay.hidden, true); assert.equal(el('start').value, unchanged);
key('1'); assert.equal(overlay.hidden, true);
el('trigger').click(); assert.equal(fields[0].querySelector('b').textContent, '2028');
const buttons = overlay.querySelectorAll('button');
buttons.at(-1).focus(); key('Tab'); assert.equal(document.activeElement, buttons[0]);
key('Tab', {shiftKey: true}); assert.equal(document.activeElement, buttons.at(-1));
find('[data-cancel]').click();

// 不同端点、等值范围和低年份均保持现有值事件接口。
el('trigger').click(); digits('20280229133046'); await find('[data-apply]').click();
assert.equal(el('start').value, el('end').value);
el('start').value = '0099-01-01T00:00:00'; el('end').value = '0099-01-02T00:00:00';
el('trigger').click(); await find('[data-apply]').click();
assert.equal(el('start').value, '0099-01-01T00:00:00');
const now = new Date(2026, 9, 28, 12, 34, 56);
for (const preset of ['last15m', 'last1h', 'last8h', 'today', 'yesterday', 'last1w', 'last1m']) {
  const range = window.TouchTimeRange.presetRange(preset, now);
  assert.ok(range.start <= range.end, preset); assert.equal(now.getHours(), 12);
}
assert.equal(window.TouchTimeRange.presetRange('last15m', now).end - window.TouchTimeRange.presetRange('last15m', now).start, 900000);
assert.throws(() => window.TouchTimeRange.presetRange('bad'), /未知时间范围/);
picker.applyPreset('last1h'); assert.match(el('start').value, /^\d{4}-\d\d-\d\dT/);
// 超限只修正草稿；再次应用才写回，限制读取失败时保留弹窗。
el('start').value = '2026-10-21T22:11:27'; el('end').value = '2026-10-28T22:11:27';
maxWindowHours = 24;
el('trigger').click(); const beforeChanges = changes;
await find('[data-apply]').click();
assert.equal(overlay.hidden, false); assert.equal(changes, beforeChanges);
assert.equal(el('start').value, '2026-10-21T22:11:27');
assert.equal(el('end').value, '2026-10-28T22:11:27');
assert.equal(error(), '超限，开始改为 2026-10-27 22:11:27，再点应用');
assert.equal(find('.touch-time-duration').textContent, '共 1天 0小时 0分 0秒');
assert.equal(fields[2].querySelector('b').textContent, '27');
await find('[data-apply]').click();
assert.equal(overlay.hidden, true); assert.equal(changes, beforeChanges + 4);
assert.equal(el('start').value, '2026-10-27T22:11:27');
// 一秒超限仍需再次确认，取消不写回；配置变化在下一次点击生效。
el('start').value = '2026-10-27T22:11:26'; el('trigger').click();
await find('[data-apply]').click(); assert.equal(overlay.hidden, false);
find('[data-cancel]').click(); assert.equal(el('start').value, '2026-10-27T22:11:26');
el('trigger').click(); maxWindowHours = 48;
await find('[data-apply]').click(); assert.equal(overlay.hidden, true);
el('trigger').click(); fetchFails = true;
await find('[data-apply]').click(); assert.equal(overlay.hidden, false);
assert.match(error(), /无法读取时间范围限制/); assert.equal(find('[data-apply]').disabled, false);
fetchFails = false; await find('[data-apply]').click(); assert.equal(overlay.hidden, true);
console.log('touch_time_range_check: all assertions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([
        "node", "-e", check, str(common / "zh.js"),
        str(common / "i18n.js"), str(script),
    ], check=True)


if __name__ == "__main__":
    main()
