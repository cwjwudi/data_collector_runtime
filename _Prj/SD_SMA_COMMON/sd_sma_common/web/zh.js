/* ============================================================
 * SD SMA 中文语言字典 (zh.js) — 当前唯一语言包
 * key 惯例: 页面.区块.元素;跨页复用的放 nav.* / common.*
 * 业务代码不写死中文,一律 SdI18n.t('key') 或 data-i18n="key"
 * ============================================================ */
window.SD_SMA_I18N_ZH = {
  "common.password.label": "密码",
  "launcher.credentials.saved": "凭据已保存",
  "launcher.credentials.storageHint": "密码保存在本机配置中，页面不会回显密码。",
  /* 页内导航(跨页复用) */
  "nav.aria": "站内导航",
  "nav.current.query": "查询页",
  "nav.link.config": "配置页",

  /* 查询页:查询条件 */
  "query.conditions.title": "查询条件",
  "query.conditions.hint": "时间范围可留空，留空时查询全部匹配记录，单页条数仍有上限。可再选一条附加条件，取值来自该条件配置的来源表。",
  "query.label.table": "表选择",
  "query.label.lang": "列名显示",
  "query.lang.zh": "中文",
  "query.lang.en": "English",
  "query.lang.both": "中文 + English",
  "query.btn.refreshGroups": "刷新组",
  "query.btn.run": "查询",
  "query.btn.prev": "上一页",
  "query.btn.next": "下一页",
  "query.page.prefix": "第",
  "query.page.suffix": "页",
  "query.page.aria": "当前页码",
  "query.label.condition": "附加条件",
  "query.condition.none": "不附加",
  "query.label.conditionValue": "条件值",
  "query.condition.valuePlaceholder": "请选择",
  "query.label.timeRange": "时间范围",
  "query.time.empty": "尚未设置时间范围",
  "query.range.15m": "最近15分钟",
  "query.range.1h": "最近1小时",
  "query.range.8h": "最近8小时",
  "query.range.today": "今天",
  "query.range.yesterday": "昨天",
  "query.range.1w": "最近1周",
  "query.range.1m": "最近1月",
  "query.range.precise": "精确时间",

  /* 查询页:查询结果 */
  "query.result.title": "查询结果",
  "query.result.aria": "查询结果，可横向滚动",

  /* 查询页:JS 动态文案 */
  "query.js.conditionSource": "条件「{name}」来源：{src}",
  "query.js.conditionEmpty": "条件「{name}」的来源表还没有可选值",
  "query.js.conditionError": "条件取值不可用：{msg}",
  "query.js.groupReady": "group={group} 已配置，可查询；当前基准表={table}，共 {count} 张表",
  "query.js.groupSchemaMismatch": "group={group} 表结构不一致，将按已配置基准表 {table} 查询",
  "query.js.fixedTable": "{table}（固定表）",
  "query.js.noView": "暂无可用 view",
  "query.js.noGroup": "view={view} 暂无已配置 group，请先到配置页保存 group 配置",
  "query.js.selectView": "请先选择 view",
  "query.js.selectGroup": "请先选择已配置 group",
  "query.js.pageInfoMore": "当前第 {page} 页，每页 {size} 条，还有更多",
  "query.js.pageInfoEnd": "当前第 {page} 页，每页 {size} 条，已到末页",
  "query.js.pageInfoTotal": "总条数 {total}，共 {pages} 页，当前第 {page} 页",
  "query.js.timePartial": "开始时间和结束时间需要同时填写，或同时留空",
  "query.js.timeOrder": "开始时间不能大于结束时间"
};
