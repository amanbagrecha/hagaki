(function () {
  "use strict";

  var FONT_UI = '-apple-system, BlinkMacSystemFont, system-ui, "Helvetica Neue", "Segoe UI", Roboto, sans-serif';
  var FONT_SERIF = '"Hiragino Mincho ProN", "Hiragino Mincho Pro", "Yu Mincho", YuMincho, "Noto Serif JP", Georgia, serif';
  var FONT_KANJI = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", YuGothic, "Noto Sans JP", sans-serif';
  var TAU = Math.PI * 2;
  var MAX_AREA = 9e7;
  var MAX_SIDE = 16384;
  var VIDEO_SIDE = 3840;

  var tileStore = {};
  var tileOrder = [];
  var shadowCanvas = null;
  var smoothCanvas = null;
  var probeCanvas = null;

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function toNumber(v, fallback) {
    if (v === null || v === undefined || v === "") return fallback;
    var n = Number(v);
    return isFinite(n) ? n : fallback;
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function today() {
    var d = new Date();
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function postmarkDate(value) {
    var m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(String(value || "")) || /^(\d{4})-(\d{2})-(\d{2})/.exec(today());
    return m[1] + "." + pad2(+m[2]) + "." + pad2(+m[3]);
  }

  function seeded(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function makeCanvas(w, h) {
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return c;
  }

  function tile(key, w, h, paint) {
    var id = key + ":" + w + "x" + h;
    if (tileStore[id]) return tileStore[id];
    var c = makeCanvas(w, h);
    paint(c.getContext("2d"), c.width, c.height);
    tileStore[id] = c;
    tileOrder.push(id);
    if (tileOrder.length > 64) delete tileStore[tileOrder.shift()];
    return c;
  }

  function paintPattern(g, source, x, y, w, h, alpha, anchorX, anchorY) {
    var ax = Math.round(anchorX || 0), ay = Math.round(anchorY || 0);
    g.save();
    g.globalAlpha = alpha === undefined ? 1 : alpha;
    g.translate(ax, ay);
    g.fillStyle = g.createPattern(source, "repeat");
    g.fillRect(x - ax, y - ay, w, h);
    g.restore();
  }

  function hexToRgb(hex) {
    var h = hex.charAt(0) === "#" ? hex.slice(1) : hex;
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function rgba(color, a) {
    var c = typeof color === "string" ? hexToRgb(color) : color;
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (+a).toFixed(3) + ")";
  }

  function mix(a, b, t) {
    var x = hexToRgb(a), y = hexToRgb(b);
    return "rgb(" + Math.round(x[0] + (y[0] - x[0]) * t) + "," + Math.round(x[1] + (y[1] - x[1]) * t) + "," + Math.round(x[2] + (y[2] - x[2]) * t) + ")";
  }

  function colorHex(rgb) {
    var m = /rgb\((\d+),(\d+),(\d+)\)/.exec(rgb);
    if (!m) return rgb;
    return "#" + [m[1], m[2], m[3]].map(function (v) { return pad2Hex(+v); }).join("");
  }

  function pad2Hex(v) { var h = v.toString(16); return h.length < 2 ? "0" + h : h; }

  function linear(g, x0, y0, x1, y1, stops) {
    var gr = g.createLinearGradient(x0, y0, x1, y1);
    for (var i = 0; i < stops.length; i += 2) gr.addColorStop(stops[i], stops[i + 1]);
    return gr;
  }

  function radial(g, x, y, r, stops) {
    var gr = g.createRadialGradient(x, y, 0, x, y, Math.max(0.01, r));
    for (var i = 0; i < stops.length; i += 2) gr.addColorStop(stops[i], stops[i + 1]);
    return gr;
  }

  function fillAll(g, W, H, style) {
    g.fillStyle = style;
    g.fillRect(0, 0, W, H);
  }

  function glow(g, x, y, r, color, alpha) {
    g.fillStyle = radial(g, x, y, r, [0, rgba(color, alpha), 0.3, rgba(color, alpha * 0.62), 0.62, rgba(color, alpha * 0.2), 1, rgba(color, 0)]);
    g.fillRect(x - r, y - r, 2 * r, 2 * r);
  }

  function vignette(g, W, H, color, alpha, inner) {
    var r = Math.sqrt(W * W + H * H) / 2;
    g.fillStyle = radial(g, W / 2, H / 2, r, [inner === undefined ? 0.45 : inner, rgba(color, 0), 1, rgba(color, alpha)]);
    g.fillRect(0, 0, W, H);
  }

  function noiseTile() {
    return tile("noise", 160, 160, function (c, w, h) {
      var img = c.createImageData(w, h), d = img.data, rand = seeded(11);
      for (var i = 0; i < d.length; i += 4) {
        var v = rand() > 0.5 ? 255 : 0;
        d[i] = d[i + 1] = d[i + 2] = v;
        d[i + 3] = (8 + rand() * 46) | 0;
      }
      c.putImageData(img, 0, 0);
    });
  }

  function grainScale(S) { return clamp(S / 1100, 0.12, 1); }

  function grainInto(c, w, h, alpha) {
    c.save();
    c.globalAlpha = alpha;
    c.fillStyle = c.createPattern(noiseTile(), "repeat");
    c.fillRect(0, 0, w, h);
    c.restore();
  }

  function grain(g, W, H, alpha) {
    paintPattern(g, noiseTile(), 0, 0, W, H, alpha * grainScale(Math.sqrt(W * H)));
  }

  function smooth(g, W, H, paint, composite) {
    var d = Math.max(1, Math.max(W, H) / 560);
    var sw = Math.max(1, Math.ceil(W / d)), sh = Math.max(1, Math.ceil(H / d));
    if (!smoothCanvas) smoothCanvas = makeCanvas(sw, sh);
    var c = smoothCanvas;
    if (c.width !== sw || c.height !== sh) { c.width = sw; c.height = sh; }
    var x = c.getContext("2d");
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.globalAlpha = 1;
    x.globalCompositeOperation = "source-over";
    x.clearRect(0, 0, sw, sh);
    x.setTransform(sw / W, 0, 0, sh / H, 0, 0);
    paint(x, W, H);
    g.save();
    if (composite) g.globalCompositeOperation = composite;
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "low";
    g.drawImage(c, 0, 0, sw, sh, 0, 0, W, H);
    g.restore();
  }

  function fibreTile(S) {
    var T = clamp(Math.round(S * 0.42 / 32) * 32, 128, 1024);
    return tile("fibre", T, T, function (c, w) {
      var fine = grainScale(w / 0.42);
      var rand = seeded(23), k = w / 512, thick = Math.max(1, k);
      var count = Math.round(640 * k * k + 50);
      c.lineCap = "round";
      for (var i = 0; i < count; i++) {
        var x = rand() * w, y = rand() * w, len = (8 + rand() * rand() * 120) * k, ang = rand() * TAU;
        var bend = (rand() - 0.5) * len * 0.32;
        var x2 = x + Math.cos(ang) * len, y2 = y + Math.sin(ang) * len;
        var mx = (x + x2) / 2 - Math.sin(ang) * bend, my = (y + y2) / 2 + Math.cos(ang) * bend;
        var light = rand() < 0.84;
        c.strokeStyle = light ? "rgba(255,255,255," + (0.26 + rand() * 0.42).toFixed(3) + ")" : "rgba(132,104,68," + (0.04 + rand() * 0.07).toFixed(3) + ")";
        c.lineWidth = (0.3 + rand() * rand() * 1.4) * thick;
        for (var ox = -1; ox <= 1; ox++) {
          for (var oy = -1; oy <= 1; oy++) {
            var minX = Math.min(x, x2, mx) + ox * w, maxX = Math.max(x, x2, mx) + ox * w;
            var minY = Math.min(y, y2, my) + oy * w, maxY = Math.max(y, y2, my) + oy * w;
            if (maxX < -4 || minX > w + 4 || maxY < -4 || minY > w + 4) continue;
            c.beginPath();
            c.moveTo(x + ox * w, y + oy * w);
            c.quadraticCurveTo(mx + ox * w, my + oy * w, x2 + ox * w, y2 + oy * w);
            c.stroke();
          }
        }
      }
      var specks = Math.round(120 * k * k + 16);
      for (var j = 0; j < specks; j++) {
        c.fillStyle = "rgba(104,76,44," + (0.12 + rand() * 0.35).toFixed(3) + ")";
        c.beginPath();
        c.ellipse(rand() * w, rand() * w, (0.4 + rand() * 1.2) * thick, (0.3 + rand() * 0.6) * thick, rand() * TAU, 0, TAU);
        c.fill();
      }
      grainInto(c, w, w, 0.5 * fine);
    });
  }

  function addRoundRect(g, x, y, w, h, r) {
    var a, b, c, d;
    if (typeof r === "number") { a = b = c = d = r; } else { a = r[0]; b = r[1]; c = r[2]; d = r[3]; }
    var m = Math.max(0, Math.min(w, h) / 2);
    a = clamp(a, 0, m); b = clamp(b, 0, m); c = clamp(c, 0, m); d = clamp(d, 0, m);
    g.moveTo(x + a, y);
    g.lineTo(x + w - b, y);
    if (b) g.arcTo(x + w, y, x + w, y + b, b);
    g.lineTo(x + w, y + h - c);
    if (c) g.arcTo(x + w, y + h, x + w - c, y + h, c);
    g.lineTo(x + d, y + h);
    if (d) g.arcTo(x, y + h, x, y + h - d, d);
    g.lineTo(x, y + a);
    if (a) g.arcTo(x, y, x + a, y, a);
    g.closePath();
  }

  function roundRect(g, x, y, w, h, r) {
    g.beginPath();
    addRoundRect(g, x, y, w, h, r);
  }

  function soft(g, W, H, blur, color, paint) {
    var m = g.getTransform ? g.getTransform() : null;
    var k = m ? Math.sqrt(m.a * m.a + m.b * m.b) : 1;
    var far = W + H + blur * 4 + 200;
    g.save();
    g.shadowColor = color;
    g.shadowBlur = blur * k;
    g.shadowOffsetX = far * k;
    g.shadowOffsetY = 0;
    g.translate(-far, 0);
    g.fillStyle = "#000";
    g.strokeStyle = "#000";
    paint(g);
    g.restore();
  }

  function castShadow(g, W, H, addPath, box, layers, hole) {
    if (!layers || !layers.length) return;
    var minBlur = Infinity, reach = 0;
    for (var i = 0; i < layers.length; i++) {
      minBlur = Math.min(minBlur, layers[i].blur);
      reach = Math.max(reach, layers[i].blur * 1.2 + Math.abs(layers[i].y) + 2);
    }
    var d = clamp(minBlur / 5, 1, 8);
    var x0 = Math.max(0, Math.floor(box.x - reach)), y0 = Math.max(0, Math.floor(box.y - reach));
    var x1 = Math.min(W, Math.ceil(box.x + box.w + reach)), y1 = Math.min(H, Math.ceil(box.y + box.h + reach));
    if (x1 <= x0 || y1 <= y0) return;
    var sw = Math.ceil((x1 - x0) / d) + 1, sh = Math.ceil((y1 - y0) / d) + 1;
    if (!shadowCanvas) shadowCanvas = makeCanvas(sw, sh);
    var c = shadowCanvas;
    if (c.width < sw || c.height < sh || c.width > sw * 2 || c.height > sh * 2) { c.width = sw; c.height = sh; }
    var x = c.getContext("2d");
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.clearRect(0, 0, c.width, c.height);
    var far = c.width + c.height + reach / d + 200;
    x.save();
    x.setTransform(1 / d, 0, 0, 1 / d, -x0 / d - far, -y0 / d);
    x.fillStyle = "#000";
    for (var j = 0; j < layers.length; j++) {
      var s = layers[j];
      x.shadowColor = s.color;
      x.shadowBlur = s.blur / d;
      x.shadowOffsetX = far;
      x.shadowOffsetY = s.y / d;
      x.beginPath();
      addPath(x);
      x.fill();
    }
    x.restore();
    g.save();
    g.beginPath();
    g.rect(x0, y0, x1 - x0, y1 - y0);
    if (hole) g.rect(hole.x, hole.y, hole.w, hole.h); else addPath(g);
    g.clip("evenodd");
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "low";
    g.drawImage(c, 0, 0, (x1 - x0) / d, (y1 - y0) / d, x0, y0, x1 - x0, y1 - y0);
    g.restore();
  }

  function contactRing(g, addPath, shade, k, u) {
    g.save();
    g.beginPath();
    addPath(g);
    g.strokeStyle = rgba(shade, 0.035 + 0.05 * k);
    g.lineWidth = 3.2 * u;
    g.stroke();
    g.strokeStyle = rgba(shade, 0.05 + 0.07 * k);
    g.lineWidth = 1.4 * u;
    g.stroke();
    g.restore();
  }

  function fitText(g, text, maxW) {
    if (maxW <= 0) return "";
    if (g.measureText(text).width <= maxW) return text;
    var lo = 0, hi = text.length;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (g.measureText(text.slice(0, mid) + "…").width <= maxW) lo = mid; else hi = mid - 1;
    }
    return lo > 0 ? text.slice(0, lo).replace(/\s+$/, "") + "…" : "";
  }

  function spacedText(g, text, cx, y, spacing) {
    var widths = [], total = 0;
    for (var i = 0; i < text.length; i++) { widths.push(g.measureText(text[i]).width); total += widths[i]; }
    total += spacing * (text.length - 1);
    var x = cx - total / 2, align = g.textAlign;
    g.textAlign = "left";
    for (var j = 0; j < text.length; j++) { g.fillText(text[j], x, y); x += widths[j] + spacing; }
    g.textAlign = align;
    return total;
  }

  function kasumi(g, x, y, w, h, color, edge) {
    var bars = [[x, y, w], [x + w * 0.24, y + h * 0.86, w * 0.58], [x - w * 0.1, y - h * 0.86, w * 0.42]];
    var rgb = /rgba?\(([^)]+)\)/.exec(color), parts = rgb ? rgb[1].split(",") : ["255", "255", "255", "0.3"];
    var base = "rgba(" + parts[0] + "," + parts[1] + "," + parts[2] + ",", a = parts.length > 3 ? +parts[3] : 1;
    for (var i = 0; i < bars.length; i++) {
      var bx = bars[i][0], bw = bars[i][2];
      g.fillStyle = linear(g, bx, 0, bx + bw, 0, [0, base + "0)", 0.22, base + a + ")", 0.75, base + a + ")", 1, base + "0)"]);
      g.beginPath();
      addRoundRect(g, bx, bars[i][1], bw, h, h / 2);
      g.fill();
      if (edge) {
        g.fillStyle = linear(g, bx, 0, bx + bw, 0, [0, "rgba(255,255,255,0)", 0.3, edge, 0.7, edge, 1, "rgba(255,255,255,0)"]);
        g.fillRect(bx + h * 0.5, bars[i][1], bw - h, Math.max(0.6, h * 0.08));
      }
    }
  }

  function stars(g, W, maxY, S, count, seed, color) {
    var rand = seeded(seed), unit = Math.max(0.55, S / 1500);
    g.save();
    g.fillStyle = color || "#fff6e0";
    for (var i = 0; i < count; i++) {
      var x = rand() * W, y = Math.pow(rand(), 1.25) * maxY, r = (0.35 + rand() * rand() * 1.5) * unit;
      g.globalAlpha = 0.2 + rand() * 0.7;
      g.beginPath();
      g.arc(x, y, r, 0, TAU);
      g.fill();
      if (r > 1.25 * unit && rand() < 0.5) {
        g.globalAlpha *= 0.5;
        g.fillRect(x - r * 4, y - r * 0.18, r * 8, r * 0.36);
        g.fillRect(x - r * 0.18, y - r * 4, r * 0.36, r * 8);
      }
    }
    g.restore();
  }

  function addPetal(g, s) {
    g.moveTo(0, 0);
    g.bezierCurveTo(-0.44 * s, -0.16 * s, -0.6 * s, -0.7 * s, -0.22 * s, -0.97 * s);
    g.quadraticCurveTo(-0.1 * s, -1.02 * s, 0, -0.85 * s);
    g.quadraticCurveTo(0.1 * s, -1.02 * s, 0.22 * s, -0.97 * s);
    g.bezierCurveTo(0.6 * s, -0.7 * s, 0.44 * s, -0.16 * s, 0, 0);
    g.closePath();
  }

  function addBlossom(g, x, y, s, rot) {
    for (var k = 0; k < 5; k++) {
      g.save();
      g.translate(x, y);
      g.rotate(rot + k * TAU / 5);
      addPetal(g, s);
      g.restore();
    }
  }

  function blossom(g, x, y, s, rot, alpha, deep) {
    g.save();
    g.globalAlpha = alpha;
    g.translate(x, y);
    g.rotate(rot);
    var fill = radial(g, 0, 0, s, [0, "#fffafb", 0.28, deep ? "#fcd9e2" : "#fde6ec", 0.8, deep ? "#f19fb6" : "#f6bccb", 1, deep ? "#ea8aa6" : "#f2abbf"]);
    for (var k = 0; k < 5; k++) {
      g.save();
      g.rotate(k * TAU / 5 + Math.sin(k * 7.3 + rot) * 0.05);
      g.beginPath();
      addPetal(g, s);
      g.fillStyle = fill;
      g.fill();
      g.lineWidth = Math.max(0.4, s * 0.018);
      g.strokeStyle = "rgba(205,96,128,0.22)";
      g.stroke();
      g.beginPath();
      g.moveTo(0, -s * 0.12);
      g.lineTo(0, -s * 0.62);
      g.strokeStyle = "rgba(214,98,134,0.16)";
      g.stroke();
      g.restore();
    }
    g.fillStyle = radial(g, 0, 0, s * 0.3, [0, "#c8406a", 0.5, "rgba(214,84,124,0.75)", 1, "rgba(240,160,186,0)"]);
    g.beginPath();
    g.arc(0, 0, s * 0.3, 0, TAU);
    g.fill();
    g.strokeStyle = "rgba(176,52,92,0.75)";
    g.lineWidth = Math.max(0.35, s * 0.016);
    g.fillStyle = "#e9b949";
    for (var i = 0; i < 12; i++) {
      var a = i * TAU / 12 + 0.13, len = s * (0.3 + (i % 3) * 0.06);
      g.beginPath();
      g.moveTo(0, 0);
      g.lineTo(Math.cos(a) * len, Math.sin(a) * len);
      g.stroke();
      g.beginPath();
      g.arc(Math.cos(a) * len, Math.sin(a) * len, Math.max(0.5, s * 0.032), 0, TAU);
      g.fill();
    }
    g.restore();
  }

  function bud(g, x, y, s, rot) {
    g.save();
    g.translate(x, y);
    g.rotate(rot);
    g.beginPath();
    g.ellipse(0, -s * 0.55, s * 0.32, s * 0.55, 0, 0, TAU);
    g.fillStyle = linear(g, 0, -s * 1.1, 0, 0, [0, "#f7a9bd", 1, "#d9587e"]);
    g.fill();
    g.beginPath();
    g.moveTo(-s * 0.3, -s * 0.15);
    g.quadraticCurveTo(0, s * 0.25, s * 0.3, -s * 0.15);
    g.quadraticCurveTo(0, -s * 0.05, -s * 0.3, -s * 0.15);
    g.fillStyle = "#6e3a3a";
    g.fill();
    g.restore();
  }

  function fallingPetal(g, x, y, s, rot, tilt, alpha) {
    g.save();
    g.globalAlpha = alpha;
    g.translate(x, y);
    g.rotate(rot);
    g.scale(1, tilt);
    g.beginPath();
    addPetal(g, s);
    g.fillStyle = linear(g, 0, 0, 0, -s, [0, "#f3a3b9", 0.55, "#f9cdd8", 1, "#fde9ee"]);
    g.fill();
    g.restore();
  }

  function branch(g, points, w0, w1, color) {
    g.save();
    g.strokeStyle = color;
    g.lineCap = "round";
    g.lineJoin = "round";
    for (var i = 0; i < points.length - 1; i++) {
      g.lineWidth = w0 + (w1 - w0) * (i / Math.max(1, points.length - 2));
      g.beginPath();
      g.moveTo(points[i][0], points[i][1]);
      g.lineTo(points[i + 1][0], points[i + 1][1]);
      g.stroke();
    }
    g.restore();
  }

  function branchPoints(x0, y0, x1, y1, n, wobble, rand) {
    var pts = [], dx = x1 - x0, dy = y1 - y0, len = Math.sqrt(dx * dx + dy * dy), nx = -dy / len, ny = dx / len;
    for (var i = 0; i <= n; i++) {
      var t = i / n, off = (i === 0 || i === n) ? 0 : (rand() - 0.5) * wobble + Math.sin(t * Math.PI) * wobble * 0.6;
      pts.push([x0 + dx * t + nx * off, y0 + dy * t + ny * off]);
    }
    return pts;
  }

  function paintSakura(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(1904);
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, W, H, [0, "#f5bfcc", 0.42, "#f9dde2", 1, "#fbf1e4"]));
      glow(c, W * 0.86, H * 0.12, S * 0.7, "#fff7ea", 0.75);
      glow(c, W * 0.08, H * 0.98, S * 0.6, "#f2a6ba", 0.5);
      var bokeh = [[0.8, 0.2, 0.09], [0.12, 0.7, 0.08], [0.6, 0.95, 0.1], [0.97, 0.48, 0.07], [0.44, 0.05, 0.075], [0.3, 0.44, 0.06], [0.72, 0.62, 0.07], [0.04, 0.06, 0.06]];
      for (var b = 0; b < bokeh.length; b++) {
        var p = bokeh[b];
        soft(c, W, H, S * p[2] * 0.45, "rgba(244,160,186,0.4)", function (k) {
          k.beginPath();
          addBlossom(k, p[0] * W, p[1] * H, S * p[2], b * 1.7);
          k.fill();
        });
      }
    });
    var bark = "rgba(78,44,48,0.92)";
    var upper = branchPoints(-0.02 * W, 0.34 * H, 0.36 * W, -0.03 * H, 7, S * 0.03, rand);
    var twig = branchPoints(upper[3][0], upper[3][1], upper[3][0] + S * 0.17, upper[3][1] + S * 0.07, 3, S * 0.015, rand);
    var lower = branchPoints(1.03 * W, 0.68 * H, 0.64 * W, 1.03 * H, 6, S * 0.03, rand);
    var lowerTwig = branchPoints(lower[2][0], lower[2][1], lower[2][0] - S * 0.13, lower[2][1] - S * 0.1, 3, S * 0.012, rand);
    branch(g, upper, S * 0.016, S * 0.004, bark);
    branch(g, twig, S * 0.007, S * 0.0025, bark);
    branch(g, lower, S * 0.016, S * 0.004, bark);
    branch(g, lowerTwig, S * 0.006, S * 0.0025, bark);
    var spots = [upper[1], upper[2], upper[3], upper[4], upper[5], upper[6], twig[2], twig[3], lower[1], lower[2], lower[3], lower[4], lower[5], lowerTwig[2], lowerTwig[3]];
    for (var i = 0; i < spots.length; i++) {
      var s = S * (0.028 + rand() * 0.026);
      var ox = (rand() - 0.5) * s * 1.6, oy = (rand() - 0.5) * s * 1.6;
      if (rand() < 0.3) bud(g, spots[i][0] - ox, spots[i][1] - oy, s * 0.4, rand() * TAU);
      blossom(g, spots[i][0] + ox, spots[i][1] + oy, s, rand() * TAU, 0.97, rand() < 0.3);
      if (rand() < 0.55) blossom(g, spots[i][0] - ox * 1.2, spots[i][1] - oy * 1.2 + s * 0.4, s * 0.72, rand() * TAU, 0.95, rand() < 0.5);
    }
    for (var p = 0; p < 50; p++) {
      fallingPetal(g, rand() * W, rand() * H, S * (0.011 + rand() * 0.013), rand() * TAU, 0.45 + rand() * 0.5, 0.55 + rand() * 0.4);
    }
    grain(g, W, H, 0.35);
  }

  function seigaihaScale(c, x, y, R) {
    var bands = ["#173a62", "#21507f", "#173a62", "#21507f", "#173a62", "#2a5f92"];
    for (var k = 0; k < bands.length; k++) {
      c.beginPath();
      c.arc(x, y, R * (1 - k * 0.17), 0, TAU);
      c.fillStyle = bands[k];
      c.fill();
    }
    c.lineWidth = Math.max(0.6, R * 0.035);
    c.strokeStyle = "rgba(190,214,236,0.62)";
    for (var j = 1; j < bands.length; j++) {
      c.beginPath();
      c.arc(x, y, R * (1 - j * 0.17) + c.lineWidth * 0.2, 0, TAU);
      c.stroke();
    }
    c.lineWidth = Math.max(0.7, R * 0.05);
    c.strokeStyle = "rgba(225,236,246,0.85)";
    c.beginPath();
    c.arc(x, y, R - c.lineWidth * 0.5, 0, TAU);
    c.stroke();
  }

  function paintSeigaiha(g, W, H) {
    var S = Math.sqrt(W * H), R = Math.max(5, Math.round(S / 19));
    var t = tile("seigaiha", 2 * R, R, function (c, w, h) {
      var fine = grainScale(S);
      for (var j = -2; j <= 4; j++) {
        var y = j * R / 2, off = (j & 1) ? R : 0;
        for (var i = -1; i <= 2; i++) seigaihaScale(c, i * 2 * R + off, y, R);
      }
      grainInto(c, w, h, 0.4 * fine);
    });
    paintPattern(g, t, 0, 0, W, H, 1, W / 2, H / 2);
    smooth(g, W, H, function (c) {
      glow(c, W * 0.18, H * 0.08, S * 1.1, "#a9cdea", 0.28);
      vignette(c, W, H, "#050f22", 0.55, 0.3);
    });
  }

  function paintAsanoha(g, W, H) {
    var S = Math.sqrt(W * H);
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, W, H, [0, "#e3d9ef", 0.5, "#cdc2e3", 1, "#b4a6d2"]));
      glow(c, W * 0.25, H * 0.15, S * 0.9, "#fbf6ff", 0.55);
      vignette(c, W, H, "#4a3d78", 0.3, 0.35);
    });
    var a = Math.max(8, Math.round(S / 11)), th = Math.round(a * Math.sqrt(3));
    var t = tile("asanoha", a, th, function (c, w, h) {
      var fine = grainScale(S);
      var rows = [];
      for (var r = -2; r <= 4; r++) {
        var pts = [], off = (r & 1) ? w / 2 : 0;
        for (var i = -2; i <= 3; i++) pts.push([i * w + off, r * h / 2]);
        rows.push({ off: off, pts: pts });
      }
      c.beginPath();
      for (var k = 0; k < rows.length - 1; k++) {
        var top = rows[k].pts, bot = rows[k + 1].pts, shift = rows[k].off === 0;
        for (var n = 0; n < top.length - 1; n++) {
          var tris = shift ? [[top[n], top[n + 1], bot[n]], [bot[n], bot[n + 1], top[n + 1]]] : [[top[n], top[n + 1], bot[n + 1]], [bot[n], bot[n + 1], top[n]]];
          for (var q = 0; q < 2; q++) {
            var v = tris[q], cx = (v[0][0] + v[1][0] + v[2][0]) / 3, cy = (v[0][1] + v[1][1] + v[2][1]) / 3;
            c.moveTo(v[0][0], v[0][1]); c.lineTo(v[1][0], v[1][1]); c.lineTo(v[2][0], v[2][1]); c.closePath();
            for (var e = 0; e < 3; e++) { c.moveTo(cx, cy); c.lineTo(v[e][0], v[e][1]); }
          }
        }
      }
      c.lineWidth = Math.max(0.8, w * 0.034);
      c.lineJoin = "round";
      c.strokeStyle = "rgba(255,255,255,0.86)";
      c.stroke();
      grainInto(c, w, h, 0.3 * fine);
    });
    paintPattern(g, t, 0, 0, W, H, 1, W / 2, H / 2);
  }

  function mountainPath(g, cx, peakY, baseY, halfTop, halfBase) {
    var mh = baseY - peakY;
    g.moveTo(cx - halfBase, baseY);
    g.bezierCurveTo(cx - halfBase * 0.52, baseY - mh * 0.1, cx - halfTop - mh * 0.28, peakY + mh * 0.36, cx - halfTop, peakY);
    g.lineTo(cx - halfTop * 0.45, peakY - mh * 0.012);
    g.lineTo(cx - halfTop * 0.05, peakY + mh * 0.008);
    g.lineTo(cx + halfTop * 0.4, peakY - mh * 0.014);
    g.lineTo(cx + halfTop, peakY);
    g.bezierCurveTo(cx + halfTop + mh * 0.28, peakY + mh * 0.36, cx + halfBase * 0.52, baseY - mh * 0.1, cx + halfBase, baseY);
    g.closePath();
  }

  function hills(g, W, H, y0, amp, color, seed) {
    var rand = seeded(seed), n = 6, pts = [];
    for (var i = 0; i <= n; i++) pts.push([W * (i / n) + (rand() - 0.5) * W * 0.06, y0 + (rand() - 0.5) * amp]);
    pts[0][0] = -W * 0.05;
    pts[n][0] = W * 1.05;
    g.beginPath();
    g.moveTo(-W * 0.05, H + 2);
    g.lineTo(pts[0][0], pts[0][1]);
    for (var j = 0; j < n; j++) {
      var mx = (pts[j][0] + pts[j + 1][0]) / 2, my = (pts[j][1] + pts[j + 1][1]) / 2;
      g.quadraticCurveTo(pts[j][0], pts[j][1], mx, my);
    }
    g.lineTo(pts[n][0], pts[n][1]);
    g.lineTo(W * 1.05, H + 2);
    g.closePath();
    g.fillStyle = color;
    g.fill();
  }

  function snowLine(g, cx, snowY, mh, rand) {
    var x = cx - mh * 0.62, end = cx + mh * 0.62;
    g.lineTo(x, snowY);
    while (x < end) {
      var span = mh * (0.018 + rand() * 0.03), centre = 1 - Math.min(1, Math.abs(x - cx) / (mh * 0.62));
      var depth = mh * (0.03 + Math.pow(rand(), 1.6) * 0.16) * (0.35 + centre * 0.75);
      g.lineTo(x + span * 0.45, snowY + depth);
      g.lineTo(x + span, snowY - mh * 0.012 * rand());
      x += span;
    }
  }

  function paintFuji(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(3776);
    var sunX = W * 0.66, sunY = H * 0.47, sunR = S * 0.12;
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, 0, H, [0, "#2c2a5e", 0.28, "#5f5596", 0.5, "#a986b2", 0.66, "#e39b92", 0.8, "#f2b182", 1, "#f6cb98"]));
      glow(c, sunX, sunY, sunR * 3.6, "#ffd8b0", 0.5);
    });
    stars(g, W, H * 0.3, S, 60, 5, "#fff3e6");
    g.beginPath();
    g.arc(sunX, sunY, sunR, 0, TAU);
    g.fillStyle = linear(g, 0, sunY - sunR, 0, sunY + sunR, [0, "rgba(255,244,228,0.96)", 1, "rgba(255,206,170,0.9)"]);
    g.fill();
    kasumi(g, W * 0.06, H * 0.19, W * 0.3, S * 0.02, "rgba(255,218,190,0.2)", "rgba(255,236,214,0.3)");
    kasumi(g, W * 0.72, H * 0.29, W * 0.34, S * 0.017, "rgba(255,218,190,0.18)", "rgba(255,236,214,0.26)");
    var cx = W * 0.44, peakY = H * 0.36, baseY = H * 1.04, mh = baseY - peakY;
    var halfBase = Math.max(W * 0.64, mh * 1.85), halfTop = mh * 0.085;
    g.beginPath();
    mountainPath(g, cx, peakY, baseY, halfTop, halfBase);
    g.fillStyle = linear(g, 0, peakY, 0, baseY, [0, "#5c5190", 0.5, "#3f3772", 1, "#262150"]);
    g.fill();
    g.fillStyle = linear(g, cx - halfBase * 0.5, 0, cx + halfBase * 0.5, 0, [0, "rgba(20,14,48,0.35)", 0.5, "rgba(20,14,48,0)", 0.75, "rgba(255,190,170,0.1)", 1, "rgba(255,190,170,0.16)"]);
    g.fill();
    g.save();
    g.clip();
    var snowY = peakY + mh * 0.17;
    g.beginPath();
    g.moveTo(cx - mh, peakY - mh * 0.1);
    snowLine(g, cx, snowY, mh, rand);
    g.lineTo(cx + mh, snowY);
    g.lineTo(cx + mh, peakY - mh * 0.1);
    g.closePath();
    g.fillStyle = linear(g, 0, peakY, 0, snowY + mh * 0.15, [0, "#ffffff", 0.55, "#efe9f6", 1, "#d6cce8"]);
    g.fill();
    g.fillStyle = linear(g, cx - mh * 0.3, 0, cx + mh * 0.3, 0, [0, "rgba(70,60,130,0.3)", 0.48, "rgba(70,60,130,0.05)", 1, "rgba(255,214,196,0.16)"]);
    g.fill();
    g.restore();
    kasumi(g, W * 0.04, H * 0.68, W * 0.44, S * 0.024, "rgba(255,214,190,0.24)", "rgba(255,236,214,0.34)");
    kasumi(g, W * 0.6, H * 0.77, W * 0.38, S * 0.02, "rgba(255,214,190,0.2)", "rgba(255,236,214,0.3)");
    hills(g, W, H, H * 0.86, H * 0.06, "rgba(38,30,72,0.94)", 7);
    hills(g, W, H, H * 0.93, H * 0.05, "#1b1638", 9);
    grain(g, W, H, 0.4);
  }

  function bambooShadow(g, x, top, bottom, S, rand) {
    var w = S * 0.011, lean = (bottom - top) * 0.05;
    g.lineCap = "round";
    g.lineWidth = w;
    g.beginPath();
    g.moveTo(x, bottom);
    g.lineTo(x + lean, top);
    g.stroke();
    var nodes = 6;
    g.beginPath();
    for (var n = 1; n < nodes; n++) {
      var t = n / nodes, nx = x + lean * t, ny = bottom + (top - bottom) * t;
      addRoundRect(g, nx - w * 0.9, ny - w * 0.25, w * 1.8, w * 0.5, w * 0.25);
      var leaves = 2 + Math.floor(rand() * 4), dir = rand() < 0.5 ? -1 : 1;
      for (var l = 0; l < leaves; l++) {
        var len = S * (0.05 + rand() * 0.05), ang = (dir > 0 ? 0.15 : Math.PI - 0.15) + (rand() - 0.2) * 1.1 * dir;
        g.save();
        g.translate(nx, ny);
        g.rotate(ang);
        g.moveTo(0, 0);
        g.quadraticCurveTo(len * 0.45, -len * 0.13, len, len * 0.05);
        g.quadraticCurveTo(len * 0.45, len * 0.1, 0, 0);
        g.restore();
      }
    }
    g.fill();
  }

  function paintShoji(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(1603);
    var beam = Math.round(H * 0.05), floorY = Math.round(H * 0.66), sill = Math.max(2, Math.round(H * 0.024));
    var kick = Math.round(H * 0.07), paperTop = beam, paperBottom = floorY - sill - kick, paperH = paperBottom - paperTop;
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, "#e9d6b3");
      c.fillStyle = radial(c, W * 0.5, H * 0.32, Math.max(W, H) * 0.8, [0, "#fffaf0", 0.35, "#fbefd6", 0.7, "#f1dfbb", 1, "#e2c99c"]);
      c.fillRect(0, paperTop, W, paperH);
      c.save();
      c.beginPath();
      c.rect(0, paperTop, W, paperH);
      c.clip();
      soft(c, W, H, S * 0.007, "rgba(98,74,44,0.2)", function (k) {
        bambooShadow(k, W * 0.86, paperTop - H * 0.05, paperBottom + H * 0.02, S, rand);
        bambooShadow(k, W * 0.95, paperTop - H * 0.08, paperBottom, S, rand);
        bambooShadow(k, W * 0.04, paperTop - H * 0.05, paperBottom + H * 0.04, S, rand);
      });
      c.restore();
      c.fillStyle = linear(c, 0, floorY, 0, H, [0, "#dccd93", 0.35, "#cbbb7a", 1, "#a6985a"]);
      c.fillRect(0, floorY, W, H - floorY);
      c.fillStyle = radial(c, W / 2, floorY, Math.max(W, H) * 0.6, [0, "rgba(255,240,200,0.42)", 1, "rgba(255,240,200,0)"]);
      c.fillRect(0, floorY, W, H - floorY);
    });
    paintPattern(g, fibreTile(S), 0, paperTop, W, paperH, 0.55);
    var panels = Math.max(2, Math.round(W / (H * 0.36))), pw = W / panels;
    var stile = Math.max(2, Math.round(S * 0.011)), bar = Math.max(1, Math.round(S * 0.0038)), edge = Math.max(1, Math.round(bar * 0.35));
    var cols = 3, rows = Math.max(3, Math.round(paperH / (pw / cols * 0.82)));
    g.fillStyle = "#b48d64";
    for (var p = 0; p < panels; p++) {
      for (var k = 1; k < cols; k++) g.fillRect(Math.round(p * pw + k * pw / cols - bar / 2), paperTop, bar, paperH);
    }
    for (var r = 1; r < rows; r++) g.fillRect(0, Math.round(paperTop + r * paperH / rows - bar / 2), W, bar);
    g.fillStyle = "rgba(90,60,30,0.25)";
    for (var p2 = 0; p2 < panels; p2++) {
      for (var k2 = 1; k2 < cols; k2++) g.fillRect(Math.round(p2 * pw + k2 * pw / cols - bar / 2) + bar, paperTop, edge, paperH);
    }
    for (var r2 = 1; r2 < rows; r2++) g.fillRect(0, Math.round(paperTop + r2 * paperH / rows - bar / 2) + bar, W, edge);
    var stileFill = linear(g, 0, 0, stile, 0, [0, "#7b5638", 0.5, "#9a734d", 1, "#6f4c30"]);
    for (var s = 0; s <= panels; s++) {
      var sx = Math.round(s * pw - stile / 2);
      g.save();
      g.translate(sx, 0);
      g.fillStyle = stileFill;
      g.fillRect(0, paperTop, stile, paperH + kick);
      g.restore();
    }
    g.fillStyle = linear(g, 0, paperBottom, 0, paperBottom + kick, [0, "#8a6240", 0.2, "#a07450", 1, "#7a5435"]);
    g.fillRect(0, paperBottom, W, kick);
    g.strokeStyle = "rgba(70,44,22,0.22)";
    g.lineWidth = Math.max(1, S * 0.0015);
    g.beginPath();
    for (var gr = 0; gr < 7; gr++) {
      var gy = paperBottom + kick * (0.18 + gr * 0.11);
      g.moveTo(0, gy);
      for (var gx = W / 24; gx <= W + 1; gx += W / 24) g.lineTo(gx, gy + Math.sin(gx / W * 9 + gr * 1.7) * kick * 0.03);
    }
    g.stroke();
    g.fillStyle = "rgba(60,36,18,0.35)";
    for (var s2 = 0; s2 <= panels; s2++) g.fillRect(Math.round(s2 * pw - stile / 2), paperBottom, stile, kick);
    g.fillStyle = "rgba(60,36,18,0.4)";
    g.fillRect(0, paperBottom, W, Math.max(1, Math.round(stile * 0.4)));
    var hair = Math.max(1, Math.round(S * 0.0015));
    g.fillStyle = linear(g, 0, 0, 0, beam, [0, "#3e2a1c", 0.7, "#5a3d28", 1, "#6c4a31"]);
    g.fillRect(0, 0, W, beam);
    g.fillStyle = "rgba(255,226,180,0.18)";
    g.fillRect(0, beam - hair, W, hair);
    g.fillStyle = "rgba(40,24,12,0.45)";
    g.fillRect(0, beam, W, 2 * hair);
    g.fillStyle = linear(g, 0, floorY - sill, 0, floorY, [0, "#9a6f48", 0.4, "#86603e", 1, "#5e412a"]);
    g.fillRect(0, floorY - sill, W, sill);
    tatami(g, W, H, floorY, S);
    smooth(g, W, H, function (c) {
      glow(c, W * 0.5, H * 0.34, S * 0.95, "#fff1cf", 0.18);
      vignette(c, W, H, "#2a1a0c", 0.42, 0.35);
    });
  }

  function tatami(g, W, H, floorY, S) {
    var vpx = W / 2, vpy = H * 0.3, wallZ = 4, f = (floorY - vpy) * wallZ;
    var zNear = f / (H - vpy);
    var sy = function (z) { return vpy + f / z; };
    var sx = function (X, z) { return vpx + f * X / z; };
    var maxX = (W / 2) * wallZ / f + 1, step = 0.028;
    g.fillStyle = "rgba(255,248,214,0.09)";
    g.beginPath();
    for (var X = -maxX; X < maxX; X += step) {
      g.moveTo(sx(X, wallZ), floorY);
      g.lineTo(sx(X + step * 0.45, wallZ), floorY);
      g.lineTo(sx(X + step * 0.45, zNear), H);
      g.lineTo(sx(X, zNear), H);
      g.closePath();
    }
    g.fill();
    g.fillStyle = "rgba(96,84,36,0.09)";
    g.beginPath();
    for (var X2 = -maxX + step * 0.5; X2 < maxX; X2 += step) {
      g.moveTo(sx(X2, wallZ), floorY);
      g.lineTo(sx(X2 + step * 0.12, wallZ), floorY);
      g.lineTo(sx(X2 + step * 0.12, zNear), H);
      g.lineTo(sx(X2, zNear), H);
      g.closePath();
    }
    g.fill();
    var depth = 0.9, heri = 0.045;
    for (var k = 0; ; k++) {
      var zFar = wallZ - k * depth, zClose = Math.max(zFar - depth, zNear * 0.5);
      if (zFar <= zNear * 0.98) break;
      var off = (k % 2) * 0.9;
      g.strokeStyle = "rgba(88,74,30,0.42)";
      g.lineWidth = Math.max(1, S * 0.0016);
      g.beginPath();
      for (var j = -6; j <= 6; j++) {
        var X3 = off + j * 1.8;
        g.moveTo(sx(X3, zFar), sy(zFar));
        g.lineTo(sx(X3, zClose), sy(zClose));
      }
      g.stroke();
      var yA = sy(zFar), yB = sy(zFar - heri);
      g.fillStyle = "#2a2c38";
      g.fillRect(0, yA, W, Math.max(1, yB - yA));
      g.fillStyle = "rgba(255,255,255,0.12)";
      g.fillRect(0, yA, W, Math.max(1, (yB - yA) * 0.12));
      g.fillStyle = "rgba(0,0,0,0.18)";
      g.fillRect(0, yB, W, Math.max(1, (yB - yA) * 0.25));
    }
    g.fillStyle = linear(g, 0, floorY, 0, floorY + H * 0.03, [0, "rgba(50,30,10,0.35)", 1, "rgba(50,30,10,0)"]);
    g.fillRect(0, floorY, W, H * 0.03);
  }

  function paintWashi(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(808);
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, W, H, [0, "#f7f0e0", 1, "#efe4cd"]));
      for (var i = 0; i < 9; i++) {
        var light = i % 2 === 0;
        glow(c, rand() * W, rand() * H, S * (0.25 + rand() * 0.35), light ? "#fffaf0" : "#e3d3b2", light ? 0.5 : 0.28);
      }
      vignette(c, W, H, "#8a6a40", 0.16, 0.5);
    });
    paintPattern(g, fibreTile(S), 0, 0, W, H, 1);
  }

  function paintIchimatsu(g, W, H) {
    var S = Math.sqrt(W * H), c = Math.max(6, Math.round(S / 9));
    var t = tile("ichimatsu", 2 * c, 2 * c, function (x, w, h) {
      x.fillStyle = "#f0e9d2";
      x.fillRect(0, 0, w, h);
      x.fillStyle = "#8c9a55";
      x.fillRect(0, 0, c, c);
      x.fillRect(c, c, c, c);
      x.fillStyle = "rgba(255,255,255,0.08)";
      x.fillRect(0, 0, c, Math.max(1, c * 0.04));
      x.fillRect(c, c, c, Math.max(1, c * 0.04));
      grainInto(x, w, h, 0.35 * grainScale(S));
    });
    paintPattern(g, t, 0, 0, W, H, 1, W / 2, H / 2);
    paintPattern(g, fibreTile(S), 0, 0, W, H, 0.6);
    smooth(g, W, H, function (k) {
      glow(k, W * 0.22, H * 0.12, S * 1.0, "#fffbe8", 0.3);
      vignette(k, W, H, "#2f3514", 0.42, 0.32);
    });
  }

  function goldCloud(g, hx, cy, len, h, dir, rand) {
    var n = Math.max(3, Math.round(len / (h * 0.62))), top = [], low = [];
    for (var i = 0; i < n; i++) {
      var t = i / (n - 1), r = h * (0.6 - 0.42 * t) * (0.9 + rand() * 0.2);
      top.push([hx - dir * len * t, cy - h * 0.06 - r * 0.32 - (i === 0 ? h * 0.08 : 0), r]);
    }
    for (var j = 0; j < n - 1; j++) {
      var tt = (j + 0.5) / (n - 1);
      low.push([hx - dir * len * tt, cy + h * (0.2 - 0.14 * tt), h * (0.27 - 0.17 * tt)]);
    }
    var tail = hx - dir * len;
    var shape = function () {
      g.beginPath();
      g.moveTo(hx + dir * h * 0.2, cy - h * 0.3);
      if (dir > 0) {
        g.lineTo(hx + dir * h * 0.2, cy + h * 0.3);
        g.lineTo(tail, cy + h * 0.06);
        g.lineTo(tail, cy - h * 0.06);
      } else {
        g.lineTo(tail, cy - h * 0.06);
        g.lineTo(tail, cy + h * 0.06);
        g.lineTo(hx + dir * h * 0.2, cy + h * 0.3);
      }
      g.closePath();
      var all = top.concat(low);
      for (var k = 0; k < all.length; k++) {
        g.moveTo(all[k][0] + all[k][2], all[k][1]);
        g.arc(all[k][0], all[k][1], all[k][2], 0, TAU);
      }
      g.moveTo(tail + h * 0.07, cy);
      g.arc(tail, cy, h * 0.07, 0, TAU);
    };
    var line = Math.max(0.8, h * 0.05);
    shape();
    g.lineJoin = "round";
    g.lineWidth = line * 2;
    g.strokeStyle = "#8b5a1a";
    g.stroke();
    g.fillStyle = linear(g, 0, cy - h * 0.75, 0, cy + h * 0.45, [0, "#fdeeb8", 0.4, "#f0cd70", 1, "#c8943a"]);
    g.fill();
    g.strokeStyle = "rgba(146,96,28,0.6)";
    g.lineCap = "round";
    g.lineWidth = line * 0.75;
    for (var m = 0; m < Math.min(3, top.length); m++) {
      var q = top[m];
      g.beginPath();
      g.arc(q[0], q[1] + q[2] * 0.12, q[2] * 0.62, Math.PI * 1.08, Math.PI * 1.9);
      g.stroke();
      g.beginPath();
      g.arc(q[0], q[1] + q[2] * 0.12, q[2] * 0.32, Math.PI * 1.1, Math.PI * 1.85);
      g.stroke();
    }
    g.beginPath();
    g.moveTo(tail + dir * h * 0.6, cy + h * 0.02);
    g.lineTo(hx - dir * h * 0.4, cy + h * 0.02);
    g.stroke();
    g.fillStyle = "rgba(255,250,228,0.4)";
    for (var p = 0; p < top.length; p++) {
      g.beginPath();
      g.ellipse(top[p][0] - top[p][2] * 0.2, top[p][1] - top[p][2] * 0.58, top[p][2] * 0.32, top[p][2] * 0.1, -0.25, 0, TAU);
      g.fill();
    }
  }

  var KUMO = [[0.34, 0.075, 0.46, 0.105, 1], [0.78, 0.2, 0.36, 0.09, -1], [0.13, 0.46, 0.32, 0.1, 1], [0.87, 0.6, 0.32, 0.095, -1], [0.62, 0.93, 0.48, 0.11, -1], [0.2, 0.83, 0.3, 0.09, 1], [0.55, 0.42, 0.22, 0.075, 1]];

  function paintKumo(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(888), m = Math.min(W, H);
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, W, H, [0, "#d8452f", 0.55, "#c3352a", 1, "#a82a22"]));
      glow(c, W * 0.3, H * 0.25, S * 0.9, "#f0704a", 0.35);
      vignette(c, W, H, "#4a0c08", 0.4, 0.4);
      soft(c, W, H, m * 0.03, "rgba(84,12,6,0.38)", function (k) {
        k.beginPath();
        for (var i = 0; i < KUMO.length; i++) {
          var b = KUMO[i], hx = b[0] * W, cy = b[1] * H + m * 0.014, len = b[2] * W, h = b[3] * m, x0 = Math.min(hx, hx - b[4] * len);
          addRoundRect(k, x0 - h * 0.2, cy - h * 0.45, len + h * 0.6, h * 0.8, h * 0.4);
        }
        k.fill();
      });
    });
    g.save();
    for (var d = 0; d < 300; d++) {
      var dx = rand() * W, dy = rand() * H, ds = S * (0.002 + rand() * rand() * 0.008);
      g.globalAlpha = 0.25 + rand() * 0.5;
      g.fillStyle = rand() < 0.5 ? "#f6d77c" : "#e2b54f";
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.translate(dx, dy);
      g.rotate(rand() * TAU);
      g.fillRect(-ds / 2, -ds / 2, ds, ds);
    }
    g.restore();
    for (var i = 0; i < KUMO.length; i++) {
      var b = KUMO[i];
      goldCloud(g, b[0] * W, b[1] * H, b[2] * W, b[3] * m, b[4], rand);
    }
    grain(g, W, H, 0.35);
  }

  function paintShippo(g, W, H) {
    var S = Math.sqrt(W * H), r = Math.max(6, Math.round(S / 15));
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, W, H, [0, "#9ccbb7", 0.5, "#80b39d", 1, "#5f9580"]));
      glow(c, W * 0.2, H * 0.1, S, "#f4fff8", 0.3);
      vignette(c, W, H, "#183a2e", 0.4, 0.32);
    });
    var t = tile("shippo", 2 * r, 2 * r, function (c, w, h) {
      var centers = [];
      for (var i = -1; i <= 2; i++) for (var j = -1; j <= 2; j++) { centers.push([i * 2 * r, j * 2 * r]); centers.push([i * 2 * r + r, j * 2 * r + r]); }
      c.fillStyle = "rgba(255,255,248,0.075)";
      for (var k = 0; k < centers.length; k++) { c.beginPath(); c.arc(centers[k][0], centers[k][1], r, 0, TAU); c.fill(); }
      c.beginPath();
      for (var n = 0; n < centers.length; n++) { c.moveTo(centers[n][0] + r, centers[n][1]); c.arc(centers[n][0], centers[n][1], r, 0, TAU); }
      c.lineWidth = Math.max(0.8, r * 0.05);
      c.strokeStyle = "rgba(250,247,232,0.8)";
      c.stroke();
      c.fillStyle = "#e5c66f";
      for (var q = 0; q < centers.length; q++) {
        var x = centers[q][0], y = centers[q][1], d = r * 0.09;
        c.beginPath();
        c.moveTo(x, y - d * 1.6); c.lineTo(x + d, y); c.lineTo(x, y + d * 1.6); c.lineTo(x - d, y); c.closePath();
        c.moveTo(x - d * 1.6, y); c.lineTo(x, y - d); c.lineTo(x + d * 1.6, y); c.lineTo(x, y + d); c.closePath();
        c.fill();
      }
      grainInto(c, w, h, 0.3 * grainScale(S));
    });
    paintPattern(g, t, 0, 0, W, H, 1, W / 2, H / 2);
  }

  function lantern(g, x, top, s, label) {
    var h = s * 1.32, cy = top + h / 2;
    g.save();
    g.beginPath();
    g.ellipse(x, cy, s * 0.5, h * 0.5, 0, 0, TAU);
    g.fillStyle = radial(g, x - s * 0.08, cy - h * 0.06, s * 0.62, [0, "#fff3c8", 0.3, "#ffc46a", 0.68, "#ee6a30", 1, "#b83a1f"]);
    g.fill();
    g.save();
    g.clip();
    g.strokeStyle = "rgba(120,36,12,0.28)";
    g.lineWidth = Math.max(0.5, s * 0.014);
    g.beginPath();
    for (var k = 1; k < 9; k++) {
      var yy = top + h * k / 9;
      g.moveTo(x - s * 0.6, yy);
      g.quadraticCurveTo(x, yy + h * 0.035, x + s * 0.6, yy);
    }
    g.stroke();
    if (label && s > 18) {
      g.fillStyle = "rgba(70,14,6,0.72)";
      g.font = "700 " + Math.round(s * 0.42) + "px " + FONT_SERIF;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(label, x, cy + s * 0.02);
    }
    g.restore();
    g.fillStyle = "#1b120e";
    roundRect(g, x - s * 0.27, top - h * 0.03, s * 0.54, h * 0.1, s * 0.04);
    g.fill();
    roundRect(g, x - s * 0.27, top + h * 0.93, s * 0.54, h * 0.1, s * 0.04);
    g.fill();
    g.fillRect(x - s * 0.02, top + h * 1.02, s * 0.04, h * 0.12);
    g.restore();
  }

  function stringPoint(a, b, c, t) {
    var u = 1 - t;
    return [u * u * a[0] + 2 * u * t * b[0] + t * t * c[0], u * u * a[1] + 2 * u * t * b[1] + t * t * c[1]];
  }

  function lanternRow(W, y0, sag, y1, count, size, alpha, labels) {
    var a = [-0.04 * W, y0], b = [0.5 * W, sag], c = [1.04 * W, y1], items = [];
    for (var i = 0; i < count; i++) {
      var p = stringPoint(a, b, c, (i + 0.5) / count);
      items.push({ x: p[0], y: p[1], top: p[1] + size * 0.35, label: labels ? labels[i % labels.length] : "" });
    }
    return { a: a, b: b, c: c, size: size, alpha: alpha, items: items };
  }

  function drawLanternRow(g, row, S) {
    g.save();
    g.globalAlpha = row.alpha;
    g.strokeStyle = "rgba(20,14,24,0.75)";
    g.lineWidth = Math.max(1, S * 0.0018);
    g.beginPath();
    g.moveTo(row.a[0], row.a[1]);
    g.quadraticCurveTo(row.b[0], row.b[1], row.c[0], row.c[1]);
    for (var i = 0; i < row.items.length; i++) {
      g.moveTo(row.items[i].x, row.items[i].y);
      g.lineTo(row.items[i].x, row.items[i].top);
    }
    g.stroke();
    for (var j = 0; j < row.items.length; j++) lantern(g, row.items[j].x, row.items[j].top, row.size, row.items[j].label);
    g.restore();
  }

  function pagoda(g, cx, baseY, h, color) {
    var tiers = 5, tierH = h * 0.135, y = baseY - h * 0.05;
    g.fillStyle = color;
    g.fillRect(cx - h * 0.13, baseY - h * 0.05, h * 0.26, h * 0.05 + 2);
    for (var i = 0; i < tiers; i++) {
      var ew = h * (0.2 - i * 0.022), bw = ew * 0.56, ey = y - tierH * 0.72;
      g.fillRect(cx - bw / 2, y - tierH, bw, tierH);
      g.beginPath();
      g.moveTo(cx - ew, ey - tierH * 0.12);
      g.quadraticCurveTo(cx - ew * 0.7, ey + tierH * 0.1, cx - ew * 0.35, ey + tierH * 0.05);
      g.lineTo(cx + ew * 0.35, ey + tierH * 0.05);
      g.quadraticCurveTo(cx + ew * 0.7, ey + tierH * 0.1, cx + ew, ey - tierH * 0.12);
      g.lineTo(cx + ew * 0.45, ey - tierH * 0.32);
      g.lineTo(cx - ew * 0.45, ey - tierH * 0.32);
      g.closePath();
      g.fill();
      y -= tierH;
    }
    g.fillRect(cx - h * 0.006, y - h * 0.24, h * 0.012, h * 0.24);
    for (var r = 0; r < 7; r++) g.fillRect(cx - h * 0.02, y - h * 0.06 - r * h * 0.022, h * 0.04, h * 0.007);
  }

  function town(g, W, H, top, rand) {
    var x = -W * 0.02, lights = [];
    g.fillStyle = "#0b0c22";
    g.beginPath();
    while (x < W * 1.02) {
      var bw = W * (0.04 + rand() * 0.06), bh = (H - top) * (0.35 + rand() * 0.5), by = H - bh;
      g.rect(x, by, bw, bh + 2);
      g.moveTo(x - bw * 0.12, by + bh * 0.02);
      g.quadraticCurveTo(x + bw * 0.2, by - bh * 0.05, x + bw * 0.3, by - bh * 0.22);
      g.lineTo(x + bw * 0.7, by - bh * 0.22);
      g.quadraticCurveTo(x + bw * 0.8, by - bh * 0.05, x + bw * 1.12, by + bh * 0.02);
      g.closePath();
      var wins = Math.floor(rand() * 3);
      for (var k = 0; k < wins; k++) lights.push([x + bw * (0.15 + rand() * 0.6), by + bh * (0.25 + rand() * 0.4), bw * 0.14, bh * 0.16]);
      x += bw * (0.9 + rand() * 0.25);
    }
    g.fill();
    return lights;
  }

  function paintYoru(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(1192);
    var mx = W * 0.14, my = H * 0.2, mr = S * 0.04;
    var rows = [
      lanternRow(W, H * 0.1, H * 0.36, H * 0.06, 9, S * 0.032, 0.78, null),
      lanternRow(W, -H * 0.02, H * 0.3, 0, 7, S * 0.05, 1, null)
    ];
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, 0, H, [0, "#080c26", 0.5, "#141c45", 0.82, "#252858", 1, "#33305f"]));
      glow(c, W * 0.5, H * 1.05, S * 0.8, "#6b4a8a", 0.35);
      glow(c, mx, my, mr * 5, "#c9d4ff", 0.18);
      vignette(c, W, H, "#02030c", 0.5, 0.4);
    });
    stars(g, W, H * 0.85, S, 190, 5, "#fff4dc");
    g.save();
    g.beginPath();
    g.rect(mx - mr * 2, my - mr * 2, mr * 4, mr * 4);
    g.arc(mx + mr * 0.42, my - mr * 0.22, mr * 0.9, 0, TAU);
    g.clip("evenodd");
    g.beginPath();
    g.arc(mx, my, mr, 0, TAU);
    g.fillStyle = "#f7efd6";
    g.fill();
    g.restore();
    pagoda(g, W * 0.86, H * 0.95, H * 0.46, "#0d0f28");
    var lights = town(g, W, H, H * 0.86, rand);
    smooth(g, W, H, function (c) {
      for (var i = 0; i < lights.length; i++) glow(c, lights[i][0] + lights[i][2] / 2, lights[i][1] + lights[i][3] / 2, S * 0.03, "#ffb35c", 0.28);
      for (var r = 0; r < rows.length; r++) {
        for (var j = 0; j < rows[r].items.length; j++) {
          var it = rows[r].items[j];
          glow(c, it.x, it.top + rows[r].size * 0.66, rows[r].size * 2.8, "#ff8a3c", 0.34 * rows[r].alpha);
        }
      }
    }, "lighter");
    g.fillStyle = "rgba(255,206,130,0.85)";
    for (var l = 0; l < lights.length; l++) g.fillRect(lights[l][0], lights[l][1], lights[l][2], lights[l][3]);
    drawLanternRow(g, rows[0], S);
    drawLanternRow(g, rows[1], S);
    grain(g, W, H, 0.35);
  }

  function waveEdge(g, x0, base, A, L, W) {
    var c = A * 0.34, x = x0, end = Math.PI * 0.62;
    g.moveTo(x, base);
    while (x < W + L) {
      g.bezierCurveTo(x + L * 0.28, base, x + L * 0.42, base - A, x + L * 0.6, base - A);
      g.arc(x + L * 0.6, base - A + c, c, -Math.PI / 2, end, false);
      var ex = x + L * 0.6 + c * Math.cos(end), ey = base - A + c + c * Math.sin(end);
      g.bezierCurveTo(ex + L * 0.06, ey + A * 0.3, x + L * 0.8, base, x + L, base);
      x += L;
    }
  }

  function waterLines(g, x0, base, A, L, W) {
    var x = x0;
    while (x < W + L) {
      g.moveTo(x + L * 0.06, base);
      g.bezierCurveTo(x + L * 0.3, base, x + L * 0.42, base - A * 0.9, x + L * 0.56, base - A * 0.98);
      x += L;
    }
  }

  function paintNami(g, W, H) {
    var S = Math.sqrt(W * H), rand = seeded(1831);
    var palette = ["#a7c9d8", "#7ba9c3", "#5287ad", "#346891", "#22497a", "#17325b", "#11264a"];
    var top = H * 0.26, span = H - top, A0 = Math.min(span / 7 * 0.82, S * 0.072), bands = Math.max(4, Math.round(span / (A0 / 0.8)));
    var colors = [];
    for (var ci = 0; ci < bands; ci++) {
      var pos = ci / (bands - 1) * (palette.length - 1), lo = Math.floor(pos), hi = Math.min(palette.length - 1, lo + 1);
      colors.push(mix(palette[lo], palette[hi], pos - lo));
    }
    smooth(g, W, H, function (c) {
      fillAll(c, W, H, linear(c, 0, 0, 0, H * 0.45, [0, "#f4ebd7", 1, "#eadcbf"]));
      glow(c, W * 0.8, H * 0.13, S * 0.22, "#f7c9a0", 0.35);
    });
    paintPattern(g, fibreTile(S), 0, 0, W, Math.ceil(top + span / bands), 0.5);
    var sunX = W * 0.8, sunY = H * 0.13, sunR = S * 0.065;
    g.beginPath();
    g.arc(sunX, sunY, sunR, 0, TAU);
    g.fillStyle = "#cf3e2a";
    g.fill();
    kasumi(g, W * 0.08, H * 0.1, W * 0.3, S * 0.02, "rgba(201,158,88,0.3)", null);
    for (var k = 0; k < bands; k++) {
      var base = top + span * (k + 0.35) / bands, A = span / bands * (0.72 + 0.35 * k / bands), L = A * 3.7;
      var x0 = -L * (0.25 + (k % 2) * 0.5 + rand() * 0.2);
      var bottom = k === bands - 1 ? H + 2 : top + span * (k + 1.35) / bands + A * 0.1;
      g.beginPath();
      waveEdge(g, x0, base, A, L, W);
      g.lineTo(W + L * 2, bottom);
      g.lineTo(x0, bottom);
      g.closePath();
      g.fillStyle = linear(g, 0, base - A, 0, base + A * 2, [0, colors[k], 1, mix(colorHex(colors[k]), "#0a1430", 0.35)]);
      g.fill();
      g.strokeStyle = "rgba(244,240,226,0.32)";
      g.lineWidth = Math.max(0.6, A * 0.028);
      g.lineCap = "round";
      g.beginPath();
      for (var w = 1; w <= 3; w++) waterLines(g, x0 - L * 0.02 * w, base + A * 0.26 * w, A * (1 - 0.2 * w), L, W);
      g.stroke();
      g.beginPath();
      waveEdge(g, x0, base, A, L, W);
      g.strokeStyle = "#f6f2e6";
      g.lineWidth = Math.max(0.8, A * 0.065);
      g.lineJoin = "round";
      g.stroke();
      g.beginPath();
      for (var cx = x0; cx < W + L; cx += L) {
        var px = cx + L * 0.6, py = base - A + A * 0.34;
        g.moveTo(px, py - A * 0.17);
        g.arc(px, py, A * 0.17, -Math.PI * 0.5, Math.PI * 0.55, false);
      }
      g.stroke();
      g.fillStyle = "#f6f2e6";
      g.beginPath();
      for (var cx2 = x0; cx2 < W + L; cx2 += L) {
        for (var d = 0; d < 5; d++) {
          var dx = cx2 + L * 0.6 + A * (0.15 + rand() * 0.45), dy = base - A * (0.21 + rand() * 0.45), dr = A * (0.025 + rand() * 0.03);
          g.moveTo(dx + dr, dy);
          g.arc(dx, dy, dr, 0, TAU);
        }
      }
      g.fill();
    }
    smooth(g, W, H, function (c) { vignette(c, W, H, "#0a1430", 0.35, 0.45); });
  }

  var KAMI = { paper: "#e6e3dd", sky: "#9ec5d4", skyLight: "#b7d3dd", navy: "#2f3a52", navySoft: "#3a4762", ink: "#27304a", sun: "#e0452f", fuji: "#a9cbd8", fujiShade: "#97bccb", snow: "#f8f6f0", gold: "#d9b46e", sand: "#ecc78c", cream: "#f1d29a" };

  function addBlob(g, cx, cy, rx, ry, seed, n) {
    var rand = seeded(seed), pts = [];
    for (var i = 0; i < n; i++) {
      var a = i / n * TAU + 0.3, k = 0.8 + rand() * 0.28;
      pts.push([cx + Math.cos(a) * rx * k, cy + Math.sin(a) * ry * k]);
    }
    var last = pts[n - 1];
    g.moveTo((last[0] + pts[0][0]) / 2, (last[1] + pts[0][1]) / 2);
    for (var j = 0; j < n; j++) {
      var p = pts[j], q = pts[(j + 1) % n];
      g.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2);
    }
    g.closePath();
  }

  function flatFlower(g, x, y, r, color, centre, rot) {
    g.beginPath();
    for (var i = 0; i < 5; i++) {
      var a = rot + i * TAU / 5, px = x + Math.cos(a) * r * 0.52, py = y + Math.sin(a) * r * 0.52;
      g.moveTo(px + r * 0.46, py);
      g.arc(px, py, r * 0.46, 0, TAU);
    }
    g.fillStyle = color;
    g.fill();
    g.beginPath();
    g.arc(x, y, r * 0.24, 0, TAU);
    g.fillStyle = centre;
    g.fill();
  }

  function flatSakura(g, x, y, r, rot) {
    g.beginPath();
    addBlossom(g, x, y, r, rot);
    g.fillStyle = KAMI.sun;
    g.fill();
    g.save();
    g.translate(x, y);
    g.rotate(rot);
    g.strokeStyle = KAMI.cream;
    g.fillStyle = KAMI.cream;
    g.lineWidth = Math.max(0.5, r * 0.05);
    g.lineCap = "round";
    g.beginPath();
    for (var i = 0; i < 5; i++) {
      var a = i * TAU / 5 + TAU / 10;
      g.moveTo(0, 0);
      g.lineTo(Math.cos(a) * r * 0.42, Math.sin(a) * r * 0.42);
    }
    g.stroke();
    g.beginPath();
    for (var j = 0; j < 5; j++) {
      var b = j * TAU / 5 + TAU / 10;
      g.moveTo(Math.cos(b) * r * 0.42 + r * 0.07, Math.sin(b) * r * 0.42);
      g.arc(Math.cos(b) * r * 0.42, Math.sin(b) * r * 0.42, r * 0.07, 0, TAU);
    }
    g.moveTo(r * 0.16, 0);
    g.arc(0, 0, r * 0.16, 0, TAU);
    g.fill();
    g.restore();
  }

  function scaleTile(key, R, fill, line) {
    return tile(key, 2 * R, R, function (c) {
      for (var j = -2; j <= 4; j++) {
        var y = j * R / 2, off = (j & 1) ? R : 0;
        for (var i = -1; i <= 2; i++) {
          var x = i * 2 * R + off;
          c.beginPath();
          c.arc(x, y, R, 0, TAU);
          c.fillStyle = fill;
          c.fill();
          c.lineWidth = Math.max(0.6, R * 0.07);
          c.strokeStyle = line;
          c.beginPath();
          for (var k = 0; k < 4; k++) {
            var rr = R * (0.94 - k * 0.24);
            c.moveTo(x + rr, y);
            c.arc(x, y, rr, 0, TAU);
          }
          c.stroke();
        }
      }
    });
  }

  function speckTile(S) {
    var T = clamp(Math.round(S * 0.25 / 16) * 16, 64, 768);
    return tile("speck" + (T > 64 ? "" : Math.round(S / 6)), T, T, function (c, w) {
      var rand = seeded(77), k = Math.max(0.55, w / 300), fill = Math.min(1, S * 0.25 / w), count = Math.round(1100 * fill * fill), fade = 0.35 + 0.65 * grainScale(S);
      for (var i = 0; i < count; i++) {
        var light = rand() < 0.5;
        c.fillStyle = light ? "rgba(255,255,255," + ((0.45 + rand() * 0.45) * fade).toFixed(3) + ")" : "rgba(39,48,74," + ((0.28 + rand() * 0.4) * fade).toFixed(3) + ")";
        c.beginPath();
        c.ellipse(rand() * w, rand() * w, (0.35 + rand() * rand() * 1.1) * k, (0.3 + rand() * 0.6) * k, rand() * TAU, 0, TAU);
        c.fill();
      }
    });
  }

  function torii(g, cx, baseY, h, color) {
    var w = h * 1.05, pillar = h * 0.11, beamH = h * 0.13;
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(cx - w * 0.33 - pillar / 2, baseY);
    g.lineTo(cx - w * 0.3 - pillar / 2, baseY - h * 0.86);
    g.lineTo(cx - w * 0.3 + pillar / 2, baseY - h * 0.86);
    g.lineTo(cx - w * 0.33 + pillar / 2, baseY);
    g.closePath();
    g.moveTo(cx + w * 0.33 + pillar / 2, baseY);
    g.lineTo(cx + w * 0.3 + pillar / 2, baseY - h * 0.86);
    g.lineTo(cx + w * 0.3 - pillar / 2, baseY - h * 0.86);
    g.lineTo(cx + w * 0.33 - pillar / 2, baseY);
    g.closePath();
    g.rect(cx - w * 0.42, baseY - h * 0.66, w * 0.84, beamH * 0.75);
    g.rect(cx - pillar * 0.35, baseY - h * 0.86, pillar * 0.7, h * 0.22);
    g.rect(cx - w * 0.44, baseY - h * 0.88, w * 0.88, beamH * 0.7);
    g.moveTo(cx - w * 0.56, baseY - h * 1.02);
    g.quadraticCurveTo(cx, baseY - h * 0.9, cx + w * 0.56, baseY - h * 1.02);
    g.lineTo(cx + w * 0.5, baseY - h * 0.9);
    g.quadraticCurveTo(cx, baseY - h * 0.8, cx - w * 0.5, baseY - h * 0.9);
    g.closePath();
    g.fill();
  }

  function cloudSwirl(g, x, y, w, h, dir) {
    var r = h * 0.5;
    g.beginPath();
    addRoundRect(g, x, y - r, w, h, r);
    g.moveTo(x + w * 0.42 + h * 0.62, y - r);
    g.arc(x + w * 0.42, y - r, h * 0.62, 0, TAU);
    g.moveTo(x + w * 0.66 + h * 0.48, y - r * 0.6);
    g.arc(x + w * 0.66, y - r * 0.6, h * 0.48, 0, TAU);
    var ex = dir > 0 ? x + w : x;
    g.moveTo(ex + h * 0.62, y - r * 0.2);
    g.arc(ex, y - r * 0.2, h * 0.62, 0, TAU);
    g.fillStyle = KAMI.cream;
    g.fill();
    g.strokeStyle = KAMI.gold;
    g.lineWidth = Math.max(0.6, h * 0.09);
    g.lineCap = "round";
    g.beginPath();
    var turns = 1.6, steps = 28;
    for (var i = 0; i <= steps; i++) {
      var t = i / steps, a = -Math.PI / 2 + dir * t * turns * TAU, rr = h * 0.48 * (1 - t * 0.85);
      var px = ex + Math.cos(a) * rr * dir, py = y - r * 0.2 + Math.sin(a) * rr;
      if (i === 0) g.moveTo(px, py); else g.lineTo(px, py);
    }
    g.moveTo(x + w * 0.42 - h * 0.35, y - r - h * 0.05);
    g.arc(x + w * 0.42, y - r, h * 0.35, Math.PI * 1.05, Math.PI * 1.8);
    g.stroke();
  }

  function paintKamifuji(g, W, H) {
    var S = Math.sqrt(W * H), m = Math.min(W, H), rand = seeded(2205);
    fillAll(g, W, H, KAMI.paper);
    g.beginPath();
    addBlob(g, W * 0.16, H * 0.2, W * 0.24, H * 0.2, 3, 9);
    addBlob(g, W * 0.95, H * 0.5, W * 0.16, H * 0.16, 8, 8);
    g.fillStyle = KAMI.skyLight;
    g.fill();
    g.beginPath();
    addBlob(g, W * 0.38, H * 0.1, W * 0.14, H * 0.1, 5, 8);
    addBlob(g, W * 0.06, H * 0.48, W * 0.1, H * 0.09, 6, 7);
    g.fillStyle = KAMI.sky;
    g.fill();
    var sunX = W * 0.56, sunY = H * 0.215, sunR = m * 0.215;
    g.beginPath();
    g.arc(sunX, sunY, sunR, 0, TAU);
    g.fillStyle = KAMI.sun;
    g.fill();
    g.beginPath();
    var p0 = [W * 0.67, -m * 0.02], ctl = [W * 0.8, H * 0.27], p1 = [W * 1.02, H * 0.44];
    g.moveTo(p0[0], p0[1]);
    g.lineTo(W + 2, -2);
    g.lineTo(p1[0], p1[1]);
    g.quadraticCurveTo(ctl[0], ctl[1], p0[0], p0[1]);
    g.closePath();
    var bumps = [];
    for (var b = 0; b <= 6; b++) {
      var t = b / 6, q = stringPoint(p0, ctl, p1, t), br = m * (0.034 + 0.026 * Math.sin(t * Math.PI) + rand() * 0.012);
      bumps.push([q[0] - br * 0.25, q[1] + br * 0.1, br]);
      g.moveTo(q[0] - br * 0.25 + br, q[1] + br * 0.1);
      g.arc(q[0] - br * 0.25, q[1] + br * 0.1, br, 0, TAU);
    }
    g.fillStyle = KAMI.navy;
    g.fill();
    g.strokeStyle = KAMI.navySoft;
    g.lineWidth = Math.max(0.6, m * 0.006);
    g.beginPath();
    for (var c = 1; c < bumps.length - 1; c++) {
      var bb = bumps[c];
      g.moveTo(bb[0] - bb[2] * 0.55, bb[1]);
      g.arc(bb[0], bb[1], bb[2] * 0.55, Math.PI, Math.PI * 0.35, true);
    }
    g.stroke();
    var clusters = [[0.86, 0.08, 5], [0.95, 0.26, 4], [0.76, 0.04, 3]];
    for (var k = 0; k < clusters.length; k++) {
      for (var f = 0; f < clusters[k][2]; f++) {
        var fr = m * (0.018 + rand() * 0.02);
        flatFlower(g, clusters[k][0] * W + (rand() - 0.5) * m * 0.12, clusters[k][1] * H + (rand() - 0.5) * m * 0.1, fr, f % 2 ? KAMI.sand : KAMI.sky, KAMI.navy, rand() * TAU);
      }
    }
    var fx = W * 0.5, peakY = H * 0.24, baseY = H * 0.68, mh = baseY - peakY, half = Math.max(W * 0.4, mh * 1.6), top = mh * 0.075;
    g.beginPath();
    g.moveTo(fx - half, baseY);
    g.quadraticCurveTo(fx - top - mh * 0.45, peakY + mh * 0.5, fx - top, peakY);
    g.lineTo(fx + top, peakY);
    g.quadraticCurveTo(fx + top + mh * 0.45, peakY + mh * 0.5, fx + half, baseY);
    g.closePath();
    g.fillStyle = KAMI.fuji;
    g.fill();
    g.save();
    g.clip();
    g.beginPath();
    g.moveTo(fx + top * 0.2, peakY);
    g.lineTo(fx + half, baseY + 2);
    g.lineTo(fx + mh * 0.3, baseY + 2);
    g.closePath();
    g.fillStyle = KAMI.fujiShade;
    g.fill();
    g.beginPath();
    g.moveTo(fx - mh, peakY - 2);
    var snowY = peakY + mh * 0.2, sx = fx - mh * 0.6;
    g.lineTo(sx, snowY);
    var drips = [0.05, 0.12, 0.04, 0.16, 0.06, 0.1, 0.03, 0.13, 0.05];
    var span = mh * 1.2 / drips.length;
    for (var d = 0; d < drips.length; d++) {
      g.quadraticCurveTo(sx + span * 0.5, snowY + mh * drips[d] * 1.6, sx + span, snowY);
      sx += span;
    }
    g.lineTo(fx + mh, peakY - 2);
    g.closePath();
    g.fillStyle = KAMI.snow;
    g.fill();
    g.restore();
    cloudSwirl(g, W * 0.3, H * 0.47, W * 0.2, m * 0.055, -1);
    cloudSwirl(g, W * 0.7, H * 0.33, W * 0.12, m * 0.045, 1);
    g.strokeStyle = KAMI.navy;
    g.lineCap = "round";
    var birds = [[0.24, 0.15, 1], [0.29, 0.2, 0.8], [0.33, 0.13, 0.65]];
    for (var bi = 0; bi < birds.length; bi++) {
      var bs = m * 0.022 * birds[bi][2], bx = birds[bi][0] * W, by = birds[bi][1] * H;
      g.lineWidth = Math.max(0.6, bs * 0.2);
      g.beginPath();
      g.moveTo(bx - bs, by - bs * 0.1);
      g.quadraticCurveTo(bx - bs * 0.45, by - bs * 0.55, bx, by);
      g.quadraticCurveTo(bx + bs * 0.45, by - bs * 0.55, bx + bs, by - bs * 0.1);
      g.stroke();
    }
    var bandTop = H * 0.7;
    var hill = function (y0, amp, color, seed) {
      var hr = seeded(seed), n = 5, pts = [];
      for (var i = 0; i <= n; i++) pts.push([W * (i / n) + (hr() - 0.5) * W * 0.08, y0 + (hr() - 0.5) * amp]);
      pts[0][0] = -W * 0.05;
      pts[n][0] = W * 1.05;
      g.beginPath();
      g.moveTo(-W * 0.05, bandTop + m * 0.05);
      g.lineTo(pts[0][0], pts[0][1]);
      for (var j = 0; j < n; j++) g.quadraticCurveTo(pts[j][0], pts[j][1], (pts[j][0] + pts[j + 1][0]) / 2, (pts[j][1] + pts[j + 1][1]) / 2);
      g.lineTo(pts[n][0], pts[n][1]);
      g.lineTo(W * 1.05, bandTop + m * 0.05);
      g.closePath();
      g.fillStyle = color;
      g.fill();
    };
    hill(H * 0.6, H * 0.08, KAMI.navySoft, 31);
    hill(H * 0.655, H * 0.06, KAMI.navy, 37);
    var R = Math.max(3, Math.round(m * 0.034));
    g.save();
    g.beginPath();
    g.moveTo(0, H + 2);
    g.lineTo(0, bandTop);
    for (var wx = 0; wx <= W; wx += W / 8) g.quadraticCurveTo(wx + W / 16, bandTop - m * 0.012, wx + W / 8, bandTop);
    g.lineTo(W, H + 2);
    g.closePath();
    g.translate(Math.round(W / 2), Math.round(bandTop));
    g.fillStyle = g.createPattern(scaleTile("kami-sky", R, KAMI.sky, "#e2eff3"), "repeat");
    g.fill();
    g.restore();
    g.save();
    g.beginPath();
    g.moveTo(-2, H * 0.92);
    g.bezierCurveTo(W * 0.3, H * 0.95, W * 0.55, H * 0.74, W + 2, H * 0.78);
    g.lineTo(W + 2, H + 2);
    g.lineTo(-2, H + 2);
    g.closePath();
    g.translate(Math.round(W / 2), Math.round(bandTop));
    g.fillStyle = g.createPattern(scaleTile("kami-navy", R, KAMI.navy, KAMI.gold), "repeat");
    g.fill();
    g.restore();
    torii(g, W * 0.86, H * 0.86, m * 0.19, KAMI.ink);
    g.beginPath();
    g.moveTo(-2, H * 0.64);
    g.bezierCurveTo(W * 0.14, H * 0.66, W * 0.16, H * 0.8, W * 0.26, H * 0.86);
    g.bezierCurveTo(W * 0.34, H * 0.91, W * 0.36, H * 0.96, W * 0.4, H + 2);
    g.lineTo(-2, H + 2);
    g.closePath();
    g.fillStyle = KAMI.sand;
    g.fill();
    g.strokeStyle = KAMI.gold;
    g.lineWidth = Math.max(0.8, m * 0.008);
    g.stroke();
    var petals = [[0.07, 0.86, 0.075, 0.2], [0.17, 0.95, 0.055, 1.1], [0.03, 0.72, 0.05, 0.7], [0.21, 0.83, 0.036, 2.0], [0.12, 0.77, 0.03, 0.4], [0.27, 0.97, 0.03, 1.6]];
    for (var p = 0; p < petals.length; p++) flatSakura(g, petals[p][0] * W, petals[p][1] * H, petals[p][2] * m, petals[p][3]);
    g.fillStyle = KAMI.sun;
    for (var lp = 0; lp < 5; lp++) {
      g.save();
      g.translate(W * (0.24 + rand() * 0.16), H * (0.74 + rand() * 0.12));
      g.rotate(rand() * TAU);
      g.scale(1, 0.6);
      g.beginPath();
      addPetal(g, m * 0.016);
      g.fill();
      g.restore();
    }
    paintPattern(g, speckTile(S), 0, 0, W, H, 1);
  }

  function mesh(base, blobs) {
    return function (g, W, H) {
      var S = Math.sqrt(W * H);
      smooth(g, W, H, function (c) {
        fillAll(c, W, H, linear(c, 0, 0, W, H, base));
        for (var i = 0; i < blobs.length; i++) {
          var b = blobs[i];
          c.save();
          c.translate(b[0] * W, b[1] * H);
          c.scale(W / S, H / S);
          c.fillStyle = radial(c, 0, 0, b[2] * S, [0, rgba(b[3], b[4]), 0.45, rgba(b[3], b[4] * 0.55), 1, rgba(b[3], 0)]);
          c.fillRect(-b[2] * S, -b[2] * S, 2 * b[2] * S, 2 * b[2] * S);
          c.restore();
        }
      });
      grain(g, W, H, 0.45);
    };
  }

  function solid(color) {
    return function (g, W, H) { fillAll(g, W, H, color); };
  }

  var BACKGROUNDS = [
    { id: "kamifuji", name: "Notebook Fuji", group: "japanese", dark: false, shade: [39, 48, 74], tint: "#9ec5d4", paint: paintKamifuji },
    { id: "seigaiha", name: "Seigaiha · Ai-iro", group: "japanese", dark: true, shade: [4, 14, 34], tint: "#1f4d7e", paint: paintSeigaiha },
    { id: "asanoha", name: "Asanoha · Fuji-iro", group: "japanese", dark: false, shade: [40, 30, 80], tint: "#cdc2e3", paint: paintAsanoha },
    { id: "sakura", name: "Sakura · Usubeni", group: "japanese", dark: false, shade: [110, 40, 62], tint: "#f5bfcc", paint: paintSakura },
    { id: "fuji", name: "Fuji · Kaki & Fuji-murasaki", group: "japanese", dark: true, shade: [26, 16, 50], tint: "#a986b2", paint: paintFuji },
    { id: "shoji", name: "Shoji · Kinari & Tatami", group: "japanese", dark: false, shade: [64, 40, 18], tint: "#e9d6b3", paint: paintShoji },
    { id: "washi", name: "Washi · Torinoko", group: "japanese", dark: false, shade: [80, 60, 36], tint: "#f4ecdb", paint: paintWashi },
    { id: "ichimatsu", name: "Ichimatsu · Matcha", group: "japanese", dark: false, shade: [40, 46, 18], tint: "#8c9a55", paint: paintIchimatsu },
    { id: "kumo", name: "Kumo · Shu-iro & Kin", group: "japanese", dark: true, shade: [70, 10, 6], tint: "#c3352a", paint: paintKumo },
    { id: "shippo", name: "Shippō · Seiji", group: "japanese", dark: false, shade: [18, 52, 40], tint: "#80b39d", paint: paintShippo },
    { id: "yoru", name: "Yoru · Kachi-iro", group: "japanese", dark: true, shade: [0, 0, 10], tint: "#141c45", paint: paintYoru },
    { id: "nami", name: "Nami · Kon & Gofun", group: "japanese", dark: true, shade: [6, 18, 40], tint: "#22497a", paint: paintNami },
    { id: "haru", name: "Sakura-iro × Wakatake", group: "gradient", dark: false, shade: [90, 50, 70], tint: "#f4b3c2",
      paint: mesh([0, "#fdeef1", 1, "#eef6ec"], [[0.08, 0.1, 0.62, "#f4b3c2", 0.9], [0.96, 0.16, 0.5, "#fcd9a8", 0.75], [0.88, 0.96, 0.66, "#9fd4b2", 0.85], [0.18, 0.96, 0.46, "#eebbcb", 0.6], [0.5, 0.5, 0.36, "#ffffff", 0.45]]) },
    { id: "yugure", name: "Kaki-iro × Fuji-murasaki", group: "gradient", dark: true, shade: [30, 14, 44], tint: "#a59aca",
      paint: mesh([0, "#3b2d63", 1, "#5d4683"], [[0.04, 0.96, 0.78, "#ed6d3d", 0.92], [0.36, 0.7, 0.55, "#f4a383", 0.6], [0.92, 0.08, 0.72, "#a59aca", 0.88], [0.62, 0.28, 0.46, "#e8879c", 0.5]]) },
    { id: "umi", name: "Ai-iro × Asagi", group: "gradient", dark: true, shade: [4, 18, 36], tint: "#165e83",
      paint: mesh([0, "#103152", 1, "#165e83"], [[0.08, 0.92, 0.72, "#00a3af", 0.78], [0.86, 0.12, 0.58, "#a2d7dd", 0.58], [0.92, 0.94, 0.6, "#223a70", 0.85], [0.3, 0.16, 0.5, "#2a83a2", 0.62]]) },
    { id: "kogane", name: "Yamabuki × Akane", group: "gradient", dark: false, shade: [90, 30, 10], tint: "#f8b500",
      paint: mesh([0, "#f8b500", 1, "#ed6d3d"], [[0.08, 0.08, 0.7, "#fcd575", 0.92], [0.94, 0.94, 0.7, "#b7282e", 0.78], [0.88, 0.12, 0.5, "#f6ad49", 0.7], [0.14, 0.88, 0.5, "#e45e32", 0.6]]) },
    { id: "matcha", name: "Matcha × Kinari", group: "gradient", dark: false, shade: [40, 48, 20], tint: "#c5c56a",
      paint: mesh([0, "#eeeed6", 1, "#c8ca7a"], [[0.08, 0.12, 0.62, "#fbfaf5", 0.92], [0.92, 0.88, 0.7, "#68be8d", 0.7], [0.84, 0.14, 0.5, "#c5c56a", 0.72], [0.16, 0.92, 0.55, "#928c36", 0.5]]) },
    { id: "paper", name: "Kinari · Washi cream", group: "solid", dark: false, shade: [80, 60, 36], tint: "#f3ede1", paint: solid("#f3ede1") },
    { id: "sumi", name: "Sumi · Ink black", group: "solid", dark: true, shade: [0, 0, 0], tint: "#1f1b18", paint: solid("#1f1b18") },
    { id: "white", name: "Shiro · White", group: "solid", dark: false, shade: [20, 20, 30], tint: "#ffffff", paint: solid("#ffffff") }
  ];

  var BG_BY_ID = {};
  BACKGROUNDS.forEach(function (b) { BG_BY_ID[b.id] = b; });

  var FRAMES = [
    { id: "none", name: "None", bar: 0 },
    { id: "macos", name: "macOS", bar: 28 },
    { id: "macos-dark", name: "macOS Dark", bar: 28 },
    { id: "windows", name: "Windows", bar: 32 },
    { id: "linux", name: "Linux", bar: 38 },
    { id: "postcard", name: "Postcard", bar: 0 }
  ];
  var FRAME_BY_ID = {};
  FRAMES.forEach(function (f) { FRAME_BY_ID[f.id] = f; });

  var ASPECTS = [
    { id: "auto", name: "Auto", ratio: 0 },
    { id: "16:9", name: "16:9", ratio: 16 / 9 },
    { id: "4:3", name: "4:3", ratio: 4 / 3 },
    { id: "1:1", name: "1:1", ratio: 1 },
    { id: "9:16", name: "9:16", ratio: 9 / 16 },
    { id: "3:2", name: "3:2", ratio: 3 / 2 },
    { id: "2:1", name: "2:1", ratio: 2 }
  ];
  var ASPECT_BY_ID = {};
  ASPECTS.forEach(function (a) { ASPECT_BY_ID[a.id] = a; });

  var DEFAULTS = { bg: "kamifuji", frame: "macos", padding: 8, radius: 12, shadow: 55, inset: 0, aspect: "auto", title: "", caption: "", stamp: false, date: today(), scale: 1 };

  var FRAME_STYLE = {
    "none": { matte: "rgba(255,255,255,0.4)", base: "#ffffff", outline: "rgba(0,0,0,0.1)", inner: null, dark: false },
    "macos": { matte: "#f4f4f4", base: "#ffffff", outline: "rgba(0,0,0,0.2)", inner: "rgba(255,255,255,0.45)", dark: false },
    "macos-dark": { matte: "#28282a", base: "#1e1e1e", outline: "rgba(0,0,0,0.72)", inner: "rgba(255,255,255,0.13)", dark: true },
    "windows": { matte: "#f3f3f3", base: "#ffffff", outline: "rgba(0,0,0,0.16)", inner: null, dark: false },
    "linux": { matte: "#242424", base: "#1e1e1e", outline: "rgba(0,0,0,0.7)", inner: "rgba(255,255,255,0.08)", dark: true }
  };

  var CARD_COLOR = "#f8f1e2";

  function resolve(opts) {
    var o = {}, src = opts || {};
    for (var k in DEFAULTS) o[k] = (src[k] === undefined || src[k] === null) ? DEFAULTS[k] : src[k];
    o.bg = BG_BY_ID[o.bg] ? o.bg : DEFAULTS.bg;
    o.frame = FRAME_BY_ID[o.frame] ? o.frame : DEFAULTS.frame;
    o.aspect = ASPECT_BY_ID[o.aspect] ? o.aspect : "auto";
    o.padding = clamp(toNumber(o.padding, DEFAULTS.padding), 0, 25);
    o.radius = clamp(toNumber(o.radius, DEFAULTS.radius), 0, 40);
    o.shadow = clamp(toNumber(o.shadow, DEFAULTS.shadow), 0, 100);
    o.inset = clamp(toNumber(o.inset, DEFAULTS.inset), 0, 40);
    o.scale = clamp(toNumber(o.scale, 1), 0.1, 4);
    o.title = String(o.title || "");
    o.caption = String(o.caption || "").replace(/\s+/g, " ").trim();
    o.stamp = o.stamp === true || o.stamp === 1 || o.stamp === "1" || o.stamp === "true";
    o.date = String(o.date || today());
    return o;
  }

  function sourceSize(img) {
    var w = img.naturalWidth || img.videoWidth || img.displayWidth || img.width || 0;
    var h = img.naturalHeight || img.videoHeight || img.displayHeight || img.height || 0;
    return [w, h];
  }

  function imageOpaque(image, size) {
    try {
      if (!probeCanvas) probeCanvas = makeCanvas(3, 3);
      var p = probeCanvas.getContext("2d");
      p.clearRect(0, 0, 3, 3);
      var xs = [0, Math.floor(size[0] / 2), size[0] - 1], ys = [0, Math.floor(size[1] / 2), size[1] - 1];
      for (var i = 0; i < 3; i++) {
        for (var j = 0; j < 3; j++) {
          if (i !== 1 || j !== 1) p.drawImage(image, xs[i], ys[j], 1, 1, i, j, 1, 1);
        }
      }
      var d = p.getImageData(0, 0, 3, 3).data;
      for (var k = 0; k < 9; k++) if (k !== 4 && d[k * 4 + 3] < 250) return false;
      return true;
    } catch (err) {
      return false;
    }
  }

  function postcardLayout(sw, sh, inset, u, o) {
    var big = Math.max(sw, sh);
    var pad = Math.round(Math.max(14 * u, big * 0.045));
    var stampW = Math.round(clamp(big * 0.095, 44 * u, 260 * u)), stampH = Math.round(stampW * 1.22);
    var top = Math.round(pad * 0.75);
    var photoY = top + stampH + Math.round(pad * 0.8);
    var pw = sw + 2 * inset, ph = sh + 2 * inset;
    var capFont = 0, capH = 0;
    if (o.caption) { capFont = Math.round(clamp(big / u * 0.02, 13, 44) * u); capH = Math.round(capFont * 2.6); }
    var w = pw + 2 * pad, h = photoY + ph + capH + pad;
    return { w: w, h: h, pad: pad, top: top, capFont: capFont, capH: capH, stamp: { x: w - pad - stampW, y: top, w: stampW, h: stampH }, photo: { x: pad, y: photoY, w: pw, h: ph } };
  }

  function computeLayout(iw, ih, o, pr, scale) {
    var u = pr * scale, sw = Math.max(1, Math.round(iw * scale)), sh = Math.max(1, Math.round(ih * scale));
    var inset = Math.round(o.inset * u);
    var L = { u: u, scale: scale, frame: o.frame, inset: inset, r: o.radius * u, bar: 0, cap: null, pc: null };
    var winW, winH, shotX, shotY;
    if (o.frame === "postcard") {
      var pc = postcardLayout(sw, sh, inset, u, o);
      L.pc = pc;
      L.r = Math.min(o.radius, 10) * u;
      winW = pc.w; winH = pc.h; shotX = pc.photo.x + inset; shotY = pc.photo.y + inset;
    } else {
      L.bar = Math.round(FRAME_BY_ID[o.frame].bar * u);
      winW = sw + 2 * inset; winH = L.bar + sh + 2 * inset; shotX = inset; shotY = L.bar + inset;
    }
    var contentW = winW, contentH = winH;
    if (o.caption && o.frame !== "postcard") {
      var font = Math.round(clamp(winW / u * 0.016, 12, 30) * u);
      L.cap = { font: font, gap: Math.round(font * 1.2), h: Math.round(font * 2.1) };
      contentH += L.cap.gap + L.cap.h;
    }
    var pad = Math.round(o.padding / 100 * Math.max(contentW, contentH));
    var W = contentW + 2 * pad, H = contentH + 2 * pad, ratio = ASPECT_BY_ID[o.aspect].ratio;
    if (ratio) { if (W / H < ratio) W = Math.round(H * ratio); else H = Math.round(W / ratio); }
    var cx = Math.round((W - contentW) / 2), cy = Math.round((H - contentH) / 2);
    L.W = W;
    L.H = H;
    L.content = { x: cx, y: cy, w: contentW, h: contentH };
    L.win = { x: cx, y: cy, w: winW, h: winH };
    L.shot = { x: cx + shotX, y: cy + shotY, w: sw, h: sh };
    return L;
  }

  function insetRect(r, by) {
    var x = Math.ceil(r.x + by), y = Math.ceil(r.y + by);
    var w = Math.floor(r.x + r.w - by) - x, h = Math.floor(r.y + r.h - by) - y;
    return w > 0 && h > 0 ? { x: x, y: y, w: w, h: h } : null;
  }

  function holeFor(L, o, opaque) {
    if (o.frame === "postcard") return { rect: insetRect(L.win, L.r + 1), fill: CARD_COLOR };
    if (o.frame === "none") {
      if (!opaque) return null;
      var ri = L.inset > 0 ? Math.max(0, L.r - L.inset) : L.r;
      return { rect: insetRect(L.shot, ri + 1), fill: "#ffffff" };
    }
    return { rect: insetRect(L.win, L.r + 1), fill: FRAME_STYLE[o.frame].base };
  }

  function shadowLayers(k, S, shade) {
    if (k <= 0) return [];
    return [
      { blur: S * (0.035 + 0.085 * k), y: S * (0.012 + 0.04 * k), color: rgba(shade, 0.1 + 0.32 * k) },
      { blur: S * (0.01 + 0.02 * k), y: S * (0.003 + 0.009 * k), color: rgba(shade, 0.07 + 0.2 * k) }
    ];
  }

  function micaColor(bg) { return mix("#f3f3f3", bg.tint, 0.07); }

  function drawMacBar(g, L, o, dark) {
    var x = L.win.x, y = L.win.y, w = L.win.w, b = L.bar, u = L.u;
    g.fillStyle = linear(g, 0, y, 0, y + b, dark ? [0, "#3b3b3d", 1, "#313133"] : [0, "#ededed", 1, "#e2e2e2"]);
    g.fillRect(x, y, w, b);
    var hair = Math.max(1, Math.round(u * 0.5));
    g.fillStyle = dark ? "#151517" : "#cbcbcb";
    g.fillRect(x, y + b - hair, w, hair);
    var cy = y + b / 2, first = x + Math.max(18, o.radius * 0.45 + 12) * u, rr = 6 * u;
    var lights = [["#ff5f57", "#e0443e"], ["#febc2e", "#dea123"], ["#28c840", "#1aab29"]];
    for (var i = 0; i < 3; i++) {
      var cx = first + i * 20 * u;
      g.beginPath();
      g.arc(cx, cy, rr, 0, TAU);
      g.fillStyle = lights[i][0];
      g.fill();
      g.beginPath();
      g.arc(cx, cy, rr - 0.25 * u, 0, TAU);
      g.lineWidth = 0.5 * u;
      g.strokeStyle = lights[i][1];
      g.stroke();
    }
    if (o.title) {
      g.font = "600 " + (13 * u) + "px " + FONT_UI;
      g.fillStyle = dark ? "#dcdcde" : "#4b4b4b";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(fitText(g, o.title, w - 2 * (first - x + 52 * u)), x + w / 2, cy + 0.5 * u);
    }
  }

  function snap(v, lw) { return Math.round(v) + (Math.round(lw) % 2 ? 0.5 : 0); }

  function drawWindowsBar(g, L, o, bg) {
    var x = L.win.x, y = L.win.y, w = L.win.w, b = L.bar, u = L.u, cy = y + b / 2;
    g.fillStyle = micaColor(bg);
    g.fillRect(x, y, w, b);
    var hair = Math.max(1, Math.round(u * 0.5));
    g.fillStyle = "rgba(0,0,0,0.07)";
    g.fillRect(x, y + b - hair, w, hair);
    var ix = x + 12 * u, is = 16 * u, iy = cy - is / 2;
    roundRect(g, ix, iy, is, is, 3.5 * u);
    g.fillStyle = linear(g, ix, iy, ix + is, iy + is, [0, "#4cc2ff", 1, "#0067c0"]);
    g.fill();
    g.fillStyle = "rgba(255,255,255,0.95)";
    roundRect(g, ix + 3.5 * u, iy + 4 * u, 9 * u, 8 * u, 1.2 * u);
    g.fill();
    g.fillStyle = "#1a7fd6";
    g.fillRect(ix + 3.5 * u, iy + 6 * u, 9 * u, 0.9 * u);
    var bw = 46 * u;
    if (o.title) {
      g.font = "400 " + (12 * u) + "px " + FONT_UI;
      g.fillStyle = "#1c1c1c";
      g.textAlign = "left";
      g.textBaseline = "middle";
      g.fillText(fitText(g, o.title, w - 3 * bw - 52 * u), ix + is + 10 * u, cy + 0.5 * u);
    }
    var lw = Math.max(1, Math.round(u)), half = 5 * u;
    g.strokeStyle = "#1c1c1c";
    g.lineWidth = lw;
    g.lineCap = "butt";
    for (var i = 0; i < 3; i++) {
      var cx = Math.round(x + w - (3 - i) * bw + bw / 2) - Math.round(Math.max(0, o.radius - 8) * 0.3 * u);
      g.beginPath();
      if (i === 0) {
        g.moveTo(cx - half, snap(cy, lw));
        g.lineTo(cx + half, snap(cy, lw));
      } else if (i === 1) {
        roundRect(g, snap(cx - half, lw), snap(cy - half, lw), Math.round(half * 2), Math.round(half * 2), 1.5 * u);
      } else {
        g.moveTo(cx - half, cy - half);
        g.lineTo(cx + half, cy + half);
        g.moveTo(cx + half, cy - half);
        g.lineTo(cx - half, cy + half);
      }
      g.stroke();
    }
  }

  function drawLinuxBar(g, L, o) {
    var x = L.win.x, y = L.win.y, w = L.win.w, b = L.bar, u = L.u, cy = y + b / 2;
    g.fillStyle = linear(g, 0, y, 0, y + b, [0, "#363636", 1, "#2e2e2e"]);
    g.fillRect(x, y, w, b);
    var hair = Math.max(1, Math.round(u * 0.5));
    g.fillStyle = "rgba(255,255,255,0.07)";
    g.fillRect(x, y, w, hair);
    g.fillStyle = "#1b1b1b";
    g.fillRect(x, y + b - hair, w, hair);
    var r = 12 * u, step = 2 * r + 6 * u, right = x + w - 8 * u - r - Math.max(0, o.radius - 10) * 0.4 * u;
    var glyph = 4 * u;
    g.lineWidth = Math.max(1, 1.3 * u);
    g.lineCap = "round";
    g.strokeStyle = "#ececec";
    for (var i = 0; i < 3; i++) {
      var cx = right - (2 - i) * step;
      g.beginPath();
      g.arc(cx, cy, r, 0, TAU);
      g.fillStyle = i === 2 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.09)";
      g.fill();
      g.beginPath();
      if (i === 0) { g.moveTo(cx - glyph, cy + glyph * 0.9); g.lineTo(cx + glyph, cy + glyph * 0.9); }
      else if (i === 1) { roundRect(g, cx - glyph, cy - glyph, glyph * 2, glyph * 2, 1.2 * u); }
      else { g.moveTo(cx - glyph, cy - glyph); g.lineTo(cx + glyph, cy + glyph); g.moveTo(cx + glyph, cy - glyph); g.lineTo(cx - glyph, cy + glyph); }
      g.stroke();
    }
    if (o.title) {
      g.font = "700 " + (13 * u) + "px " + FONT_UI;
      g.fillStyle = "#f4f4f4";
      g.textAlign = "center";
      g.textBaseline = "middle";
      var reserve = (x + w) - (right - 2 * step - r) + 12 * u;
      g.fillText(fitText(g, o.title, w - 2 * reserve), x + w / 2, cy + 0.5 * u);
    }
  }

  function drawWindow(g, image, size, L, o, bg, hole) {
    var x = L.win.x, y = L.win.y, w = L.win.w, h = L.win.h, r = L.r, u = L.u, b = L.bar;
    var style = FRAME_STYLE[o.frame], k = o.shadow / 100;
    var shape = function (c) { addRoundRect(c, x, y, w, h, r); };
    castShadow(g, L.W, L.H, shape, L.win, L.shadows, hole ? hole.rect : null);
    if (hole && k > 0) contactRing(g, shape, bg.shade, k, u);
    g.save();
    g.beginPath();
    shape(g);
    g.clip();
    if (L.inset > 0) {
      g.fillStyle = o.frame === "windows" ? micaColor(bg) : style.matte;
      g.fillRect(x, y, w, h);
      if (o.frame === "none") {
        g.fillStyle = linear(g, 0, y, 0, y + h, [0, "rgba(255,255,255,0.3)", 0.5, "rgba(255,255,255,0.06)", 1, "rgba(255,255,255,0.16)"]);
        g.fillRect(x, y, w, h);
      }
    } else if (hole && o.frame !== "none") {
      g.fillStyle = style.base;
      g.fillRect(x, y + b, w, h - b);
    }
    if (o.frame === "macos" || o.frame === "macos-dark") drawMacBar(g, L, o, o.frame === "macos-dark");
    else if (o.frame === "windows") drawWindowsBar(g, L, o, bg);
    else if (o.frame === "linux") drawLinuxBar(g, L, o);
    var s = L.shot;
    if (L.inset > 0) {
      var ri = Math.max(0, r - L.inset), top = b ? Math.min(ri, 5 * u) : ri;
      g.save();
      roundRect(g, s.x, s.y, s.w, s.h, [top, top, ri, ri]);
      g.clip();
      g.drawImage(image, 0, 0, size[0], size[1], s.x, s.y, s.w, s.h);
      g.restore();
      g.lineWidth = 1;
      g.strokeStyle = style.dark ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.1)";
      roundRect(g, s.x - 0.5, s.y - 0.5, s.w + 1, s.h + 1, [top + 0.5, top + 0.5, ri + 0.5, ri + 0.5]);
      g.stroke();
    } else {
      g.drawImage(image, 0, 0, size[0], size[1], s.x, s.y, s.w, s.h);
    }
    g.restore();
    g.lineWidth = 1;
    g.strokeStyle = o.frame === "none" ? (L.inset > 0 ? "rgba(255,255,255,0.55)" : (bg.dark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.1)")) : style.outline;
    roundRect(g, x + 0.5, y + 0.5, w - 1, h - 1, Math.max(0, r - 0.5));
    g.stroke();
    if (style.inner) {
      g.strokeStyle = style.inner;
      roundRect(g, x + 1.5, y + 1.5, w - 3, h - 3, Math.max(0, r - 1.5));
      g.stroke();
    }
  }

  function stampFuji(g, x, y, w, h) {
    g.fillStyle = linear(g, 0, y, 0, y + h, [0, "#e9e6e0", 0.6, "#dfe9ec", 1, "#c9dfe6"]);
    g.fillRect(x, y, w, h);
    g.beginPath();
    g.arc(x + w * 0.72, y + h * 0.28, w * 0.17, 0, TAU);
    g.fillStyle = "#e0452f";
    g.fill();
    g.save();
    g.beginPath();
    g.moveTo(x - w * 0.1, y + h * 0.84);
    g.quadraticCurveTo(x + w * 0.28, y + h * 0.66, x + w * 0.41, y + h * 0.4);
    g.lineTo(x + w * 0.55, y + h * 0.4);
    g.quadraticCurveTo(x + w * 0.68, y + h * 0.66, x + w * 1.1, y + h * 0.84);
    g.lineTo(x + w * 1.1, y + h);
    g.lineTo(x - w * 0.1, y + h);
    g.closePath();
    g.fillStyle = "#a9cbd8";
    g.fill();
    g.clip();
    g.beginPath();
    g.moveTo(x, y + h * 0.3);
    var steps = 9;
    for (var i = 0; i <= steps; i++) g.lineTo(x + w * (0.3 + 0.36 * i / steps), y + h * (i % 2 ? 0.56 : 0.5));
    g.lineTo(x + w, y + h * 0.3);
    g.closePath();
    g.fillStyle = "#fbf8f1";
    g.fill();
    g.restore();
    g.fillStyle = "#2f3a52";
    g.fillRect(x, y + h * 0.84, w, h * 0.16);
    g.strokeStyle = "#d9b46e";
    g.lineWidth = Math.max(0.6, w * 0.018);
    for (var k = 0; k < 4; k++) {
      var wx = x + w * (0.08 + k * 0.26), wy = y + h * 0.9;
      g.beginPath();
      g.arc(wx + w * 0.06, wy, w * 0.06, Math.PI, TAU);
      g.stroke();
    }
  }

  function stampSakura(g, x, y, w, h) {
    g.fillStyle = linear(g, 0, y, 0, y + h, [0, "#fbe6ea", 1, "#f4c3cf"]);
    g.fillRect(x, y, w, h);
    branch(g, [[x + w * 1.05, y + h * 0.2], [x + w * 0.7, y + h * 0.42], [x + w * 0.35, y + h * 0.5], [x - w * 0.05, y + h * 0.72]], w * 0.05, w * 0.02, "#5a3438");
    blossom(g, x + w * 0.58, y + h * 0.5, w * 0.27, 0.3, 1, false);
    blossom(g, x + w * 0.2, y + h * 0.66, w * 0.17, 1.2, 1, true);
    blossom(g, x + w * 0.9, y + h * 0.25, w * 0.14, 0.7, 1, true);
    fallingPetal(g, x + w * 0.3, y + h * 0.3, w * 0.08, 0.6, 0.7, 0.9);
    g.fillStyle = "#7a3048";
    g.fillRect(x, y + h * 0.86, w, h * 0.14);
  }

  function buildStamp(w, motif) {
    w = Math.max(16, Math.round(w));
    var h = Math.round(w * 1.22), c = makeCanvas(w, h), g = c.getContext("2d");
    g.fillStyle = "#fffcf4";
    g.fillRect(0, 0, w, h);
    var m = Math.round(w * 0.1), ix = m, iy = m, iw = w - 2 * m, ih = h - 2 * m;
    g.save();
    g.beginPath();
    g.rect(ix, iy, iw, ih);
    g.clip();
    if (motif === "sakura") stampSakura(g, ix, iy, iw, ih); else stampFuji(g, ix, iy, iw, ih);
    g.restore();
    g.fillStyle = motif === "sakura" ? "#5e2238" : "#27304a";
    g.font = "800 " + Math.round(iw * 0.2) + "px " + FONT_UI;
    g.textAlign = "left";
    g.textBaseline = "top";
    g.fillText("63", ix + iw * 0.07, iy + ih * 0.05);
    g.fillStyle = "rgba(255,252,244,0.95)";
    g.font = "700 " + Math.max(4, Math.round(iw * 0.075)) + "px " + FONT_UI;
    g.textBaseline = "middle";
    g.fillText("POST", ix + iw * 0.06, iy + ih * 0.93);
    g.textAlign = "right";
    g.font = "700 " + Math.max(4, Math.round(iw * 0.075)) + "px " + FONT_UI;
    g.fillText("NIPPON", ix + iw * 0.95, iy + ih * 0.93);
    g.strokeStyle = "rgba(60,40,30,0.18)";
    g.lineWidth = Math.max(1, w * 0.006);
    g.strokeRect(ix + 0.5, iy + 0.5, iw - 1, ih - 1);
    g.globalCompositeOperation = "destination-out";
    var hole = w * 0.036, nx = Math.max(5, Math.round(w / (hole * 2.9))), ny = Math.max(6, Math.round(h / (hole * 2.9)));
    g.beginPath();
    for (var i = 0; i <= nx; i++) { var px = i * w / nx; g.moveTo(px + hole, 0); g.arc(px, 0, hole, 0, TAU); g.moveTo(px + hole, h); g.arc(px, h, hole, 0, TAU); }
    for (var j = 1; j < ny; j++) { var py = j * h / ny; g.moveTo(hole, py); g.arc(0, py, hole, 0, TAU); g.moveTo(w + hole, py); g.arc(w, py, hole, 0, TAU); }
    g.fill();
    return c;
  }

  function buildPostmark(R, dateText, ink, seed) {
    var size = Math.ceil(R * 2.2), c = makeCanvas(size, size), g = c.getContext("2d"), cx = size / 2, cy = size / 2;
    g.strokeStyle = ink;
    g.fillStyle = ink;
    var outer = R * 0.06;
    g.lineWidth = outer;
    g.beginPath();
    g.arc(cx, cy, R - outer / 2, 0, TAU);
    g.stroke();
    var innerR = R * 0.84;
    g.lineWidth = R * 0.028;
    g.beginPath();
    g.arc(cx, cy, innerR, 0, TAU);
    g.stroke();
    var band = R * 0.21, chord = Math.sqrt(innerR * innerR - band * band);
    g.lineWidth = R * 0.03;
    g.beginPath();
    g.moveTo(cx - chord, cy - band); g.lineTo(cx + chord, cy - band);
    g.moveTo(cx - chord, cy + band); g.lineTo(cx + chord, cy + band);
    g.stroke();
    g.textAlign = "center";
    g.textBaseline = "middle";
    var fs = R * 0.27;
    g.font = "700 " + fs.toFixed(1) + "px " + FONT_UI;
    var tw = g.measureText(dateText).width;
    if (tw > chord * 1.8) { fs *= chord * 1.8 / tw; g.font = "700 " + fs.toFixed(1) + "px " + FONT_UI; }
    g.fillText(dateText, cx, cy + R * 0.01);
    g.font = "700 " + (R * 0.17).toFixed(1) + "px " + FONT_UI;
    spacedText(g, "POST", cx, cy - R * 0.5, R * 0.06);
    g.font = "700 " + (R * 0.155).toFixed(1) + "px " + FONT_UI;
    spacedText(g, "HAGAKI", cx, cy + R * 0.52, R * 0.045);
    g.beginPath();
    g.arc(cx - R * 0.5, cy + R * 0.52, R * 0.03, 0, TAU);
    g.arc(cx + R * 0.5, cy + R * 0.52, R * 0.03, 0, TAU);
    g.fill();
    var rand = seeded(seed);
    g.globalCompositeOperation = "destination-out";
    var specks = Math.round(R * R * 0.04) + 30;
    for (var i = 0; i < specks; i++) {
      g.globalAlpha = 0.3 + rand() * 0.7;
      g.beginPath();
      g.arc(rand() * size, rand() * size, R * (0.004 + rand() * rand() * 0.03), 0, TAU);
      g.fill();
    }
    g.globalAlpha = 1;
    g.fillStyle = linear(g, 0, 0, size, size * 0.4, [0, "rgba(0,0,0,0)", 0.55, "rgba(0,0,0,0.12)", 1, "rgba(0,0,0,0.5)"]);
    g.fillRect(0, 0, size, size);
    return c;
  }

  function buildSeal(size) {
    size = Math.max(8, Math.round(size));
    var c = makeCanvas(size, size), g = c.getContext("2d"), rand = seeded(64);
    roundRect(g, size * 0.04, size * 0.04, size * 0.92, size * 0.92, size * 0.1);
    g.fillStyle = "#c2381f";
    g.fill();
    g.globalCompositeOperation = "destination-out";
    g.font = "700 " + Math.round(size * 0.62) + "px " + FONT_UI;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText("H", size / 2, size * 0.54);
    for (var i = 0; i < 40; i++) {
      g.globalAlpha = 0.4 + rand() * 0.6;
      g.beginPath();
      g.arc(rand() * size, rand() * size, size * (0.005 + rand() * 0.02), 0, TAU);
      g.fill();
    }
    return c;
  }

  function stampMotif(bgId) { return bgId === "sakura" || bgId === "haru" ? "sakura" : "fuji"; }

  function inkFor(bg) { return bg.dark ? "#e0583f" : "#27304a"; }

  function drawStampGroup(g, x, y, w, o, ink) {
    var stamp = buildStamp(w, stampMotif(o.bg)), h = stamp.height;
    g.save();
    g.translate(x + w / 2, y + h / 2);
    g.rotate(-0.035);
    g.shadowColor = "rgba(0,0,0,0.26)";
    g.shadowBlur = Math.max(2, w * 0.06);
    g.shadowOffsetY = Math.max(1, w * 0.02);
    g.drawImage(stamp, -stamp.width / 2, -h / 2);
    g.restore();
    var R = w * 0.44, mark = buildPostmark(R, postmarkDate(o.date), ink, 7 + Math.round(w));
    g.save();
    g.translate(x - w * 0.02, y + h * 0.64);
    g.rotate(-0.24);
    g.globalAlpha = 0.86;
    g.drawImage(mark, -mark.width / 2, -mark.height / 2);
    g.restore();
  }

  function drawCornerStamp(g, L, o, bg) {
    var W = L.W, H = L.H, u = L.u, S = Math.max(W, H);
    var topSpace = L.content.y, rightSpace = W - (L.content.x + L.content.w);
    var desired = clamp(S * 0.07, 34 * u, 160 * u);
    var byTop = topSpace * 0.78 / 1.22, byRight = rightSpace * 0.82 / 1.45;
    var w = Math.min(desired, Math.max(byTop, byRight, Math.min(desired, S * 0.045)));
    var h = w * 1.22, x, y;
    if (byTop >= byRight || byTop >= w) {
      y = Math.max(S * 0.012, (topSpace - h) / 2);
      x = W - Math.max(y * 1.1, S * 0.02) - w;
    } else {
      x = L.content.x + L.content.w + (rightSpace - w * 1.45) / 2 + w * 0.45;
      y = Math.max(S * 0.02, (rightSpace - w * 1.45) / 2);
    }
    drawStampGroup(g, Math.round(x), Math.round(y), w, o, inkFor(bg));
  }

  function drawCaptionPill(g, L, o, bg) {
    var c = L.cap, dark = bg.dark;
    g.save();
    g.font = "500 " + c.font + "px " + FONT_UI;
    var maxW = Math.max(c.font * 8, Math.min(L.W - c.font * 3, L.win.w));
    var text = fitText(g, o.caption, maxW - c.font * 1.9);
    var pw = Math.round(g.measureText(text).width + c.font * 1.9), ph = c.h;
    var px = Math.round(L.win.x + L.win.w / 2 - pw / 2), py = L.win.y + L.win.h + c.gap;
    var shape = function (k) { addRoundRect(k, px, py, pw, ph, ph / 2); };
    castShadow(g, L.W, L.H, shape, { x: px, y: py, w: pw, h: ph }, [
      { blur: c.font * 1.4, y: c.font * 0.3, color: rgba(bg.shade, 0.22) },
      { blur: c.font * 0.35, y: c.font * 0.06, color: rgba(bg.shade, 0.14) }
    ], null);
    g.beginPath();
    shape(g);
    g.fillStyle = dark ? "rgba(16,14,22,0.58)" : "rgba(255,255,255,0.86)";
    g.fill();
    g.lineWidth = 1;
    g.strokeStyle = dark ? "rgba(255,255,255,0.16)" : "rgba(0,0,0,0.07)";
    roundRect(g, px + 0.5, py + 0.5, pw - 1, ph - 1, ph / 2 - 0.5);
    g.stroke();
    g.fillStyle = dark ? "#f7f2ea" : "#27304a";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, px + pw / 2, py + ph / 2 + c.font * 0.04);
    g.restore();
  }

  function drawPostalBoxes(g, L, x, y) {
    var P = L.pc, u = L.u, st = P.stamp, vermilion = "#e0452f";
    var boxH = Math.round(st.h * 0.24), boxW = Math.round(boxH * 0.72), gap = Math.round(boxW * 0.2), dash = Math.round(boxW * 0.95);
    var groupW = 7 * boxW + 5 * gap + dash, room = st.x - st.w * 0.45 - P.pad * 1.3;
    if (groupW > room) {
      var k = Math.max(0.3, room / groupW);
      boxH = Math.round(boxH * k); boxW = Math.round(boxW * k); gap = Math.round(gap * k); dash = Math.round(dash * k);
      groupW = 7 * boxW + 5 * gap + dash;
    }
    var bx = Math.round(x + P.pad), by = Math.round(y + st.y + st.h * 0.04);
    var thin = Math.max(1, Math.round(0.75 * u)), thick = Math.max(2, Math.round(1.4 * u));
    g.strokeStyle = vermilion;
    for (var i = 0; i < 7; i++) {
      var lw = i < 3 ? thick : thin, px = bx + i * (boxW + gap) + (i >= 3 ? dash - gap : 0);
      g.lineWidth = lw;
      g.strokeRect(Math.round(px) + lw / 2, by + lw / 2, boxW - lw, boxH - lw);
    }
    g.fillStyle = vermilion;
    g.fillRect(Math.round(bx + 3 * boxW + 2 * gap + dash * 0.28), Math.round(by + boxH / 2 - thin / 2), Math.round(dash * 0.44), thin);
    var labelFont = Math.max(8, Math.round(boxH * 0.42));
    g.font = "600 " + labelFont + "px " + FONT_UI;
    g.textBaseline = "middle";
    g.fillStyle = "rgba(39,48,74,0.82)";
    var left = bx + groupW + P.pad * 0.6, right = x + st.x - st.w * 0.5;
    if (right - left > labelFont * 8) spacedText(g, "POST CARD", (left + right) / 2, by + boxH * 0.5, labelFont * 0.55);
  }

  function drawPostcard(g, image, size, L, o, bg, hole) {
    var P = L.pc, u = L.u, x = L.win.x, y = L.win.y, w = L.win.w, h = L.win.h, r = L.r, k = o.shadow / 100;
    var shape = function (c) { addRoundRect(c, x, y, w, h, r); };
    castShadow(g, L.W, L.H, shape, L.win, L.shadows, hole ? hole.rect : null);
    if (k > 0) contactRing(g, shape, bg.shade, k, u);
    var ph = P.photo, pr = Math.min(L.r, 4 * u), qx = x + ph.x, qy = y + ph.y;
    var photoHole = insetRect({ x: qx, y: qy, w: ph.w, h: ph.h }, pr + 1);
    g.save();
    if (photoHole) {
      g.beginPath();
      g.rect(x - 2, y - 2, w + 4, h + 4);
      g.rect(photoHole.x, photoHole.y, photoHole.w, photoHole.h);
      g.clip("evenodd");
    }
    g.beginPath();
    shape(g);
    g.fillStyle = linear(g, x, y, x + w, y + h, [0, "#fcf7ec", 1, "#f3e9d4"]);
    g.fill();
    g.save();
    g.translate(x, y);
    g.globalAlpha = 0.6;
    g.fillStyle = g.createPattern(fibreTile(Math.sqrt(w * h)), "repeat");
    g.beginPath();
    addRoundRect(g, 0, 0, w, h, r);
    g.fill();
    g.restore();
    var edge = Math.ceil(Math.max(4, Math.min(w, h) * 0.035)), tone = "rgba(150,112,62,";
    var strips = [[x, y, edge, h, x, 0, x + edge, 0], [x + w - edge, y, edge, h, x + w, 0, x + w - edge, 0], [x, y, w, edge, 0, y, 0, y + edge], [x, y + h - edge, w, edge, 0, y + h, 0, y + h - edge]];
    g.beginPath();
    shape(g);
    g.clip();
    for (var e = 0; e < 4; e++) {
      var sp = strips[e];
      g.fillStyle = linear(g, sp[4], sp[5], sp[6], sp[7], [0, tone + "0.2)", 0.35, tone + "0.07)", 1, tone + "0)"]);
      g.fillRect(sp[0], sp[1], sp[2], sp[3]);
    }
    g.restore();
    g.lineWidth = 1;
    g.strokeStyle = "rgba(140,108,66,0.3)";
    roundRect(g, x + 0.5, y + 0.5, w - 1, h - 1, Math.max(0, r - 0.5));
    g.stroke();
    drawPostalBoxes(g, L, x, y);
    drawStampGroup(g, x + P.stamp.x, y + P.stamp.y, P.stamp.w, o, "#27304a");
    var photoShape = function (c) { addRoundRect(c, qx, qy, ph.w, ph.h, pr); };
    castShadow(g, L.W, L.H, photoShape, { x: qx, y: qy, w: ph.w, h: ph.h }, [
      { blur: Math.max(3, 7 * u), y: 2 * u, color: "rgba(90,64,30,0.24)" }
    ], photoHole);
    contactRing(g, photoShape, [90, 64, 30], 0.6, u * 0.6);
    var s = L.shot;
    g.save();
    g.beginPath();
    photoShape(g);
    g.clip();
    if (L.inset > 0) {
      g.fillStyle = "#fffdf7";
      g.fillRect(qx, qy, ph.w, ph.h);
      g.save();
      roundRect(g, s.x, s.y, s.w, s.h, Math.min(pr, 2 * u));
      g.clip();
      g.drawImage(image, 0, 0, size[0], size[1], s.x, s.y, s.w, s.h);
      g.restore();
    } else {
      g.drawImage(image, 0, 0, size[0], size[1], s.x, s.y, s.w, s.h);
    }
    g.restore();
    g.lineWidth = 1;
    g.strokeStyle = "rgba(70,50,30,0.2)";
    roundRect(g, qx + 0.5, qy + 0.5, ph.w - 1, ph.h - 1, Math.max(0, pr - 0.5));
    g.stroke();
    if (o.caption) {
      var f = P.capFont, lineY = qy + ph.h + P.capH * 0.72, sealS = Math.round(f * 1.55);
      g.font = "500 " + f + "px " + FONT_SERIF;
      g.fillStyle = "#27304a";
      g.textAlign = "left";
      g.textBaseline = "alphabetic";
      g.fillText(fitText(g, o.caption, ph.w - sealS - f * 1.6), qx + f * 0.3, lineY - f * 0.32);
      g.strokeStyle = "rgba(150,116,80,0.45)";
      g.lineWidth = Math.max(1, Math.round(0.6 * u));
      g.setLineDash([Math.max(1, u), Math.max(2, 3 * u)]);
      g.beginPath();
      g.moveTo(qx, Math.round(lineY) + 0.5);
      g.lineTo(qx + ph.w - sealS - f * 0.6, Math.round(lineY) + 0.5);
      g.stroke();
      g.setLineDash([]);
      var seal = buildSeal(sealS);
      g.save();
      g.translate(qx + ph.w - sealS / 2, lineY - f * 0.45);
      g.rotate(0.04);
      g.globalAlpha = 0.9;
      g.drawImage(seal, -sealS / 2, -sealS / 2);
      g.restore();
    }
  }

  function pixelRatio(meta) {
    return meta && toNumber(meta.pixelRatio, 0) > 0 ? toNumber(meta.pixelRatio, 1) : 1;
  }

  function render(canvas, image, opts, meta) {
    if (!canvas || !image) throw new Error("HagakiStudio.render needs a canvas and an image");
    var o = resolve(opts);
    var pr = pixelRatio(meta);
    var size = sourceSize(image);
    if (!size[0] || !size[1]) throw new Error("HagakiStudio.render: the image has no pixels yet");
    var L = computeLayout(size[0], size[1], o, pr, o.scale);
    var over = Math.max(Math.sqrt(L.W * L.H / MAX_AREA), Math.max(L.W, L.H) / MAX_SIDE);
    if (over > 1) L = computeLayout(size[0], size[1], o, pr, o.scale / over * 0.995);
    canvas.width = L.W;
    canvas.height = L.H;
    var g = canvas.getContext("2d");
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.imageSmoothingEnabled = true;
    if ("imageSmoothingQuality" in g) g.imageSmoothingQuality = "high";
    var bg = BG_BY_ID[o.bg];
    var hole = holeFor(L, o, imageOpaque(image, size));
    if (hole && !hole.rect) hole = null;
    g.save();
    if (hole) {
      g.beginPath();
      g.rect(0, 0, L.W, L.H);
      g.rect(hole.rect.x, hole.rect.y, hole.rect.w, hole.rect.h);
      g.clip("evenodd");
    }
    bg.paint(g, L.W, L.H);
    g.restore();
    if (hole) {
      g.fillStyle = hole.fill;
      g.fillRect(hole.rect.x, hole.rect.y, hole.rect.w, hole.rect.h);
    }
    L.shadows = shadowLayers(o.shadow / 100, Math.max(L.win.w, L.win.h), bg.shade);
    g.save();
    if (o.frame === "postcard") drawPostcard(g, image, size, L, o, bg, hole); else drawWindow(g, image, size, L, o, bg, hole);
    g.restore();
    if (L.cap) drawCaptionPill(g, L, o, bg);
    if (o.stamp && o.frame !== "postcard") {
      g.save();
      drawCornerStamp(g, L, o, bg);
      g.restore();
    }
    return { width: L.W, height: L.H, shot: { x: L.shot.x, y: L.shot.y, w: L.shot.w, h: L.shot.h } };
  }

  function plate(width, height, opts, meta) {
    var base = makeCanvas(1, 1), mask = makeCanvas(1, 1), stand = makeCanvas(width, height), gs = stand.getContext("2d");
    gs.fillStyle = "#000000";
    gs.fillRect(0, 0, stand.width, stand.height);
    var out = render(base, stand, opts, meta);
    gs.fillStyle = "#ffffff";
    gs.fillRect(0, 0, stand.width, stand.height);
    render(mask, stand, opts, meta);
    var w = base.width, h = base.height;
    var gm = mask.getContext("2d");
    var dark = base.getContext("2d").getImageData(0, 0, w, h).data, lit = gm.getImageData(0, 0, w, h);
    var d = lit.data;
    for (var i = 0; i < d.length; i += 4) {
      var m = Math.max(d[i] - dark[i], d[i + 1] - dark[i + 1], d[i + 2] - dark[i + 2], 0);
      d[i] = d[i + 1] = d[i + 2] = m;
      d[i + 3] = 255;
    }
    gm.putImageData(lit, 0, 0);
    return { width: w, height: h, shot: out.shot, base: base, mask: mask };
  }

  function videoPlate(width, height, opts, meta, limits) {
    var lim = limits || {};
    var pr = pixelRatio(meta);
    var L1 = computeLayout(width, height, resolve(Object.assign({}, opts, { scale: 1 })), pr, 1);
    var out = Math.min(1, (lim.maxSide || VIDEO_SIDE) / Math.max(L1.W, L1.H));
    var k = clamp(Math.min(toNumber(lim.zoom, 1), Math.sqrt((lim.maxArea || 2.4e7) / (L1.W * L1.H * out * out))), 1, 2);
    var p = plate(width, height, Object.assign({}, opts, { scale: out * k }), meta);
    var W = Math.max(4, 4 * Math.round(p.width / k / 4)), H = Math.max(4, 4 * Math.round(p.height / k / 4));
    var kx = p.width / W, ky = p.height / H;
    var full = Math.min(1, VIDEO_SIDE / Math.max(L1.W, L1.H));
    return {
      base: p.base, mask: p.mask,
      layout: { W: W, H: H, shot: { x: p.shot.x / kx, y: p.shot.y / ky, w: p.shot.w / kx, h: p.shot.h / ky } },
      full: { W: Math.round(L1.W * full), H: Math.round(L1.H * full) }
    };
  }

  function toBlob(image, opts, meta) {
    return new Promise(function (resolvePromise, reject) {
      try {
        var c = document.createElement("canvas");
        render(c, image, opts, meta);
        c.toBlob(function (blob) {
          if (blob) resolvePromise(blob); else reject(new Error("HagakiStudio.toBlob: PNG encoding failed"));
        }, "image/png");
      } catch (err) {
        reject(err);
      }
    });
  }

  function swatch(canvas, bgId) {
    var bg = BG_BY_ID[bgId] || BG_BY_ID[DEFAULTS.bg];
    var g = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
    g.save();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, W, H);
    g.imageSmoothingEnabled = true;
    if ("imageSmoothingQuality" in g) g.imageSmoothingQuality = "high";
    bg.paint(g, W, H);
    g.restore();
  }

  window.HagakiStudio = {
    version: 1,
    backgrounds: BACKGROUNDS.map(function (b) { return { id: b.id, name: b.name, group: b.group }; }),
    frames: FRAMES.map(function (f) { return { id: f.id, name: f.name }; }),
    aspects: ASPECTS.map(function (a) { return { id: a.id, name: a.name }; }),
    defaults: DEFAULTS,
    render: render,
    videoPlate: videoPlate,
    toBlob: toBlob,
    swatch: swatch
  };
})();
