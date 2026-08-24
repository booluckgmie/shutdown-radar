(function () {
  "use strict";

  var CAUSES = ["conflict", "disaster", "shutdown", "unexplained"];
  var CAUSE_LABEL = { conflict: "Conflict", disaster: "Disaster", shutdown: "Shutdown", unexplained: "Unexplained" };
  var CAUSE_PRIORITY = { conflict: 0, shutdown: 1, disaster: 2, unexplained: 3 };
  var CONF_RANK = { low: 0, medium: 1, high: 2 };

  function causeColor(cause) {
    return getComputedStyle(document.documentElement).getPropertyValue("--cause-" + cause).trim();
  }

  // ---------- theme ----------
  var themeBtn = document.getElementById("themeToggle");
  function applyTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
  }
  (function initTheme() {
    try {
      var saved = localStorage.getItem("radar-theme");
      if (saved) applyTheme(saved);
    } catch (e) { /* localStorage unavailable, fall back to system theme */ }
  })();
  themeBtn.addEventListener("click", function () {
    var current = document.documentElement.getAttribute("data-theme");
    var next = current === "dark" ? "light" : (current === "light" ? null : "dark");
    applyTheme(next);
    try { localStorage.setItem("radar-theme", next || ""); } catch (e) {}
  });

  // ---------- provenance banner ----------
  (function banner() {
    var el = document.getElementById("provenanceBanner");
    // Substring match, not an end-anchor: semantic-layer seed records look
    // like "Serper+Groq (seed; Reuters, AP)" — the marker isn't always the
    // last thing in the string. any() rather than every(): if even one
    // record in the DB is synthetic (e.g. a `demo` run on top of a prior
    // `fetch`), that's not "live pipeline data" any more.
    var isSeed = DATA.events.some(function (e) { return e.source_name && e.source_name.indexOf("(seed") !== -1; });
    if (isSeed) {
      el.innerHTML = "<strong>Synthetic demo data.</strong> This sandbox has no outbound network access, so the map below is seeded from realistic-but-fabricated events (src/seed_demo_data.py) rather than live IODA/GDACS/ACLED/#KeepItOn feeds. Run <code>python main.py all</code> with network access and API keys configured (see README/.env.example) to replace this with real pipeline output — the scheduled GitHub Actions workflow does exactly that.";
    } else {
      var sources = Array.from(new Set(DATA.events.map(function (e) { return e.source_name; }))).sort();
      el.innerHTML = "<strong>Live pipeline data.</strong> " + DATA.meta.total_events + " events from " + sources.length + " source feeds. Generated " + fmtDateTime(DATA.generated_at) + ".";
    }
  })();

  // ---------- helpers ----------
  function parseDate(s) { return s ? new Date(s) : null; }
  function fmtDateTime(s) { var d = parseDate(s); return d ? d.toISOString().slice(0, 16).replace("T", " ") + " UTC" : "–"; }
  function fmtDate(s) { var d = parseDate(s); return d ? d.toISOString().slice(0, 10) : "–"; }
  function fmtHours(h) { if (h == null) return "–"; if (h < 1) return (h * 60).toFixed(0) + "m"; if (h < 48) return h.toFixed(1) + "h"; return (h / 24).toFixed(1) + "d"; }
  function fmtNum(n) { return n.toLocaleString("en-US"); }
  function isoWeekKey(d) {
    var date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    var day = (date.getUTCDay() + 6) % 7; // Mon=0
    date.setUTCDate(date.getUTCDate() - day);
    return date.toISOString().slice(0, 10);
  }

  // ---------- filter state ----------
  var state = { causes: new Set(CAUSES), rangeDays: 90, minConf: "low", sourceOnly: "all", region: "all", search: "" };

  function withinRange(event) {
    if (state.rangeDays === "all") return true;
    var start = parseDate(event.timestamp_start);
    if (!start) return false;
    var cutoff = new Date(Date.now() - state.rangeDays * 86400000);
    return start >= cutoff;
  }

  function matchesSearch(event) {
    if (!state.search) return true;
    var q = state.search;
    return [event.country, event.region_name, event.cause_subtype, event.source_name, CAUSE_LABEL[event.cause]]
      .some(function (f) { return f && String(f).toLowerCase().indexOf(q) !== -1; });
  }

  function applyFilters() {
    return DATA.events.filter(function (e) {
      if (!state.causes.has(e.cause)) return false;
      if (CONF_RANK[e.confidence] < CONF_RANK[state.minConf]) return false;
      if (state.sourceOnly === "structured" && e.source_type !== "structured") return false;
      if (state.region !== "all" && (!e.regions || e.regions.indexOf(state.region) === -1)) return false;
      if (!matchesSearch(e)) return false;
      if (!withinRange(e)) return false;
      return true;
    });
  }

  // ---------- tooltip ----------
  var tooltipEl = document.getElementById("tooltip");
  function showTooltip(html, x, y) {
    tooltipEl.innerHTML = html;
    tooltipEl.style.display = "block";
    var pad = 14;
    var w = tooltipEl.offsetWidth, h = tooltipEl.offsetHeight;
    var left = Math.min(x + pad, window.innerWidth - w - pad);
    var top = Math.min(y + pad, window.innerHeight - h - pad);
    tooltipEl.style.left = left + "px";
    tooltipEl.style.top = top + "px";
  }
  function hideTooltip() { tooltipEl.style.display = "none"; }

  // ---------- map ----------
  // Leaflet loads from a CDN; if that's blocked (offline viewing, locked-down
  // network) the rest of the dashboard must still work, so this is fully
  // guarded and every renderMap call becomes a no-op rather than a hard crash.
  var map = null, markerLayer = null;
  try {
    if (typeof L !== "undefined") {
      map = L.map("map", { scrollWheelZoom: true, worldCopyJump: true }).setView([15, 10], 2);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
        maxZoom: 18,
      }).addTo(map);
      markerLayer = L.layerGroup().addTo(map);
    } else {
      throw new Error("Leaflet not loaded");
    }
  } catch (e) {
    var mapEl = document.getElementById("map");
    mapEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:12.5px;padding:20px;text-align:center">Map basemap unavailable offline (Leaflet/OpenStreetMap tiles need network access). Every other panel below still reflects the live filters.</div>';
  }

  function radiusScale(downtimeHours, count) {
    var v = downtimeHours > 0 ? downtimeHours : count * 6;
    return Math.max(7, Math.min(42, 6 + Math.sqrt(v) * 2.4));
  }

  function renderMap(filtered) {
    if (!markerLayer) return;
    markerLayer.clearLayers();
    var byCountry = {};
    filtered.forEach(function (e) {
      var key = e.country || "Unknown";
      if (!byCountry[key]) byCountry[key] = { events: [], latSum: 0, lonSum: 0 };
      var g = byCountry[key];
      g.events.push(e);
      g.latSum += e.lat; g.lonSum += e.lon;
    });

    Object.keys(byCountry).forEach(function (country) {
      var g = byCountry[country];
      var n = g.events.length;
      var lat = g.latSum / n, lon = g.lonSum / n;

      var counts = {}, downtime = 0, mostRecent = null, resolvedHours = [];
      g.events.forEach(function (e) {
        counts[e.cause] = (counts[e.cause] || 0) + 1;
        if (e.duration_hours) downtime += e.duration_hours;
        if (e.timestamp_end && e.duration_hours) resolvedHours.push(e.duration_hours);
        if (!mostRecent || e.timestamp_start > mostRecent) mostRecent = e.timestamp_start;
      });
      var dominant = CAUSES.slice().sort(function (a, b) {
        return (counts[b] || 0) - (counts[a] || 0) || CAUSE_PRIORITY[a] - CAUSE_PRIORITY[b];
      })[0];
      var avgRecovery = resolvedHours.length ? resolvedHours.reduce(function (a, b) { return a + b; }, 0) / resolvedHours.length : null;

      var marker = L.circleMarker([lat, lon], {
        radius: radiusScale(downtime, n),
        color: causeColor(dominant),
        weight: 2,
        fillColor: causeColor(dominant),
        fillOpacity: 0.55,
      }).addTo(markerLayer);

      var breakdown = CAUSES.filter(function (c) { return counts[c]; })
        .map(function (c) { return '<div class="pop-row"><span>' + CAUSE_LABEL[c] + '</span><b>' + counts[c] + "</b></div>"; })
        .join("");
      var popupHtml = '<div class="pop-title">' + escapeHtml(country) + "</div>" +
        '<div class="pop-row"><span>Events in view</span><b>' + n + "</b></div>" +
        '<div class="pop-row"><span>Cumulative downtime</span><b>' + fmtHours(downtime) + "</b></div>" +
        '<div class="pop-row"><span>Avg recovery time</span><b>' + fmtHours(avgRecovery) + "</b></div>" +
        '<div class="pop-row"><span>Most recent</span><b>' + fmtDate(mostRecent) + "</b></div><hr style='border:none;border-top:1px solid var(--border);margin:6px 0'/>" + breakdown;
      marker.bindPopup(popupHtml);
    });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // ---------- SVG chart helpers ----------
  var SVG_NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function clearSvg(svg) { while (svg.firstChild) svg.removeChild(svg.firstChild); }
  function chartWidth(svg) { return svg.parentElement.clientWidth - 32; }

  // ---------- cause breakdown chart (horizontal bars) ----------
  function renderCauseChart(filtered) {
    var svg = document.getElementById("causeChart");
    clearSvg(svg);
    var w = chartWidth(svg), h = 210;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);

    var counts = {}; CAUSES.forEach(function (c) { counts[c] = 0; });
    filtered.forEach(function (e) { counts[e.cause] = (counts[e.cause] || 0) + 1; });
    var total = filtered.length || 1;
    var max = Math.max.apply(null, CAUSES.map(function (c) { return counts[c]; })) || 1;

    var rowH = h / CAUSES.length;
    var labelW = 92, valueW = 92, chartAreaW = w - labelW - valueW;

    CAUSES.forEach(function (cause, i) {
      var y = i * rowH + rowH / 2;
      var barLen = (counts[cause] / max) * chartAreaW;
      var label = svgEl("text", { x: 0, y: y + 4, "font-weight": 600 });
      label.textContent = CAUSE_LABEL[cause];
      label.setAttribute("fill", "var(--text-primary)");
      svg.appendChild(label);

      var barY = y - 9;
      var track = svgEl("rect", { x: labelW, y: barY, width: chartAreaW, height: 18, rx: 4, fill: "var(--grid)" });
      svg.appendChild(track);
      var bar = svgEl("rect", {
        x: labelW, y: barY, width: Math.max(2, barLen), height: 18, rx: 4,
        fill: causeColor(cause), class: "bar-hit",
      });
      svg.appendChild(bar);

      var pct = ((counts[cause] / total) * 100).toFixed(1);
      var valLabel = svgEl("text", { x: labelW + Math.max(2, barLen) + 8, y: y + 4, class: "bar-label" });
      valLabel.textContent = fmtNum(counts[cause]) + " (" + pct + "%)";
      svg.appendChild(valLabel);

      var hit = svgEl("rect", { x: labelW, y: barY - 4, width: chartAreaW, height: 26, fill: "transparent", class: "bar-hit" });
      hit.addEventListener("pointermove", function (ev) {
        showTooltip('<div>' + CAUSE_LABEL[cause] + '</div><div class="t-val">' + fmtNum(counts[cause]) + " events (" + pct + "%)</div>", ev.clientX, ev.clientY);
      });
      hit.addEventListener("pointerleave", hideTooltip);
      svg.appendChild(hit);
    });
  }

  // ---------- avg recovery duration chart ----------
  function renderDurationChart(filtered) {
    var svg = document.getElementById("durationChart");
    clearSvg(svg);
    var w = chartWidth(svg), h = 190;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);

    var sums = {}, ns = {};
    CAUSES.forEach(function (c) { sums[c] = 0; ns[c] = 0; });
    filtered.forEach(function (e) {
      if (e.timestamp_end && e.duration_hours != null) { sums[e.cause] += e.duration_hours; ns[e.cause] += 1; }
    });
    var avg = {}; CAUSES.forEach(function (c) { avg[c] = ns[c] ? sums[c] / ns[c] : null; });
    var max = Math.max.apply(null, CAUSES.map(function (c) { return avg[c] || 0; })) || 1;

    var rowH = h / CAUSES.length;
    var labelW = 92, valueW = 110, chartAreaW = w - labelW - valueW;

    CAUSES.forEach(function (cause, i) {
      var y = i * rowH + rowH / 2;
      var val = avg[cause];
      var barLen = val ? (val / max) * chartAreaW : 0;
      var label = svgEl("text", { x: 0, y: y + 4, "font-weight": 600 });
      label.textContent = CAUSE_LABEL[cause];
      svg.appendChild(label);

      var barY = y - 8;
      svg.appendChild(svgEl("rect", { x: labelW, y: barY, width: chartAreaW, height: 16, rx: 4, fill: "var(--grid)" }));
      if (val) {
        var bar = svgEl("rect", { x: labelW, y: barY, width: Math.max(2, barLen), height: 16, rx: 4, fill: causeColor(cause) });
        svg.appendChild(bar);
      }
      var valLabel = svgEl("text", { x: labelW + Math.max(2, barLen) + 8, y: y + 4, class: "bar-label" });
      valLabel.textContent = val ? fmtHours(val) + " (n=" + ns[cause] + ")" : "no resolved events";
      svg.appendChild(valLabel);
    });
  }

  // ---------- weekly timeline (stacked bars) ----------
  function renderTimeline(filtered) {
    var svg = document.getElementById("timelineChart");
    clearSvg(svg);
    var w = chartWidth(svg), h = 220;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);

    var buckets = {};
    filtered.forEach(function (e) {
      var d = parseDate(e.timestamp_start);
      if (!d) return;
      var wk = isoWeekKey(d);
      if (!buckets[wk]) buckets[wk] = { conflict: 0, disaster: 0, shutdown: 0, unexplained: 0 };
      buckets[wk][e.cause]++;
    });
    var weeks = Object.keys(buckets).sort();
    if (weeks.length === 0) {
      svg.appendChild(svgEl("text", { x: 10, y: 20 })).textContent = "No events in the current filter.";
      return;
    }
    var maxTotal = 0;
    weeks.forEach(function (wk) {
      var t = CAUSES.reduce(function (s, c) { return s + buckets[wk][c]; }, 0);
      maxTotal = Math.max(maxTotal, t);
    });

    var padL = 30, padB = 22, padT = 10;
    var chartH = h - padB - padT, chartW = w - padL - 10;
    var slot = chartW / weeks.length;
    var barW = Math.max(3, Math.min(22, slot - 4));

    // gridlines (0, 25%, 50%, 75%, 100% of maxTotal)
    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var y = padT + chartH * (1 - f);
      svg.appendChild(svgEl("line", { x1: padL, x2: w - 10, y1: y, y2: y, class: "grid-line" }));
      var t = svgEl("text", { x: 2, y: y + 3 });
      t.textContent = Math.round(maxTotal * f);
      svg.appendChild(t);
    });

    weeks.forEach(function (wk, i) {
      var x = padL + i * slot + (slot - barW) / 2;
      var yCursor = padT + chartH;
      CAUSES.forEach(function (cause) {
        var v = buckets[wk][cause];
        if (!v) return;
        var segH = (v / maxTotal) * chartH;
        var y = yCursor - segH;
        var rect = svgEl("rect", {
          x: x, y: y, width: barW, height: Math.max(0, segH - 1.5), fill: causeColor(cause), rx: 2,
        });
        rect.addEventListener("pointermove", function (ev) {
          showTooltip('<div>' + wk + " — " + CAUSE_LABEL[cause] + '</div><div class="t-val">' + v + " events</div>", ev.clientX, ev.clientY);
        });
        rect.addEventListener("pointerleave", hideTooltip);
        svg.appendChild(rect);
        yCursor = y - 1.5;
      });
      if (i % Math.ceil(weeks.length / 8 || 1) === 0) {
        var lbl = svgEl("text", { x: x + barW / 2, y: h - 6, "text-anchor": "middle" });
        lbl.textContent = wk.slice(5);
        svg.appendChild(lbl);
      }
    });
    svg.appendChild(svgEl("line", { x1: padL, x2: w - 10, y1: padT + chartH, y2: padT + chartH, class: "axis-line" }));
  }

  // ---------- resilience / fragility ranking table ----------
  function renderResilienceTable(filtered) {
    var wrap = document.getElementById("resilienceTable");
    var byCountry = {};
    filtered.forEach(function (e) {
      var key = e.country || "Unknown";
      if (!byCountry[key]) byCountry[key] = { n: 0, downtime: 0, counts: {} };
      var g = byCountry[key];
      g.n++;
      g.downtime += e.duration_hours || 0;
      g.counts[e.cause] = (g.counts[e.cause] || 0) + 1;
    });
    var rows = Object.keys(byCountry).map(function (c) { return Object.assign({ country: c }, byCountry[c]); });
    rows.sort(function (a, b) { return b.downtime - a.downtime; });
    rows = rows.slice(0, 10);

    var html = '<table class="data-table"><thead><tr><th>Country</th><th>Events</th><th>Downtime</th><th>Cause mix</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var total = r.n || 1;
      var pips = CAUSES.map(function (c) {
        var cnt = r.counts[c] || 0;
        if (!cnt) return "";
        var width = Math.max(6, (cnt / total) * 60);
        return '<div class="pip" style="background:' + causeColor(c) + ";width:" + width + 'px" title="' + CAUSE_LABEL[c] + ": " + cnt + '"></div>';
      }).join("");
      html += "<tr><td>" + escapeHtml(r.country) + "</td><td>" + r.n + "</td><td>" + fmtHours(r.downtime) + '</td><td><div class="causepips">' + pips + "</div></td></tr>";
    });
    html += "</tbody></table>";
    if (rows.length === 0) html = '<p style="color:var(--text-muted)">No events in the current filter.</p>';
    wrap.innerHTML = html;
  }

  // ---------- KPI row ----------
  var KPI_ICONS = {
    events: '<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.8 1c0 1.7-2.3 1.7-2.3 3.5"/><path d="M12 17h.01"/>',
    shield: '<path d="M12 3l7 3v6c0 5-3 8-7 9-4-1-7-4-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
  };
  function kpiIcon(name) {
    return '<span class="icon-badge"><svg class="ic" viewBox="0 0 24 24">' + KPI_ICONS[name] + "</svg></span>";
  }

  function renderKpis(filtered) {
    var el = document.getElementById("kpiRow");
    var countries = new Set(filtered.map(function (e) { return e.country; }));
    var unexplainedPct = filtered.length ? (filtered.filter(function (e) { return e.cause === "unexplained"; }).length / filtered.length * 100) : 0;
    var totalDowntime = filtered.reduce(function (s, e) { return s + (e.duration_hours || 0); }, 0);
    var highConfPct = filtered.length ? (filtered.filter(function (e) { return e.confidence === "high"; }).length / filtered.length * 100) : 0;

    var tiles = [
      { label: "Events in view", value: fmtNum(filtered.length), icon: "events" },
      { label: "Countries affected", value: fmtNum(countries.size), icon: "globe" },
      { label: "Cumulative downtime", value: fmtHours(totalDowntime), icon: "clock" },
      { label: "Unexplained share", value: unexplainedPct.toFixed(0) + "%", icon: "question" },
      { label: "High-confidence share", value: highConfPct.toFixed(0) + "%", icon: "shield" },
    ];
    el.innerHTML = tiles.map(function (t) {
      return '<div class="card kpi"><div class="kpi-top"><span class="label">' + t.label + "</span>" + kpiIcon(t.icon) +
        '</div><div class="value">' + t.value + "</div></div>";
    }).join("");
  }

  // ---------- insights ----------
  function avgDuration(events, cause) {
    var vals = events.filter(function (e) { return e.cause === cause && e.timestamp_end && e.duration_hours != null; }).map(function (e) { return e.duration_hours; });
    if (!vals.length) return null;
    return vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
  }

  function renderInsights(filtered) {
    var list = document.getElementById("insightsList");
    var items = [];

    if (filtered.length === 0) {
      list.innerHTML = "<li>No events match the current filters.</li>";
      return;
    }

    var byCountry = {};
    filtered.forEach(function (e) {
      byCountry[e.country] = byCountry[e.country] || { n: 0, downtime: 0 };
      byCountry[e.country].n++;
      byCountry[e.country].downtime += e.duration_hours || 0;
    });
    var topCountry = Object.keys(byCountry).sort(function (a, b) { return byCountry[b].downtime - byCountry[a].downtime; })[0];
    if (topCountry) {
      items.push('<span class="tag">fragility</span><b>' + escapeHtml(topCountry) + "</b> recorded the most cumulative downtime in view: " +
        fmtNum(byCountry[topCountry].n) + " events totaling " + fmtHours(byCountry[topCountry].downtime) + ".");
    }

    var confDur = avgDuration(filtered, "conflict"), disDur = avgDuration(filtered, "disaster"), sdDur = avgDuration(filtered, "shutdown");
    if (confDur != null && disDur != null) {
      var longer = confDur > disDur ? "longer" : "shorter";
      items.push('<span class="tag">duration</span>Conflict-attributed outages average <b>' + fmtHours(confDur) + "</b> vs <b>" + fmtHours(disDur) +
        "</b> for disasters in this view — conflict outages run " + longer + " on average, consistent with sustained infrastructure damage vs. episodic weather/seismic disruption.");
    }
    if (sdDur != null && (confDur != null || disDur != null)) {
      var other = confDur != null ? confDur : disDur;
      if (sdDur < other) {
        items.push('<span class="tag">signature</span>Shutdown-attributed outages average <b>' + fmtHours(sdDur) +
          "</b> — markedly shorter than conflict/disaster outages, consistent with a deliberate, centrally-reversible order rather than physical damage that takes time to repair.");
      }
    }

    var unexplained = filtered.filter(function (e) { return e.cause === "unexplained"; });
    if (filtered.length) {
      var pct = (unexplained.length / filtered.length * 100).toFixed(0);
      items.push('<span class="tag">coverage</span><b>' + pct + "%</b> of events in view have no matched structured cause (GDACS/ACLED/#KeepItOn) within the ±72h attribution window — candidates for the Phase 3 semantic gap-filling layer.");
    }

    var highConfShutdown = filtered.filter(function (e) { return e.cause === "shutdown" && e.confidence === "high"; }).length;
    var totalShutdown = filtered.filter(function (e) { return e.cause === "shutdown"; }).length;
    if (totalShutdown > 0) {
      items.push('<span class="tag">attribution</span>' + highConfShutdown + " of " + totalShutdown +
        " shutdown-attributed outages matched a #KeepItOn/ACLED record within 24h (high confidence) — the tighter the time match, the stronger the evidence the outage was ordered rather than incidental.");
    }

    var mostRecent = filtered.reduce(function (m, e) { return (!m || e.timestamp_start > m.timestamp_start) ? e : m; }, null);
    if (mostRecent) {
      items.push('<span class="tag">latest</span>Most recent event in view: <b>' + escapeHtml(mostRecent.country) + "</b> (" + CAUSE_LABEL[mostRecent.cause] +
        ") starting " + fmtDate(mostRecent.timestamp_start) + ".");
    }

    list.innerHTML = items.map(function (h) { return "<li>" + h + "</li>"; }).join("");
  }

  // ---------- raw table ----------
  function renderRawTable(filtered) {
    var wrap = document.getElementById("rawTableWrap");
    var rows = filtered.slice().sort(function (a, b) { return b.timestamp_start.localeCompare(a.timestamp_start); }).slice(0, 500);
    var html = '<table class="data-table"><thead><tr><th>Start</th><th>Country</th><th>Cause</th><th>Subtype</th><th>Confidence</th><th>Source</th><th>Duration</th></tr></thead><tbody>';
    rows.forEach(function (e) {
      html += "<tr><td>" + fmtDate(e.timestamp_start) + "</td><td>" + escapeHtml(e.country) + "</td><td>" + CAUSE_LABEL[e.cause] +
        "</td><td>" + escapeHtml(e.cause_subtype || "–") + "</td><td>" + e.confidence + "</td><td>" + escapeHtml(e.source_name) +
        "</td><td>" + fmtHours(e.duration_hours) + "</td></tr>";
    });
    html += "</tbody></table>";
    if (filtered.length > 500) html += '<p class="card-note">Showing 500 most recent of ' + fmtNum(filtered.length) + " filtered events.</p>";
    wrap.innerHTML = html;
  }

  // ---------- data pipeline & sources ----------
  var PHASE_STEPS = [
    { phase: "Phase 1", title: "Detect", desc: "IODA, Cloudflare Radar, RIPE Atlas flag connectivity loss — no cause attached yet." },
    { phase: "Phase 2", title: "Attribute", desc: "Join each outage to GDACS/ACLED/#KeepItOn records within ±72h, same country. Closest match wins; confidence tracks the time gap." },
    { phase: "Phase 3", title: "Semantic gap-fill", desc: "Still unexplained? News search (Serper) + LLM extraction (Groq), geocoded via Nominatim. Capped at low/medium confidence." },
    { phase: "Phase 4", title: "Visualize", desc: "Export to this dashboard — every filter re-aggregates the map, charts, and insights client-side." },
  ];

  function renderPipelineSection() {
    var flow = document.getElementById("pipelineFlow");
    flow.innerHTML = PHASE_STEPS.map(function (s, i) {
      var arrow = i < PHASE_STEPS.length - 1 ? '<span class="pipeline-arrow">→</span>' : "";
      return '<div class="pipeline-step"><div class="phase">' + s.phase + '</div><div class="title">' + s.title +
        '</div><div class="desc">' + s.desc + "</div></div>" + arrow;
    }).join("");

    var catalog = (DATA.meta && DATA.meta.sources_catalog) || [];
    var table = document.getElementById("sourcesTable");
    var html = "<thead><tr><th>Source</th><th>Role</th><th>Real-time</th><th>Access</th><th>Granularity</th><th>Status</th><th>Contributed</th></tr></thead><tbody>";
    catalog.forEach(function (s) {
      var pill;
      if (s.configured === null) pill = '<span class="status-pill on"><span class="dot"></span>No key needed</span>';
      else if (s.configured) pill = '<span class="status-pill on"><span class="dot"></span>Configured</span>';
      else pill = '<span class="status-pill off"><span class="dot"></span>Not configured</span>';
      html += "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.category) + "</td><td>" + escapeHtml(s.realtime) +
        "</td><td>" + escapeHtml(s.access) + "</td><td>" + escapeHtml(s.granularity) + "</td><td>" + pill +
        "</td><td>" + escapeHtml(s.contributed_label) + "</td></tr>";
    });
    html += "</tbody>";
    table.innerHTML = html;
  }

  // ---------- render orchestration ----------
  function renderAll() {
    var filtered = applyFilters();
    renderKpis(filtered);
    renderMap(filtered);
    renderCauseChart(filtered);
    renderDurationChart(filtered);
    renderTimeline(filtered);
    renderResilienceTable(filtered);
    renderInsights(filtered);
    renderRawTable(filtered);

    if (map && state.search) {
      var countries = Array.from(new Set(filtered.map(function (e) { return e.country; })));
      if (countries.length === 1 && filtered.length) {
        var match = filtered[0];
        map.flyTo([match.lat, match.lon], Math.max(map.getZoom(), 5), { duration: 0.6 });
      }
    }
  }

  // ---------- wire up filter controls ----------
  document.querySelectorAll(".chip").forEach(function (chip) {
    var cause = chip.getAttribute("data-cause");
    var checkbox = chip.querySelector("input");
    // Clicking anywhere in a <label> wrapping its <input> already toggles the
    // checkbox natively (whether the click lands on the text, the swatch, or
    // the input itself) — listen for the resulting "change", don't also
    // flip it by hand, or every click cancels itself out.
    checkbox.addEventListener("change", function () {
      if (checkbox.checked) { state.causes.add(cause); chip.classList.remove("off"); }
      else { state.causes.delete(cause); chip.classList.add("off"); }
      renderAll();
    });
  });
  document.getElementById("rangeSelect").addEventListener("change", function (e) {
    state.rangeDays = e.target.value === "all" ? "all" : parseInt(e.target.value, 10);
    renderAll();
  });
  document.getElementById("confSelect").addEventListener("change", function (e) {
    state.minConf = e.target.value;
    renderAll();
  });
  document.getElementById("sourceSelect").addEventListener("change", function (e) {
    state.sourceOnly = e.target.value;
    renderAll();
  });

  var regionSelectEl = document.getElementById("regionSelect");
  (function populateRegions() {
    var options = (DATA.meta && DATA.meta.region_options) || [{ value: "all", label: "All regions" }];
    regionSelectEl.innerHTML = options.map(function (o) {
      return '<option value="' + escapeHtml(o.value) + '">' + escapeHtml(o.label) + "</option>";
    }).join("");
  })();
  regionSelectEl.addEventListener("change", function (e) {
    state.region = e.target.value;
    renderAll();
  });

  var searchBoxEl = document.getElementById("searchBox");
  var searchTimer;
  searchBoxEl.addEventListener("input", function (e) {
    clearTimeout(searchTimer);
    var val = e.target.value;
    searchTimer = setTimeout(function () {
      state.search = val.trim().toLowerCase();
      renderAll();
    }, 200);
  });

  var mapResetBtn = document.getElementById("mapResetBtn");
  mapResetBtn.addEventListener("click", function () {
    if (map) map.setView([15, 10], 2);
  });

  document.getElementById("resetBtn").addEventListener("click", function () {
    state = { causes: new Set(CAUSES), rangeDays: 90, minConf: "low", sourceOnly: "all", region: "all", search: "" };
    document.querySelectorAll(".chip").forEach(function (chip) {
      chip.querySelector("input").checked = true;
      chip.classList.remove("off");
    });
    document.getElementById("rangeSelect").value = "90";
    document.getElementById("confSelect").value = "low";
    document.getElementById("sourceSelect").value = "all";
    regionSelectEl.value = "all";
    searchBoxEl.value = "";
    if (map) map.setView([15, 10], 2);
    renderAll();
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderAll, 150);
  });

  // ---------- hourly auto-refresh ----------
  // The GitHub Actions workflow (.github/workflows/refresh.yml) regenerates
  // dist/data.json + dist/dashboard.html hourly with live pipeline output.
  // When this page is served over http(s) (GitHub Pages, any static host —
  // NOT a plain file:// open, which fetch() can't reach) it polls the
  // sibling data.json periodically and hot-swaps in newer data without a
  // manual reload. Silently gives up on failure; the page is fully usable
  // on whatever data it loaded with either way.
  var AUTO_REFRESH_POLL_MS = 15 * 60 * 1000; // check every 15min; source data updates hourly
  var liveStatusEl = document.getElementById("liveStatus");
  var isFileProtocol = window.location.protocol === "file:";

  function setLiveStatus(text) { if (liveStatusEl) liveStatusEl.textContent = text; }

  function checkForUpdate() {
    fetch("data.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (fresh) {
        if (fresh && fresh.generated_at && fresh.generated_at > DATA.generated_at) {
          DATA = fresh;
          document.getElementById("generatedAt").textContent = "Generated " + fmtDateTime(DATA.generated_at);
          renderPipelineSection();
          renderAll();
          setLiveStatus("· updated " + fmtDateTime(DATA.generated_at));
        } else {
          setLiveStatus("· up to date (checked " + new Date().toISOString().slice(11, 16) + " UTC)");
        }
      })
      .catch(function () {
        setLiveStatus(isFileProtocol ? "" : "· auto-refresh unavailable");
      });
  }

  if (!isFileProtocol) {
    setLiveStatus("· auto-refreshing (data updates hourly)");
    setInterval(checkForUpdate, AUTO_REFRESH_POLL_MS);
  }

  // ---------- nav rail: scroll-spy + mobile toggle ----------
  var railEl = document.getElementById("rail");
  var railToggleEl = document.getElementById("railToggle");
  var railLinks = Array.prototype.slice.call(document.querySelectorAll("#railNav a"));
  var sections = railLinks
    .map(function (a) { return document.getElementById(a.getAttribute("data-target")); })
    .filter(Boolean);

  function setActiveLink(id) {
    railLinks.forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-target") === id);
    });
  }

  if ("IntersectionObserver" in window && sections.length) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) setActiveLink(entry.target.id);
        });
      },
      { rootMargin: "-72px 0px -70% 0px", threshold: 0 }
    );
    sections.forEach(function (s) { observer.observe(s); });
  }

  railLinks.forEach(function (a) {
    a.addEventListener("click", function () {
      setActiveLink(a.getAttribute("data-target"));
      railEl.classList.remove("open"); // close on mobile after navigating
    });
  });

  if (railToggleEl) {
    railToggleEl.addEventListener("click", function () {
      railEl.classList.toggle("open");
    });
  }

  renderPipelineSection();
  renderAll();
})();
