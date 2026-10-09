// Comb: a honeycomb over the whole window. Every attack lights a cell that fades out again, attacks
// from new sources light brighter. On load the comb assembles from the logo outwards.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};
  TPot.effects = TPot.effects || {};

  var R = 22;               // circumradius of a cell, CSS pixels
  var ASSEMBLE = 1400;      // ms the comb takes to assemble
  var LIFE = 2600;          // ms a lit cell takes to fade
  var LIFE_FRESH = 3800;

  var env, bd, cells, layer, lit, raf, born, origin, resting, still, onResize;

  function build() {
    bd.resize();
    cells = TPot.combCells(bd.width, bd.height, R);
    layer = TPot.combLayer(bd, cells, R, TPot.color('comb'));
  }

  // the distances from the logo, measured anew in every frame of the assembly: the layout may still
  // move while the page loads, the wave keeps starting at the logo
  function measure() {
    origin = TPot.centreOf(env.logo);
    var far = 0;
    for (var i = 0; i < cells.length; i++) {
      var dx = cells[i].x - origin.x, dy = cells[i].y - origin.y;
      cells[i].d = Math.sqrt(dx * dx + dy * dy);
      if (cells[i].d > far) far = cells[i].d;
    }
    cells.far = far;
  }

  // a cell that is not under the text of the page, if one is found in a few tries
  function pick() {
    var avoid = env.avoid(), best = null;
    for (var t = 0; t < 6; t++) {
      var c = cells[Math.floor(Math.random() * cells.length)], free = true;
      for (var k = 0; k < avoid.length; k++) {
        var a = avoid[k];
        if (c.x > a.left - R && c.x < a.right + R && c.y > a.top - R && c.y < a.bottom + R) { free = false; break; }
      }
      best = c;
      if (free) break;
    }
    return best;
  }

  function draw(now) {
    var ctx = bd.ctx;
    bd.clear();
    var age = now - born;
    if (age < ASSEMBLE && !still) {
      measure();
      // the comb grows from the logo: a ring of cells at the front, the rest already there
      var front = (age / ASSEMBLE) * cells.far * 1.1;
      ctx.lineWidth = 1;
      for (var i = 0; i < cells.length; i++) {
        var c = cells[i];
        if (c.d > front) continue;
        var edge = Math.max(0, 1 - (front - c.d) / 160);
        ctx.strokeStyle = edge > 0 ? TPot.rgba(TPot.color('magenta'), 0.15 + edge * 0.5) : TPot.color('comb');
        TPot.hexPath(ctx, c.x, c.y, R * 0.9);
        ctx.stroke();
      }
    } else {
      ctx.drawImage(layer, 0, 0, bd.width, bd.height);
    }
    var magenta = TPot.color('magenta'), glass = TPot.color('glass');
    for (var j = lit.length - 1; j >= 0; j--) {
      var l = lit[j], life = l.fresh ? LIFE_FRESH : LIFE;
      var k = still ? 1 : 1 - (now - l.t) / life;
      if (k <= 0) { lit.splice(j, 1); continue; }
      var a = k * k * (l.fresh ? 0.95 : 0.7) * l.strength;
      TPot.hexPath(ctx, l.c.x, l.c.y, R * 0.9);
      ctx.fillStyle = TPot.rgba(magenta, a);
      ctx.fill();
      ctx.strokeStyle = l.fresh ? TPot.rgba(glass, a * 0.7) : TPot.rgba(magenta, Math.min(1, a * 1.6));
      ctx.lineWidth = l.fresh ? 1.5 : 1;
      ctx.stroke();
    }
    if (resting) {
      ctx.fillStyle = 'rgba(0,0,0,0.45)';
      ctx.fillRect(0, 0, bd.width, bd.height);
    }
  }

  function loop(now) {
    draw(now);
    var assembling = now - born < ASSEMBLE;
    raf = (assembling || lit.length) && !still ? requestAnimationFrame(loop) : 0;
  }

  function wake() {
    if (!raf && !still) raf = requestAnimationFrame(loop);
    if (still) draw(performance.now());
  }

  TPot.effects.comb = {
    start: function (e) {
      env = e;
      bd = e.backdrop;
      lit = [];
      resting = false;
      still = e.reduced;
      build();
      born = performance.now();
      onResize = function () { build(); wake(); };
      window.addEventListener('resize', onResize);
      wake();
    },
    attack: function (fresh, strength) {
      if (!cells) return;
      if (still && lit.length > 90) lit.shift();
      lit.push({ c: pick(), t: performance.now(), fresh: !!fresh, strength: strength || 1 });
      wake();
    },
    // reduced motion: a still picture of the last minute instead of fading cells
    minute: function () {
      if (still) lit = [];
    },
    rest: function (on) {
      resting = !!on;
      wake();
      if (!raf) draw(performance.now());
    },
    stop: function () {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      window.removeEventListener('resize', onResize);
      if (bd) bd.clear();
      cells = null;
      lit = [];
    }
  };
})();
