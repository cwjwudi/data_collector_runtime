"""查询页错误不弹窗回归自检；使用 Node 执行真实页面脚本及失败请求。"""
from pathlib import Path
import subprocess


def main():
    script = Path(__file__).parent / 'app/static/query.js'
    check = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
assert.doesNotMatch(source, /\b(?:alert|confirm|prompt)\s*\(/);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', textContent: '', innerHTML: '', options: [],
    addEventListener(type, handler) { this[type] = handler; },
    appendChild() {}, classList: { contains() { return false; }, remove() {}, add() {} },
    setAttribute() {},
    replaceWith(next) { elements.set(id, next); },
  });
  return elements.get(id);
}
let requests = 0;
const context = vm.createContext({
  document: { addEventListener() {}, getElementById: element, querySelectorAll: () => [], createElement: () => ({ appendChild() {}, setAttribute() {}, options: [] }) },
  window: {
    SdI18n: { t: key => key }, localStorage: { getItem: () => null, setItem() {} },
    TouchTimeRange: { attach: () => ({ applyPreset() {} }) },
  },
  fetch: async () => { requests++; return { ok: false, json: async () => ({ detail: 'database unavailable' }) }; },
  alert() { throw new Error('Unexpected browser popup'); },
});
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  vm.runInContext(source, context);
  await flush();
  assert.equal(element('queryWarnings').textContent, 'database unavailable');
  for (const [id, event] of [['btnRefreshGroups', 'click'], ['btnRunQuery', 'click'], ['group', 'change'], ['viewName', 'change'], ['queryCondition', 'change'], ['btnPrevPage', 'click'], ['btnNextPage', 'click']]) {
    vm.runInContext("queryViews = {v: {per_group: {g: {}}}}; currentQueryConditions = [{name: 'c'}]; lastQueryContext = {viewName: 'v', group: 'g'}; currentPage = 2; totalPages = 3; hasMore = true; nextCursor = 'next';", context);
    element('viewName').value = 'v';
    element('group').value = 'g';
    element('queryCondition').value = 'c';
    element('queryWarnings').textContent = '';
    element(id)[event]();
    await flush();
    assert.equal(element('queryWarnings').textContent, 'database unavailable', id);
  }
  const before = requests;
  element('viewName').value = '';
  await vm.runInContext('runQuery()', context);
  assert.equal(requests, before);
  assert.equal(element('queryWarnings').textContent, 'query.js.selectView');
  element('viewName').value = 'v';
  element('startTime').value = '2026-10-26T10:00';
  await vm.runInContext('runQuery()', context);
  assert.equal(requests, before);
  assert.equal(element('queryWarnings').textContent, 'query.js.timePartial');
  element('endTime').value = '2026-10-26T09:00';
  await vm.runInContext('runQuery()', context);
  assert.equal(requests, before);
  assert.equal(element('queryWarnings').textContent, 'query.js.timeOrder');
  element('startTime').value = '';
  element('endTime').value = '';
  context.fetch = async () => ({ok: true, json: async () => ({columns: [], rows: [], page: 1, page_size: 10})});
  await vm.runInContext('runQuery()', context);
  assert.equal(element('queryWarnings').textContent, '');
  console.log('query_error_ui_check: all assertions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(['node', '-e', check, str(script)], check=True)


if __name__ == '__main__':
    main()
