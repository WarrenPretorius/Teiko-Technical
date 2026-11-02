/* ==========================================================================
   Immune profiling dashboard: client
   Fetches JSON from the FastAPI backend and renders one view at a time.
   No framework and no build step; Plotly is loaded from a CDN.
   ========================================================================== */

"use strict";

/* ---------- Constants ---------- */

// Fixed colour per population, in panel order (validated for colour-blind
// separation). Colour follows the population everywhere it appears.
const POPULATION_COLORS = {
  b_cell: "#E8433F",
  cd8_t_cell: "#2F5D9E",
  cd4_t_cell: "#C98A1A",
  nk_cell: "#2E9C8A",
  monocyte: "#8E6BB8",
};

const RESPONSE = {
  yes: { label: "Responders", color: "#E5322D" },
  no: { label: "Non-responders", color: "#2F5D9E" },
};

// Filter chip groups in the sidebar: API parameter -> group label.
const FILTERS = {
  project: "Project",
  condition: "Condition",
  treatment: "Treatment",
  response: "Response",
  sex: "Sex",
  sample_type: "Sample type",
  timepoint: "Day",
  population: "Population",
};

const VIEWS = ["frequencies", "responders", "trends", "baseline"];

const FONT = "IBM Plex Sans, system-ui, sans-serif";
const INK = "#16181D";
const MUTED = "#6B6E76";
const GRID = "#EFEDE9";

/* ---------- State ---------- */

// Population key -> display label, filled from /api/overview.
let populationLabels = {};

const table = {
  filters: {},
  search: "",
  sort: "sample",
  desc: false,
  page: 1,
  pageSize: 25,
  total: 0,
  rows: [],
  selected: null, // sample ID shown in the inspector
  requestId: 0, // guards against out-of-order responses when filters change quickly
};

// Views whose data has been fetched at least once (each loads on first visit).
const loaded = new Set();

/* ---------- Helpers ---------- */

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response.json();
}

/** Escape text before inserting it into HTML. */
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

const fmtInt = (n) => n.toLocaleString("en-US");
const fmtPct = (n, digits = 2) => `${n.toFixed(digits)}%`;
const fmtNum = (n, digits = 2) =>
  n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const fmtDiff = (n) => `${n > 0 ? "+" : ""}${n.toFixed(2)} pp`;

/** p-values: three decimals, or "< 0.001". */
const fmtP = (p) => (p < 0.001 ? "< 0.001" : p.toFixed(3));

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** Query string from an object, skipping empty values. */
function toQuery(params) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value !== null && value !== undefined) q.set(key, value);
  }
  return q.toString();
}

function showError(container, message) {
  container.innerHTML = `<p class="muted">Could not load data: ${esc(message)}</p>`;
}

function popChip(pop) {
  return `<span class="pop-chip"><i style="background:${POPULATION_COLORS[pop]}"></i>${esc(populationLabels[pop])}</span>`;
}

/* ---------- Plotly theme ---------- */

/** Shared layout so every chart matches the page: Plex, quiet axes, no chrome. */
function baseLayout(overrides = {}) {
  const axis = {
    gridcolor: GRID,
    linecolor: GRID,
    zeroline: false,
    tickfont: { color: MUTED, size: 12 },
    title: { font: { color: MUTED, size: 12 } },
  };
  return {
    font: { family: FONT, color: INK, size: 13 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    margin: { t: 16, r: 8, b: 40, l: 52 },
    showlegend: false,
    hoverlabel: {
      bgcolor: "#FFFFFF",
      bordercolor: "#E6E4E0",
      font: { family: FONT, color: INK, size: 13 },
    },
    ...overrides,
    xaxis: { ...axis, ...overrides.xaxis },
    yaxis: { ...axis, ...overrides.yaxis },
  };
}

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

/* ==========================================================================
   View routing (#frequencies, #responders, #trends, #baseline)
   ========================================================================== */

const LOADERS = {
  frequencies: () => loadTable(),
  responders: () => loadResponders(currentTimepoint()),
  trends: () => loadTrends(),
  baseline: () => loadBaseline(),
};

function currentView() {
  const view = location.hash.slice(1);
  return VIEWS.includes(view) ? view : "frequencies";
}

function showView() {
  const view = currentView();

  $$(".view").forEach((el) => (el.hidden = el.dataset.view !== view));
  $$("#view-nav .chip").forEach((chip) => {
    if (chip.dataset.view === view) chip.setAttribute("aria-current", "page");
    else chip.removeAttribute("aria-current");
  });
  // Sidebar cards declare which views they belong to.
  $$(".sidebar [data-for]").forEach((card) => {
    card.hidden = !card.dataset.for.split(" ").includes(view);
  });

  if (!loaded.has(view)) {
    loaded.add(view);
    LOADERS[view]();
  }
  // Charts drawn while their view was hidden need to pick up their real size.
  $$(`#view-${view} .js-plotly-plot`).forEach((el) => Plotly.Plots.resize(el));
}

/* ==========================================================================
   Overview: sidebar stats and filter chips
   ========================================================================== */

async function loadOverview() {
  const data = await fetchJSON("/api/overview");
  populationLabels = Object.fromEntries(data.populations.map((p) => [p.key, p.label]));

  $("#stat-samples").textContent = fmtInt(data.totals.samples);
  $("#stat-subjects").textContent = fmtInt(data.totals.subjects);

  // One chip group per filter. Response and population need custom labels.
  const options = {
    ...data.filters,
    response: [
      ["yes", "Responder"],
      ["no", "Non-responder"],
      ["none", "Untreated"],
    ],
    sex: data.filters.sex.map((s) => [s, { M: "Male", F: "Female" }[s] ?? s]),
    population: data.populations.map((p) => [p.key, p.label]),
    timepoint: data.filters.timepoint.map((d) => [d, `Day ${d}`]),
  };
  $("#filter-groups").innerHTML = Object.entries(FILTERS)
    .map(([name, label]) => {
      const chips = options[name]
        .map((o) => (Array.isArray(o) ? o : [o, o]))
        .map(
          ([value, text]) =>
            `<button type="button" class="chip" data-filter="${name}" data-value="${esc(value)}" aria-pressed="false">${esc(text)}</button>`,
        )
        .join("");
      return `<div class="filter-group" role="group" aria-label="${label}">
        <span class="group-label">${label}</span><div class="chips">${chips}</div></div>`;
    })
    .join("");
}

/* ==========================================================================
   Part 2: frequency table
   ========================================================================== */

function tableParams() {
  return { ...table.filters, search: table.search, sort: table.sort, desc: table.desc };
}

async function loadTable() {
  const tbody = $("#freq-table tbody");
  const params = tableParams();
  const query = toQuery({ ...params, page: table.page, page_size: table.pageSize });

  // Export always mirrors what is on screen (all pages).
  $("#export-csv").href = `/api/frequencies.csv?${toQuery(params)}`;

  const requestId = ++table.requestId;
  try {
    const data = await fetchJSON(`/api/frequencies?${query}`);
    if (requestId !== table.requestId) return; // a newer request has superseded this one
    table.total = data.total;
    table.rows = data.rows;
    tbody.innerHTML = data.rows.length
      ? data.rows.map(renderFrequencyRow).join("")
      : `<tr class="empty-row"><td colspan="6">No samples match these filters.</td></tr>`;
    renderPager();
    renderSummary(data.summary);

    // Keep the inspector populated: default to the first sample on the page.
    if (!table.selected && data.rows.length) selectSample(data.rows[0].sample);
    else markSelected();
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">${esc(err.message)}</td></tr>`;
  }
}

function renderFrequencyRow(row) {
  return `<tr data-sample="${esc(row.sample)}" tabindex="0">
    <td><span class="sample-link">${esc(row.sample)}</span></td>
    <td class="num">${fmtInt(row.total_count)}</td>
    <td>${popChip(row.population)}</td>
    <td class="num">${fmtInt(row.count)}</td>
    <td class="num"><span class="pct-cell">
      <span class="pct-bar"><span style="width:${Math.min(row.percentage, 100)}%"></span></span>
      ${fmtPct(row.percentage)}
    </span></td>
    <td class="go" aria-hidden="true">›</td>
  </tr>`;
}

/**
 * Summary of every row matching the filters, not just the current page.
 * Means are only shown for a single population: averaging B cell and
 * monocyte counts together would not mean anything.
 */
function renderSummary(summary) {
  const population = table.filters.population;
  const hasMeans = Boolean(population) && summary.rows > 0;
  const label = population ? populationLabels[population] : "";
  const items = [
    ["Samples", fmtInt(summary.samples)],
    ["Rows", fmtInt(summary.rows)],
    [hasMeans ? `Mean ${label} count` : "Mean count", hasMeans ? fmtNum(summary.mean_count) : "–"],
    [
      hasMeans ? `Mean ${label} frequency` : "Mean frequency",
      hasMeans ? fmtPct(summary.mean_percentage) : "–",
    ],
  ];
  $("#table-summary").innerHTML =
    items
      .map(
        ([name, value]) =>
          `<div class="summary-item"><span class="card-label">${esc(name)}</span><span class="summary-value">${value}</span></div>`,
      )
      .join("") +
    (hasMeans ? "" : `<p class="summary-note">Select one population to see its mean count and frequency.</p>`);
}

function renderPager() {
  const first = table.total ? (table.page - 1) * table.pageSize + 1 : 0;
  const last = Math.min(table.page * table.pageSize, table.total);
  $("#page-info").textContent = `${fmtInt(first)}–${fmtInt(last)} of ${fmtInt(table.total)} rows`;
  $("#prev-page").disabled = table.page <= 1;
  $("#next-page").disabled = last >= table.total;
}

function updateSortHeaders() {
  $$("#freq-table th[data-sort]").forEach((th) => {
    const active = th.dataset.sort === table.sort;
    th.setAttribute("aria-sort", active ? (table.desc ? "descending" : "ascending") : "none");
  });
}

/** Highlight every row of the selected sample (one per population). */
function markSelected() {
  $$("#freq-table tbody tr[data-sample]").forEach((tr) =>
    tr.classList.toggle("selected", tr.dataset.sample === table.selected),
  );
}

/** Move the selection to the previous/next distinct sample on this page. */
function stepSelection(direction) {
  const samples = [...new Set(table.rows.map((r) => r.sample))];
  if (!samples.length) return;
  const index = samples.indexOf(table.selected);
  const next = samples[Math.max(0, Math.min(samples.length - 1, index + direction))];
  if (next && next !== table.selected) {
    selectSample(next);
    $(`#freq-table tr[data-sample="${CSS.escape(next)}"]`)?.scrollIntoView({ block: "nearest" });
  }
}

function bindTable() {
  const reload = () => {
    table.page = 1;
    loadTable();
  };

  // Filter chips: one value per group; clicking the active chip clears it.
  $("#filter-groups").addEventListener("click", (event) => {
    const chip = event.target.closest("button[data-filter]");
    if (!chip) return;
    const { filter, value } = chip.dataset;
    const active = table.filters[filter] === value;
    table.filters[filter] = active ? "" : value;
    $$(`#filter-groups button[data-filter="${filter}"]`).forEach((c) =>
      c.setAttribute("aria-pressed", String(!active && c === chip)),
    );
    reload();
  });

  $("#search").addEventListener(
    "input",
    debounce((event) => {
      table.search = event.target.value.trim();
      reload();
    }, 250),
  );

  $("#reset-filters").addEventListener("click", () => {
    table.filters = {};
    table.search = "";
    $("#search").value = "";
    $$("#filter-groups .chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    reload();
  });

  $$("#freq-table th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      // Clicking the active column flips direction; a new column starts ascending.
      table.desc = table.sort === th.dataset.sort ? !table.desc : false;
      table.sort = th.dataset.sort;
      updateSortHeaders();
      reload();
    });
  });

  $("#page-size").addEventListener("change", (event) => {
    table.pageSize = Number(event.target.value);
    reload();
  });
  $("#prev-page").addEventListener("click", () => {
    table.page -= 1;
    loadTable();
  });
  $("#next-page").addEventListener("click", () => {
    table.page += 1;
    loadTable();
  });

  // Row click (or Enter) selects the sample for the inspector.
  const tbody = $("#freq-table tbody");
  tbody.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-sample]");
    if (row) selectSample(row.dataset.sample);
  });
  tbody.addEventListener("keydown", (event) => {
    const row = event.target.closest("tr[data-sample]");
    if (row && event.key === "Enter") selectSample(row.dataset.sample);
  });

  // Arrow keys step through samples, unless the user is typing.
  document.addEventListener("keydown", (event) => {
    if (currentView() !== "frequencies" || event.target.matches("input, select")) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      stepSelection(event.key === "ArrowDown" ? 1 : -1);
    }
  });
}

/* ==========================================================================
   Sample inspector (docked beside the table)
   ========================================================================== */

let inspectorRequest = 0;

async function selectSample(sampleId) {
  table.selected = sampleId;
  markSelected();

  const requestId = ++inspectorRequest;
  try {
    const { sample, timeline } = await fetchJSON(`/api/samples/${encodeURIComponent(sampleId)}`);
    if (requestId !== inspectorRequest) return;
    renderInspector(sample, timeline);
  } catch (err) {
    showError($("#inspector-body"), err.message);
  }
}

function renderInspector(sample, timeline) {
  const body = $("#inspector-body");
  body.className = "inspector-content";

  const response = sample.response
    ? sample.response === "yes"
      ? "Responder"
      : "Non-responder"
    : "Untreated";
  const meta = [
    ["Project", sample.project_id],
    ["Condition", sample.condition],
    ["Treatment", sample.treatment],
    ["Response", response],
    ["Sex", sample.sex === "M" ? "Male" : "Female"],
    ["Age", sample.age],
  ];

  const days = [...new Set(timeline.map((r) => r.timepoint))];
  const byPop = (pop) => timeline.filter((r) => r.population === pop);
  const tableHead = days.map((d) => `<th class="num">Day ${d}</th>`).join("");
  const tableRows = Object.keys(POPULATION_COLORS)
    .map((pop) => {
      const cells = byPop(pop)
        .map((r) => `<td class="num">${fmtPct(r.percentage)}</td>`)
        .join("");
      return `<tr><td>${popChip(pop)}</td>${cells}</tr>`;
    })
    .join("");

  body.innerHTML = `
    <div>
      <h3>${esc(sample.sample_id)}</h3>
      <p class="sub">Subject ${esc(sample.subject_id)} · Day ${sample.time_from_treatment_start} · ${esc(sample.sample_type)}</p>
    </div>
    <dl class="meta-grid">${meta.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <div>
      <p class="card-label">Composition across timepoints</p>
      <div id="inspector-chart" class="chart chart-sm" role="img" aria-label="Stacked composition by timepoint"></div>
    </div>
    <div class="table-wrap">
      <table class="data-table compact"><thead><tr><th>Population</th>${tableHead}</tr></thead>
      <tbody>${tableRows}</tbody></table>
    </div>`;

  // Stacked bars: one segment per population, a thin surface gap between segments.
  const traces = Object.keys(POPULATION_COLORS).map((pop) => ({
    type: "bar",
    name: populationLabels[pop],
    x: byPop(pop).map((r) => `Day ${r.timepoint}`),
    y: byPop(pop).map((r) => r.percentage),
    customdata: byPop(pop).map((r) => r.count),
    marker: { color: POPULATION_COLORS[pop], line: { color: "#FFFFFF", width: 2 } },
    hovertemplate: `<b>${populationLabels[pop]}</b><br>%{x}: %{y:.2f}% (%{customdata:,} cells)<extra></extra>`,
  }));

  Plotly.react(
    "inspector-chart",
    traces,
    baseLayout({
      barmode: "stack",
      bargap: 0.45,
      showlegend: true,
      legend: { orientation: "h", y: -0.2, traceorder: "normal", font: { size: 11, color: MUTED } },
      margin: { t: 22, r: 4, b: 56, l: 40 },
      yaxis: { range: [0, 100], ticksuffix: "%" },
      annotations: [
        {
          x: `Day ${sample.time_from_treatment_start}`,
          y: 100,
          yanchor: "bottom",
          text: "this sample",
          showarrow: false,
          font: { size: 11, color: "#E5322D" },
        },
      ],
    }),
    PLOT_CONFIG,
  );
}

/* ==========================================================================
   Part 3: responders vs non-responders
   ========================================================================== */

let respondersRequest = 0; // same stale-response guard as the table

function currentTimepoint() {
  return $("#timepoint-toggle [aria-checked='true']")?.dataset.timepoint ?? "all";
}

async function loadResponders(timepoint = "all") {
  const requestId = ++respondersRequest;
  try {
    const data = await fetchJSON(`/api/responders?timepoint=${timepoint}`);
    if (requestId !== respondersRequest) return;
    renderCohortNote(data);
    renderVerdict(data);
    renderBoxplot(data);
    renderStatsTable(data);
  } catch (err) {
    showError($("#verdict"), err.message);
  }
}

function renderCohortNote(data) {
  const scope = data.timepoint === "all" ? "all timepoints" : `day ${data.timepoint}`;
  $("#cohort-note").textContent =
    `${fmtInt(data.n_samples)} samples from ${fmtInt(data.n_subjects)} subjects · ${scope}`;

  const best = [...data.stats].sort((a, b) => a.q_value - b.q_value)[0];
  $("#stat-min-q").textContent = fmtP(best.q_value);
  $("#stat-min-q-note").textContent =
    `${best.label} · ${best.significant ? "significant" : `not significant at ${data.alpha}`}`;
}

/** Plain-English conclusion Bob can pass on, driven by the BH-adjusted q-values. */
function renderVerdict(data) {
  const scope = data.timepoint === "all" ? "across all timepoints" : `at day ${data.timepoint}`;
  const significant = data.stats.filter((s) => s.significant);
  const strongest = [...data.stats].sort((a, b) => a.p_value - b.p_value)[0];
  const direction = (s) => (s.median_difference > 0 ? "higher" : "lower");

  let badge;
  let text;
  if (significant.length) {
    badge = `<span class="status significant">Significant</span>`;
    const names = significant
      .map((s) => `<strong>${s.label}</strong> (${direction(s)} in responders, q = ${fmtP(s.q_value)})`)
      .join(", ");
    text = `${names} ${significant.length > 1 ? "differ" : "differs"} significantly between responders and non-responders ${scope}, after correcting for multiple testing.`;
  } else if (strongest.p_value < data.alpha) {
    badge = `<span class="status nominal">Lead to confirm</span>`;
    text = `<strong>No population differs significantly ${scope}</strong> once corrected for testing five populations (q < ${data.alpha}).
      The strongest candidate is <strong>${strongest.label}</strong>, ${direction(strongest)} in responders
      (median ${fmtDiff(strongest.median_difference)}; p = ${fmtP(strongest.p_value)}, q = ${fmtP(strongest.q_value)}).`;
  } else {
    badge = `<span class="status">No difference</span>`;
    text = `<strong>No population differs between responders and non-responders ${scope}</strong>
      (smallest p = ${fmtP(strongest.p_value)}, ${strongest.label}).`;
  }
  $("#verdict").innerHTML = `${badge}<p>${text}</p>`;
}

function renderBoxplot(data) {
  const ordered = Object.keys(POPULATION_COLORS).filter((p) => p in data.values);

  // One trace per response group; boxmode "group" places them side by side.
  const traces = ["yes", "no"].map((response) => {
    const x = [];
    const y = [];
    for (const pop of ordered) {
      for (const v of data.values[pop][response] ?? []) {
        x.push(populationLabels[pop]);
        y.push(v);
      }
    }
    return {
      type: "box",
      name: RESPONSE[response].label,
      x,
      y,
      marker: { color: RESPONSE[response].color, size: 4, opacity: 0.5 },
      line: { width: 1.5 },
      fillcolor: `${RESPONSE[response].color}2E`, // ~18% alpha fill
      boxpoints: "outliers",
      hoverinfo: "y",
      hoveron: "boxes",
    };
  });

  // Direct label under each pair: the adjusted q-value, in the axis ticks so it
  // never collides with the boxes on narrow screens.
  const top = Math.max(...traces.flatMap((t) => t.y));
  const narrow = window.matchMedia("(max-width: 640px)").matches;
  const qColor = (s) => (s.significant || s.p_value < data.alpha ? "#E5322D" : MUTED);
  const ticktext = data.stats.map(
    // On phones, "CD8 T cell" becomes "CD8 T" and "q = " is dropped so five labels fit.
    (s) =>
      `${narrow ? s.label.replace(" cell", "") : s.label}<br><span style="font-size:11px;color:${qColor(s)}">${narrow ? "" : "q = "}${fmtP(s.q_value)}</span>`,
  );

  Plotly.react(
    "boxplot",
    traces,
    baseLayout({
      boxmode: "group",
      boxgap: 0.35,
      boxgroupgap: 0.15,
      margin: { t: 16, r: 8, b: 56, l: narrow ? 40 : 52 },
      // Keep labels level; drop the y-axis title on phones to free up width.
      xaxis: { tickvals: data.stats.map((s) => s.label), ticktext, tickangle: 0 },
      yaxis: {
        title: { text: narrow ? "" : "Relative frequency (%)" },
        ticksuffix: "%",
        range: [0, top * 1.05],
      },
    }),
    PLOT_CONFIG,
  );
}

function renderStatsTable(data) {
  $("#stats-n").textContent =
    `${fmtInt(data.n_samples)} samples · ${fmtInt(data.n_subjects)} subjects`;

  $("#stats-table tbody").innerHTML = data.stats
    .map((s) => {
      // Three states: significant after correction, nominal (p < alpha only), or neither.
      const state = s.significant ? "significant" : s.p_value < data.alpha ? "nominal" : "";
      const result = {
        significant: `<span class="status significant">Significant</span>`,
        nominal: `<span class="status nominal" title="p < ${data.alpha} before correction only">Nominal only</span>`,
        "": `<span class="status">n.s.</span>`,
      }[state];
      return `<tr class="${state ? "highlight" : ""}">
        <td>${popChip(s.population)}</td>
        <td class="num">${fmtPct(s.median_responders)}</td>
        <td class="num">${fmtPct(s.median_non_responders)}</td>
        <td class="num">${fmtDiff(s.median_difference)}</td>
        <td class="num">${fmtP(s.p_value)}</td>
        <td class="num q-cell ${state}"><span>${fmtP(s.q_value)}</span></td>
        <td>${result}</td>
      </tr>`;
    })
    .join("");

  // Robustness check: one averaged value per subject removes repeated measures.
  const best = [...data.subject_average].sort((a, b) => a.p_value - b.p_value)[0];
  const n = best.n_responders + best.n_non_responders;
  $("#robustness").innerHTML = `<strong>Robustness check:</strong> averaging each subject's samples into a
    single value (${fmtInt(n)} subjects) gives the same picture. The strongest signal is again
    ${best.label} (p = ${fmtP(best.p_value)}, q = ${fmtP(best.q_value)}).`;
}

function bindTimepointToggle() {
  const group = $("#timepoint-toggle");
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-timepoint]");
    if (!button) return;
    group
      .querySelectorAll("button")
      .forEach((b) => b.setAttribute("aria-checked", String(b === button)));
    loadResponders(button.dataset.timepoint);
  });
}

/* ==========================================================================
   Trends over time (small multiples)
   ========================================================================== */

async function loadTrends() {
  const container = $("#trend-charts");
  try {
    const { rows } = await fetchJSON("/api/trends");
    container.innerHTML = Object.keys(POPULATION_COLORS)
      .map(
        (pop) => `<div><h4>${populationLabels[pop]}</h4>
          <div class="chart" id="trend-${pop}" role="img" aria-label="${populationLabels[pop]} over time"></div></div>`,
      )
      .join("");

    for (const pop of Object.keys(POPULATION_COLORS)) {
      const traces = ["yes", "no"].map((response) => {
        const points = rows.filter((r) => r.population === pop && r.response === response);
        const color = RESPONSE[response].color;
        return {
          type: "scatter",
          mode: "lines+markers",
          name: RESPONSE[response].label,
          x: points.map((r) => r.timepoint),
          y: points.map((r) => r.mean),
          error_y: { type: "data", array: points.map((r) => r.sem), color, thickness: 1.5, width: 4 },
          line: { color, width: 2 },
          marker: { color, size: 8, line: { color: "#FFFFFF", width: 2 } },
          hovertemplate: `${RESPONSE[response].label}<br>Day %{x}: %{y:.2f}%<extra></extra>`,
        };
      });
      Plotly.react(
        `trend-${pop}`,
        traces,
        baseLayout({
          margin: { t: 8, r: 8, b: 36, l: 44 },
          hovermode: "x unified",
          xaxis: { tickvals: [0, 7, 14], ticktext: ["D0", "D7", "D14"], range: [-1.5, 15.5] },
          yaxis: { ticksuffix: "%" },
        }),
        PLOT_CONFIG,
      );
    }
  } catch (err) {
    showError(container, err.message);
  }
}

/* ==========================================================================
   Part 4: baseline subset
   ========================================================================== */

async function loadBaseline() {
  try {
    const data = await fetchJSON("/api/baseline");
    $("#baseline-total").textContent = fmtInt(data.n_samples);

    const s = data.summary;
    renderBarCard("#baseline-project", "Samples per project", s.samples_per_project, (k) =>
      k.replace("prj", "Project "),
    );
    renderBarCard(
      "#baseline-response",
      "Subjects by response",
      s.subjects_by_response,
      (k) => ({ yes: "Responders", no: "Non-responders" })[k] ?? k,
    );
    renderBarCard(
      "#baseline-sex",
      "Subjects by sex",
      s.subjects_by_sex,
      (k) => ({ M: "Male", F: "Female" })[k] ?? k,
    );

    const titles = {
      samples: "4.1 · Baseline samples",
      samples_per_project: "4.2 · Samples per project",
      subjects_by_response: "4.2 · Subjects by response",
      subjects_by_sex: "4.2 · Subjects by sex",
    };
    $("#baseline-sql").innerHTML = Object.entries(data.sql)
      .map(([key, sql]) => `<h4>${titles[key] ?? key}</h4><pre><code>${esc(sql)}</code></pre>`)
      .join("");
  } catch (err) {
    showError($("#baseline-project"), err.message);
  }
}

/** A small card of labelled horizontal bars, with counts and shares. */
function renderBarCard(selector, title, counts, labelFor) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const max = Math.max(...Object.values(counts));
  const items = Object.entries(counts)
    .map(
      ([key, n]) => `<li>
        <div class="bar-label"><span>${esc(labelFor(key))}</span>
          <span><strong>${fmtInt(n)}</strong> <span class="muted">${Math.round((100 * n) / total)}%</span></span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${(100 * n) / max}%"></div></div>
      </li>`,
    )
    .join("");
  $(selector).innerHTML = `<p class="card-label">${title}</p><ul class="bar-list">${items}</ul>`;
}

/* ==========================================================================
   Boot
   ========================================================================== */

async function init() {
  bindTimepointToggle();
  try {
    await loadOverview(); // populations and filters are needed by everything else
  } catch (err) {
    showError($("#view-frequencies"), err.message);
    return;
  }
  bindTable();
  window.addEventListener("hashchange", showView);
  showView();

  // The cohort card and q-value stat are shared by several views, so fetch
  // the responder summary up front even if the user starts elsewhere.
  if (!loaded.has("responders")) {
    loaded.add("responders");
    loadResponders("all");
  }
}

document.addEventListener("DOMContentLoaded", init);
