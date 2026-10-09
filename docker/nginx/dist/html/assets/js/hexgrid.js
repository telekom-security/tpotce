// The backdrop canvas and the honeycomb the effects draw on.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};
  TPot.effects = TPot.effects || {};

  TPot.reducedMotion = function () {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };

  // the tokens of tpot.css, so the canvas paints in the same colours
  TPot.color = function (name) {
    return getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim();
  };

  // rgba() of a #rrggbb token
  TPot.rgba = function (hex, alpha) {
    var n = parseInt(hex.replace('#', ''), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  };

  // a pointy-top hexagon around x, y with the circumradius r
  TPot.hexPath = function (ctx, x, y, r) {
    var w = r * 0.8660254;
    ctx.beginPath();
    ctx.moveTo(x, y - r);
    ctx.lineTo(x + w, y - r / 2);
    ctx.lineTo(x + w, y + r / 2);
    ctx.lineTo(x, y + r);
    ctx.lineTo(x - w, y + r / 2);
    ctx.lineTo(x - w, y - r / 2);
    ctx.closePath();
  };

  // A canvas over the whole window, in device pixels, drawn in CSS pixels.
  function Backdrop(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.width = 0;
    this.height = 0;
    this.resize();
  }

  Backdrop.prototype.resize = function () {
    var dpr = Math.min(window.devicePixelRatio || 1, 3);
    this.width = this.canvas.clientWidth || window.innerWidth;
    this.height = this.canvas.clientHeight || window.innerHeight;
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.dpr = dpr;
  };

  Backdrop.prototype.clear = function () {
    this.ctx.clearRect(0, 0, this.width, this.height);
  };

  TPot.Backdrop = Backdrop;

  // The centres of a honeycomb of circumradius r that covers width x height.
  TPot.combCells = function (width, height, r) {
    var w = r * 0.8660254 * 2, dy = r * 1.5, cells = [];
    for (var row = 0, y = 0; y < height + r; row++, y += dy) {
      for (var x = (row % 2) ? w / 2 : 0; x < width + w; x += w) {
        cells.push({ x: x, y: y });
      }
    }
    return cells;
  };

  // The still honeycomb as an image, drawn once per size.
  TPot.combLayer = function (backdrop, cells, r, stroke) {
    var layer = document.createElement('canvas');
    layer.width = backdrop.canvas.width;
    layer.height = backdrop.canvas.height;
    var ctx = layer.getContext('2d');
    ctx.setTransform(backdrop.dpr, 0, 0, backdrop.dpr, 0, 0);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1;
    for (var i = 0; i < cells.length; i++) {
      TPot.hexPath(ctx, cells[i].x, cells[i].y, r * 0.9);
      ctx.stroke();
    }
    return layer;
  };

  // Small marks (icons, outlines, the curves) on whole device pixels: a box that a centred layout puts
  // between two pixels is moved by the rest of a pixel, so its lines are not drawn half in each.
  // Read everything first, then write, so the layout is computed once.
  // Two passes: the boxes first, then the marks inside them, which move with their box.
  var SNAP = ['.cell, .logo, .honey, .fact-icon, #band-canvas, .series canvas, .menu-button, .readme, .live',
              '.cell .icon, .cell .away, .menu-button svg, .readme svg, .live svg, .fact-icon svg, .sensor-rows .dot'];

  function snapPass(selector, dpr) {
    var nodes = Array.prototype.slice.call(document.querySelectorAll(selector));
    var rects = nodes.map(function (n) { return n.getBoundingClientRect(); });
    nodes.forEach(function (n, i) {
      var r = rects[i];
      if (!r.width) return;
      var dx = (Math.round(r.left * dpr) - r.left * dpr) / dpr, dy = (Math.round(r.top * dpr) - r.top * dpr) / dpr;
      if (Math.abs(dx) > 0.005 || Math.abs(dy) > 0.005) n.style.setProperty('translate', dx.toFixed(3) + 'px ' + dy.toFixed(3) + 'px');
    });
  }

  TPot.snap = function () {
    var dpr = window.devicePixelRatio || 1;
    SNAP.forEach(function (s) {
      document.querySelectorAll(s).forEach(function (n) { n.style.removeProperty('translate'); });
    });
    SNAP.forEach(function (s) { snapPass(s, dpr); });
  };

  // The centre of an element in window coordinates.
  TPot.centreOf = function (el) {
    var b = el.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  };
})();
