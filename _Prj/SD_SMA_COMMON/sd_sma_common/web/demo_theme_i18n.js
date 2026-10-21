/* 主题 / 多语言示范页逻辑 — 仅演示 SdI18n.setLang / t */
(function () {
  'use strict';

  function refreshDynamic() {
    var status = document.getElementById('demoStatus');
    var greeting = document.getElementById('demoGreeting');
    if (!window.SdI18n) return;
    if (status) status.textContent = SdI18n.t('demo.status.ready');
    if (greeting) {
      greeting.textContent = SdI18n.t('demo.js.greeting', {
        name: 'SD SMA',
        lang: SdI18n.getLang()
      });
    }
    var title = document.getElementById('demoDocTitle');
    if (title) document.title = SdI18n.t('demo.title');
    document.documentElement.lang = SdI18n.getLang() === 'en' ? 'en' : 'zh-CN';
    var buttons = document.querySelectorAll('[data-demo-lang]');
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var on = btn.getAttribute('data-demo-lang') === SdI18n.getLang();
      if (on) btn.classList.add('is-active');
      else btn.classList.remove('is-active');
    }
  }

  function bindLangSwitch() {
    var buttons = document.querySelectorAll('[data-demo-lang]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function (ev) {
        var lang = ev.currentTarget.getAttribute('data-demo-lang');
        if (!window.SdI18n || !SdI18n.setLang(lang)) return;
        refreshDynamic();
        var note = document.getElementById('demoSwitchNote');
        if (note) note.textContent = SdI18n.t('demo.js.switched', { lang: lang });
      });
    }
  }

  function onReady() {
    bindLangSwitch();
    refreshDynamic();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
})();
