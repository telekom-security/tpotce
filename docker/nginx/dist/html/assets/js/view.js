// Shows the pulse on the page: the hero (the attacks of the range and four facts), the top sources
// and the sensors. Values only ever go in as text (textContent) or as attributes of elements made
// here, never as markup.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};

  var SVG = 'http://www.w3.org/2000/svg';
  var LIVE = 10 * 60 * 1000;
  // the arrow of the trend: up, down, level
  var TREND = { up: 'M4 15L15 4M8 4h7v7', down: 'M4 5l11 11M15 9v7H8', level: 'M3 10h13M11 5l5 5-5 5' };

  var fmt = function (n) { return Number(n).toLocaleString('en-US'); };
  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function ago(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + ' s ago';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400 * 2) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' days ago';
  }

  // ---------- the number of the range, counted up ----------

  var shown = null, anim = 0;

  function setTotal(n, jump) {
    var node = $('total');
    cancelAnimationFrame(anim);
    if (shown === null || jump || TPot.reducedMotion()) {
      shown = n;
      node.textContent = fmt(n);
      return;
    }
    var from = shown, start = performance.now(), span = 900;
    (function step(now) {
      var k = Math.min(1, (now - start) / span);
      node.textContent = fmt(Math.round(from + (n - from) * (1 - Math.pow(1 - k, 3))));
      if (k < 1) anim = requestAnimationFrame(step);
    })(start);
    shown = n;
  }

  // ---------- the facts ----------

  function fact(id, value, label, title) {
    var li = $(id);
    li.querySelector('.fact-value').textContent = value;
    if (label !== null) li.querySelector('.fact-label').textContent = label;
    li.setAttribute('title', title);
  }

  var latestAt = null;

  function tickLatest() {
    if (latestAt === null) {
      fact('fact-latest', '\u2013', null, 'No attack yet');
      return;
    }
    fact('fact-latest', ago(Date.now() - latestAt), null,
         'The latest attack came in ' + ago(Date.now() - latestAt) + ' (' + new Date(latestAt).toLocaleTimeString('en-GB') + ')');
  }

  function setTrend(o, meta) {
    var path = $('trend-path'), short = meta.short;
    if (!o.previous) {
      path.setAttribute('d', o.total ? TREND.up : TREND.level);
      fact('fact-trend', o.total ? 'new' : '\u2013', 'vs previous ' + short,
           o.total ? 'No attacks in the ' + short + ' before, ' + fmt(o.total) + ' now' : 'No attacks, neither now nor before');
      return;
    }
    var pct = Math.round((o.total - o.previous) / o.previous * 100);
    path.setAttribute('d', pct > 0 ? TREND.up : pct < 0 ? TREND.down : TREND.level);
    var text = (pct > 0 ? '+' : pct < 0 ? '\u2212' : '\u00b1') + Math.abs(pct) + ' %';
    fact('fact-trend', text, 'vs previous ' + short,
         fmt(o.total) + ' attacks in ' + meta.name + ', ' + fmt(o.previous) + ' in the ' + short + ' before');
  }

  // ---------- top sources ----------

  function link(href, title) {
    var a = el('a');
    a.setAttribute('href', href);
    a.setAttribute('target', '_blank');
    a.setAttribute('rel', 'noopener noreferrer');
    a.setAttribute('title', title);
    return a;
  }

  // five honeypots and sources next to the sensors, seven when there are none (the column stays full)
  function limit() {
    return sensorList.length ? 5 : 7;
  }

  var lastSources = null, lastSourcesMeta = null;

  function setSources(sources, meta) {
    lastSources = sources;
    lastSourcesMeta = meta;
    sources = sources.slice(0, limit());
    var list = $('sources');
    list.textContent = '';
    $('sources-title').textContent = 'Top sources, ' + meta.name.replace(/^the /, '');
    if (!sources.length) {
      list.appendChild(el('li', 'none', 'No sources in ' + meta.name + '.'));
      return;
    }
    sources.forEach(function (s) {
      var li = el('li'), a = link(s.link, 'Open the events of ' + s.ip + ' in Kibana');
      a.appendChild(el('span', 'ip', s.ip));
      var where = el('span', 'where', s.country);
      if (s.country) where.setAttribute('title', s.country);
      a.appendChild(where);
      a.appendChild(el('span', 'rep', s.rep));
      a.appendChild(el('span', 'count', fmt(s.n)));
      li.appendChild(a);
      list.appendChild(li);
    });
  }

  // ---------- sensors of a HIVE ----------

  function dot() {
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', 'dot');
    svg.setAttribute('viewBox', '0 0 10 12');
    svg.setAttribute('width', '10');
    svg.setAttribute('height', '12');
    svg.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(SVG, 'path');
    p.setAttribute('d', 'M5 0l5 3v6l-5 3-5-3V3z');
    svg.appendChild(p);
    return svg;
  }

  var sensorList = [], sensorMeta = null, ticks = 0;

  function setSensors(list, meta) {
    var was = limit();
    sensorList = list;
    sensorMeta = meta;
    $('sensors-box').hidden = !list.length;
    $('sensors-title').textContent = 'Sensors, ' + meta.name.replace(/^the /, '');
    var ol = $('sensors');
    ol.textContent = '';
    list.forEach(function (s) {
      var live = s.latest !== null && Date.now() - s.latest < LIVE;
      var li = el('li'), a = link(s.link, 'Open the events of the sensor ' + s.name + ' in Kibana');
      a.className = live ? 'is-live' : 'is-quiet';
      a.appendChild(dot());
      a.appendChild(el('span', 'name', s.name));
      a.appendChild(el('span', 'seen', s.latest === null ? '' : (live ? 'live, ' : 'quiet, ') + ago(Date.now() - s.latest)));
      a.appendChild(el('span', 'count', fmt(s.n)));
      li.appendChild(a);
      ol.appendChild(li);
    });
    if (limit() !== was) {
      if (lastSources) setSources(lastSources, lastSourcesMeta);
      if (TPot.graph && TPot.graph.refresh) TPot.graph.refresh();
    }
  }

  // ---------- states and the live badge ----------

  var updatedAt = null, every = 0;

  function setStatus(state, text) {
    var band = $('band'), live = $('live');
    band.setAttribute('data-state', state);
    live.setAttribute('data-state', state);
    $('live-text').textContent = state === 'live' ? 'live' : state === 'down' ? 'offline' : 'connecting';
    $('band-message').textContent = state === 'down' ? text : '';
    if (state !== 'live') live.setAttribute('title', text);
  }

  // an answer came in: the ring fills again until the next one
  function updated(seconds) {
    updatedAt = Date.now();
    every = seconds;
    var live = $('live');
    live.style.setProperty('--every', seconds + 's');
    live.classList.remove('turn');
    void live.offsetWidth;          // starts the animation of the ring anew
    live.classList.add('turn');
    tickLive();
  }

  function tickLive() {
    if (updatedAt === null || $('live').getAttribute('data-state') !== 'live') return;
    $('live').setAttribute('title', 'Updated ' + ago(Date.now() - updatedAt) + ', every ' + every + ' s');
  }

  TPot.view = {
    // o: an overview of pulse.js, meta: its range; jump: a new range, no counting up
    overview: function (o, meta, jump) {
      $('hero-title').textContent = 'Attacks in ' + meta.name;
      setTotal(o.total, jump);
      setTrend(o, meta);
      latestAt = o.latest;
      tickLatest();
      fact('fact-sources', fmt(o.sources), o.sources === 1 ? 'source' : 'sources',
           fmt(o.sources) + ' different source addresses in ' + meta.name);
      fact('fact-honeypots', fmt(o.honeypots), 'honeypots hit',
           fmt(o.honeypots) + (o.honeypots === 1 ? ' honeypot' : ' honeypots') + ' saw attacks in ' + meta.name);
    },
    sources: setSources,
    sensors: setSensors,
    limit: limit,
    status: setStatus,
    updated: updated,
    down: function () {
      shown = null;
      $('total').textContent = '\u00a0';
      latestAt = null;
      $('sources').textContent = '';
    },
    tick: function () {
      tickLatest();
      tickLive();
      ticks += 1;
      if (sensorList.length && ticks % 15 === 0) setSensors(sensorList, sensorMeta);
    }
  };
})();
