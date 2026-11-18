/* RISE 优先级场 + 信号卡联动 */
(function () {
  'use strict';

  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var SIGNALS = [
    { id: 'A1', label: 'A1 合规强制', speed: 4, impact: 4, reach: 4, score: 4.00, prio: 'P1' },
    { id: 'A2', label: 'A2 标准成型', speed: 3, impact: 4, reach: 4, score: 3.80, prio: 'P1' },
    { id: 'A3', label: 'A3 竞品占位', speed: 3, impact: 5, reach: 3, score: 3.70, prio: 'P1' },
    { id: 'A4', label: 'A4 平台吞并', speed: 2, impact: 3, reach: 5, score: 3.50, prio: 'P1' },
    { id: 'A5', label: 'A5 格式草案', speed: 3, impact: 3, reach: 3, score: 3.20, prio: 'P2' },
    { id: 'A6', label: 'A6 欧洲延期', speed: 2, impact: 2, reach: 4, score: 2.90, prio: 'P2' }
  ];

  var chart = null;
  var currentIndex = -1;

  function showFallback() {
    var fb = document.querySelector('[data-chart-status="chart-rise"]');
    if (fb) { fb.hidden = false; }
    var shell = document.getElementById('chart-rise');
    if (shell) { shell.style.display = 'none'; }
  }

  function announce(text) {
    var live = document.querySelector('[data-radar-live]');
    if (live) { live.textContent = text; }
  }

  function paint(index) {
    currentIndex = index;
    var id = index >= 0 ? SIGNALS[index].id : '';

    var triggers = document.querySelectorAll('[data-signal-trigger]');
    for (var i = 0; i < triggers.length; i++) {
      var pressed = triggers[i].getAttribute('data-signal-id') === id;
      triggers[i].setAttribute('aria-pressed', pressed ? 'true' : 'false');
    }

    var cards = document.querySelectorAll('[data-signal-card]');
    for (var j = 0; j < cards.length; j++) {
      if (cards[j].getAttribute('data-signal-id') === id) {
        cards[j].classList.add('is-selected');
      } else {
        cards[j].classList.remove('is-selected');
      }
    }

    if (chart && index >= 0) {
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: index });
    }

    if (index >= 0) {
      announce('已选中 ' + SIGNALS[index].label + '，RISE ' + SIGNALS[index].score.toFixed(2) + '，级别 ' + SIGNALS[index].prio);
    }
  }

  function bindSignals() {
    var triggers = document.querySelectorAll('[data-signal-trigger]');
    for (var i = 0; i < triggers.length; i++) {
      triggers[i].addEventListener('click', function (event) {
        var id = this.getAttribute('data-signal-id');
        var next = -1;
        for (var k = 0; k < SIGNALS.length; k++) {
          if (SIGNALS[k].id === id) { next = k; }
        }
        if (next === currentIndex) { next = -1; }
        event.preventDefault();
        paint(next);
      });
    }
  }

  function buildChart() {
    var el = document.getElementById('chart-rise');
    if (!el) { return; }
    if (typeof window.echarts === 'undefined' || !window.echarts) { showFallback(); return; }

    try {
      var existing = window.echarts.getInstanceByDom(el);
      if (existing) { existing.dispose(); }

      chart = window.echarts.init(el, null, { renderer: 'svg' });

      chart.setOption({
        animation: false,
        backgroundColor: 'transparent',
        aria: { enabled: true, decal: { show: false } },
        grid: { left: 64, right: 40, top: 44, bottom: 56 },
        tooltip: {
          appendToBody: true,
          trigger: 'item',
          backgroundColor: bg2,
          borderColor: rule,
          textStyle: { color: ink, fontFamily: 'WorkSans' },
          formatter: function (p) {
            var d = SIGNALS[p.dataIndex];
            return '<strong>' + d.label + '</strong><br/>RISE ' + d.score.toFixed(2) + ' · ' + d.prio +
              '<br/>影响面 ' + d.reach + ' · 影响 ' + d.impact + ' · 紧迫度 ' + d.speed;
          }
        },
        xAxis: {
          name: '紧迫度',
          nameLocation: 'middle',
          nameGap: 32,
          nameTextStyle: { color: muted, fontSize: 11 },
          min: 0.6,
          max: 5.4,
          interval: 1,
          axisLine: { lineStyle: { color: rule } },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: rule, type: 'dashed' } },
          axisLabel: { color: muted, fontSize: 11 }
        },
        yAxis: {
          name: '影响',
          nameLocation: 'middle',
          nameGap: 44,
          nameTextStyle: { color: muted, fontSize: 11 },
          min: 0.6,
          max: 5.4,
          interval: 1,
          axisLine: { lineStyle: { color: rule } },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: rule, type: 'dashed' } },
          axisLabel: { color: muted, fontSize: 11 }
        },
        series: [{
          type: 'scatter',
          symbol: 'circle',
          emphasis: { scale: 1.35, itemStyle: { borderColor: ink, borderWidth: 2 } },
          label: {
            show: true,
            position: 'top',
            distance: 8,
            color: ink,
            fontSize: 11,
            fontFamily: 'WorkSans',
            formatter: function (p) { return SIGNALS[p.dataIndex].id; }
          },
          data: SIGNALS.map(function (d) {
            return {
              value: [d.speed, d.impact, d.score],
              signalId: d.id,
              symbolSize: 12 + d.reach * 6,
              itemStyle: {
                color: d.prio === 'P2' ? accent2 : accent,
                opacity: 0.92,
                borderColor: rule,
                borderWidth: 1
              }
            };
          })
        }]
      });

      chart.on('click', function (params) {
        var id = params.data && params.data.signalId;
        for (var k = 0; k < SIGNALS.length; k++) {
          if (SIGNALS[k].id === id) { paint(k); }
        }
      });

      window.addEventListener('resize', function () {
        if (chart) { chart.resize(); }
      });
    } catch (err) {
      showFallback();
    }
  }

  function boot() {
    bindSignals();
    buildChart();
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      window.requestAnimationFrame(boot);
    });
  } else {
    window.addEventListener('load', boot);
  }
})();
