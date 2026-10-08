"""The self-contained HTML page of ``shape timelapse -o OUT.html`` (a template: the frames are
embedded as JSON and the script draws everything from them; it makes no request)."""

# ruff: noqa: E501

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
:root{--bg:#fff;--fg:#1b1f24;--muted:#5b6570;--line:#d3d8de;--accent:#2467d1;--alert:#c7352f;--panel:#f5f7f9}
@media (prefers-color-scheme: dark){:root{--bg:#14171a;--fg:#e6e9ec;--muted:#98a2ad;--line:#333a42;--accent:#6aa3ff;--alert:#ff7b72;--panel:#1c2024}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:1000px;margin-inline:auto}
h1{font-size:1.25rem;margin:0 0 2px}
.sub{color:var(--muted);margin:0 0 14px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
button,select{font:inherit;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 12px}
#slider{flex:1 1 220px;accent-color:var(--accent)}
#when{font-variant-numeric:tabular-nums;min-width:11ch}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:12px}
.panel h2{font-size:.85rem;margin:0 0 6px;color:var(--muted);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
svg{width:100%;height:auto;display:block}
svg text{fill:var(--muted);font-size:11px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
td{padding:2px 4px;border-bottom:1px solid var(--line)}
td:last-child{text-align:right}
.flag{color:var(--alert);font-weight:600}
.empty{color:var(--muted)}
</style>
</head>
<body>
<h1 id="title"></h1>
<p class="sub" id="sub"></p>
<div class="controls">
<button id="play" type="button">Play</button>
<input id="slider" type="range" min="0" max="0" value="0" step="1" aria-label="Frame">
<span id="when"></span>
<label>Statistic <select id="stat"></select></label>
</div>
<div class="panel"><h2 id="chart-title"></h2><div id="chart"></div></div>
<div class="grid">
<div class="panel"><h2>Quantiles of this frame (grey: the first frame)</h2><div id="dist"></div></div>
<div class="panel"><h2>Top values</h2><div id="top"></div></div>
<div class="panel"><h2>This frame</h2><table id="facts"></table></div>
</div>
<p class="sub" id="notes"></p>
<script type="application/json" id="shape-timelapse-data">@@DATA@@</script>
<script>
(function () {
  var doc = JSON.parse(document.getElementById('shape-timelapse-data').textContent);
  var frames = doc.frames;
  var QUANTILES = ['p1', 'p5', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99'];
  var STATS = ['row_count', 'null_rate', 'cardinality', 'mean', 'std'].concat(QUANTILES);
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  };
  var fmt = function (v) { return v === null || v === undefined ? '-' : String(Number(v.toPrecision(6))); };
  var value = function (f, stat) {
    if (QUANTILES.indexOf(stat) >= 0) { return f.quantiles ? f.quantiles[stat] : null; }
    return f[stat];
  };
  var present = STATS.filter(function (s) {
    return frames.some(function (f) { return value(f, s) !== null && value(f, s) !== undefined; });
  });
  var current = 0, timer = null, stat = present.indexOf('mean') >= 0 ? 'mean' : present[0];

  $('title').textContent = doc.name + '.' + doc.column + (doc.window ? ' (' + doc.window + ' windows)' : '');
  $('sub').textContent = frames.length + ' frames, ' + frames[0].date + ' to ' + frames[frames.length - 1].end +
    ', ' + doc.change_points.length + ' change point(s)';
  $('notes').textContent = (doc.notes || []).join(' ');
  $('slider').max = String(frames.length - 1);
  $('stat').innerHTML = present.map(function (s) {
    return '<option value="' + s + '"' + (s === stat ? ' selected' : '') + '>' + s + '</option>';
  }).join('');

  function chart() {
    var W = 640, H = 220, L = 52, R = 10, T = 10, B = 24;
    var vals = frames.map(function (f) { return value(f, stat); });
    var known = vals.filter(function (v) { return v !== null && v !== undefined; });
    var lo = Math.min.apply(null, known), hi = Math.max.apply(null, known);
    if (hi === lo) { hi = lo + 1; }
    var n = frames.length;
    var x = function (i) { return L + (n === 1 ? 0.5 : i / (n - 1)) * (W - L - R); };
    var y = function (v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); };
    var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(stat) + ' over time">';
    out += '<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '" stroke="var(--line)"/>';
    out += '<text x="4" y="' + (T + 8) + '">' + esc(fmt(hi)) + '</text>';
    out += '<text x="4" y="' + (H - B) + '">' + esc(fmt(lo)) + '</text>';
    out += '<text x="' + L + '" y="' + (H - 6) + '">' + esc(frames[0].date) + '</text>';
    out += '<text x="' + (W - R) + '" y="' + (H - 6) + '" text-anchor="end">' + esc(frames[n - 1].date) + '</text>';
    frames.forEach(function (f, i) {
      if (f.change_point) {
        out += '<line class="change-point" x1="' + x(i) + '" y1="' + T + '" x2="' + x(i) + '" y2="' + (H - B) +
          '" stroke="var(--alert)" stroke-dasharray="4 3"/>';
      }
    });
    var d = '', pen = false;
    vals.forEach(function (v, i) {
      if (v === null || v === undefined) { pen = false; return; }
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
      pen = true;
    });
    out += '<path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="2"/>';
    vals.forEach(function (v, i) {
      if (v === null || v === undefined) { return; }
      out += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="' + (i === current ? 5 : 2.5) +
        '" fill="' + (i === current ? 'var(--fg)' : 'var(--accent)') + '"/>';
    });
    out += '<line x1="' + x(current) + '" y1="' + T + '" x2="' + x(current) + '" y2="' + (H - B) + '" stroke="var(--muted)"/>';
    $('chart').innerHTML = out + '</svg>';
    $('chart-title').textContent = stat + ' per frame (dashed: change points; gaps where the column is missing)';
  }

  function dist() {
    var W = 300, H = 180, L = 44, R = 8, T = 8, B = 22;
    var all = [];
    frames.forEach(function (f) { if (f.quantiles) { QUANTILES.forEach(function (q) { if (f.quantiles[q] !== null) { all.push(f.quantiles[q]); } }); } });
    if (!all.length) { $('dist').innerHTML = '<span class="empty">no quantiles stored</span>'; return; }
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    if (hi === lo) { hi = lo + 1; }
    var x = function (i) { return L + i / (QUANTILES.length - 1) * (W - L - R); };
    var y = function (v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); };
    var line = function (f, color, width) {
      if (!f || !f.quantiles) { return ''; }
      var d = QUANTILES.map(function (q, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(f.quantiles[q]).toFixed(1); }).join('');
      return '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="' + width + '"/>';
    };
    var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="quantiles">';
    out += '<text x="2" y="' + (T + 8) + '">' + esc(fmt(hi)) + '</text><text x="2" y="' + (H - B) + '">' + esc(fmt(lo)) + '</text>';
    out += '<text x="' + L + '" y="' + (H - 6) + '">p1</text><text x="' + (W - R) + '" y="' + (H - 6) + '" text-anchor="end">p99</text>';
    var first = frames.filter(function (f) { return f.quantiles; })[0];
    out += line(first, 'var(--line)', 2) + line(frames[current], 'var(--accent)', 2.5);
    $('dist').innerHTML = out + '</svg>';
  }

  function top(f) {
    if (!f.top_values) { $('top').innerHTML = '<span class="empty">none stored for this frame</span>'; return; }
    $('top').innerHTML = '<table>' + f.top_values.map(function (t) {
      return '<tr><td>' + esc(t.value) + '</td><td>' + (t.share * 100).toFixed(1) + '%</td></tr>';
    }).join('') + '</table>';
  }

  function facts(f) {
    var rows = [['frame', f.date === f.end ? f.date : f.date + ' to ' + f.end], ['versions', f.versions], ['source', f.form]];
    if (f.gap) { rows.push(['column', 'missing (a gap)']); } else {
      present.forEach(function (s) { rows.push([s, fmt(value(f, s))]); });
    }
    var html = rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td></tr>'; }).join('');
    if (f.change_point) { html += '<tr><td class="flag">change point</td><td class="flag">' + esc((f.changes || []).join(', ')) + '</td></tr>'; }
    $('facts').innerHTML = html;
  }

  function show(i) {
    current = i;
    $('slider').value = String(i);
    var f = frames[i];
    $('when').textContent = f.date + (f.change_point ? ' (change)' : '') + (f.gap ? ' (gap)' : '');
    chart(); dist(); top(f); facts(f);
  }

  function stop() { if (timer !== null) { clearInterval(timer); timer = null; } $('play').textContent = 'Play'; }
  $('play').addEventListener('click', function () {
    if (timer !== null) { stop(); return; }
    if (current >= frames.length - 1) { show(0); }
    $('play').textContent = 'Pause';
    timer = setInterval(function () {
      if (current >= frames.length - 1) { stop(); return; }
      show(current + 1);
    }, 450);
  });
  $('slider').addEventListener('input', function () { stop(); show(Number($('slider').value)); });
  $('stat').addEventListener('change', function () { stat = $('stat').value; chart(); });
  show(0);
})();
</script>
</body>
</html>
"""
