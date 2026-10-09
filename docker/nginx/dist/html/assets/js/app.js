// Wires the page: the clock, the switches of the time range and of the background effect, the wall
// mode and the polling, whose new attacks are handed to the effect spread over the interval.
(function () {
  'use strict';
  var TPot = window.TPot;

  var MOST_EVENTS = 40;       // visible attacks per interval, more only make them brighter
  var KEY_EFFECT = 'tpot.effect';
  var KEY_RANGE = 'tpot.range';
  // the attacks that fill the glass of the honey effect, per range (log scale)
  var FULL = { '24h': 1e7, '1h': 5e5, '1m': 1e4 };

  var current = null, name = null, env = null, timers = [], rangeMenu = null, effectMenu = null;
  var range = null, previous = null, down = false, overviewTimer = 0, listsTimer = 0;

  // ---------- small helpers ----------

  function load(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }

  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private window: not remembered */ }
  }

  // the query of the page: only known names count
  function asked(key) {
    try { return new URLSearchParams(window.location.search).get(key); } catch (e) { return null; }
  }

  function meta() {
    return TPot.pulse.ranges()[range];
  }

  // ---------- clock ----------

  function clock() {
    var now = new Date(), node = document.getElementById('clock');
    var date = now.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    var time = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2);
    node.setAttribute('datetime', now.toISOString());
    node.textContent = '';
    var d = document.createElement('span');
    d.className = 'date';
    d.textContent = date;
    node.appendChild(d);
    var t = document.createElement('span');
    t.className = 'time';
    t.textContent = time;
    node.appendChild(t);
  }

  // ---------- effects ----------

  function avoid() {
    // the cells under these stay dark, so the text stays readable
    var out = [];
    document.querySelectorAll('.band-in, .multiples, .top-sources, .sensors, .brand, .clock, .menus').forEach(function (n) {
      out.push(n.getBoundingClientRect());
    });
    return out;
  }

  function useEffect(next) {
    if (!TPot.effects[next]) next = 'comb';
    if (current) current.stop();
    timers.forEach(clearTimeout);
    timers = [];
    document.body.classList.remove('effect-' + name);
    document.body.classList.add('effect-' + next);
    if (effectMenu) effectMenu.set(next);
    name = next;
    current = TPot.effects[next];
    current.start(env);
    if (previous && current.data) current.data({ total: previous.total, full: FULL[range] });
    if (down) current.rest(true);
  }

  // the attacks of one interval as single events at random moments of it
  function spread(attacks, sources, every) {
    if (!current || attacks <= 0) return;
    var events = Math.min(attacks, MOST_EVENTS);
    var strength = attacks > MOST_EVENTS ? 1 : 0.85;
    var fresh = Math.min(events, sources);
    if (env.reduced) {
      if (current.minute) current.minute();
      for (var j = 0; j < events; j++) current.attack(j < fresh, strength);
      return;
    }
    for (var i = 0; i < events; i++) {
      (function (isFresh) {
        timers.push(setTimeout(function () { if (current) current.attack(isFresh, strength); }, Math.random() * every));
      })(i < fresh);
    }
  }

  // ---------- polling ----------

  function overview() {
    clearTimeout(overviewTimer);
    var want = range, m = meta(), every = m.every * 1000;
    TPot.pulse.overview(want).then(function (o) {
      if (want !== range) return;
      var first = !previous;
      var news = TPot.pulse.fresh(previous, o);
      if (!news) {
        // the first answer of a range: as many as the last full point brings in an interval
        var s = o.slots, width = s.length > 1 ? s[1].key - s[0].key : 0;
        var full = s.length > 1 ? s[s.length - 2].n : 0;
        news = { attacks: width ? Math.round(full * every / width) : 0, sources: 0 };
      }
      var wasDown = down;
      down = false;
      previous = o;
      TPot.view.overview(o, m, first);
      TPot.graph.render(o, m, first);
      TPot.view.status('live', '');
      TPot.view.updated(m.every);
      TPot.snap();
      if (current) {
        if (wasDown) current.rest(false);
        if (current.data) current.data({ total: o.total, full: FULL[range] });
      }
      if (wasDown) lists();
      timers = timers.slice(-MOST_EVENTS);     // the ones of the interval before have fired
      spread(news.attacks, news.sources, every);
    }, function () {
      if (want !== range) return;
      down = true;
      previous = null;
      TPot.view.down();
      TPot.view.status('down', 'Elasticsearch is not reachable. Kibana and the Attack Map need it too; ' +
                               'after a start it can take a few minutes.');
      if (current) current.rest(true);
      TPot.snap();
    }).then(function () {
      if (want === range && !document.hidden) overviewTimer = setTimeout(overview, every);
    });
  }

  function lists() {
    clearTimeout(listsTimer);
    var want = range, m = meta();
    var quiet = function () { /* the overview reports the state */ };
    Promise.all([
      TPot.pulse.sources(want).then(function (s) { if (want === range) TPot.view.sources(s, m); }, quiet),
      TPot.pulse.sensors(want).then(function (s) { if (want === range) TPot.view.sensors(s, m); }, quiet)
    ]).then(function () {
      TPot.snap();
      if (want === range && !document.hidden) listsTimer = setTimeout(lists, m.lists * 1000);
    });
  }

  function useRange(next, remembered) {
    var ranges = TPot.pulse.ranges();
    if (!ranges[next]) next = Object.keys(ranges)[0];
    if (next === range) return;
    range = next;
    if (remembered) save(KEY_RANGE, next);
    previous = null;
    timers.forEach(clearTimeout);
    timers = [];
    if (rangeMenu) rangeMenu.set(next);
    overview();
    lists();
  }

  // ---------- wall mode: ?kiosk, or the key f (full screen, esc ends it) ----------

  var idle = 0;

  function kiosk(on) {
    document.body.classList.toggle('kiosk', on);
    if (env && current) useEffect(name);
    TPot.snap();
  }

  function still() {
    document.body.classList.remove('idle');
    clearTimeout(idle);
    if (document.body.classList.contains('kiosk')) {
      idle = setTimeout(function () { document.body.classList.add('idle'); }, 3000);
    }
  }

  function keys(ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.target.closest('input, textarea, select')) return;
    if (ev.key === 'f' || ev.key === 'F') {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().then(function () { kiosk(true); still(); },
                                                          function () { kiosk(!document.body.classList.contains('kiosk')); });
      }
    }
  }

  // ---------- start ----------

  document.addEventListener('DOMContentLoaded', function () {
    clock();
    setInterval(clock, 1000);
    setInterval(TPot.view.tick, 1000);

    env = {
      backdrop: new TPot.Backdrop(document.getElementById('backdrop')),
      honey: document.getElementById('honey'),
      logo: document.querySelector('.logo'),
      avoid: avoid,
      reduced: TPot.reducedMotion()
    };

    effectMenu = TPot.menu(document.getElementById('effect-menu'), function (next) {
      if (next === name) return;
      save(KEY_EFFECT, next);
      useEffect(next);
    });
    var effect = load(KEY_EFFECT);
    useEffect(effect && TPot.effects[effect] ? effect : 'comb');

    if (asked('kiosk') !== null) { kiosk(true); still(); }
    document.addEventListener('keydown', keys);
    document.addEventListener('pointermove', still, { passive: true });
    document.addEventListener('fullscreenchange', function () {
      if (!document.fullscreenElement && asked('kiosk') === null) kiosk(false);
    });

    // whole device pixels again after the fonts came in and after every change of the size
    TPot.snap();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(TPot.snap);
    var resized = 0;
    var later = function () {
      cancelAnimationFrame(resized);
      resized = requestAnimationFrame(TPot.snap);
    };
    window.addEventListener('resize', later);
    // a block that grows (the lists come in later) moves the centred layout by parts of a pixel
    if (typeof ResizeObserver === 'function') {
      var watch = new ResizeObserver(later);
      document.querySelectorAll('.hive, .pulse, .details, .top').forEach(function (n) { watch.observe(n); });
    }

    // a hidden tab asks nothing; back in view it asks at once
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        clearTimeout(overviewTimer);
        clearTimeout(listsTimer);
      } else if (range) {
        overview();
        lists();
      }
    });

    TPot.pulse.load().then(function () {
      rangeMenu = TPot.menu(document.getElementById('range-menu'), function (next) { useRange(next, true); });
      useRange(asked('range') || load(KEY_RANGE), false);
    }, function () {
      down = true;
      TPot.view.status('down', 'The page could not load its queries (assets/queries.json).');
    });
  });
})();
