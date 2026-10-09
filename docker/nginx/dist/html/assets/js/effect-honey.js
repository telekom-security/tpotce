// Honey: every attack drips from the dipper of the logo into the pot, the level in the glass shows
// the attacks of 24 hours. It draws over logo.webp and never redraws the logo itself.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};
  TPot.effects = TPot.effects || {};

  // places in the logo, as fractions of its width and height
  var TIP = { x0: 0.41, x1: 0.58, y: 0.37 };          // where the drops leave the dipper
  // the inside of the glass, measured from the pixels of logo.webp
  var JAR = [
    [0.343, 0.476], [0.655, 0.476], [0.657, 0.535], [0.666, 0.552], [0.684, 0.571], [0.714, 0.591],
    [0.731, 0.606], [0.731, 0.618], [0.266, 0.618], [0.266, 0.606], [0.283, 0.591], [0.314, 0.571],
    [0.332, 0.552], [0.341, 0.535]
  ];
  var TOP = 0.48, BOTTOM = 0.618;
  var G = 1.4;                                          // gravity, logo heights per second squared

  var env, bd, cv, ctx, w, h, drops, ripples, level, shown, raf, still, last, wobble, onResize;

  function size() {
    var dpr = Math.min(window.devicePixelRatio || 1, 3);
    w = cv.clientWidth;
    h = cv.clientHeight;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // the still comb behind the page
    bd.resize();
    bd.clear();
    var cells = TPot.combCells(bd.width, bd.height, 22);
    bd.ctx.drawImage(TPot.combLayer(bd, cells, 22, TPot.color('comb')), 0, 0, bd.width, bd.height);
  }

  function surface() {
    return BOTTOM - shown * (BOTTOM - TOP);
  }

  function jar() {
    ctx.beginPath();
    for (var i = 0; i < JAR.length; i++) {
      var p = JAR[i];
      if (i) ctx.lineTo(p[0] * w, p[1] * h); else ctx.moveTo(p[0] * w, p[1] * h);
    }
    ctx.closePath();
  }

  function draw(now) {
    var dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    shown += (level - shown) * (still ? 1 : Math.min(1, dt * 1.5));
    wobble *= Math.pow(0.12, dt);
    ctx.clearRect(0, 0, w, h);
    var magenta = TPot.color('magenta'), glass = TPot.color('glass');

    // honey in the glass, its surface moves where drops fall in
    var y = surface() * h;
    ctx.save();
    jar();
    ctx.clip();
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (var x = 0; x <= w; x += 3) {
      var wave = still ? 0 : Math.sin(x / w * 18 + now / 260) * wobble * h * 0.008;
      ctx.lineTo(x, y + wave);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = TPot.rgba(magenta, 0.42);
    ctx.fill();
    ctx.strokeStyle = TPot.rgba(magenta, 0.9);
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();

    // ripples where a drop hit the honey
    for (var r = ripples.length - 1; r >= 0; r--) {
      var rp = ripples[r], k = (now - rp.t) / 700;
      if (k >= 1) { ripples.splice(r, 1); continue; }
      ctx.beginPath();
      ctx.ellipse(rp.x * w, y, 2 + k * w * 0.05, 1 + k * h * 0.008, 0, 0, Math.PI * 2);
      ctx.strokeStyle = TPot.rgba(glass, (1 - k) * 0.6);
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // falling drops, longer while they fall
    for (var d = drops.length - 1; d >= 0; d--) {
      var dr = drops[d], t = (now - dr.t) / 1000;
      if (t < 0) continue;
      var dy = TIP.y + 0.5 * G * t * t;
      if (dy >= surface()) {
        drops.splice(d, 1);
        ripples.push({ x: dr.x, t: now });
        wobble = Math.min(1, wobble + (dr.fresh ? 0.5 : 0.25));
        continue;
      }
      var rad = w * (dr.fresh ? 0.017 : 0.012), stretch = 1 + Math.min(1.6, G * t * 1.4);
      ctx.beginPath();
      ctx.ellipse(dr.x * w, dy * h, rad, rad * stretch, 0, 0, Math.PI * 2);
      ctx.fillStyle = dr.fresh ? glass : magenta;
      ctx.fill();
    }
  }

  function loop(now) {
    draw(now);
    var busy = drops.length || ripples.length || wobble > 0.01 || Math.abs(level - shown) > 0.001;
    raf = busy ? requestAnimationFrame(loop) : 0;
  }

  function wake() {
    if (still) { draw(performance.now()); return; }
    if (!raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
  }

  TPot.effects.honey = {
    start: function (e) {
      env = e;
      bd = e.backdrop;
      cv = e.honey;
      ctx = cv.getContext('2d');
      still = e.reduced;
      drops = [];
      ripples = [];
      level = shown = 0.06;
      wobble = 0;
      last = performance.now();
      size();
      onResize = function () { size(); draw(performance.now()); };
      window.addEventListener('resize', onResize);
      draw(last);
    },
    // the level: the attacks of the range on a log scale, `full` of them fill the glass
    data: function (d) {
      var total = d && d.total > 0 ? d.total : 0, full = d && d.full > 1 ? d.full : 1e7;
      level = Math.max(0.06, Math.min(1, Math.log(total + 1) / Math.log(full)));
      wake();
    },
    attack: function (fresh) {
      if (still || !drops || drops.length > 40) return;
      drops.push({ x: TIP.x0 + Math.random() * (TIP.x1 - TIP.x0), t: performance.now(), fresh: !!fresh });
      wake();
    },
    rest: function () {
      wake();
    },
    stop: function () {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      window.removeEventListener('resize', onResize);
      if (ctx) ctx.clearRect(0, 0, w, h);
      if (bd) bd.clear();
      drops = null;
    }
  };
})();
