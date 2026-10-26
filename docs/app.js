/* Malaysia Flood Watch dashboard: reads data/latest.json, recent.json and analytics.json */
(() => {
  "use strict";

  const STATUS = {
    DANGER: { label: "Danger", bm: "Bahaya", rank: 4 },
    WARNING: { label: "Warning", bm: "Amaran", rank: 3 },
    ALERT: { label: "Alert", bm: "Waspada", rank: 2 },
    NORMAL: { label: "Normal", bm: "Normal", rank: 1 },
    UNKNOWN: { label: "No status", bm: "", rank: 0 },
  };
  const QUALITY_TEXT = {
    OK: "Reporting",
    OFFLINE: "Sensor offline",
    STALE: "Not updated recently",
    BAD_THRESHOLDS: "Thresholds unreliable",
    BAD_TIMESTAMP: "Invalid timestamp",
  };

  const state = { stations: [], byId: new Map(), series: {}, analytics: {}, summary: {}, markers: new Map(), tab: "attention", selected: null };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtM = (v) => (v == null ? "–" : `${Number(v).toFixed(2)} m`);
  const tz = { timeZone: "Asia/Kuala_Lumpur" };
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-MY", { ...tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "–");
  const ago = (iso) => {
    if (!iso) return "–";
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    if (h < 48) return `${h} h ago`;
    return `${Math.round(h / 24)} days ago`;
  };
  const isLive = (s) => s.quality === "OK";
  const statusOf = (s) => (isLive(s) ? s.status : "UNKNOWN");
  const titleCase = (t) => String(t || "").toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase()).replace(/\bSg\b/g, "Sg").replace(/\bDi\b/g, "di");
  const nice = (name) => (name === name.toUpperCase() ? titleCase(name) : name);

  /* ---------- Theme ---------- */
  function initTheme() {
    try {
      const saved = localStorage.getItem("fw-theme");
      if (saved) document.documentElement.dataset.theme = saved;
    } catch (_) { /* storage unavailable */ }
    $("#themeToggle").addEventListener("click", () => {
      const dark = document.documentElement.dataset.theme
        ? document.documentElement.dataset.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = dark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("fw-theme", next); } catch (_) {}
      applyTheme();
    });
  }
  const isDark = () => (document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

  /* ---------- Map ---------- */
  let map, tiles;
  const tileUrl = () => "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

  function initMap() {
    map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 5 });
    map.fitBounds([[0.85, 99.6], [7.4, 119.3]]);  // Peninsular Malaysia to Sabah
    tiles = L.tileLayer(tileUrl(), {
      maxZoom: 14,
      className: "basemap",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · Data: JPS',
    }).addTo(map);
  }

  function markerStyle(s) {
    const st = statusOf(s);
    const color = { DANGER: css("--danger"), WARNING: css("--warning"), ALERT: css("--alert"), NORMAL: css("--normal") }[st] || css("--nodata");
    const r = { DANGER: 9, WARNING: 8, ALERT: 7, NORMAL: 5 }[st] || 4;
    return {
      radius: r,
      color: s.location_source === "district" ? css("--text-2") : css("--surface"),
      weight: s.location_source === "district" ? 1.5 : 2,
      dashArray: s.location_source === "district" ? "3 2" : null,
      fillColor: color,
      fillOpacity: st === "UNKNOWN" ? 0.55 : 0.95,
    };
  }

  function drawMarkers() {
    state.markers.forEach((m) => m.remove());
    state.markers.clear();
    const visible = filtered().filter((s) => s.lat != null);
    // Draw calm stations first so alerts sit on top
    visible.sort((a, b) => STATUS[statusOf(a)].rank - STATUS[statusOf(b)].rank);
    for (const s of visible) {
      const m = L.circleMarker([s.lat, s.lon], markerStyle(s)).addTo(map);
      const st = statusOf(s);
      m.bindTooltip(
        `<div class="tip-name">${esc(nice(s.station_name))}</div>
         <div class="tip-meta">${esc(nice(s.district))}, ${esc(s.state)}</div>
         <div><span class="dot ${st}"></span> ${STATUS[st].label}${isLive(s) ? ` · ${fmtM(s.water_level)}` : ` · ${QUALITY_TEXT[s.quality]}`}</div>`,
        { direction: "top", offset: [0, -6] }
      );
      m.on("click", () => select(s.station_id));
      state.markers.set(s.station_id, m);
    }
  }

  function drawLegend() {
    $("#legend").innerHTML =
      ["DANGER", "WARNING", "ALERT", "NORMAL"].map((k) => `<span><i class="dot ${k}"></i>${STATUS[k].label} (${STATUS[k].bm})</span>`).join("") +
      `<span><i class="dot UNKNOWN"></i>No current data</span><span><i class="ring"></i>Approximate location (district centre)</span>`;
  }

  /* ---------- Filters ---------- */
  function filtered() {
    const st = $("#stateFilter").value;
    const status = $("#statusFilter").value;
    const q = $("#search").value.trim().toLowerCase();
    return state.stations.filter((s) => {
      if (st && s.state !== st) return false;
      const cur = statusOf(s);
      if (status === "attention" && !["ALERT", "WARNING", "DANGER"].includes(cur)) return false;
      if (status === "nodata" && cur !== "UNKNOWN") return false;
      if (status && !["attention", "nodata"].includes(status) && cur !== status) return false;
      if (q && !`${s.station_name} ${s.basin} ${s.sub_basin} ${s.district} ${s.station_id}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  function initFilters() {
    const states = [...new Set(state.stations.map((s) => s.state))].sort();
    $("#stateFilter").insertAdjacentHTML("beforeend", states.map((s) => `<option>${esc(s)}</option>`).join(""));
    ["#stateFilter", "#statusFilter"].forEach((id) => $(id).addEventListener("change", refresh));
    $("#search").addEventListener("input", refresh);
    document.querySelectorAll(".tab").forEach((t) =>
      t.addEventListener("click", () => {
        state.tab = t.dataset.tab;
        document.querySelectorAll(".tab").forEach((x) => { x.classList.toggle("active", x === t); x.setAttribute("aria-selected", x === t); });
        closeDetail();
        drawList();
      })
    );
  }

  function refresh() {
    drawMarkers();
    if (!state.selected) drawList();
    const st = $("#stateFilter").value;
    const pts = filtered().filter((s) => s.lat != null).map((s) => [s.lat, s.lon]);
    if (st && pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 10 });
  }

  /* ---------- Side list ---------- */
  function drawList() {
    const list = $("#stationList");
    let rows = filtered().filter(isLive);
    if (state.tab === "attention") {
      rows = rows.filter((s) => STATUS[s.status].rank >= 2)
        .sort((a, b) => STATUS[b.status].rank - STATUS[a.status].rank || (b.danger_ratio ?? 0) - (a.danger_ratio ?? 0));
    } else {
      rows = rows.filter((s) => s.trend === "RISING").sort((a, b) => (b.rate_m_per_h ?? 0) - (a.rate_m_per_h ?? 0));
    }
    $("#listCount").textContent = `${rows.length} station${rows.length === 1 ? "" : "s"}`;
    if (!rows.length) {
      list.innerHTML = `<li class="empty">${state.tab === "attention" ? "No stations at alert level or above for this filter." : "No rising stations yet. Trends appear once a few readings have been collected for a station."}</li>`;
      return;
    }
    list.innerHTML = rows.slice(0, 200).map((s) => {
      const right = state.tab === "rising" ? `+${(s.rate_m_per_h * 100).toFixed(0)} cm/h` : fmtM(s.water_level);
      const eta = s.hours_to_danger != null ? ` · danger in ~${s.hours_to_danger} h` : "";
      return `<li><button type="button" data-id="${esc(s.station_id)}">
        <span class="dot ${s.status}" aria-hidden="true"></span>
        <span class="name">${esc(nice(s.station_name))}</span>
        <span class="lvl">${right}</span>
        <span class="meta">${STATUS[s.status].label} · ${esc(nice(s.district))}, ${esc(s.state)}${eta}</span>
      </button></li>`;
    }).join("");
    list.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => select(b.dataset.id)));
  }

  /* ---------- Station detail ---------- */
  let detailChart;
  function select(id) {
    const s = state.byId.get(id);
    if (!s) return;
    state.selected = id;
    const st = statusOf(s);
    const rate = s.rate_m_per_h;
    const trendText = s.trend ? `${s.trend[0]}${s.trend.slice(1).toLowerCase()}${rate != null ? ` (${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(0)} cm/h)` : ""}` : "Not enough readings yet";
    const notes = [];
    if (s.quality !== "OK") notes.push(QUALITY_TEXT[s.quality] + (s.quality === "STALE" ? `: last reading ${ago(s.observed_at)}.` : "."));
    if (!s.thresholds_ok) notes.push("JPS thresholds for this station look like placeholders or are out of order, so no status is shown.");
    if (s.location_source === "district") notes.push("Map position is the district centre, as exact coordinates aren't published for this station.");
    if (s.hours_to_danger != null) notes.push(`At the current rate this river would reach danger level in about ${s.hours_to_danger} hours. This is a straight-line estimate, not a forecast.`);

    const d = $("#detail");
    d.innerHTML = `
      <button class="back-btn" type="button">← Back to list</button>
      <span class="badge"><i class="dot ${st}"></i>${STATUS[st].label}${STATUS[st].bm && st !== "NORMAL" ? ` · ${STATUS[st].bm}` : ""}</span>
      <h3 class="station-title">${esc(nice(s.station_name))}</h3>
      <p class="place">${esc(s.basin || s.sub_basin || "")}${s.basin ? " · " : ""}${esc(nice(s.district))}, ${esc(s.state)} · ID ${esc(s.station_id)}</p>
      <div class="big"><strong>${fmtM(s.water_level)}</strong><span>${fmtTime(s.observed_at)} (${ago(s.observed_at)})</span></div>
      <div class="facts">
        <div class="fact"><div class="k">Trend (last 3 h)</div><div class="v">${trendText}</div></div>
        <div class="fact"><div class="k">To danger level</div><div class="v">${s.danger != null && s.water_level != null && s.thresholds_ok ? (s.danger - s.water_level > 0 ? `${(s.danger - s.water_level).toFixed(2)} m below` : `${(s.water_level - s.danger).toFixed(2)} m above`) : "–"}</div></div>
      </div>
      <div class="chart-box"><canvas id="detailChart" aria-label="Water level over the last 48 hours"></canvas></div>
      <p class="empty-note" id="detailEmpty" hidden>History builds up as the pipeline runs every 30 minutes.</p>
      <table class="thresholds" aria-label="JPS thresholds">
        <tr><td><span class="dot NORMAL"></span> Normal</td><td>${fmtM(s.normal)}</td></tr>
        <tr><td><span class="dot ALERT"></span> Alert (Waspada)</td><td>${fmtM(s.alert)}</td></tr>
        <tr><td><span class="dot WARNING"></span> Warning (Amaran)</td><td>${fmtM(s.warning)}</td></tr>
        <tr><td><span class="dot DANGER"></span> Danger (Bahaya)</td><td>${fmtM(s.danger)}</td></tr>
      </table>
      ${notes.map((n) => `<p class="note">${esc(n)}</p>`).join("")}`;
    $("#stationList").hidden = true;
    d.hidden = false;
    d.querySelector(".back-btn").addEventListener("click", closeDetail);
    drawDetailChart(s);
    if (s.lat != null) {
      map.setView([s.lat, s.lon], Math.max(map.getZoom(), 10), { animate: true });
      state.markers.get(id)?.openTooltip();
    }
  }

  function closeDetail() {
    state.selected = null;
    $("#detail").hidden = true;
    $("#stationList").hidden = false;
    if (detailChart) { detailChart.destroy(); detailChart = null; }
    drawList();
  }

  function axisOptions() {
    const grid = css("--grid"), text = css("--text-2"), axis = css("--axis");
    return { grid: { color: grid, drawTicks: false }, border: { color: axis }, ticks: { color: text, padding: 6, font: { size: 11 } } };
  }

  function drawDetailChart(s) {
    if (detailChart) detailChart.destroy();
    const pts = (state.series[s.station_id] || []).map(([m, v]) => ({ x: m * 60000, y: v }));
    $("#detailEmpty").hidden = pts.length >= 2;
    if (pts.length < 2) { $("#detailChart").parentElement.hidden = true; return; }
    const ax = axisOptions();
    const x0 = pts[0].x, x1 = pts[pts.length - 1].x;
    const flat = (v) => (v == null ? [] : [{ x: x0, y: v }, { x: x1, y: v }]);
    const ds = [
      { label: "Water level", data: pts, borderColor: css("--accent"), backgroundColor: css("--accent"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.2 },
    ];
    if (s.thresholds_ok) {
      [["Alert", s.alert, "--alert"], ["Warning", s.warning, "--warning"], ["Danger", s.danger, "--danger"]].forEach(([l, v, c]) =>
        ds.push({ label: `${l} level`, data: flat(v), borderColor: css(c), borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 0 }));
    }
    detailChart = new Chart($("#detailChart"), {
      type: "line",
      data: { datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { color: css("--text-2"), boxWidth: 14, boxHeight: 2, font: { size: 11 } } },
          tooltip: {
            filter: (i) => i.datasetIndex === 0,
            callbacks: { title: (it) => fmtTime(new Date(it[0].parsed.x).toISOString()), label: (it) => ` ${it.parsed.y.toFixed(2)} m` },
          },
        },
        scales: {
          x: { type: "linear", ...ax, ticks: { ...ax.ticks, maxTicksLimit: 5, callback: (v) => new Date(v).toLocaleString("en-MY", { ...tz, hour: "2-digit", minute: "2-digit", hour12: false, day: "numeric", month: "short" }) } },
          y: { ...ax, title: { display: true, text: "metres", color: css("--text-3"), font: { size: 11 } } },
        },
      },
    });
  }

  /* ---------- Tiles ---------- */
  function drawTiles() {
    const sm = state.summary, c = sm.status_counts || {};
    const tile = (label, value, sub, dotCls, hot) =>
      `<div class="tile card${hot ? " is-hot" : ""}"><div class="label">${dotCls ? `<span class="dot ${dotCls}"></span>` : ""}${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
    $("#tiles").innerHTML =
      tile("Stations reporting", `${sm.reporting}<span style="font-size:16px;color:var(--text-3);font-weight:500"> / ${sm.stations}</span>`, `${sm.offline} offline · ${sm.stale} not updating`, "", false) +
      tile("Danger (Bahaya)", c.DANGER ?? 0, "at or above danger level", "DANGER", c.DANGER > 0) +
      tile("Warning (Amaran)", c.WARNING ?? 0, "at or above warning level", "WARNING", false) +
      tile("Alert (Waspada)", c.ALERT ?? 0, "at or above alert level", "ALERT", false) +
      tile("Rising fast", sm.rising_fast ?? 0, "rising 10 cm/h or more", "", false);
  }

  function drawUpdated() {
    const g = state.summary.generated_at;
    const old = Date.now() - new Date(g) > 2 * 3600 * 1000;
    const el = $("#updated");
    el.classList.toggle("is-old", old);
    el.innerHTML = `<span class="live-dot" aria-hidden="true"></span>Checked ${fmtTime(g)} (${ago(g)})`;
    el.title = old ? "The collector hasn't run for a while" : "Data refreshes every 30 minutes";
  }

  /* ---------- Analytics ---------- */
  let sortKey = "danger", sortDir = -1;
  function drawStateTable() {
    const cols = [
      ["state", "State", false], ["stations", "Stations", true], ["pct_reporting", "Reporting", true],
      ["danger", "Danger", true], ["warning", "Warning", true], ["alert", "Alert", true],
      ["stale", "Not updating", true], ["offline", "Offline", true], ["latest_reading", "Latest reading", false],
    ];
    const rows = [...(state.analytics.by_state || [])].sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      return (typeof x === "string" ? String(x).localeCompare(y) : (x ?? 0) - (y ?? 0)) * sortDir || String(a.state).localeCompare(b.state);
    });
    const cell = (r, k) => {
      const v = r[k];
      if (k === "state") return `<td>${esc(v)}</td>`;
      if (k === "pct_reporting") return `<td class="num"><span class="bar-cell">${v}%<span class="mini-bar"><i style="width:${v}%"></i></span></span></td>`;
      if (["danger", "warning", "alert"].includes(k)) return `<td class="num">${v ? `<span class="pill-count"><i class="dot ${k.toUpperCase()}"></i>${v}</span>` : `<span class="zero">0</span>`}</td>`;
      if (k === "latest_reading") {
        const stale = v && Date.now() - new Date(v) > 6 * 3600 * 1000;
        return `<td>${fmtTime(v)}${stale ? `<span class="stale-tag">${ago(v)}</span>` : ""}</td>`;
      }
      return `<td class="num">${v ? v : `<span class="zero">0</span>`}</td>`;
    };
    $("#stateTable").innerHTML =
      `<thead><tr>${cols.map(([k, l, n]) => `<th class="${n ? "num" : ""}" data-k="${k}" ${k === sortKey ? `aria-sort="${sortDir < 0 ? "descending" : "ascending"}"` : ""}>${l}${k === sortKey ? (sortDir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>` +
      `<tbody>${rows.map((r) => `<tr>${cols.map(([k]) => cell(r, k)).join("")}</tr>`).join("")}</tbody>`;
    $("#stateTable").querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.k;
      sortDir = sortKey === k ? -sortDir : (k === "state" ? 1 : -1);
      sortKey = k;
      drawStateTable();
    }));
  }

  let trendChart;
  function drawTrendChart() {
    const t = state.analytics.national_trend || [];
    $("#trendEmpty").hidden = t.length >= 3;
    $("#trendChart").parentElement.hidden = t.length < 3;
    if (trendChart) trendChart.destroy();
    if (t.length < 3) return;
    const ax = axisOptions();
    const line = (label, key, color) => ({ label, data: t.map((r) => r[key]), borderColor: css(color), backgroundColor: css(color), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, stepped: true });
    trendChart = new Chart($("#trendChart"), {
      type: "line",
      data: {
        labels: t.map((r) => r.run_at),
        datasets: [line("Danger", "danger", "--danger"), line("Warning", "warning", "--warning"), line("Alert", "alert", "--alert")],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { color: css("--text-2"), boxWidth: 14, boxHeight: 2, font: { size: 11 } } },
          tooltip: { callbacks: { title: (it) => fmtTime(it[0].label + ":00+08:00") } },
        },
        scales: {
          x: { ...ax, ticks: { ...ax.ticks, maxTicksLimit: 6, callback: (v, i) => fmtTime(t[i].run_at + ":00+08:00") } },
          y: { ...ax, beginAtZero: true, ticks: { ...ax.ticks, precision: 0 }, title: { display: true, text: "stations", color: css("--text-3"), font: { size: 11 } } },
        },
      },
    });
  }

  function drawQuality() {
    const q = Object.fromEntries((state.analytics.quality_summary || []).map((r) => [r.quality, r.stations]));
    const total = Object.values(q).reduce((a, b) => a + b, 0) || 1;
    const stalestStates = (state.analytics.by_state || [])
      .filter((r) => r.latest_reading && Date.now() - new Date(r.latest_reading) > 24 * 3600 * 1000)
      .map((r) => `${r.state} (last update ${ago(r.latest_reading)})`);
    const rows = [
      ["OK", "Reporting normally", "--normal", "Fresh reading with usable thresholds."],
      ["STALE", "Not updating", "--alert", stalestStates.length ? `Whole states not updating: ${stalestStates.join(", ")}.` : "Last reading is more than 6 hours old."],
      ["OFFLINE", "Sensor offline", "--nodata", "JPS reports −9999, meaning no valid reading."],
      ["BAD_THRESHOLDS", "Unreliable thresholds", "--warning", "Placeholder values (e.g. 310 m) or alert above danger."],
      ["BAD_TIMESTAMP", "Invalid timestamp", "--danger", "Reading dated in the future."],
    ].filter(([k]) => q[k]);
    $("#quality").innerHTML = rows.map(([k, l, c, d]) =>
      `<div class="quality-row"><span class="ql">${l}</span><span class="qv">${q[k]} <span style="color:var(--text-3);font-weight:400">(${Math.round((100 * q[k]) / total)}%)</span></span>
       <div class="qbar"><i style="width:${(100 * q[k]) / total}%;background:var(${c})"></i></div><span class="qd">${esc(d)}</span></div>`).join("");
  }

  function drawRank(sel, rows, valueFn, emptyText) {
    const el = $(sel);
    if (!rows || !rows.length) { el.innerHTML = `<li class="empty-note" style="grid-template-columns:1fr;cursor:default">${emptyText}</li>`; return; }
    el.innerHTML = rows.map((r) => `<li data-id="${esc(r.station_id)}"><span class="rn">${esc(nice(r.station_name))}<span class="rs">${esc(r.state)}</span></span><span class="rv">${valueFn(r)}</span></li>`).join("");
    el.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => { select(li.dataset.id); $(".workspace").scrollIntoView({ behavior: "smooth" }); }));
  }

  function drawAnalytics() {
    drawStateTable();
    drawTrendChart();
    drawQuality();
    drawRank("#closest", state.analytics.closest_to_danger, (r) => `<span class="badge"><i class="dot ${r.status}"></i>${Math.round(r.danger_ratio * 100)}%</span>`, "No data yet.");
    drawRank("#rise", state.analytics.biggest_rise_24h, (r) => `+${r.rise_m.toFixed(2)} m`, "Needs a few hours of collected readings.");
  }

  function applyTheme() {
    tiles?.setUrl(tileUrl());
    drawMarkers();
    drawAnalytics();
    if (state.selected) drawDetailChart(state.byId.get(state.selected));
  }

  /* ---------- Boot ---------- */
  async function load(name) {
    const r = await fetch(`data/${name}?t=${Math.floor(Date.now() / 300000)}`);
    if (!r.ok) throw new Error(`${name}: ${r.status}`);
    return r.json();
  }

  async function boot() {
    initTheme();
    try {
      const [latest, recent, analytics] = await Promise.all([load("latest.json"), load("recent.json"), load("analytics.json")]);
      state.summary = latest.summary;
      state.stations = latest.stations;
      state.stations.forEach((s) => state.byId.set(s.station_id, s));
      state.series = recent.series || {};
      state.analytics = analytics;
    } catch (e) {
      $("#updated").textContent = "Couldn't load data";
      $("#tiles").innerHTML = `<div class="card tile" style="grid-column:1/-1">The data files couldn't be loaded (${esc(e.message)}). If you're viewing this locally, serve the folder with a web server.</div>`;
      return;
    }
    Chart.defaults.font.family = css("--font");
    initMap();
    initFilters();
    drawLegend();
    drawUpdated();
    drawTiles();
    drawMarkers();
    drawList();
    drawAnalytics();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) applyTheme(); });
    setInterval(drawUpdated, 60000);
  }

  boot();
})();
