// The graphs of the chosen range, drawn on canvases in device pixels, so they stay sharp at any
// pixel ratio:
//   the band - all attacks as an area under a smooth line, the range before as a thin line;
//   the rows - one row of columns per honeypot (its own scale), its peak and its total.
// One cursor runs through both: pointing at a time in the band or in a row shows the values at that
// time everywhere. Names come from pulse.js and are checked there; they only go in as text.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};

  var BAND_H = 100;      // CSS pixels of the band's curve (the keys sit in its top 26)
  var ROW_H = 34;        // CSS pixels of a row

  var fmt = function (n) { return Number(n).toLocaleString('en-US'); };
  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function two(n) { return ('0' + n).slice(-2); }

  // ---------- time ----------

  var TIME = {
    '24h': { step: 6 * 3600000, label: function (t) { return two(t.getHours()) + ':' + two(t.getMinutes()); } },
    '1h': { step: 15 * 60000, label: function (t) { return two(t.getHours()) + ':' + two(t.getMinutes()); } },
    '1m': { step: 15000, label: function (t) { return two(t.getHours()) + ':' + two(t.getMinutes()) + ':' + two(t.getSeconds()); } }
  };

  function pointName(range, key, width, last) {
    var a = new Date(key), b = new Date(key + width), f = TIME[range].label;
    var name = range === '1h' ? f(a) : f(a) + '\u2013' + (range === '1m' ? two(b.getSeconds()) : f(b));
    return last ? name + ' (so far)' : name;
  }

  // ---------- canvas in device pixels ----------

  // sizes the canvas to its box (whole CSS pixels) and returns its context in device pixels
  function prepare(canvas, height) {
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(1, Math.floor(canvas.parentNode.getBoundingClientRect().width));
    canvas.style.setProperty('width', w + 'px');
    canvas.style.setProperty('height', height + 'px');
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(height * dpr);
    var ctx = canvas.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return { ctx: ctx, w: canvas.width, h: canvas.height, dpr: dpr, css: w };
  }

  function px(dpr, n) { return Math.max(1, Math.round(n * dpr)); }

  // A curve in device pixels: straight pieces through the points, an area under them, the peak as a
  // dot with a ring, the end as a light dot; the running last piece dashed. Returns the points.
  // line: the width of the line in CSS pixels (2 in the rows, 3 in the band)
  function curve(c, values, bottom, top, most, line) {
    var ctx = c.ctx, dpr = c.dpr, n = values.length;
    var magenta = TPot.color('magenta'), ink = TPot.color('ink'), glass = TPot.color('glass');
    var pts = values.map(function (v, i) {
      return [n > 1 ? Math.round(px(dpr, 1) + i * (c.w - 2 * px(dpr, 1)) / (n - 1)) : Math.round(c.w / 2),
              Math.round(bottom - (most ? v / most : 0) * (bottom - top))];
    });
    ctx.beginPath();
    ctx.moveTo(pts[0][0], bottom);
    pts.forEach(function (p) { ctx.lineTo(p[0], p[1]); });
    ctx.lineTo(pts[n - 1][0], bottom);
    ctx.closePath();
    ctx.fillStyle = TPot.rgba(magenta, 0.16);
    ctx.fill();

    var done = n > 2 ? pts.slice(0, n - 1) : pts;
    ctx.beginPath();
    done.forEach(function (p, i) { if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
    ctx.lineWidth = px(dpr, line || 2);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.strokeStyle = magenta;
    ctx.stroke();
    if (n > 2) {
      ctx.beginPath();
      ctx.moveTo(pts[n - 2][0], pts[n - 2][1]);
      ctx.lineTo(pts[n - 1][0], pts[n - 1][1]);
      ctx.setLineDash([px(dpr, 2), px(dpr, 3)]);
      ctx.globalAlpha = 0.7;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    if (most) {
      var peak = values.indexOf(Math.max.apply(null, values));
      dot(ctx, pts[peak][0], pts[peak][1], px(dpr, 3), magenta, px(dpr, 2), ink);
    }
    dot(ctx, pts[n - 1][0], pts[n - 1][1], Math.max(1, Math.round(2.5 * dpr)), glass, Math.max(1, Math.round(1.5 * dpr)), ink);
    return pts;
  }

  function cross(c, x) {
    var lw = px(c.dpr, 1);
    c.ctx.fillStyle = TPot.rgba(TPot.color('glass'), 0.7);
    c.ctx.fillRect(x - Math.floor(lw / 2), 0, lw, c.h);
  }

  // ---------- state ----------

  var state = { data: null, meta: null, cursor: null, rows: [], band: null };

  // ---------- the band ----------

  function drawBand() {
    var d = state.data, canvas = $('band-canvas');
    if (!d || !canvas) return;
    var c = prepare(canvas, BAND_H), ctx = c.ctx, dpr = c.dpr;
    var v = d.slots.map(function (s) { return s.n; }), n = v.length;
    // the running point holds only the attacks so far: drawn as its rate over the whole point
    // (dashed, an estimate), so the line does not fall at the right edge; the readout shows the count
    var width = slotWidth(), run = Date.now() - d.slots[n - 1].key, scale = 1;
    if (n > 2 && width && run > 0 && run < width) scale = width / Math.max(run, Math.min(width, 30000));
    v[n - 1] *= scale;
    // the range before ends one span ago, in the middle of its last point too: the same estimate
    var before = d.before.length >= n ? d.before.slice(-n).map(function (s) { return s.n; }) : null;
    var beforeRaw = before ? before.slice() : null;
    if (before) before[n - 1] *= scale;
    var most = Math.max.apply(null, v.concat(before || [], [1]));
    var top = px(dpr, 30), bottom = c.h - px(dpr, 1);
    var mist = TPot.color('mist');

    // the baseline
    ctx.fillStyle = TPot.color('comb-lit');
    ctx.fillRect(0, bottom, c.w, px(dpr, 1));

    // the range before, a thin line under the curve of the range
    var y = function (x) { return Math.round(bottom - x / most * (bottom - top)); };
    var xs = v.map(function (_, i) { return n > 1 ? Math.round(px(dpr, 1) + i * (c.w - 2 * px(dpr, 1)) / (n - 1)) : Math.round(c.w / 2); });
    if (before) {
      ctx.beginPath();
      before.forEach(function (x, i) { if (i) ctx.lineTo(xs[i], y(x)); else ctx.moveTo(xs[i], y(x)); });
      ctx.lineWidth = px(dpr, 2);
      ctx.lineJoin = 'round';
      ctx.strokeStyle = TPot.rgba(mist, 0.75);
      ctx.stroke();
    }
    var pts = curve(c, v, bottom, top, most, 3);

    // the cursor
    var i = state.cursor;
    if (i !== null && i < n) {
      cross(c, xs[i]);
      dot(ctx, xs[i], pts[i][1], px(dpr, 4), TPot.color('magenta'), px(dpr, 2), TPot.color('ink'));
      if (before) dot(ctx, xs[i], y(before[i]), px(dpr, 3), mist, px(dpr, 2), TPot.color('ink'));
    }
    state.band = { xs: xs, dpr: dpr, before: beforeRaw, css: c.css };
    axis($('band-axis'), c.css, d, function (key) {
      return (key - d.slots[0].key) / Math.max(1, d.slots[n - 1].key - d.slots[0].key) * c.css;
    });
    readout();
  }

  function dot(ctx, x, y, r, color, ring, ringColor) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = ring;
    ctx.strokeStyle = ringColor;
    ctx.stroke();
  }

  // time marks as text under a graph, at whole CSS pixels
  function axis(box, width, d, place) {
    if (!box) return;
    box.textContent = '';
    var n = d.slots.length, first = d.slots[0].key, last = d.slots[n - 1].key;
    var step = TIME[d.range].step, label = TIME[d.range].label;
    var room = d.range === '1m' ? 72 : 52, prev = -1e9;
    for (var t = Math.ceil(first / step) * step; t <= last; t += step) {
      var x = Math.round(place(t));
      // marks that would run into each other or into "now" are left out
      if (x < 28 || x > width - 56 || x - prev < room) continue;
      prev = x;
      var s = el('span', 'tick', label(new Date(t)));
      s.style.setProperty('left', x + 'px');
      box.appendChild(s);
    }
    box.appendChild(el('span', 'tick now', 'now'));
  }

  // the text over the band: the keys, or the values at the cursor
  function readout() {
    var d = state.data, m = state.meta, box = $('band-read');
    box.textContent = '';
    var i = state.cursor, n = d.slots.length;
    if (i === null || i >= n) {
      var a = el('span', 'key-now'), b = el('span', 'key-before');
      a.appendChild(document.createTextNode(m.name.replace(/^the /, '')));
      b.appendChild(document.createTextNode(m.short + ' before'));
      box.appendChild(a);
      if (state.band && state.band.before) box.appendChild(b);
      return;
    }
    box.appendChild(el('span', 'when', pointName(d.range, d.slots[i].key, slotWidth(), i === n - 1)));
    var now = el('span', 'key-now');
    now.appendChild(el('strong', '', fmt(d.slots[i].n)));
    now.appendChild(document.createTextNode(d.slots[i].n === 1 ? ' attack' : ' attacks'));
    box.appendChild(now);
    if (state.band && state.band.before) {
      var be = el('span', 'key-before');
      be.appendChild(el('strong', '', fmt(state.band.before[i])));
      be.appendChild(document.createTextNode(' the ' + m.short + ' before'));
      box.appendChild(be);
    }
  }

  function slotWidth() {
    var s = state.data.slots;
    return s.length > 1 ? s[1].key - s[0].key : 0;
  }

  // ---------- the rows of columns ----------

  function buildRows() {
    var d = state.data, m = state.meta, list = $('series');
    list.textContent = '';
    state.rows = [];
    $('series-title').textContent = 'Attacks per honeypot, ' + m.name.replace(/^the /, '');

    if (!d.total || !d.types.length) {
      list.appendChild(el('li', 'none', 'No attacks in ' + m.name + '.'));
      return;
    }
    var head = el('li', 'series-head');
    head.appendChild(el('span', '', 'Honeypot'));
    head.appendChild(el('span', ''));
    state.peakHead = el('span', 'num peak', 'peak / ' + m.per);
    head.appendChild(state.peakHead);
    head.appendChild(el('span', 'num', 'total'));
    list.appendChild(head);

    d.types.slice(0, TPot.view ? TPot.view.limit() : 5).forEach(function (t) {
      var byKey = {};
      t.slots.forEach(function (s) { byKey[s.key] = s.n; });
      var r = { name: t.type, values: d.slots.map(function (s) { return byKey[s.key] || 0; }), total: t.n };
      r.most = Math.max.apply(null, r.values.concat([0]));
      var li = el('li');
      var a = el('a', 'name', t.type);
      a.setAttribute('href', t.link);
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
      a.setAttribute('title', 'Open the ' + t.type + ' events of ' + m.name + ' in Kibana');
      li.appendChild(a);
      var cell = el('span', 'spark');
      r.canvas = document.createElement('canvas');
      r.canvas.setAttribute('aria-hidden', 'true');
      cell.appendChild(r.canvas);
      li.appendChild(cell);
      r.peak = el('span', 'num peak', fmt(r.most));
      li.appendChild(r.peak);
      li.appendChild(el('span', 'num total', fmt(r.total)));
      list.appendChild(li);
      state.rows.push(r);
    });

    var axisRow = el('li', 'series-axis');
    axisRow.appendChild(el('span'));
    state.rowAxis = el('span', 'spark axis-box');
    axisRow.appendChild(state.rowAxis);
    axisRow.appendChild(el('span', 'peak'));
    axisRow.appendChild(el('span'));
    list.appendChild(axisRow);
  }

  function drawRows() {
    var d = state.data;
    if (!d || !state.rows.length) return;
    var base = TPot.color('comb-lit'), last = null;
    state.rows.forEach(function (r) {
      var c = prepare(r.canvas, ROW_H), ctx = c.ctx;
      var bottom = c.h - px(c.dpr, 1), top = px(c.dpr, 5);
      ctx.fillStyle = base;
      ctx.fillRect(0, bottom, c.w, px(c.dpr, 1));
      r.pts = curve(c, r.values, bottom, top, r.most);
      if (state.cursor !== null) cross(c, r.pts[state.cursor][0]);
      r.peak.textContent = fmt(state.cursor === null ? r.most : r.values[state.cursor]);
      last = { c: c, pts: r.pts };
    });
    if (state.peakHead) state.peakHead.textContent = state.cursor === null ? 'peak / ' + state.meta.per : 'at this time';
    $('series').classList.toggle('pointing', state.cursor !== null);
    $('series-when').textContent = state.cursor === null ? '' :
      pointName(d.range, d.slots[state.cursor].key, slotWidth(), state.cursor === d.slots.length - 1);
    if (last) {
      state.rowGeo = last;
      var k = last.c.dpr;
      axis(state.rowAxis, last.c.css, d, function (key) {
        var i = Math.max(0, Math.min(last.pts.length - 1, Math.round((key - d.slots[0].key) / Math.max(1, slotWidth()))));
        return last.pts[i][0] / k;
      });
    }
  }

  // ---------- tables for screen readers ----------

  function tables() {
    var d = state.data, m = state.meta;
    var t = $('band-table');
    t.textContent = '';
    var head = el('tr');
    ['Time', 'Attacks', 'The ' + m.short + ' before'].concat(state.rows.map(function (r) { return r.name; }))
      .forEach(function (h) { head.appendChild(el('th', '', h)); });
    t.appendChild(head);
    var before = d.before.slice(-d.slots.length);
    d.slots.forEach(function (s, i) {
      var tr = el('tr');
      tr.appendChild(el('td', '', pointName(d.range, s.key, slotWidth(), i === d.slots.length - 1)));
      tr.appendChild(el('td', '', fmt(s.n)));
      tr.appendChild(el('td', '', before[i] ? fmt(before[i].n) : ''));
      state.rows.forEach(function (r) { tr.appendChild(el('td', '', fmt(r.values[i]))); });
      t.appendChild(tr);
    });
  }

  // ---------- pointing ----------

  function point(i) {
    var d = state.data;
    if (!d || !d.slots.length) return;
    state.cursor = i === null ? null : Math.max(0, Math.min(d.slots.length - 1, i));
    drawBand();
    drawRows();
  }

  function fromBand(clientX) {
    var b = $('band-canvas').getBoundingClientRect(), n = state.data.slots.length;
    return Math.round((clientX - b.left) / Math.max(1, b.width) * (n - 1));
  }

  function fromRows(clientX) {
    var r = state.rows[0], geo = state.rowGeo;
    if (!r || !geo) return null;
    var b = r.canvas.getBoundingClientRect();
    if (clientX < b.left - 4 || clientX > b.right + 4) return null;
    var x = (clientX - b.left) * geo.c.dpr, best = 0;
    geo.pts.forEach(function (p, i) { if (Math.abs(p[0] - x) < Math.abs(geo.pts[best][0] - x)) best = i; });
    return best;
  }

  function keys(ev) {
    if (!state.data) return;
    if (ev.key === 'Escape') { point(null); return; }
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    ev.preventDefault();
    var n = state.data.slots.length;
    point(state.cursor === null ? n - 1 : state.cursor + (ev.key === 'ArrowLeft' ? -1 : 1));
  }

  function wire() {
    var band = $('band-chart'), list = $('series');
    band.addEventListener('pointermove', function (ev) { if (state.data) point(fromBand(ev.clientX)); });
    band.addEventListener('pointerleave', function () { point(null); });
    band.addEventListener('keydown', keys);
    band.addEventListener('blur', function () { point(null); });
    list.addEventListener('pointermove', function (ev) {
      if (!state.data) return;
      var i = fromRows(ev.clientX);
      point(i);
    });
    list.addEventListener('pointerleave', function () { point(null); });
    list.addEventListener('keydown', keys);
    list.addEventListener('blur', function () { point(null); });
    var redraw = function () { if (state.data) { drawBand(); drawRows(); if (TPot.snap) TPot.snap(); } };
    if (typeof ResizeObserver === 'function') {
      var last = [0, 0];
      new ResizeObserver(function () {
        var w = [Math.floor(band.getBoundingClientRect().width), Math.floor(list.getBoundingClientRect().width)];
        if (w[0] !== last[0] || w[1] !== last[1]) { last = w; redraw(); }
      }).observe(document.body);
    } else {
      window.addEventListener('resize', redraw);
    }
    // a new pixel ratio (browser zoom, another screen): draw again for it
    (function watchRatio() {
      if (!window.matchMedia) return;
      var mq = window.matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
      var on = function () { mq.removeEventListener('change', on); redraw(); watchRatio(); };
      if (mq.addEventListener) mq.addEventListener('change', on);
    })();
  }

  var wired = false;

  TPot.graph = {
    // the same data again, i.e. after the number of rows changed
    refresh: function () {
      if (!state.data || !state.data.slots.length) return;
      buildRows();
      drawBand();
      drawRows();
      tables();
      if (TPot.snap) TPot.snap();
    },
    // d: an overview of pulse.js, meta: its range of queries.json; fresh: a new range
    render: function (d, meta, fresh) {
      if (!wired) { wire(); wired = true; }
      if (fresh) state.cursor = null;
      state.data = d;
      state.meta = meta;
      if (!d.slots.length) return;
      buildRows();
      drawBand();
      drawRows();
      tables();
    }
  };
})();
