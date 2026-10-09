// The pulse: the attacks of this T-Pot from Elasticsearch, per time range. The queries come from
// assets/queries.json, which tpotctl/landing.py writes (python3 -m tpotctl.landing).
//
// Everything Elasticsearch returns comes from attackers: source addresses, countries, reputations and
// names are checked here and anything that does not fit is dropped before it is shown or put in a
// link. A source with a value that does not fit is left out as a whole. The page only ever sets text
// (textContent), never markup.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};

  var SEARCH = 'es/logstash-*/_search';
  var QUERIES = 'assets/queries.json';
  var TIMEOUT = 5000;
  var SHOWN = 7;      // the page shows five of them, or seven when there are no sensors

  // ---------- checks of the values that come from attackers ----------

  var IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
  var IPV6_CHARS = /^[0-9A-Fa-f:.]{2,45}$/;
  var TYPE = /^[A-Za-z0-9_-]{1,40}$/;
  // a country of the GeoIP database: letters, spaces and . , ' ( ) -
  var COUNTRY = /^[\p{L}\p{M}][\p{L}\p{M} .,'()-]{0,55}$/u;
  // a category of the reputation list (Listbot), i.e. "known attacker", "bot, crawler"
  var REPUTATION = /^[a-z0-9][a-z0-9 ,-]{0,39}$/i;
  // a sensor is a web user of Logstash, the rule of tpotctl/sensors.py check_user
  var SENSOR = /^[A-Za-z_][A-Za-z0-9_.-]{0,31}$/;

  function isIPv6(s) {
    if (!IPV6_CHARS.test(s) || s.indexOf(':') < 0) return false;
    try {
      // the URL parser knows every form of IPv6; only hex digits, colons and dots get this far
      return new URL('http://[' + s + ']/').hostname.length > 2;
    } catch (e) {
      return false;
    }
  }

  function ip(value) {
    return typeof value === 'string' && (IPV4.test(value) || isIPv6(value)) ? value : null;
  }

  function type(value) {
    return typeof value === 'string' && TYPE.test(value) ? value : null;
  }

  function sensor(value) {
    return typeof value === 'string' && SENSOR.test(value) ? value : null;
  }

  // an optional text: '' when there is none, null when there is one that does not fit
  function optional(value, pattern) {
    if (value === undefined || value === null || value === '') return '';
    return typeof value === 'string' && pattern.test(value) ? value : null;
  }

  function count(value) {
    var n = Number(value);
    return isFinite(n) && n >= 0 ? Math.floor(n) : 0;
  }

  function stamp(value) {
    return typeof value === 'number' && isFinite(value) && value > 0 ? value : null;
  }

  // ---------- links into Kibana Discover, built from checked values only ----------

  var KIBANA_TIME = { '24h': 'now-24h', '1h': 'now-1h', '1m': 'now-1m' };

  // value has passed type(), ip() or sensor(): no quotes, no rison specials
  function kibana(field, value, range) {
    var kql = field + ':"' + value + '"';
    return '/kibana/app/discover#/?_g=(time:(from:' + (KIBANA_TIME[range] || 'now-24h') + ',to:now))' +
      '&_a=(dataSource:(dataViewId:\'logstash-*\',type:dataView),query:(language:kuery,query:\'' +
      encodeURIComponent(kql) + '\'))';
  }

  // ---------- reading the answers ----------

  function aggs(answer) {
    return (answer && answer.aggregations) || {};
  }

  function slots(histogram, points) {
    return ((histogram || {}).buckets || []).map(function (b) {
      return { key: count(b.key), n: count(b.doc_count) };
    }).sort(function (x, y) { return x.key - y.key; }).slice(-points);
  }

  function parseOverview(answer, range, points) {
    var a = aggs(answer), cur = a.current || {};
    var types = [];
    ((cur.types || {}).buckets || []).forEach(function (b) {
      var t = type(b.key);
      if (t) types.push({ type: t, n: count(b.doc_count), slots: slots(b.slots, points), link: kibana('type', t, range) });
    });
    return {
      range: range,
      total: count(cur.doc_count),
      previous: count((a.previous || {}).doc_count),
      // the range before, point for point (one span earlier)
      before: slots((a.previous || {}).slots, points),
      slots: slots(cur.slots, points),
      types: types.slice(0, SHOWN),
      sources: count((cur.sources || {}).value),
      honeypots: count((cur.honeypots || {}).value),
      latest: stamp((a.latest || {}).value)
    };
  }

  function first(bucket, name) {
    var found = ((bucket[name] || {}).buckets) || [];
    return found.length ? found[0].key : '';
  }

  function parseSources(answer, range) {
    var out = [];
    ((aggs(answer).sources || {}).buckets || []).forEach(function (b) {
      var address = ip(b.key), country = optional(first(b, 'country'), COUNTRY),
          rep = optional(first(b, 'rep'), REPUTATION);
      if (!address || country === null || rep === null) return;
      out.push({ ip: address, n: count(b.doc_count), country: country, rep: rep,
                 link: kibana('src_ip', address, range) });
    });
    return out.slice(0, SHOWN);
  }

  function parseSensors(answer, range) {
    var out = [];
    ((aggs(answer).sensors || {}).buckets || []).forEach(function (b) {
      var name = sensor(b.key);
      if (!name) return;
      out.push({ name: name, n: count(b.doc_count), latest: stamp((b.latest || {}).value),
                 link: kibana('t-pot_sensor', name, range) });
    });
    return out;
  }

  // attacks that came in since the previous answer of the same range, per point (the sum slides)
  function fresh(previous, now) {
    if (!previous || previous.range !== now.range) return null;
    var seen = {}, newest = 0;
    previous.slots.forEach(function (b) { seen[b.key] = b.n; if (b.key > newest) newest = b.key; });
    var n = 0;
    now.slots.forEach(function (b) {
      if (b.key in seen) n += Math.max(0, b.n - seen[b.key]);
      else if (b.key > newest) n += b.n;
    });
    return { attacks: n, sources: Math.max(0, now.sources - previous.sources) };
  }

  // ---------- asking Elasticsearch ----------

  function post(body) {
    var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, TIMEOUT) : 0;
    return fetch(SEARCH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error('Elasticsearch answered ' + r.status);
      return r.json();
    }, function (e) {
      clearTimeout(timer);
      throw e;
    });
  }

  var queries = null;

  function load() {
    if (queries) return Promise.resolve(queries);
    return fetch(QUERIES, { cache: 'no-cache', credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('queries.json: ' + r.status);
      return r.json();
    }).then(function (q) {
      if (!q || !q.ranges || !q.ranges[q.default]) throw new Error('queries.json: no ranges');
      queries = q;
      return q;
    });
  }

  function range(key) {
    return queries.ranges[key] ? key : queries.default;
  }

  TPot.pulse = {
    load: load,
    // the ranges of queries.json: { key: {points, every, lists, name, short, per} }
    ranges: function () { return queries ? queries.ranges : {}; },
    overview: function (key) {
      return load().then(function () {
        var k = range(key), r = queries.ranges[k];
        return post(r.overview).then(function (a) { return parseOverview(a, k, r.points); });
      });
    },
    sources: function (key) {
      return load().then(function () {
        var k = range(key);
        return post(queries.ranges[k].sources).then(function (a) { return parseSources(a, k); });
      });
    },
    sensors: function (key) {
      return load().then(function () {
        var k = range(key);
        return post(queries.ranges[k].sensors).then(function (a) { return parseSensors(a, k); });
      });
    },
    fresh: fresh
  };
})();
