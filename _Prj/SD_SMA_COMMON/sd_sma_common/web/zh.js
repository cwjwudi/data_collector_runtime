/* ============================================================
 * SD SMA 中文语言字典 (zh.js) — 当前唯一语言包
 * key 惯例: 页面.区块.元素;跨页复用的放 nav.* / common.*
 * 业务代码不写死中文,一律 SdI18n.t('key') 或 data-i18n="key"
 * ============================================================ */
window.SD_SMA_I18N_ZH = {
  /* 页内导航(跨页复用) */
  "nav.aria": "站内导航",
  "nav.current.query": "查询页",
  "nav.link.config": "配置页",

  /* 查询页:查询条件 */
  "query.conditions.title": "查询条件",
  "query.conditions.hint": "选择按时间或按批次号查询，两种条件互斥；结果按游标分页展示。",
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
  "query.label.mode": "查询方式",
  "query.mode.time": "按时间",
  "query.mode.batch": "按批次号",
  "query.batch.placeholder": "请选择批次号",
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
  "query.js.batchSource": "批次号来源：{src}",
  "query.js.batchSourceEmpty": "批次号来源没有可选数据，仍可按时间查询",
  "query.js.batchSourceError": "批次号列表不可用，仍可按时间查询：{msg}",
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
  "query.js.batchNeedsCode": "按批次号查询时必须选择 BatchCode",
  "query.js.timeNeedsRange": "按时间查询时必须填写开始时间和结束时间",
  "query.js.timeOrder": "开始时间不能大于结束时间"
};
