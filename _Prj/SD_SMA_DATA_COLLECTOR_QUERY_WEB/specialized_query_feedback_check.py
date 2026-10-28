"""插件查询反馈回归：执行真实 JS，验证慢请求、重复点击、分页和失败恢复。"""
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).parent
    check = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', disabled: false, attributes: {}, children: [],
    set innerHTML(value) { this.children = []; },
    setAttribute(k, v) { this.attributes[k] = v; }, appendChild(child) { this.children.push(child); },
    addEventListener() {}, classList: { add() {}, contains() { return false; } },
  });
  return elements.get(id);
}
const dict = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {window: dict});
let requests = [], resolveRequest;
const context = vm.createContext({
  document: {getElementById: element, createElement: () => element(Symbol()), addEventListener() {}},
  window: {addEventListener() {}}, performance: {now: () => 1000},
  SdI18n: {t: (key, params = {}) => dict.SD_SMA_I18N_ZH[key].replace(/\{(\w+)\}/g, (_, k) => params[k])},
  fetch: (url, options) => {requests.push({url, payload: JSON.parse(options.body)}); return new Promise(r => resolveRequest = r);},
});
let source = fs.readFileSync(process.argv[1], 'utf8');
source = source.replace('  run().catch((error) => {', `
  currentBinding = {page_size: 10}; activePluginKey = 'general_2';
  window.test = {runFreshQuery, runAdjacentPage, advanced() {advancedOpcuaMode = true;}};
  return;
  run().catch((error) => {`);
vm.runInContext(source, context);
const api = context.window.test;
const data = {columns: ['id'], rows: [{id: 1}], total: 21, page: 1, page_size: 10, has_more: true, next_cursor: 'next'};
function respond(data) { resolveRequest({ok: true, json: async () => data}); }
function restored() {
  assert.equal(element('btnGo').disabled, false);
  assert.equal(element('btnGo').textContent, '查询');
  assert.equal(element('resultTable').attributes['aria-busy'], 'false');
}
(async () => {
  const pending = api.runFreshQuery();
  assert.equal(element('btnGo').disabled, true);
  assert.equal(element('btnGo').textContent, '查询中…');
  assert.match(element('meta').textContent, /正在查询/);
  assert.equal(element('btnNextPage').disabled, true);
  await api.runFreshQuery(); await api.runAdjacentPage(1);
  assert.equal(requests.length, 1);
  respond(data); await pending; restored();
  assert.match(element('meta').textContent, /查询完成，本页 1 条/);
  assert.equal(element('btnNextPage').disabled, false);
  let next = api.runAdjacentPage(1);
  assert.equal(requests.at(-1).payload.page_cursor, 'next');
  assert.equal(requests.at(-1).payload.write_sql, false);
  respond({...data, page: 2, next_cursor: 'third'}); await next; restored();
  assert.equal(element('pageNumber').value, '2');
  const oldTable = element('resultTable').children.slice();
  assert.equal(oldTable.length, 2);
  next = api.runFreshQuery();
  resolveRequest({ok: false, json: async () => ({detail: 'database unavailable'})});
  await next; restored();
  assert.equal(element('meta').textContent, '查询失败：database unavailable');
  assert.equal(element('pageNumber').value, '2');
  assert.deepEqual(element('resultTable').children, oldTable);
  assert.equal(element('btnPrevPage').disabled, false);
  next = api.runFreshQuery();
  respond({...data, rows: [], total: 0, has_more: false, warnings: ['warning']}); await next; restored();
  assert.match(element('meta').textContent, /没有数据.*warning/);
  assert.equal(element('btnNextPage').disabled, true);
  const before = requests.length;
  await api.runAdjacentPage(1);
  assert.equal(requests.length, before);
  element('startDate').value = '2026-10-28T12:00';
  await api.runFreshQuery(); restored();
  assert.equal(requests.length, before);
  assert.match(element('meta').textContent, /查询失败/);
  api.advanced();
  next = api.runFreshQuery(); respond({...data, total: 21}); await next;
  next = api.runAdjacentPage(1);
  assert.match(requests.at(-1).url, /snapshot-page/);
  respond({...data, page: 2}); await next; restored();
  console.log('specialized_query_feedback_check: all assertions passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', check, str(root / 'app/static/specialized_query.js'),
                    str(root.parent / 'SD_SMA_COMMON/sd_sma_common/web/zh.js')], check=True)


if __name__ == '__main__':
    main()
