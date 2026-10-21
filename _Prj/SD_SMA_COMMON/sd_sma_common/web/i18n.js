/* ============================================================
 * SD SMA 极简 i18n 引擎 (i18n.js)
 *
 * 用法(页面按顺序引入):
 *   <script src="/common/zh.js"></>          <!-- 中文词典 -->
 *   <script src="/common/en.js"></>          <!-- 英文词典(多语言时) -->
 *   <script src="/common/i18n.js"></>
 *   <script src="/static/xxx.js"></>         <!-- 业务代码里用 SdI18n.t('key') -->
 *
 * HTML 静态文案:
 *   <span data-i18n="nav.query">查询页</span>       -> textContent
 *   <input data-i18n-placeholder="a.b" />           -> placeholder 属性
 *   <div data-i18n-aria="a.b"></div>                -> aria-label 属性
 * 字典缺 key 时保留 HTML 原文兜底,不会清空。
 *
 * 多语言:字典在 zh.js / en.js,业务代码一律走 key 不写死文案。
 * 再加语言 = 新增 <lang>.js + 在 LANGS 注册 + 页面引入,业务 HTML/JS 零改动。
 * ============================================================ */
(function (global) {
  'use strict';

  var LANGS = ['zh', 'en']; // 可用语言列表
  var DICT_WINDOW_KEY = 'SD_SMA_I18N_'; // 各语言字典挂 window 上的前缀
  var STORAGE_KEY = 'sd_sma_lang';

  var current = null;
  var dicts = {};

  function detectLang() {
    var saved = null;
    try {
      saved = global.localStorage.getItem(STORAGE_KEY);
    } catch (e) { /* 隐私模式等 localStorage 不可用时用默认语言 */ }
    if (saved && LANGS.indexOf(saved) >= 0) return saved;
    return LANGS[0];
  }

  function loadDict(lang) {
    if (dicts[lang]) return dicts[lang];
    var fromWindow = global[DICT_WINDOW_KEY + String(lang).toUpperCase()];
    dicts[lang] = fromWindow || {};
    return dicts[lang];
  }

  /** 取文案;缺 key 返回 key 本身(便于发现漏配),params 用 {name} 占位替换 */
  function t(key, params) {
    var dict = loadDict(current);
    var text = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
    if (params) {
      for (var name in params) {
        if (Object.prototype.hasOwnProperty.call(params, name)) {
          text = text.split('{' + name + '}').join(String(params[name]));
        }
      }
    }
    return text;
  }

  /** 扫描并替换 DOM 文案;root 传节点可只处理子树 */
  function apply(root) {
    var scope = root || global.document;
    var nodes = scope.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var key = el.getAttribute('data-i18n');
      var dict = loadDict(current);
      if (Object.prototype.hasOwnProperty.call(dict, key)) el.textContent = dict[key];
    }
    var withAttr = scope.querySelectorAll('[data-i18n-placeholder], [data-i18n-aria]');
    for (var j = 0; j < withAttr.length; j++) {
      var node = withAttr[j];
      var ph = node.getAttribute('data-i18n-placeholder');
      var aria = node.getAttribute('data-i18n-aria');
      if (ph && Object.prototype.hasOwnProperty.call(loadDict(current), ph)) {
        node.setAttribute('placeholder', t(ph));
      }
      if (aria && Object.prototype.hasOwnProperty.call(loadDict(current), aria)) {
        node.setAttribute('aria-label', t(aria));
      }
    }
  }

  /** 切换语言(单语言阶段一般用不到;预留给多语言切换控件) */
  function setLang(lang) {
    if (LANGS.indexOf(lang) < 0) return false;
    current = lang;
    try {
      global.localStorage.setItem(STORAGE_KEY, lang);
    } catch (e) { /* 同 detectLang */ }
    apply();
    syncLangButtons();
    return true;
  }

  current = detectLang();
  // 手动注册字典的口子(测试或动态语言包用)
  function register(lang, dict) {
    dicts[lang] = dict || {};
  }


  function syncLangButtons() {
    var buttons = global.document.querySelectorAll('[data-lang-switch]');
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var on = btn.getAttribute('data-lang-switch') === current;
      if (on) btn.classList.add('is-active');
      else btn.classList.remove('is-active');
    }
    if (global.document.documentElement) {
      global.document.documentElement.lang = current === 'en' ? 'en' : 'zh-CN';
    }
  }

  function bindLangSwitch() {
    var root = global.document;
    if (!root || root.__sdSmaLangSwitchBound) return;
    root.__sdSmaLangSwitchBound = true;
    root.addEventListener('click', function (ev) {
      var target = ev.target;
      if (!target || !target.closest) return;
      var btn = target.closest('[data-lang-switch]');
      if (!btn) return;
      var lang = btn.getAttribute('data-lang-switch');
      if (setLang(lang)) {
        syncLangButtons();
      }
    });
  }

  global.SdI18n = {
    t: t,
    apply: apply,
    register: register,
    setLang: setLang,
    getLang: function () { return current; },
    langs: LANGS,
  };

  // DOM 就绪后自动替换静态文案(zh.js 若在前面已加载即生效)
  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', function () { apply(); bindLangSwitch(); syncLangButtons(); });
  } else {
    apply();
    bindLangSwitch();
    syncLangButtons();
  }
})(window);
