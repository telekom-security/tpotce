// Network: the drifting net of hexagons of the former landing page, its own code instead of
// particles.js. Every attack flashes a node and adds one at the edge, the pointer pushes nodes away.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};
  TPot.effects = TPot.effects || {};

  var LINK = 150;           // longest line, CSS pixels
  var DENSITY = 80 / 800;   // nodes per 1000 square pixels, as the former particles_conf.js
  var SPEED = 0.35;         // CSS pixels per frame at 60 fps
  var EXTRA = 40;           // nodes attacks may add on top of the base number

  var env, bd, nodes, base, raf, still, pointer, last, onResize, onMove, onLeave;

  function node(x, y) {
    var a = Math.random() * Math.PI * 2, v = SPEED * (0.4 + Math.random() * 0.8);
    return { x: x, y: y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: 1.5 + Math.random() * 4.5,
             flash: 0, fresh: false, added: false };
  }

  function populate() {
    bd.resize();
    base = Math.round(bd.width * bd.height / 1000 * DENSITY);
    nodes = [];
    for (var i = 0; i < base; i++) nodes.push(node(Math.random() * bd.width, Math.random() * bd.height));
  }

  function draw(now, dt) {
    var ctx = bd.ctx, magenta = TPot.color('magenta'), glass = TPot.color('glass');
    bd.clear();
    var i, j, n, m;
    if (!still) {
      for (i = 0; i < nodes.length; i++) {
        n = nodes[i];
        if (pointer) {
          var px = n.x - pointer.x, py = n.y - pointer.y, d2 = px * px + py * py;
          if (d2 < 120 * 120 && d2 > 1) {
            var push = (1 - Math.sqrt(d2) / 120) * 3;
            n.x += px / Math.sqrt(d2) * push;
            n.y += py / Math.sqrt(d2) * push;
          }
        }
        n.x += n.vx * dt;
        n.y += n.vy * dt;
        if (n.x < -10) n.x = bd.width + 10; else if (n.x > bd.width + 10) n.x = -10;
        if (n.y < -10) n.y = bd.height + 10; else if (n.y > bd.height + 10) n.y = -10;
        n.flash = Math.max(0, n.flash - dt / 150);
      }
    }
    ctx.lineWidth = 1.2;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        m = nodes[j];
        var dx = n.x - m.x, dy = n.y - m.y;
        if (dx > LINK || dx < -LINK || dy > LINK || dy < -LINK) continue;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d >= LINK) continue;
        var glow = Math.max(n.flash, m.flash);
        ctx.strokeStyle = TPot.rgba(magenta, (1 - d / LINK) * (0.32 + glow * 0.6));
        ctx.beginPath();
        ctx.moveTo(n.x, n.y);
        ctx.lineTo(m.x, m.y);
        ctx.stroke();
      }
    }
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      TPot.hexPath(ctx, n.x, n.y, n.r * (1 + n.flash * 1.4));
      ctx.fillStyle = n.flash > 0 ? TPot.rgba(n.fresh ? glass : magenta, 0.5 + n.flash * 0.5)
                                   : TPot.rgba(magenta, 0.5);
      ctx.fill();
    }
  }

  function loop(now) {
    var dt = Math.min(3, (now - last) / 16.67);
    last = now;
    draw(now, dt);
    raf = requestAnimationFrame(loop);
  }

  TPot.effects.net = {
    start: function (e) {
      env = e;
      bd = e.backdrop;
      still = e.reduced;
      pointer = null;
      populate();
      onResize = function () { populate(); if (still) draw(0, 0); };
      onMove = function (ev) { pointer = { x: ev.clientX, y: ev.clientY }; };
      onLeave = function () { pointer = null; };
      window.addEventListener('resize', onResize);
      window.addEventListener('pointermove', onMove, { passive: true });
      document.addEventListener('pointerleave', onLeave);
      last = performance.now();
      if (still) draw(0, 0); else raf = requestAnimationFrame(loop);
    },
    attack: function (fresh) {
      if (!nodes) return;
      var n = nodes[Math.floor(Math.random() * nodes.length)];
      n.flash = 1;
      n.fresh = !!fresh;
      if (!still) {
        // a new node comes in at an edge, the oldest added one leaves
        var side = Math.floor(Math.random() * 4), x = Math.random() * bd.width, y = Math.random() * bd.height;
        if (side === 0) x = -8; else if (side === 1) x = bd.width + 8; else if (side === 2) y = -8; else y = bd.height + 8;
        var add = node(x, y);
        add.added = true;
        add.flash = 0.6;
        nodes.push(add);
        if (nodes.length > base + EXTRA) {
          for (var i = base; i < nodes.length; i++) if (nodes[i].added) { nodes.splice(i, 1); break; }
        }
      } else {
        draw(0, 0);
      }
    },
    rest: function () {},
    stop: function () {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      window.removeEventListener('resize', onResize);
      window.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerleave', onLeave);
      if (bd) bd.clear();
      nodes = null;
    }
  };
})();
