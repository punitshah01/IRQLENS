const PRIMARY_VIEWS = ["systems", "overview", "irq", "network", "cpu", "diagnostics", "sessions"];
const SECONDARY_VIEWS = ["settings"];
const TIME_RANGES = [
  { key: "30", label: "30s", seconds: 30 },
  { key: "60", label: "1m", seconds: 60 },
  { key: "300", label: "5m", seconds: 300 },
  { key: "900", label: "15m", seconds: 900 },
  { key: "1800", label: "30m", seconds: 1800 },
  { key: "3600", label: "1h", seconds: 3600 },
  { key: "custom", label: "Custom", seconds: null },
];

const storage = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch (_) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {
      // Ignore storage issues.
    }
  },
};

const state = {
  backend: window.location.origin,
  view: storage.get("irqlens:view", "systems"),
  host: storage.get("irqlens:host", ""),
  systems: [],
  health: null,
  snapshot: null,
  viz: null,
  topology: null,
  interfaceHistory: null,
  sessions: [],
  sessionDetail: null,
  sessionFiles: [],
  selectedSessionId: "",
  selectedIface: storage.get("irqlens:iface", "ALL"),
  selectedCpu: storage.get("irqlens:cpu", ""),
  cpuMetric: storage.get("irqlens:cpuMetric", "irq"),
  irqSearch: "",
  irqSort: "rate",
  timeRange: storage.get("irqlens:timeRange", "300"),
  customFrom: storage.get("irqlens:customFrom", ""),
  customTo: storage.get("irqlens:customTo", ""),
  paused: false,
  staleMs: 5000,
  lastDataAt: 0,
  ws: null,
  wsStatus: "connecting",
  charts: {},
  loading: { global: false, sessions: false, interfaceHistory: false },
  diag: {
    running: false,
    sessionId: "",
    duration: 60,
    startedAt: 0,
    endsAt: 0,
    timer: null,
    categories: ["irq", "softirq", "network", "system", "interfaces", "ethtool"],
    files: [],
    completedSessionId: "",
  },
};

const el = {};

function qs(id) {
  return document.getElementById(id);
}

function initializeElements() {
  [
    "primary-nav", "secondary-nav", "sut-title", "sut-status-dot", "sut-status-label",
    "sut-meta", "page-title", "page-subtitle", "breadcrumb", "ws-pill", "last-updated",
    "refresh-button", "live-button", "custom-range", "custom-from", "custom-to", "custom-apply",
    "time-controls", "section-systems", "section-overview", "section-irq", "section-network",
    "section-cpu", "section-diagnostics", "section-sessions", "section-settings",
  ].forEach(id => { el[id] = qs(id); });
}

function nowSeconds() {
  return Date.now() / 1000;
}

function currentSystem() {
  return state.systems.find(item => item.id === state.host) || null;
}

function api(path) {
  return state.backend.replace(/\/$/, "") + path;
}

async function fetchJson(path) {
  const response = await fetch(api(path));
  if (!response.ok) {
    throw new Error(path + " -> " + response.status);
  }
  return response.json();
}

function fmtCount(value) {
  const n = Number(value || 0);
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return n.toFixed(1);
}

function fmtBytesPerSecond(value) {
  const n = Number(value || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " Gbps";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " Mbps";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " Kbps";
  return n.toFixed(0) + " bps";
}

function fmtBytesRate(value) {
  const n = Number(value || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB/s";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " MB/s";
  if (n >= 1e3) return (n / 1e3).toFixed(2) + " KB/s";
  return n.toFixed(0) + " B/s";
}

function fmtTs(ts) {
  if (!ts) return "N/A";
  return new Date(ts * 1000).toLocaleString();
}

function fmtAgo(ts) {
  if (!ts) return "never";
  const seconds = Math.max(0, Math.round(nowSeconds() - ts));
  if (seconds < 60) return seconds + " sec ago";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + " min ago";
  const hours = Math.floor(minutes / 60);
  return hours + " hr ago";
}

function statusTone(status) {
  if (status === "ONLINE") return "online";
  if (status === "STALE") return "warn";
  return "offline";
}

function wsTone() {
  if (state.wsStatus === "connected") return "good";
  if (state.wsStatus === "connecting") return "warn";
  return "bad";
}

function viewTitle(view) {
  switch (view) {
    case "systems": return ["Systems", "Choose a target system and open its dashboard."];
    case "overview": return ["Overview", "See what is happening on this machine right now."];
    case "irq": return ["IRQ", "Trace which interrupts are driving activity."];
    case "network": return ["Network", "Inspect interface traffic, drops, errors, and related IRQs."];
    case "cpu": return ["CPU", "See which CPU and NUMA regions are absorbing IRQ activity."];
    case "diagnostics": return ["Diagnostics", "Capture evidence for the selected SUT."];
    case "sessions": return ["Sessions", "Review diagnostic captures, files, and downloads."];
    case "settings": return ["Settings", "Operational and backend information."];
    default: return ["IRQLENS", "Monitoring dashboard"];
  }
}

function renderNavigation() {
  el["primary-nav"].innerHTML = PRIMARY_VIEWS.map(view => {
    const active = state.view === view ? "nav-button active" : "nav-button";
    const label = view.charAt(0).toUpperCase() + view.slice(1);
    return `<button class="${active}" data-view="${view}">${label}</button>`;
  }).join("");
  el["secondary-nav"].innerHTML = SECONDARY_VIEWS.map(view => {
    const active = state.view === view ? "secondary-button nav-button active" : "secondary-button nav-button";
    const label = view.charAt(0).toUpperCase() + view.slice(1);
    return `<button class="${active}" data-view="${view}">${label}</button>`;
  }).join("");
  [...document.querySelectorAll("[data-view]")].forEach(node => {
    node.onclick = async () => {
      state.view = node.dataset.view;
      storage.set("irqlens:view", state.view);
      if (state.view === "sessions") await loadSessions();
      if (state.view === "network" && state.selectedIface !== "ALL") await loadInterfaceHistory();
      render();
    };
  });
}

function renderTimeControls() {
  el["time-controls"].innerHTML = TIME_RANGES.map(item => {
    const active = state.timeRange === item.key ? "time-chip active" : "time-chip";
    return `<button class="${active}" data-range="${item.key}">${item.label}</button>`;
  }).join("");
  [...el["time-controls"].querySelectorAll("[data-range]")].forEach(node => {
    node.onclick = async () => {
      state.timeRange = node.dataset.range;
      storage.set("irqlens:timeRange", state.timeRange);
      el["custom-range"].classList.toggle("active", state.timeRange === "custom");
      await refreshSelectedHistory();
      render();
    };
  });
  el["custom-from"].value = state.customFrom || "";
  el["custom-to"].value = state.customTo || "";
  el["custom-range"].classList.toggle("active", state.timeRange === "custom");
}

function timeBounds() {
  if (state.timeRange === "custom") {
    const fromMs = Date.parse(state.customFrom || "");
    const toMs = Date.parse(state.customTo || "");
    if (!Number.isNaN(fromMs) && !Number.isNaN(toMs)) {
      return { from: Math.min(fromMs, toMs) / 1000, to: Math.max(fromMs, toMs) / 1000 };
    }
  }
  const selected = TIME_RANGES.find(item => item.key === state.timeRange) || TIME_RANGES[2];
  const to = nowSeconds();
  return { from: to - selected.seconds, to };
}

function filterRowsToRange(rows) {
  const bounds = timeBounds();
  return (rows || []).filter(row => Number(row.timestamp || 0) >= bounds.from && Number(row.timestamp || 0) <= bounds.to);
}

function persistSelections() {
  storage.set("irqlens:host", state.host || "");
  storage.set("irqlens:iface", state.selectedIface || "ALL");
  storage.set("irqlens:cpu", state.selectedCpu || "");
  storage.set("irqlens:cpuMetric", state.cpuMetric || "irq");
  storage.set("irqlens:customFrom", state.customFrom || "");
  storage.set("irqlens:customTo", state.customTo || "");
}

function renderContextBar() {
  const system = currentSystem();
  const title = system ? (system.name || system.id) : "No SUT selected";
  const status = system ? system.status : "NONE";
  el["sut-title"].textContent = system ? `SUT: ${title}` : "Select a SUT";
  el["sut-status-dot"].className = `dot ${statusTone(status)}`;
  el["sut-status-label"].textContent = system ? status : "No selection";
  const meta = [];
  if (system) {
    meta.push(system.id);
    meta.push(system.os_distribution || "Unknown OS");
    meta.push(system.kernel || "Unknown kernel");
    meta.push(`Last update ${fmtAgo(system.last_seen)}`);
  } else {
    meta.push("Open Systems to select a target");
  }
  el["sut-meta"].textContent = meta.join(" • ");
  const [titleText, subtitleText] = viewTitle(state.view);
  el["page-title"].textContent = titleText;
  el["page-subtitle"].textContent = subtitleText;
  el["breadcrumb"].textContent = buildBreadcrumb();
  el["ws-pill"].className = `tag ${wsTone()}`;
  el["ws-pill"].textContent = `WebSocket: ${state.wsStatus}`;
  el["last-updated"].textContent = state.lastDataAt ? fmtAgo(state.lastDataAt) : "never";
  el["live-button"].textContent = state.paused ? "Resume Live" : "Pause Live";
}

function buildBreadcrumb() {
  const parts = [];
  const system = currentSystem();
  if (system) parts.push(system.name || system.id);
  if (state.view !== "systems") parts.push(viewTitle(state.view)[0]);
  if (state.view === "network" && state.selectedIface && state.selectedIface !== "ALL") parts.push(state.selectedIface);
  if (state.view === "cpu" && state.selectedCpu) parts.push(`CPU ${state.selectedCpu}`);
  if (state.view === "sessions" && state.selectedSessionId) parts.push(state.selectedSessionId);
  return parts.join(" → ") || "Systems";
}

function aggregateCpuActivity() {
  const cpu = {};
  const soft = state.snapshot?.softirq?.sample?.per_cpu_rates || {};
  const rows = state.snapshot?.irq?.rows || [];
  for (const row of rows) {
    for (const [cpuId, rate] of Object.entries(row.cpu_rates || {})) {
      cpu[cpuId] = cpu[cpuId] || { irq: 0, softirq: 0, total: 0 };
      cpu[cpuId].irq += Number(rate || 0);
    }
  }
  for (const [cpuId, rate] of Object.entries(soft)) {
    cpu[cpuId] = cpu[cpuId] || { irq: 0, softirq: 0, total: 0 };
    cpu[cpuId].softirq += Number(rate || 0);
  }
  for (const [cpuId, entry] of Object.entries(cpu)) {
    entry.total = entry.irq + entry.softirq;
  }
  return cpu;
}

function topCpuSummary() {
  const cpu = aggregateCpuActivity();
  const entries = Object.entries(cpu).sort((a, b) => b[1].irq - a[1].irq);
  const totalIrq = entries.reduce((sum, item) => sum + item[1].irq, 0);
  const top = entries[0];
  if (!top) return null;
  return {
    cpu: top[0],
    irq: top[1].irq,
    softirq: top[1].softirq,
    share: totalIrq > 0 ? (top[1].irq / totalIrq) * 100 : 0,
  };
}

function buildFindings() {
  const findings = [];
  const viz = state.viz || {};
  const net = state.snapshot?.network?.global || state.snapshot?.networkGlobal || state.network?.global || {};
  const cpuTop = topCpuSummary();
  if (cpuTop && cpuTop.share >= 35) {
    findings.push({
      tone: "warn",
      title: "High IRQ concentration",
      text: `CPU ${cpuTop.cpu} is handling ${cpuTop.share.toFixed(0)}% of visible IRQ activity.`,
      action: "View CPU",
      target: "cpu",
    });
  }
  const balance = viz.cpu_heatmap?.balance || {};
  if (balance.status && balance.status !== "Balanced") {
    findings.push({
      tone: "warn",
      title: "IRQ balance needs attention",
      text: `Balance score ${fmtCount(balance.score || 0)}/100 with status ${balance.status}.`,
      action: "View IRQ",
      target: "irq",
    });
  }
  const drops = Number(net.rx_drop_ps || 0) + Number(net.tx_drop_ps || 0);
  if (drops > 0) {
    findings.push({
      tone: "bad",
      title: "Packet drops detected",
      text: `${fmtCount(drops)} drops/sec observed across current interfaces.`,
      action: "View Network",
      target: "network",
    });
  } else {
    findings.push({
      tone: "good",
      title: "No packet drops detected",
      text: "Current network samples show zero packet drops.",
      action: "View Network",
      target: "network",
    });
  }
  const topIface = (state.snapshot?.network?.interfaces || []).slice().sort((a, b) => Number(b.rx_bps || 0) - Number(a.rx_bps || 0))[0];
  if (topIface) {
    findings.push({
      tone: "info",
      title: "Most active interface",
      text: `${topIface.interface} is currently receiving ${fmtBytesRate(topIface.rx_bps)} and transmitting ${fmtBytesRate(topIface.tx_bps)}.`,
      action: "View Network",
      target: "network",
    });
  }
  return findings.slice(0, 4);
}

function healthSummaryText() {
  const findings = buildFindings();
  if (findings.some(item => item.tone === "bad")) return "Problem";
  if (findings.some(item => item.tone === "warn")) return "Warning";
  return "Healthy";
}

function emptyState(title, text, actionHtml = "") {
  return `<div class="empty-state"><h3 class="empty-title">${title}</h3><div class="muted">${text}</div>${actionHtml}</div>`;
}

function selectedIfaceRow() {
  const interfaces = state.snapshot?.network?.interfaces || [];
  if (!interfaces.length) return null;
  if (!state.selectedIface || state.selectedIface === "ALL") return interfaces.slice().sort((a, b) => Number(b.rx_bps || 0) - Number(a.rx_bps || 0))[0] || null;
  return interfaces.find(item => item.interface === state.selectedIface) || null;
}

function relatedIrqsForIface(ifaceName) {
  if (!ifaceName) return [];
  return (state.snapshot?.irq?.rows || []).filter(row => String(row.nic || "") === String(ifaceName));
}

function networkHistoryRows() {
  if (state.selectedIface !== "ALL" && state.interfaceHistory) {
    return filterRowsToRange(state.interfaceHistory.samples || []).slice().sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0));
  }
  return filterRowsToRange(state.viz?.series?.network || []).slice().sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0));
}

function showSection(view) {
  ["systems", "overview", "irq", "network", "cpu", "diagnostics", "sessions", "settings"].forEach(name => {
    const node = qs(`section-${name}`);
    node.classList.toggle("active", name === view);
  });
}

function renderOverview() {
  const system = currentSystem();
  const irqRows = state.snapshot?.irq?.rows || [];
  const soft = state.snapshot?.softirq?.sample?.rates || {};
  const net = state.snapshot?.network?.global || {};
  const cpuTop = topCpuSummary();
  const status = healthSummaryText();
  const findings = buildFindings();
  const irqRate = irqRows.reduce((sum, row) => sum + Number(row.total_rate || 0), 0);
  const softRate = Object.values(soft).reduce((sum, value) => sum + Number(value || 0), 0);

  qs("overview-kpis").innerHTML = [
    { label: "IRQ/s", value: fmtCount(irqRate), note: "Current interrupt rate" },
    { label: "SoftIRQ/s", value: fmtCount(softRate), note: "Current softirq rate" },
    { label: "RX", value: fmtBytesRate(net.rx_bps), note: "Current receive traffic" },
    { label: "TX", value: fmtBytesRate(net.tx_bps), note: "Current transmit traffic" },
    { label: "CPU Activity", value: status, note: cpuTop ? `Hottest CPU: ${cpuTop.cpu}` : "Waiting for CPU activity" },
  ].map(item => `
    <div class="kpi-card">
      <div class="kpi-label">${item.label}</div>
      <div class="kpi-value">${item.value}</div>
      <div class="kpi-note">${item.note}</div>
    </div>
  `).join("");

  if (!system) {
    qs("overview-topology").innerHTML = emptyState("No SUT selected", "Select a remote or local system in Systems to load health, trends, and topology.");
    qs("overview-findings").innerHTML = emptyState("No findings yet", "Choose a SUT first.");
    clearChart("overviewIrqTrend");
    clearChart("overviewNetTrend");
    return;
  }

  renderSimpleTrendChart(
    "overviewIrqTrend",
    "chart-overview-irq",
    filterRowsToRange(state.viz?.series?.irq || []).map(item => [item.timestamp * 1000, Number(item.irq_rate || 0)]),
    "IRQ/s",
    "IRQ activity",
    "#2368d1"
  );
  renderDualTrendChart(
    "overviewNetTrend",
    "chart-overview-network",
    filterRowsToRange(state.viz?.series?.network || []).map(item => [item.timestamp * 1000, Number(item.rx_bps || 0)]),
    filterRowsToRange(state.viz?.series?.network || []).map(item => [item.timestamp * 1000, Number(item.tx_bps || 0)]),
    "RX",
    "TX"
  );
  renderTopologyMap("overview-topology", { compact: true });
  qs("overview-findings").innerHTML = findings.map(item => `
    <div class="finding ${item.tone}">
      <div class="finding-title">${item.title}</div>
      <div class="finding-text">${item.text}</div>
      <div class="button-row" style="margin-top:10px;"><button class="action-button ghost" data-target-view="${item.target}">${item.action}</button></div>
    </div>
  `).join("");
  [...qs("overview-findings").querySelectorAll("[data-target-view]")].forEach(node => {
    node.onclick = async () => {
      state.view = node.dataset.targetView;
      storage.set("irqlens:view", state.view);
      if (state.view === "network" && state.selectedIface !== "ALL") await loadInterfaceHistory();
      render();
    };
  });
}

function renderSystems() {
  const root = qs("systems-list");
  if (!state.systems.length) {
    root.innerHTML = emptyState("No systems registered", "IRQLENS has not received telemetry yet.");
    return;
  }
  root.innerHTML = state.systems.map(system => {
    const selected = system.id === state.host;
    return `
      <div class="system-item">
        <div class="topology-heading">
          <div>
            <div class="item-title">${escapeHtml(system.name || system.id)}</div>
            <div class="item-text mono">${escapeHtml(system.id)}</div>
          </div>
          <span class="tag ${statusTone(system.status) === "online" ? "good" : (statusTone(system.status) === "warn" ? "warn" : "bad")}">
            <span class="dot ${statusTone(system.status)}"></span>${escapeHtml(system.status)}
          </span>
        </div>
        <div class="meta-grid">
          <div><div class="small">OS</div><div>${escapeHtml(system.os_distribution || "Unknown")}</div></div>
          <div><div class="small">Kernel</div><div>${escapeHtml(system.kernel || "Unknown")}</div></div>
          <div><div class="small">CPUs</div><div>${Number(system.cpu_count || 0)}</div></div>
          <div><div class="small">Interfaces</div><div>${(system.interfaces || []).length}</div></div>
          <div><div class="small">Mode</div><div>${escapeHtml(system.mode || "remote")}</div></div>
          <div><div class="small">Last Seen</div><div>${fmtAgo(system.last_seen)}</div></div>
        </div>
        <div class="button-row" style="margin-top:16px;">
          <button class="action-button ${selected ? "ghost" : "primary"}" data-open-dashboard="${escapeHtml(system.id)}">Open Dashboard</button>
        </div>
      </div>
    `;
  }).join("");
  [...root.querySelectorAll("[data-open-dashboard]")].forEach(node => {
    node.onclick = async () => {
      await openDashboard(node.dataset.openDashboard);
    };
  });
}

async function openDashboard(sutId) {
  state.host = sutId;
  state.view = "overview";
  state.selectedIface = "ALL";
  state.selectedCpu = "";
  persistSelections();
  storage.set("irqlens:view", state.view);
  await loadSelectedData();
  render();
}

function renderIrq() {
  const rows = filterIrqRows();
  const topRows = rows.slice(0, 12);
  qs("irq-ranking").innerHTML = topRows.length ? topRows.map((row, index) => `
    <div class="rank-item">
      <div class="rank-index">${index + 1}</div>
      <div class="rank-main">
        <div class="item-title">${escapeHtml(row.irq_name || row.irq)}</div>
        <div class="muted mono">IRQ ${escapeHtml(row.irq)} • CPU ${topCpuForIrq(row).cpu} • ${escapeHtml(row.source_class || "other")}</div>
      </div>
      <div class="rank-value">${fmtCount(row.total_rate)}/s</div>
    </div>
  `).join("") : emptyState("No IRQ data", "No IRQ samples are available for the selected SUT and time context.");

  renderTopIrqChart(topRows);
  renderIrqHeatmap(rows.slice(0, 40));

  qs("irq-table").innerHTML = rows.length ? rows.map(row => {
    const topCpu = topCpuForIrq(row);
    return `<tr>
      <td class="mono">${escapeHtml(row.irq)}</td>
      <td>${escapeHtml(row.irq_name || "N/A")}</td>
      <td>${fmtCount(row.total_rate)}</td>
      <td class="mono">${escapeHtml(topCpu.cpu)}</td>
      <td>${fmtCount(topCpu.rate)}</td>
      <td>${escapeHtml(row.nic || "Mapping unavailable")}</td>
      <td>${escapeHtml(row.direction || "Other")}</td>
      <td>${escapeHtml(row.numa_node || "N/A")}</td>
      <td class="mono">${escapeHtml(row.affinity_list || "N/A")}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9">No IRQ rows available.</td></tr>`;
}

function filterIrqRows() {
  const rows = [...(state.snapshot?.irq?.rows || [])];
  const query = state.irqSearch.trim().toLowerCase();
  let filtered = rows;
  if (query) {
    filtered = filtered.filter(row => [row.irq, row.irq_name, row.device, row.nic, row.source_class].join(" ").toLowerCase().includes(query));
  }
  if (state.selectedIface !== "ALL") {
    filtered = filtered.filter(row => String(row.nic || "") === state.selectedIface);
  }
  if (state.selectedCpu) {
    filtered = filtered.filter(row => Number((row.cpu_rates || {})[state.selectedCpu] || 0) > 0);
  }
  filtered.sort((a, b) => {
    if (state.irqSort === "name") return String(a.irq_name || "").localeCompare(String(b.irq_name || ""));
    if (state.irqSort === "irq") return String(a.irq).localeCompare(String(b.irq));
    return Number(b.total_rate || 0) - Number(a.total_rate || 0);
  });
  return filtered;
}

function topCpuForIrq(row) {
  const entries = Object.entries(row.cpu_rates || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
  const top = entries[0] || ["N/A", 0];
  return { cpu: String(top[0]), rate: Number(top[1] || 0) };
}

function renderNetwork() {
  const interfaces = state.snapshot?.network?.interfaces || [];
  const selector = qs("network-interface-selector");
  const options = [`<option value="ALL">All Interfaces</option>`].concat(interfaces.map(item => `<option value="${escapeHtml(item.interface)}">${escapeHtml(item.interface)}</option>`));
  selector.innerHTML = options.join("");
  if (!interfaces.find(item => item.interface === state.selectedIface) && state.selectedIface !== "ALL") {
    state.selectedIface = "ALL";
  }
  selector.value = state.selectedIface || "ALL";

  qs("network-overview-list").innerHTML = interfaces.length ? interfaces
    .slice()
    .sort((a, b) => Number(b.rx_bps || 0) - Number(a.rx_bps || 0))
    .map(item => `
      <button class="rank-item" data-select-iface="${escapeHtml(item.interface)}">
        <div class="rank-main">
          <div class="item-title mono">${escapeHtml(item.interface)}</div>
          <div class="muted">RX ${fmtBytesRate(item.rx_bps)} • TX ${fmtBytesRate(item.tx_bps)}</div>
        </div>
        <div class="rank-value">${Number(item.rx_drop_ps || 0) + Number(item.tx_drop_ps || 0) > 0 ? "Drops" : "Healthy"}</div>
      </button>
    `).join("") : emptyState("No interfaces", "No interface samples are available for the selected SUT.");
  [...qs("network-overview-list").querySelectorAll("[data-select-iface]")].forEach(node => {
    node.onclick = async () => {
      state.selectedIface = node.dataset.selectIface;
      persistSelections();
      await loadInterfaceHistory();
      render();
    };
  });

  const iface = selectedIfaceRow();
  const title = state.selectedIface === "ALL" ? "All Interfaces" : escapeHtml(state.selectedIface);
  qs("network-current-title").textContent = title;
  if (!iface && state.selectedIface !== "ALL") {
    qs("network-current-cards").innerHTML = emptyState("Interface unavailable", "The selected interface is not present in current telemetry.");
  } else {
    const source = iface || { rx_bps: 0, tx_bps: 0, rx_pps: 0, tx_pps: 0, rx_err_ps: 0, tx_err_ps: 0, rx_drop_ps: 0, tx_drop_ps: 0 };
    qs("network-current-cards").innerHTML = [
      { label: "RX", value: fmtBytesRate(source.rx_bps), note: "Receive throughput" },
      { label: "TX", value: fmtBytesRate(source.tx_bps), note: "Transmit throughput" },
      { label: "RX Packets", value: fmtCount(source.rx_pps), note: "Packets per second" },
      { label: "TX Packets", value: fmtCount(source.tx_pps), note: "Packets per second" },
      { label: "Errors", value: fmtCount(Number(source.rx_err_ps || 0) + Number(source.tx_err_ps || 0)), note: "RX + TX errors" },
      { label: "Drops", value: fmtCount(Number(source.rx_drop_ps || 0) + Number(source.tx_drop_ps || 0)), note: "RX + TX drops" },
    ].map(item => `
      <div class="kpi-card">
        <div class="kpi-label">${item.label}</div>
        <div class="kpi-value">${item.value}</div>
        <div class="kpi-note">${item.note}</div>
      </div>
    `).join("");
  }

  renderNetworkTrend();
  renderNetworkRelatedIrqs();
  renderNetworkMeta();
}

function renderNetworkTrend() {
  const rows = networkHistoryRows();
  const usingInterface = state.selectedIface !== "ALL";
  if (!rows.length) {
    clearChart("networkTrend");
    qs("network-trend-empty").classList.remove("hidden");
    qs("network-trend-empty").innerHTML = usingInterface
      ? emptyState("No network history available yet", "IRQLENS needs more samples before it can show historical traffic for this interface.")
      : emptyState("Collecting history", "IRQLENS needs a few samples before historical traffic can be displayed.");
    return;
  }
  qs("network-trend-empty").classList.add("hidden");
  const rxKey = usingInterface ? "rx_bps" : "rx_bps";
  const txKey = usingInterface ? "tx_bps" : "tx_bps";
  const rx = rows.map(item => [Number(item.timestamp || 0) * 1000, Number(item[rxKey] || 0)]);
  const tx = rows.map(item => [Number(item.timestamp || 0) * 1000, Number(item[txKey] || 0)]);
  renderDualTrendChart("networkTrend", "chart-network-trend", rx, tx, "RX", "TX");
}

function renderNetworkRelatedIrqs() {
  const iface = state.selectedIface === "ALL" ? selectedIfaceRow()?.interface : state.selectedIface;
  const rows = relatedIrqsForIface(iface);
  const root = qs("network-related-irqs");
  if (!iface) {
    root.innerHTML = emptyState("No interface selected", "Choose an interface to inspect related IRQs.");
    return;
  }
  if (!rows.length) {
    root.innerHTML = emptyState("IRQ mapping unavailable", "Reliable IRQ mapping is not available for this interface on the current SUT.");
    return;
  }
  root.innerHTML = rows
    .slice()
    .sort((a, b) => Number(b.total_rate || 0) - Number(a.total_rate || 0))
    .slice(0, 12)
    .map(row => `
      <div class="irq-item">
        <div class="item-title">${escapeHtml(row.irq_name || row.irq)}</div>
        <div class="item-text">IRQ ${escapeHtml(row.irq)} • ${escapeHtml(row.direction || "Other")} • ${fmtCount(row.total_rate)}/s • CPU ${topCpuForIrq(row).cpu}</div>
      </div>
    `).join("");
}

function renderNetworkMeta() {
  const ifaceMeta = (state.interfacesMeta || []).find(item => item.name === state.selectedIface);
  const current = selectedIfaceRow();
  const root = qs("network-meta");
  if (state.selectedIface === "ALL") {
    root.innerHTML = emptyState("All interfaces selected", "Select one interface to view metadata, errors, drops, and IRQ relationships.");
    return;
  }
  const sections = [];
  if (current) {
    sections.push(`
      <div class="detail-panel">
        <div class="item-title">Packet Statistics</div>
        <div class="detail-list">
          <div class="key-value"><span>RX packets</span><strong>${fmtCount(current.rx_pps)}</strong></div>
          <div class="key-value"><span>TX packets</span><strong>${fmtCount(current.tx_pps)}</strong></div>
          <div class="key-value"><span>RX errors</span><strong>${fmtCount(current.rx_err_ps)}</strong></div>
          <div class="key-value"><span>TX errors</span><strong>${fmtCount(current.tx_err_ps)}</strong></div>
          <div class="key-value"><span>RX drops</span><strong>${fmtCount(current.rx_drop_ps)}</strong></div>
          <div class="key-value"><span>TX drops</span><strong>${fmtCount(current.tx_drop_ps)}</strong></div>
        </div>
      </div>
    `);
  }
  if (ifaceMeta) {
    sections.push(`
      <div class="detail-panel">
        <div class="item-title">Interface Metadata</div>
        <div class="detail-list">
          <div class="key-value"><span>State</span><strong>${escapeHtml(ifaceMeta.state || "N/A")}</strong></div>
          <div class="key-value"><span>MTU</span><strong>${ifaceMeta.mtu ?? "N/A"}</strong></div>
          <div class="key-value"><span>Speed</span><strong>${ifaceMeta.speed_mbps ?? "N/A"} Mbps</strong></div>
          <div class="key-value"><span>Driver</span><strong>${escapeHtml(ifaceMeta.driver || "N/A")}</strong></div>
          <div class="key-value"><span>IPv4</span><strong>${escapeHtml((ifaceMeta.ipv4 || []).join(", ") || "N/A")}</strong></div>
        </div>
      </div>
    `);
  }
  root.innerHTML = sections.join("") || emptyState("Metadata unavailable", "No interface metadata is available for the selected SUT.");
}

function renderCpu() {
  [...document.querySelectorAll("[data-cpu-metric]")].forEach(node => {
    const active = node.dataset.cpuMetric === state.cpuMetric;
    node.classList.toggle("active", active);
  });
  renderTopologyMap("cpu-topology", { compact: false });
  renderCpuDetail();
  renderCpuHotList();
  renderCpuNumaBars();
}

function renderTopologyMap(containerId, options) {
  const root = qs(containerId);
  const topo = state.topology;
  if (!currentSystem()) {
    root.innerHTML = emptyState("No SUT selected", "Select a SUT to load CPU and NUMA topology.");
    return;
  }
  if (!topo) {
    root.innerHTML = emptyState("Loading topology", "Fetching CPU topology metadata for the selected SUT.");
    return;
  }
  if (!topo.available || !(topo.rows || []).length) {
    root.innerHTML = emptyState("Topology unavailable", topo.reason || "The SUT did not expose sufficient topology information.");
    return;
  }
  const cpuActivity = aggregateCpuActivity();
  const metricKey = state.cpuMetric === "softirq" ? "softirq" : "irq";
  const values = (topo.rows || []).map(row => Number((cpuActivity[String(row.cpu_id)] || {})[metricKey] || 0));
  const max = Math.max(1, ...values);
  const grouped = groupTopologyRows(topo.rows || []);
  const compact = !!options.compact;
  root.innerHTML = `
    <div class="topology-shell">
      <div class="topology-heading">
        <div>
          <h3 class="card-title">${compact ? "CPU / NUMA Activity" : "CPU Topology"}</h3>
          <div class="card-subtitle">Metric: ${metricKey === "irq" ? "IRQ Rate" : "SoftIRQ Rate"}</div>
        </div>
        <div class="legend"><span>Low</span><div class="legend-bar"></div><span>High</span></div>
      </div>
      <div class="topology-group">
        ${grouped.map(numa => `
          <div class="topology-node">
            <div class="topology-heading">
              <div>
                <h4>NUMA ${numa.numa}</h4>
                <div class="card-subtitle">${numa.packages.length} package group${numa.packages.length === 1 ? "" : "s"}</div>
              </div>
              <span class="tag info">${fmtCount(numa.totalRate)}/s</span>
            </div>
            <div class="topology-group">
              ${numa.packages.map(pkg => `
                <div class="core-card">
                  <div class="topology-heading">
                    <h5>Package ${pkg.package}</h5>
                    <span class="tag">${fmtCount(pkg.totalRate)}/s</span>
                  </div>
                  <div class="core-grid">
                    ${pkg.cores.map(core => `
                      <div class="core-card">
                        <div class="item-title">Core ${core.core}</div>
                        <div class="cpu-grid">
                          ${core.cpus.map(cpu => {
                            const entry = cpuActivity[String(cpu.cpu_id)] || { irq: 0, softirq: 0, total: 0 };
                            const metricValue = Number(entry[metricKey] || 0);
                            const level = intensityLevel(metricValue, max);
                            const active = String(cpu.cpu_id) === String(state.selectedCpu) ? "active" : "";
                            return `
                              <button class="cpu-cell ${active}" data-level="${level}" data-cpu="${cpu.cpu_id}">
                                <span class="cpu-name">CPU ${cpu.cpu_id}</span>
                                <span class="cpu-metric">IRQ ${fmtCount(entry.irq)}/s</span>
                                <span class="cpu-submetric">SoftIRQ ${fmtCount(entry.softirq)}/s</span>
                              </button>
                            `;
                          }).join("")}
                        </div>
                      </div>
                    `).join("")}
                  </div>
                </div>
              `).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
  [...root.querySelectorAll("[data-cpu]")].forEach(node => {
    node.onclick = () => {
      state.selectedCpu = node.dataset.cpu;
      persistSelections();
      if (state.view !== "cpu") {
        state.view = "cpu";
        storage.set("irqlens:view", state.view);
      }
      render();
    };
  });
}

function intensityLevel(value, max) {
  if (max <= 0) return 0;
  const ratio = Math.max(0, Math.min(1, value / max));
  if (ratio >= 0.85) return 5;
  if (ratio >= 0.65) return 4;
  if (ratio >= 0.45) return 3;
  if (ratio >= 0.25) return 2;
  if (ratio >= 0.1) return 1;
  return 0;
}

function groupTopologyRows(rows) {
  const cpuActivity = aggregateCpuActivity();
  const map = new Map();
  rows.slice().sort((a, b) => Number(a.cpu_id) - Number(b.cpu_id)).forEach(row => {
    const numaKey = row.numa_node ?? "N/A";
    const packageKey = row.socket_id ?? "N/A";
    const coreKey = row.core_id ?? "N/A";
    if (!map.has(numaKey)) {
      map.set(numaKey, { numa: numaKey, totalRate: 0, packages: new Map() });
    }
    const numa = map.get(numaKey);
    if (!numa.packages.has(packageKey)) {
      numa.packages.set(packageKey, { package: packageKey, totalRate: 0, cores: new Map() });
    }
    const pkg = numa.packages.get(packageKey);
    if (!pkg.cores.has(coreKey)) {
      pkg.cores.set(coreKey, { core: coreKey, cpus: [] });
    }
    const activity = cpuActivity[String(row.cpu_id)] || { irq: 0, softirq: 0, total: 0 };
    numa.totalRate += activity.irq;
    pkg.totalRate += activity.irq;
    pkg.cores.get(coreKey).cpus.push(row);
  });
  return [...map.values()].map(numa => ({
    numa: numa.numa,
    totalRate: numa.totalRate,
    packages: [...numa.packages.values()].map(pkg => ({
      package: pkg.package,
      totalRate: pkg.totalRate,
      cores: [...pkg.cores.values()],
    })),
  }));
}

function renderCpuDetail() {
  const root = qs("cpu-detail");
  const selected = state.selectedCpu;
  if (!selected) {
    root.innerHTML = emptyState("Choose a CPU", "Select a CPU from the topology map to inspect IRQ ownership, NUMA placement, and top IRQ sources.");
    return;
  }
  const topoRows = state.topology?.rows || [];
  const topo = topoRows.find(row => String(row.cpu_id) === String(selected));
  const activity = aggregateCpuActivity()[String(selected)] || { irq: 0, softirq: 0, total: 0 };
  const irqRows = (state.snapshot?.irq?.rows || [])
    .filter(row => Number((row.cpu_rates || {})[selected] || 0) > 0)
    .sort((a, b) => Number((b.cpu_rates || {})[selected] || 0) - Number((a.cpu_rates || {})[selected] || 0))
    .slice(0, 10);
  root.innerHTML = `
    <div class="detail-panel">
      <div class="item-title">CPU ${escapeHtml(selected)}</div>
      <div class="detail-list">
        <div class="key-value"><span>IRQ Rate</span><strong>${fmtCount(activity.irq)}/s</strong></div>
        <div class="key-value"><span>SoftIRQ Rate</span><strong>${fmtCount(activity.softirq)}/s</strong></div>
        <div class="key-value"><span>CPU Utilization</span><strong>Unavailable</strong></div>
        <div class="key-value"><span>NUMA Node</span><strong>${topo ? topo.numa_node : "Unavailable"}</strong></div>
        <div class="key-value"><span>Core</span><strong>${topo ? topo.core_id : "Unavailable"}</strong></div>
        <div class="key-value"><span>Package</span><strong>${topo ? topo.socket_id : "Unavailable"}</strong></div>
      </div>
    </div>
    <div class="detail-panel" style="margin-top:14px;">
      <div class="item-title">Top IRQs</div>
      <div class="stack-list">
        ${irqRows.length ? irqRows.map(row => `
          <div class="irq-item">
            <div class="item-title">${escapeHtml(row.irq_name || row.irq)}</div>
            <div class="item-text">IRQ ${escapeHtml(row.irq)} • ${fmtCount((row.cpu_rates || {})[selected])}/s • ${escapeHtml(row.nic || row.source_class || "other")}</div>
          </div>
        `).join("") : `<div class="muted">No IRQ rows mapped to this CPU in the current snapshot.</div>`}
      </div>
    </div>
  `;
}

function renderCpuHotList() {
  const root = qs("cpu-hot-list");
  const entries = Object.entries(aggregateCpuActivity()).sort((a, b) => b[1].irq - a[1].irq).slice(0, 12);
  root.innerHTML = entries.length ? entries.map((item, index) => `
    <button class="rank-item" data-cpu-select="${item[0]}">
      <div class="rank-index">${index + 1}</div>
      <div class="rank-main">
        <div class="item-title">CPU ${item[0]}</div>
        <div class="muted">IRQ ${fmtCount(item[1].irq)}/s • SoftIRQ ${fmtCount(item[1].softirq)}/s</div>
      </div>
      <div class="rank-value">${fmtCount(item[1].total)}/s</div>
    </button>
  `).join("") : emptyState("No CPU activity", "No CPU IRQ activity is available yet.");
  [...root.querySelectorAll("[data-cpu-select]")].forEach(node => {
    node.onclick = () => {
      state.selectedCpu = node.dataset.cpuSelect;
      persistSelections();
      render();
    };
  });
}

function renderCpuNumaBars() {
  const root = qs("cpu-numa-bars");
  const bucket = {};
  const rows = state.snapshot?.irq?.rows || [];
  rows.forEach(row => {
    const key = row.numa_node || "N/A";
    bucket[key] = (bucket[key] || 0) + Number(row.total_rate || 0);
  });
  const labels = Object.keys(bucket);
  if (!labels.length) {
    root.innerHTML = emptyState("No NUMA data", "NUMA labels are not available in current IRQ metadata.");
    return;
  }
  const max = Math.max(...labels.map(label => bucket[label]));
  root.innerHTML = `<div class="bar-stack">${labels.map(label => `
    <div class="bar-row">
      <div>NUMA ${escapeHtml(label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${((bucket[label] / max) * 100).toFixed(1)}%"></div></div>
      <div>${fmtCount(bucket[label])}/s</div>
    </div>
  `).join("")}</div>`;
}

function renderDiagnostics() {
  const system = currentSystem();
  qs("diag-selected-sut").textContent = system ? (system.name || system.id) : "No SUT selected";
  qs("diag-checkboxes").innerHTML = [
    { key: "irq", title: "IRQ", text: "Capture current interrupt distribution." },
    { key: "softirq", title: "SoftIRQ", text: "Capture current softirq summary." },
    { key: "network", title: "Network", text: "Capture current interface traffic." },
    { key: "system", title: "System information", text: "Capture system identity and baseline details." },
    { key: "interfaces", title: "Interface metadata", text: "Capture NIC metadata and addressing." },
    { key: "ethtool", title: "Network commands", text: "Capture ethtool output when supported." },
  ].map(item => `
    <label class="checkbox-card">
      <input type="checkbox" data-diag-cat="${item.key}" ${state.diag.categories.includes(item.key) ? "checked" : ""} />
      <span>
        <strong>${item.title}</strong><br />
        <span class="muted">${item.text}</span>
      </span>
    </label>
  `).join("");
  [...qs("diag-checkboxes").querySelectorAll("[data-diag-cat]")].forEach(node => {
    node.onchange = () => {
      const values = [...qs("diag-checkboxes").querySelectorAll("[data-diag-cat]:checked")].map(box => box.dataset.diagCat);
      state.diag.categories = values;
    };
  });

  qs("diag-progress").innerHTML = renderDiagProgressHtml();
  qs("diag-complete").innerHTML = renderDiagCompleteHtml();
}

function renderDiagProgressHtml() {
  if (!state.diag.running) {
    return emptyState("Ready to capture", "Start a diagnostic capture to collect evidence for the selected SUT.");
  }
  const remaining = Math.max(0, Math.round(state.diag.endsAt - nowSeconds()));
  const elapsed = Math.max(0, Math.round(nowSeconds() - state.diag.startedAt));
  return `
    <div class="progress-card">
      <div class="item-title">Capturing...</div>
      <div class="progress-timer">${elapsed}s / ${state.diag.duration}s</div>
      <div class="muted">Capture window closes in ${remaining}s. Files are being collected for session ${escapeHtml(state.diag.sessionId)}.</div>
      <div class="progress-list">
        ${state.diag.categories.map(cat => `<div class="progress-item"><span>${escapeHtml(cat)}</span><strong>✓</strong></div>`).join("")}
      </div>
    </div>
  `;
}

function renderDiagCompleteHtml() {
  if (!state.diag.completedSessionId) {
    return emptyState("No completed capture yet", "Completed captures will appear here with quick actions.");
  }
  return `
    <div class="detail-panel">
      <div class="item-title">Capture complete</div>
      <div class="muted">Session ${escapeHtml(state.diag.completedSessionId)}</div>
      <div class="button-row" style="margin-top:14px;">
        <button class="action-button ghost" id="diag-view-session">View Session</button>
        <a class="action-button primary" href="${api("/api/sessions/" + encodeURIComponent(state.diag.completedSessionId) + "/download")}">Download ZIP</a>
      </div>
    </div>
  `;
}

async function startCapture() {
  if (!state.host) {
    return;
  }
  const body = { categories: state.diag.categories, sut_id: state.host };
  const response = await fetch(api("/api/sessions/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  state.diag.running = true;
  state.diag.sessionId = data.session?.session_id || "";
  state.diag.files = data.files || [];
  state.diag.startedAt = nowSeconds();
  state.diag.endsAt = state.diag.startedAt + Number(state.diag.duration || 60);
  state.diag.completedSessionId = "";
  if (state.diag.timer) clearInterval(state.diag.timer);
  state.diag.timer = setInterval(async () => {
    if (nowSeconds() >= state.diag.endsAt) {
      clearInterval(state.diag.timer);
      state.diag.timer = null;
      await stopCapture(true);
      return;
    }
    renderDiagnostics();
    bindDiagnosticsActions();
  }, 1000);
  renderDiagnostics();
  bindDiagnosticsActions();
  await loadSessions();
}

async function stopCapture(autoComplete = false) {
  if (!state.diag.sessionId) return;
  await fetch(api("/api/sessions/" + encodeURIComponent(state.diag.sessionId) + "/stop"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: autoComplete ? "duration-complete" : "manual" }),
  });
  state.diag.running = false;
  state.diag.completedSessionId = state.diag.sessionId;
  state.diag.sessionId = "";
  await loadSessions();
  if (state.selectedSessionId !== state.diag.completedSessionId) {
    state.selectedSessionId = state.diag.completedSessionId;
  }
  renderDiagnostics();
  bindDiagnosticsActions();
}

function bindDiagnosticsActions() {
  const viewButton = qs("diag-view-session");
  if (viewButton) {
    viewButton.onclick = async () => {
      state.view = "sessions";
      storage.set("irqlens:view", state.view);
      state.selectedSessionId = state.diag.completedSessionId;
      await loadSessionDetail(state.selectedSessionId);
      render();
    };
  }
}

async function loadSessions() {
  state.loading.sessions = true;
  const data = await fetchJson("/api/sessions");
  state.sessions = (data.sessions || []).filter(session => !state.host || session.sut_id === state.host || !session.sut_id);
  state.loading.sessions = false;
}

async function loadSessionDetail(sessionId) {
  if (!sessionId) return;
  const [detail, files] = await Promise.all([
    fetchJson("/api/sessions/" + encodeURIComponent(sessionId)),
    fetchJson("/api/sessions/" + encodeURIComponent(sessionId) + "/files"),
  ]);
  state.selectedSessionId = sessionId;
  state.sessionDetail = detail;
  state.sessionFiles = files.files || [];
}

function renderSessions() {
  const listRoot = qs("sessions-list");
  const detailRoot = qs("session-detail");
  if (state.loading.sessions) {
    listRoot.innerHTML = emptyState("Loading sessions", "Fetching diagnostic capture history.");
    detailRoot.innerHTML = emptyState("Select a session", "Choose a session to inspect summary, files, and download options.");
    return;
  }
  if (!state.sessions.length) {
    listRoot.innerHTML = emptyState("No diagnostic sessions", "Start a capture from Diagnostics to create the first session.");
    detailRoot.innerHTML = emptyState("Select a session", "Choose a session after captures are available.");
    return;
  }
  listRoot.innerHTML = state.sessions.map(session => {
    const selected = session.session_id === state.selectedSessionId;
    const duration = session.end_time ? Math.max(0, Math.round(session.end_time - session.start_time)) : 0;
    return `
      <button class="session-item" data-session="${escapeHtml(session.session_id)}">
        <div class="item-title">${new Date(session.start_time * 1000).toLocaleString()}</div>
        <div class="item-text">${escapeHtml(session.hostname)}${session.sut_id ? ` • ${escapeHtml(session.sut_id)}` : ""}</div>
        <div class="summary-grid" style="margin-top:10px;">
          <span class="tag ${selected ? "info" : ""}">${escapeHtml(session.status)}</span>
          <span class="tag">Duration ${duration}s</span>
          <span class="tag">${(session.categories || []).length} data types</span>
        </div>
      </button>
    `;
  }).join("");
  [...listRoot.querySelectorAll("[data-session]")].forEach(node => {
    node.onclick = async () => {
      await loadSessionDetail(node.dataset.session);
      renderSessions();
      renderContextBar();
    };
  });

  if (!state.selectedSessionId) {
    detailRoot.innerHTML = emptyState("Select a session", "Choose a session to inspect summary, files, and download options.");
    return;
  }
  if (!state.sessionDetail || state.sessionDetail.session_id !== state.selectedSessionId) {
    detailRoot.innerHTML = emptyState("Loading session", "Fetching session detail and generated files.");
    return;
  }
  const grouped = groupFilesByCategory(state.sessionFiles);
  const detail = state.sessionDetail;
  const duration = detail.end_time ? Math.max(0, Math.round(detail.end_time - detail.start_time)) : 0;
  detailRoot.innerHTML = `
    <div class="detail-panel">
      <div class="card-head">
        <div>
          <h3 class="card-title">Session ${escapeHtml(detail.session_id)}</h3>
          <div class="card-subtitle">A user-created diagnostic capture for ${escapeHtml(detail.hostname)}${detail.sut_id ? ` (${escapeHtml(detail.sut_id)})` : ""}.</div>
        </div>
        <a class="action-button primary" href="${api("/api/sessions/" + encodeURIComponent(detail.session_id) + "/download")}">Download ZIP</a>
      </div>
      <div class="summary-grid">
        <div class="detail-panel"><div class="small">Status</div><div class="item-title">${escapeHtml(detail.status)}</div></div>
        <div class="detail-panel"><div class="small">Start</div><div class="item-title">${fmtTs(detail.start_time)}</div></div>
        <div class="detail-panel"><div class="small">End</div><div class="item-title">${fmtTs(detail.end_time)}</div></div>
        <div class="detail-panel"><div class="small">Duration</div><div class="item-title">${duration}s</div></div>
      </div>
      <div class="detail-panel" style="margin-top:16px;">
        <div class="item-title">Captured Data</div>
        <div class="button-row" style="margin-top:10px;">${(detail.categories || []).map(cat => `<span class="tag good">✓ ${escapeHtml(cat)}</span>`).join("")}</div>
      </div>
      <div class="detail-panel" style="margin-top:16px;">
        <div class="item-title">Session Files</div>
        <div class="file-list" style="margin-top:14px;">
          ${Object.keys(grouped).length ? Object.entries(grouped).map(([category, files]) => `
            <div class="file-group">
              <div class="item-title">${escapeHtml(category)}</div>
              <div class="stack-list">
                ${files.map(file => `<div class="key-value"><span>${escapeHtml(file.name)}</span><a href="${api("/api/files?path=" + encodeURIComponent(file.path))}">Download file</a></div>`).join("")}
              </div>
            </div>
          `).join("") : `<div class="muted">No files were recorded for this session.</div>`}
        </div>
      </div>
    </div>
  `;
}

function groupFilesByCategory(files) {
  return (files || []).reduce((acc, file) => {
    const key = file.category || "other";
    acc[key] = acc[key] || [];
    acc[key].push(file);
    return acc;
  }, {});
}

function renderSettings() {
  const root = qs("settings-content");
  const health = state.health;
  if (!health) {
    root.innerHTML = emptyState("Loading settings", "Fetching backend health and dependency information.");
    return;
  }
  root.innerHTML = `
    <div class="detail-panel">
      <div class="item-title">Backend Health</div>
      <div class="detail-list">
        <div class="key-value"><span>Collector</span><strong>${escapeHtml(health.collector_status || "N/A")}</strong></div>
        <div class="key-value"><span>Database</span><strong>${escapeHtml(health.database_status || "N/A")}</strong></div>
        <div class="key-value"><span>WebSocket</span><strong>${escapeHtml(health.websocket_status || "N/A")}</strong></div>
        <div class="key-value"><span>Collection Interval</span><strong>${Number(health.interval_seconds || 1)} sec</strong></div>
      </div>
    </div>
    <div class="detail-panel" style="margin-top:16px;">
      <div class="item-title">Dependencies</div>
      <div class="stack-list" style="margin-top:12px;">
        ${(health.dependencies || []).map(dep => `<div class="key-value"><span>${escapeHtml(dep.name)}</span><strong>${dep.available ? "Available" : "Missing"}</strong></div>`).join("")}
      </div>
    </div>
  `;
}

function render() {
  renderNavigation();
  renderTimeControls();
  renderContextBar();
  showSection(state.view);
  renderSystems();
  renderOverview();
  renderIrq();
  renderNetwork();
  renderCpu();
  renderDiagnostics();
  renderSessions();
  renderSettings();
  resizeCharts();
}

function ensureChart(key, elementId) {
  if (!window.echarts) return null;
  if (!state.charts[key]) {
    const node = qs(elementId);
    if (!node) return null;
    state.charts[key] = echarts.init(node);
  }
  return state.charts[key];
}

function clearChart(key) {
  if (state.charts[key]) {
    state.charts[key].clear();
  }
}

function resizeCharts() {
  Object.values(state.charts).forEach(chart => chart.resize());
}

function baseChart() {
  return {
    backgroundColor: "transparent",
    grid: { left: 54, right: 24, top: 26, bottom: 42 },
    textStyle: { color: "#1f2430", fontFamily: "Aptos, Segoe UI Variable Display, sans-serif" },
  };
}

function renderSimpleTrendChart(key, elementId, data, seriesName, yName, color) {
  const chart = ensureChart(key, elementId);
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "axis" },
    xAxis: { type: "time", axisLabel: { color: "#6b7280" } },
    yAxis: { type: "value", name: yName, axisLabel: { color: "#6b7280" } },
    series: [{ name: seriesName, type: "line", showSymbol: false, smooth: true, data, lineStyle: { width: 3, color }, areaStyle: { color: withAlpha(color, 0.12) } }],
  });
}

function renderDualTrendChart(key, elementId, first, second, firstName, secondName) {
  const chart = ensureChart(key, elementId);
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#6b7280" } },
    xAxis: { type: "time", axisLabel: { color: "#6b7280" } },
    yAxis: { type: "value", axisLabel: { color: "#6b7280" } },
    series: [
      { name: firstName, type: "line", showSymbol: false, smooth: true, data: first, lineStyle: { width: 3, color: "#2368d1" }, areaStyle: { color: "rgba(35,104,209,0.12)" } },
      { name: secondName, type: "line", showSymbol: false, smooth: true, data: second, lineStyle: { width: 3, color: "#237a57" }, areaStyle: { color: "rgba(35,122,87,0.12)" } },
    ],
  });
}

function renderTopIrqChart(rows) {
  const chart = ensureChart("irqTop", "chart-irq-top");
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "item" },
    xAxis: { type: "value", axisLabel: { color: "#6b7280" } },
    yAxis: { type: "category", data: rows.map(row => row.irq_name || row.irq), axisLabel: { color: "#6b7280" } },
    series: [{ type: "bar", data: rows.map(row => Number(row.total_rate || 0)), itemStyle: { color: "#2368d1", borderRadius: 8 } }],
  });
}

function renderIrqHeatmap(rows) {
  const chart = ensureChart("irqHeat", "chart-irq-heatmap");
  if (!chart) return;
  const cpus = [...new Set(rows.flatMap(row => Object.keys(row.cpu_rates || {})))].sort((a, b) => Number(a) - Number(b));
  if (!rows.length || !cpus.length) {
    chart.clear();
    return;
  }
  const values = [];
  rows.forEach((row, rowIndex) => {
    cpus.forEach((cpu, cpuIndex) => {
      const rate = Number((row.cpu_rates || {})[cpu] || 0);
      if (rate > 0) values.push([cpuIndex, rowIndex, rate, row]);
    });
  });
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: {
      formatter: params => {
        const row = params.data[3];
        return `${escapeHtml(row.irq_name || row.irq)}<br/>CPU ${cpus[params.data[0]]}<br/>IRQ ${fmtCount(params.data[2])}/s`;
      },
    },
    xAxis: { type: "category", data: cpus.map(cpu => `CPU ${cpu}`), axisLabel: { color: "#6b7280", interval: cpus.length > 24 ? Math.ceil(cpus.length / 24) : 0 } },
    yAxis: { type: "category", data: rows.map(row => row.irq), axisLabel: { color: "#6b7280" } },
    visualMap: { min: 0, max: Math.max(1, ...values.map(item => item[2])), orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#edf3ff", "#bfd6ff", "#2368d1"] } },
    series: [{ type: "heatmap", data: values }],
  });
}

function withAlpha(hex, alpha) {
  if (hex.startsWith("#") && hex.length === 7) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return hex;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadHealth() {
  state.health = await fetchJson("/api/health");
  state.staleMs = Math.max(3000, Number(state.health.interval_seconds || 1) * 3000);
}

async function loadSystems() {
  const data = await fetchJson("/api/systems");
  state.systems = data.systems || [];
  if (!state.host || !state.systems.some(item => item.id === state.host)) {
    const remote = state.systems.find(item => item.mode === "remote") || state.systems[0];
    state.host = remote ? remote.id : "";
  }
  persistSelections();
}

async function loadSelectedData() {
  if (!state.host) {
    state.snapshot = null;
    state.viz = null;
    state.topology = null;
    state.interfacesMeta = [];
    return;
  }
  const [irq, softirq, network, interfaces] = await Promise.all([
    fetchJson("/api/irq/current?sut_id=" + encodeURIComponent(state.host) + "&limit=2000"),
    fetchJson("/api/softirq/current?sut_id=" + encodeURIComponent(state.host)),
    fetchJson("/api/network/current?sut_id=" + encodeURIComponent(state.host)),
    fetchJson("/api/interfaces?sut_id=" + encodeURIComponent(state.host)),
  ]);
  state.snapshot = { irq, softirq, network };
  state.interfacesMeta = interfaces.interfaces || [];
  const bounds = timeBounds();
  const query = state.timeRange === "custom"
    ? `from_ts=${encodeURIComponent(bounds.from)}&to_ts=${encodeURIComponent(bounds.to)}`
    : `window_seconds=${encodeURIComponent(bounds.to - bounds.from)}`;
  state.viz = await fetchJson(`/api/systems/${encodeURIComponent(state.host)}/visualization?${query}&top_n=20`);
  state.topology = await fetchJson(`/api/systems/${encodeURIComponent(state.host)}/visualization/topology`);
  if (state.selectedIface !== "ALL") {
    await loadInterfaceHistory();
  } else {
    state.interfaceHistory = null;
  }
  state.lastDataAt = nowSeconds();
}

async function loadInterfaceHistory() {
  if (!state.host || !state.selectedIface || state.selectedIface === "ALL") {
    state.interfaceHistory = null;
    return;
  }
  state.loading.interfaceHistory = true;
  try {
    state.interfaceHistory = await fetchJson(`/api/network/${encodeURIComponent(state.selectedIface)}?sut_id=${encodeURIComponent(state.host)}`);
  } catch (_) {
    state.interfaceHistory = null;
  }
  state.loading.interfaceHistory = false;
}

async function refreshSelectedHistory() {
  if (!state.host) return;
  await loadSelectedData();
}

async function refreshAll() {
  state.loading.global = true;
  try {
    await loadHealth();
    await loadSystems();
    await loadSelectedData();
    if (state.view === "sessions") {
      await loadSessions();
      if (state.selectedSessionId) await loadSessionDetail(state.selectedSessionId);
    }
  } finally {
    state.loading.global = false;
  }
}

function bindGlobalActions() {
  el["refresh-button"].onclick = async () => {
    await refreshAll();
    render();
  };
  el["live-button"].onclick = () => {
    state.paused = !state.paused;
    renderContextBar();
  };
  el["custom-apply"].onclick = async () => {
    state.customFrom = el["custom-from"].value || "";
    state.customTo = el["custom-to"].value || "";
    persistSelections();
    await refreshSelectedHistory();
    render();
  };
  qs("irq-search").oninput = event => {
    state.irqSearch = event.target.value || "";
    renderIrq();
  };
  qs("irq-sort").onchange = event => {
    state.irqSort = event.target.value || "rate";
    renderIrq();
  };
  qs("network-interface-selector").onchange = async event => {
    state.selectedIface = event.target.value || "ALL";
    persistSelections();
    await loadInterfaceHistory();
    render();
  };
  [...document.querySelectorAll("[data-cpu-metric]")].forEach(node => {
    node.onclick = () => {
      if (node.dataset.cpuMetric === "load") return;
      state.cpuMetric = node.dataset.cpuMetric;
      persistSelections();
      render();
    };
  });
  qs("diag-duration").onchange = event => {
    state.diag.duration = Number(event.target.value || 60);
  };
  qs("diag-start").onclick = async () => {
    await startCapture();
  };
  qs("diag-stop").onclick = async () => {
    await stopCapture(false);
  };
}

function wireWebSocket() {
  const wsUrl = state.backend.replace(/^http/, "ws").replace(/\/$/, "") + "/ws";
  try {
    state.ws = new WebSocket(wsUrl);
  } catch (_) {
    state.wsStatus = "offline";
    renderContextBar();
    return;
  }
  state.wsStatus = "connecting";
  renderContextBar();
  state.ws.onopen = () => {
    state.wsStatus = "connected";
    renderContextBar();
  };
  state.ws.onclose = () => {
    state.wsStatus = "offline";
    renderContextBar();
    setTimeout(wireWebSocket, 1500);
  };
  state.ws.onerror = () => {
    state.wsStatus = "offline";
    renderContextBar();
  };
  state.ws.onmessage = async event => {
    if (state.paused) return;
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    const sutId = payload.sut_id || payload.host || "";
    if (!sutId || sutId === state.host || payload.type === "system_registered" || payload.type === "session_started" || payload.type === "session_stopped") {
      await loadSystems();
      if (state.host) await loadSelectedData();
      if (state.view === "sessions" || payload.type === "session_started" || payload.type === "session_stopped") {
        await loadSessions();
        if (state.selectedSessionId) await loadSessionDetail(state.selectedSessionId);
      }
      render();
    }
  };
}

async function boot() {
  initializeElements();
  renderNavigation();
  renderTimeControls();
  bindGlobalActions();
  await refreshAll();
  if (state.view === "network" && state.selectedIface !== "ALL") {
    await loadInterfaceHistory();
  }
  render();
  wireWebSocket();
  setInterval(() => {
    renderContextBar();
    if (state.diag.running) {
      renderDiagnostics();
      bindDiagnosticsActions();
    }
  }, 1000);
  setInterval(async () => {
    if (!state.paused && (!state.ws || state.ws.readyState !== WebSocket.OPEN)) {
      await refreshAll();
      render();
    }
  }, 5000);
  window.addEventListener("resize", resizeCharts);
}

boot();
