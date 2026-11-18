(function () {
  'use strict';

  var el = document.getElementById('chart-position');
  var fallback = el ? el.parentNode.querySelector('.chart-fallback') : null;
  var live = document.querySelector('[data-radar-live]');
  var activeSignal = '';
  var chart = null;

  function showFallback() {
    if (el) el.style.display = 'none';
    if (fallback) fallback.style.display = 'block';
  }

  function detailFor(id) {
    return document.querySelector('[data-signal-detail][data-signal-id="' + id + '"]');
  }

  function applySignal(id, opts) {
    opts = opts || {};
    activeSignal = id;
    var triggers = document.querySelectorAll('[data-signal-trigger]');
    for (var i = 0; i < triggers.length; i++) {
      var t = triggers[i];
      var tid = t.getAttribute('data-signal-id') || '';
      var on = tid === id;
      t.setAttribute('aria-pressed', on ? 'true' : 'false');
      var card = t.closest ? t.closest('[data-signal-card]') : null;
      if (card) {
        if (on) card.classList.add('is-selected');
        else card.classList.remove('is-selected');
      }
      var d = detailFor(tid);
      if (d) {
        if (on && opts.open !== false) d.setAttribute('open', 'open');
        else d.removeAttribute('open');
      }
    }
    if (live) {
      if (id) {
        var cardNow = document.querySelector('[data-signal-card][data-signal-id="' + id + '"]');
        var title = cardNow && cardNow.querySelector('h3');
        live.textContent = '已选中信号 ' + id + (title ? '：' + title.textContent.trim() : '') + '，证据已展开。';
      } else {
        live.textContent = '已取消信号选择，全部证据收起。';
      }
    }
  }

  function bindTriggers() {
    var triggers = document.querySelectorAll('[data-signal-trigger]');
    for (var i = 0; i < triggers.length; i++) {
      (function (t) {
        t.addEventListener('click', function () {
          var id = t.getAttribute('data-signal-id') || '';
          applySignal(activeSignal === id ? '' : id, {});
        });
      })(triggers[i]);
    }
  }

  function mount() {
    var node = document.getElementById('chart-position');
    if (!node) return;
    var fb = node.parentNode.querySelector('.chart-fallback');
    if (!window.echarts || typeof window.echarts.init !== 'function') {
      showFallback();
      return;
    }

    var cs = getComputedStyle(document.documentElement);
    var accent = cs.getPropertyValue('--accent').trim() || '#d93c20';
    var accent2 = cs.getPropertyValue('--accent2').trim() || '#b92e18';
    var ink = cs.getPropertyValue('--ink').trim() || '#1a0e0b';
    var muted = cs.getPropertyValue('--muted').trim() || '#6d4c40';
    var rule = cs.getPropertyValue('--rule').trim() || '#d9c0a6';
    var bg = cs.getPropertyValue('--bg').trim() || '#fff4df';
    var bg2 = cs.getPropertyValue('--bg2').trim() || '#f8e6c8';

    var X_LABELS = { 1: '链路 / 日志', 2: '动作 / 策略', 3: '决策（意图+结果）', 4: '合规裁定' };
    var Y_LABELS = { 1: '平台内', 2: '导出 / 校验', 3: '第三方离线' };

    var data = {
      platform: [
        { name: 'Google Agent Platform', value: [1.95, 1.90], signalId: 'S01', label: { position: 'bottom' } },
        { name: 'Microsoft Entra', value: [2.25, 2.10], signalId: 'S01', label: { position: 'right' } },
        { name: 'AWS AgentCore', value: [1.70, 2.10], signalId: 'S01', label: { position: 'left' } }
      ],
      observe: [
        { name: 'LangSmith', value: [1.05, 0.95], signalId: 'S02', label: { position: 'bottom' } },
        { name: 'Langfuse', value: [1.35, 1.15], signalId: 'S02', label: { position: 'top' } },
        { name: 'Arize', value: [0.78, 1.22], signalId: 'S02', label: { position: 'top' } },
        { name: 'Braintrust', value: [1.62, 0.88], signalId: 'S02', label: { position: 'bottom' } }
      ],
      evidence: [
        { name: '阿里云 AgentLoop', value: [2.05, 2.25], signalId: 'S03', label: { position: 'left' } },
        { name: 'Execlave', value: [2.00, 2.70], signalId: 'S05', label: { position: 'left' } },
        { name: 'halo-record', value: [2.40, 2.55], signalId: 'S05', label: { position: 'bottom' } },
        { name: '奇安信 智能体安全平台', value: [3.00, 1.70], signalId: 'S04', label: { position: 'bottom' } },
        { name: 'Sanna', value: [2.85, 3.05], signalId: 'S05', label: { position: 'top' } },
        { name: 'acipta', value: [4.00, 3.00], signalId: 'S05', label: { position: 'left' } }
      ],
      self: [
        { name: 'TemisGuard（本项目）', value: [3.20, 2.65], signalId: '', label: { position: 'bottom' } }
      ]
    };

    function nearestAxis(map, v) {
      var best = '', bestDiff = Infinity;
      Object.keys(map).forEach(function (k) {
        var diff = Math.abs(Number(k) - v);
        if (diff < bestDiff) { bestDiff = diff; best = map[k]; }
      });
      return best;
    }

    node.style.display = 'block';
    var existing = echarts.getInstanceByDom(node);
    if (existing) { existing.dispose(); }
    chart = echarts.init(node, null, { renderer: 'svg' });

    chart.setOption({
      backgroundColor: 'transparent',
      animation: false,
      aria: {
        enabled: true,
        decal: { show: false },
        label: { description: '竞品定位散点图：横轴为记录对象，纵轴为独立验证能力，红色三角为本项目 TemisGuard。' }
      },
      grid: { left: 120, right: 60, top: 40, bottom: 92 },
      tooltip: {
        trigger: 'item',
        appendToBody: true,
        backgroundColor: ink,
        borderWidth: 0,
        padding: [10, 12],
        textStyle: { color: bg, fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif', fontSize: 12, lineHeight: 18 },
        formatter: function (p) {
          var v = p.value;
          return '<b>' + p.seriesName + '</b><br/>' + p.name + '<br/>' +
            '记录对象：' + nearestAxis(X_LABELS, v[0]) + '<br/>' +
            '独立验证：' + nearestAxis(Y_LABELS, v[1]);
        }
      },
      xAxis: {
        type: 'value',
        min: 0.5,
        max: 4.5,
        name: '记录对象',
        nameLocation: 'middle',
        nameGap: 46,
        nameTextStyle: { color: muted, fontSize: 12, fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif' },
        axisLine: { lineStyle: { color: rule } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: muted,
          fontFamily: 'JetBrainsMono, Consolas, monospace',
          fontSize: 11,
          margin: 12,
          customValues: [1, 2, 3, 4],
          formatter: function (v) { return X_LABELS[v] || ''; }
        }
      },
      yAxis: {
        type: 'value',
        min: 0.5,
        max: 3.5,
        name: '独立验证',
        nameLocation: 'middle',
        nameGap: 68,
        nameTextStyle: { color: muted, fontSize: 12, fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif' },
        axisLine: { lineStyle: { color: rule } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: muted,
          fontFamily: 'JetBrainsMono, Consolas, monospace',
          fontSize: 11,
          margin: 12,
          customValues: [1, 2, 3],
          formatter: function (v) { return Y_LABELS[v] || ''; }
        }
      },
      series: [
        {
          name: '平台捆绑',
          type: 'scatter',
          symbol: 'rect',
          symbolSize: 13,
          itemStyle: { color: ink, borderColor: bg, borderWidth: 1.5 },
          label: {
            show: true, position: 'bottom', distance: 6,
            color: ink, fontSize: 10.5,
            fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif',
            formatter: function (p) { return p.name; }
          },
          labelLayout: { hideOverlap: false },
          emphasis: { scale: 1.6, itemStyle: { color: ink }, label: { fontWeight: 700 } },
          markArea: {
            silent: true,
            itemStyle: { color: bg2 },
            label: { show: false },
            data: [[{ yAxis: 2.5 }, { yAxis: 3.5 }]]
          },
          data: data.platform
        },
        {
          name: '可观测',
          type: 'scatter',
          symbol: 'circle',
          symbolSize: 11,
          itemStyle: { color: muted, borderColor: bg, borderWidth: 1.5 },
          label: {
            show: true, position: 'bottom', distance: 6,
            color: muted, fontSize: 10.5,
            fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif',
            formatter: function (p) { return p.name; }
          },
          labelLayout: { hideOverlap: false },
          emphasis: { scale: 1.6, itemStyle: { color: muted }, label: { fontWeight: 700 } },
          data: data.observe
        },
        {
          name: '证据与审计',
          type: 'scatter',
          symbol: 'diamond',
          symbolSize: 13,
          itemStyle: { color: accent2, borderColor: bg, borderWidth: 1.5 },
          label: {
            show: true, position: 'bottom', distance: 6,
            color: ink, fontSize: 10.5,
            fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif',
            formatter: function (p) { return p.name; }
          },
          labelLayout: { hideOverlap: false },
          emphasis: { scale: 1.6, itemStyle: { color: accent2 }, label: { fontWeight: 700 } },
          data: data.evidence
        },
        {
          name: '本项目 TemisGuard',
          type: 'scatter',
          symbol: 'triangle',
          symbolSize: 19,
          z: 5,
          itemStyle: { color: accent, borderColor: bg, borderWidth: 2 },
          label: {
            show: true, position: 'top', distance: 8,
            color: accent, fontSize: 11.5, fontWeight: 700,
            fontFamily: 'InstrumentSans, Microsoft YaHei, sans-serif',
            formatter: function (p) { return p.name; }
          },
          labelLayout: { hideOverlap: false },
          emphasis: { scale: 1.4 },
          data: data.self
        }
      ]
    });

    chart.on('click', function (params) {
      var sig = params && params.data ? params.data.signalId : '';
      if (sig) applySignal(sig, {});
    });

    node.style.display = 'block';
    if (fb) fb.style.display = 'none';

    window.addEventListener('resize', function () {
      if (chart) chart.resize();
    });
    window.addEventListener('beforeprint', function () {
      node.style.display = 'none';
      if (fb) fb.style.display = 'block';
    });
    window.addEventListener('afterprint', function () {
      node.style.display = 'block';
      if (fb) fb.style.display = 'none';
    });
  }

  function isPdfRender() {
    return /[?&]pdf=1/.test(location.search);
  }

  function expandForPrint() {
    var ds = document.querySelectorAll('[data-signal-detail]');
    for (var i = 0; i < ds.length; i++) ds[i].setAttribute('open', 'open');
    document.documentElement.setAttribute('data-render', 'pdf');
  }

  function boot() {
    try {
      mount();
    } catch (err) {
      showFallback();
    }
  }

  bindTriggers();
  if (isPdfRender()) expandForPrint();

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(boot, boot);
  } else {
    boot();
  }
})();
