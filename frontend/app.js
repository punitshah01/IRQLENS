const PRIMARY_VIEWS = ["overview", "cpu", "irq", "network", "storage", "diagnostics"];
const NAV_LABELS = {
  overview: "Overview",
  cpu: "CPU",
  irq: "IRQ",
  network: "Network",
  storage: "Storage & PCIe",
  diagnostics: "Collect Logs",
};
const LIVE_RENDER_INTERVAL_MS = 1000;
// CPU topology and interface details rarely change.
const DETAILS_REFRESH_MS = 30000;
const IRQ_TOP_N_OPTIONS = ["25", "50", "100", "all"];
const IRQ_AGGREGATE_OPTIONS = ["cpu", "numa", "socket"];
// The entry of the SUT dropdown that opens the form instead of selecting a machine.
const ADD_MACHINE = "+add";
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

function initialViewFromUrl() {
  const fallback = "overview";
  try {
    const params = new URLSearchParams(window.location.search || "");
    const queryView = String(params.get("view") || "").trim().toLowerCase();
    if (PRIMARY_VIEWS.includes(queryView)) return queryView;
  } catch (_) {
    // Ignore malformed query params.
  }
  const rawHash = String(window.location.hash || "").trim();
  if (rawHash) {
    const normalized = rawHash.replace(/^#\/?/, "").split(/[?&]/, 1)[0].toLowerCase();
    if (PRIMARY_VIEWS.includes(normalized)) return normalized;
  }
  return fallback;
}

const state = {
  backend: window.location.origin,
  view: initialViewFromUrl(),
  host: storage.get("irqlens:host", ""),
  systems: [],
  sshMachines: [],
  canManageMachines: false,
  fleet: null,
  fleetRequest: null,
  health: null,
  snapshot: null,
  viz: null,
  io: null,
  topology: null,
  interfaceHistory: null,
  sessions: [],
  expandedSessionId: "",
  deletingSessions: {},
  reportingSessions: {},
  qualityRequests: {},
  qualityErrors: {},
  folds: new Map(),
  sessionDetail: null,
  sessionFiles: [],
  selectedSessionId: "",
  selectedIface: storage.get("irqlens:iface", "ALL"),
  selectedCpu: storage.get("irqlens:cpu", ""),
  cpuMetric: storage.get("irqlens:cpuMetric", "irq"),
  irqSearch: "",
  irqSort: "rate",
  irqTopN: storage.get("irqlens:irqTopN", "25"),
  irqAggregate: storage.get("irqlens:irqAggregate", "cpu"),
  timeRange: storage.get("irqlens:timeRange", "300"),
  customFrom: storage.get("irqlens:customFrom", ""),
  customTo: storage.get("irqlens:customTo", ""),
  paused: false,
  lastLiveRenderAt: 0,
  lastRefreshAt: 0,
  loadSeq: 0,
  historyKey: "",
  historyAt: 0,
  detailsHost: "",
  detailsAt: 0,
  pointerDownAt: 0,
  switcherSignature: "",
  ifaceSignature: null,
  staleMs: 5000,
  lastDataAt: 0,
  ws: null,
  wsStatus: "connecting",
  charts: {},
  loading: { global: false, sessions: false, interfaceHistory: false },
  refreshTimer: null,
  refreshInFlight: false,
  cpuUtilTimestamp: 0,
  diag: {
    running: false,
    starting: false,
    stopping: false,
    sessionId: "",
    sessionName: "",
    duration: 60,
    startedAt: 0,
    endsAt: 0,
    timer: null,
    categories: ["irq", "softirq", "network", "system", "interfaces", "ethtool", "storage", "pcie"],
    files: [],
    completedSessionId: "",
    error: "",
  },
};

if (!PRIMARY_VIEWS.includes(state.view)) {
  state.view = "overview";
  storage.set("irqlens:view", state.view);
}
if (!["irq", "softirq", "util"].includes(state.cpuMetric)) {
  state.cpuMetric = "irq";
  storage.set("irqlens:cpuMetric", state.cpuMetric);
}
if (!IRQ_TOP_N_OPTIONS.includes(state.irqTopN)) state.irqTopN = "25";
if (!IRQ_AGGREGATE_OPTIONS.includes(state.irqAggregate)) state.irqAggregate = "cpu";

const el = {};

function qs(id) {
  return document.getElementById(id);
}

function initializeElements() {
  [
    "primary-nav", "sut-title", "sut-status-dot", "sut-status-label",
    "sut-meta", "page-title", "page-subtitle", "breadcrumb", "ws-pill",
    "time-controls", "custom-range", "custom-from", "custom-to", "custom-apply",
    "section-overview", "section-irq", "section-network",
    "section-cpu", "section-diagnostics", "cpu-metric-selector", "cpu-legend",
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

// Next to a link speed, traffic reads in bits; samples arrive as bytes per second.
function fmtBitRate(bytesPerSecond) {
  const bits = Number(bytesPerSecond || 0) * 8;
  if (bits >= 1e9) return (bits / 1e9).toFixed(1) + " Gbit/s";
  if (bits >= 1e6) return (bits / 1e6).toFixed(1) + " Mbit/s";
  if (bits >= 1e3) return (bits / 1e3).toFixed(1) + " kbit/s";
  return bits.toFixed(0) + " bit/s";
}

function fmtLinkSpeed(mbps) {
  const n = Number(mbps || 0);
  if (!n) return "speed n/a";
  return n >= 1000 ? `${Number((n / 1000).toFixed(1))}G` : `${n}M`;
}

function fmtTs(ts) {
  if (!ts) return "N/A";
  return new Date(ts * 1000).toLocaleString();
}

function fmtDuration(secondsRaw) {
  const seconds = Math.max(0, Number(secondsRaw || 0));
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin ? `${hours} hr ${remMin} min` : `${hours} hr`;
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
    case "overview": return ["Overview", "See what is happening on this machine right now."];
    case "irq": return ["IRQ", "Trace which interrupts are driving activity."];
    case "network": return ["Network", "Inspect interface traffic, drops, errors, and related IRQs."];
    case "cpu": return ["CPU", "See which CPU and NUMA regions are absorbing IRQ activity."];
    case "storage": return ["Storage & PCIe", "Track block-device I/O and add-in card link health."];
    case "diagnostics": return ["Collect Logs", "Capture evidence for the selected SUT and review recent captures."];
    default: return ["IRQLENS", "Monitoring dashboard"];
  }
}

function renderNavigation() {
  const nav = el["primary-nav"];
  if (!nav.firstElementChild) {
    const renderButtons = views => views.map(view => `<button class="nav-button" data-view="${view}">${NAV_LABELS[view] || view}</button>`).join("");
    nav.innerHTML = `
      <div class="nav-stack">
        <div class="nav-label">Monitor</div>
        ${renderButtons(["overview", "cpu", "irq", "network", "storage"])}
        <div class="nav-label" style="margin-top:14px;">Operations</div>
        ${renderButtons(["diagnostics"])}
      </div>
    `;
    // Built once: a button that is replaced between press and release never gets its click.
    nav.onclick = event => {
      const button = event.target.closest("[data-view]");
      if (button) showView(button.dataset.view);
    };
  }
  nav.querySelectorAll("[data-view]").forEach(node => node.classList.toggle("active", node.dataset.view === state.view));
}

function showView(view) {
  state.view = view;
  storage.set("irqlens:view", state.view);
  // Draw at once from what is loaded; the capture list follows without holding up the click.
  const sessions = view === "diagnostics" ? loadSessions() : null;
  render();
  if (sessions) sessions.then(render, render);
}

function renderTimeControls() {
  if (!el["time-controls"]) return;
  el["time-controls"].innerHTML = TIME_RANGES.map(item => {
    const active = state.timeRange === item.key ? "time-chip active" : "time-chip";
    return `<button class="${active}" data-range="${item.key}">${item.label}</button>`;
  }).join("");
  [...el["time-controls"].querySelectorAll("[data-range]")].forEach(node => {
    node.onclick = async () => {
      state.timeRange = node.dataset.range;
      storage.set("irqlens:timeRange", state.timeRange);
      renderTimeControls();
      // Loaded samples are re-cut to the new range at once; the full history follows.
      render();
      await refreshSelectedHistory();
      render();
    };
  });
  if (el["custom-from"]) el["custom-from"].value = state.customFrom || "";
  if (el["custom-to"]) el["custom-to"].value = state.customTo || "";
  if (el["custom-range"]) el["custom-range"].classList.toggle("active", state.timeRange === "custom");
}

function timeBounds() {
  if (state.timeRange === "custom") {
    const fromMs = Date.parse(state.customFrom || "");
    const toMs = Date.parse(state.customTo || "");
    if (!Number.isNaN(fromMs) && !Number.isNaN(toMs)) {
      return { from: Math.min(fromMs, toMs) / 1000, to: Math.max(fromMs, toMs) / 1000 };
    }
  }
  const selected = TIME_RANGES.find(item => item.key === state.timeRange && item.seconds !== null) || TIME_RANGES[2];
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
  const title = system ? (system.hostname || system.name || system.id) : "No SUT selected";
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
    meta.push("Waiting for telemetry from a local or remote SUT");
  }
  el["sut-meta"].textContent = meta.join(" • ");
  const [titleText, subtitleText] = viewTitle(state.view);
  el["page-title"].textContent = titleText;
  el["page-subtitle"].textContent = subtitleText;
  el["breadcrumb"].textContent = buildBreadcrumb();
  el["ws-pill"].className = `tag ${wsTone()}`;
  el["ws-pill"].textContent = `WebSocket: ${state.wsStatus}`;
  renderSutSwitcher();
  const irqbalance = state.io?.irqbalance || state.viz?.irqbalance || "unknown";
  const balancePill = qs("irqbalance-pill");
  if (balancePill) {
    balancePill.className = irqbalance === "unknown" ? "tag" : "tag info";
    balancePill.textContent = `irqbalance: ${irqbalance}`;
  }
  const liveToggle = qs("live-toggle");
  if (liveToggle) {
    liveToggle.textContent = state.paused ? "Resume Live Updates" : "Pause Live Updates";
    liveToggle.className = `action-button ${state.paused ? "warn" : "ghost"}`;
    liveToggle.setAttribute("aria-pressed", state.paused ? "true" : "false");
  }
}

function renderSutSwitcher() {
  const select = qs("sut-switcher");
  if (!select) return;
  // Rebuilding on every tick would close an open dropdown.
  const signature = JSON.stringify([state.host, state.canManageMachines, state.systems.map(item => [item.id, item.hostname, item.name, item.status])]);
  if (signature === state.switcherSignature) return;
  state.switcherSignature = signature;
  select.innerHTML = (state.systems.length
    ? state.systems.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.hostname || item.name || item.id)} [${escapeHtml(item.status)}]</option>`).join("")
    : `<option value="">No SUT registered</option>`)
    // Offered only where the server accepts it: with a token, or in a browser on the server host.
    + (state.canManageMachines ? `<option value="${ADD_MACHINE}">+ Add machine\u2026</option>` : "");
  select.value = state.host || "";
}

function buildBreadcrumb() {
  const parts = [];
  const system = currentSystem();
  if (system) parts.push(system.hostname || system.name || system.id);
  parts.push(viewTitle(state.view)[0]);
  if (state.view === "network" && state.selectedIface && state.selectedIface !== "ALL") parts.push(state.selectedIface);
  if (state.view === "cpu" && state.selectedCpu) parts.push(`CPU ${state.selectedCpu}`);
  if (state.view === "diagnostics" && state.selectedSessionId) parts.push(state.selectedSessionId);
  return parts.join(" → ") || "Overview";
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
  const crossNuma = (state.snapshot?.irq?.rows || []).filter(row => row.cross_numa_flag);
  if (crossNuma.length) {
    const worst = crossNuma[0];
    findings.push({
      tone: "warn",
      title: "Cross-NUMA IRQ handling",
      text: `${crossNuma.length} IRQ(s) run on CPUs outside their device's NUMA node, e.g. ${worst.irq_name || worst.irq} (device node ${worst.numa_node}, handled on node ${(worst.handler_numa_nodes || []).join(", ")}).`,
      action: "View IRQ",
      target: "irq",
    });
  }
  const pcie = state.io?.pcie || {};
  if (Number(pcie.link_warnings || 0) > 0) {
    findings.push({
      tone: "bad",
      title: "PCIe link degraded",
      text: `${Number(pcie.link_warnings)} card(s) are running below their maximum link speed or width.`,
      action: "View Storage & PCIe",
      target: "storage",
    });
  }
  if (Number(pcie.aer_errors || 0) > 0) {
    findings.push({
      tone: Number(pcie.aer_increasing || 0) > 0 ? "bad" : "warn",
      title: "PCIe AER errors",
      text: `${Number(pcie.aer_errors)} AER error(s) recorded${Number(pcie.aer_increasing || 0) > 0 ? "; counters are still increasing." : "."}`,
      action: "View Storage & PCIe",
      target: "storage",
    });
  }
  const softnetDropped = Number(state.io?.softnet?.latest?.total?.dropped || 0);
  if (softnetDropped > 0) {
    findings.push({
      tone: "bad",
      title: "Softnet backlog drops",
      text: `${softnetDropped} packet(s) were dropped from per-CPU backlog queues in the last sample.`,
      action: "View Network",
      target: "network",
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
  return findings.slice(0, 6);
}

function healthSummaryText() {
  const findings = buildFindings();
  if (findings.some(item => item.tone === "bad")) return "Problem";
  if (findings.some(item => item.tone === "warn")) return "Warning";
  return "Healthy";
}

function emptyState(title, text, actionHtml = "") {
  return `<div class="empty-state"><h3 class="empty-title">${escapeHtml(title)}</h3><div class="muted">${escapeHtml(text)}</div>${actionHtml}</div>`;
}

function selectedIfaceRow() {
  const interfaces = state.snapshot?.network?.interfaces || [];
  if (!interfaces.length) return null;
  if (!state.selectedIface || state.selectedIface === "ALL") return null;
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
  if (view !== "cpu") {
    const tooltip = qs("cpu-hover-tooltip");
    if (tooltip) tooltip.classList.add("hidden");
  }
  ["overview", "irq", "network", "cpu", "storage", "diagnostics"].forEach(name => {
    const node = qs(`section-${name}`);
    node.classList.toggle("active", name === view);
  });
}

// Machine map: one box per machine and one pill per network that two or more of them share.
function renderFleet() {
  const card = qs("fleet-card");
  if (!card) return;
  const machines = state.fleet?.machines || [];
  const networks = state.fleet?.networks || [];
  // One machine that reports needs no map; one that cannot be reached has to say why somewhere.
  const shown = machines.length >= 2 || machines.some(machine => machine.connection && machine.connection.state !== "online");
  card.classList.toggle("hidden", !shown);
  if (!shown) return;
  const root = qs("fleet-map");
  const links = networks.flatMap((network, networkIndex) => network.members.map(member => ({ member, networkIndex })));
  // Built once per shape and updated in place, so a box never changes under the pointer.
  const shape = JSON.stringify([
    machines.map(machine => [machine.id, machine.hostname, machine.role, Boolean(machine.connection)]),
    networks.map(network => [network.cidr, network.members.map(member => [member.machine, member.nic, member.address])]),
    state.canManageMachines,
  ]);
  if (root.dataset.shape !== shape) {
    root.dataset.shape = shape;
    const box = machine => `
      <div class="fleet-machine" data-machine="${escapeHtml(machine.id)}" role="button" tabindex="0">
        <div class="fleet-head">
          <span class="fleet-role">${machine.role === "server" ? "Server" : "Client"}</span>
          <span class="fleet-name">${escapeHtml(machine.hostname)}</span>
          ${machine.connection && state.canManageMachines ? `<button class="fleet-remove" data-remove-machine="${escapeHtml(machine.id)}">Remove</button>` : ""}
          <span class="dot"></span>
        </div>
        <div class="fleet-stats"></div>
        <div class="fleet-problem"></div>
        ${links.map((link, index) => (link.member.machine !== machine.id ? "" : `
          <div class="fleet-nic" data-link="${index}">
            <div class="fleet-nic-head">
              <span class="fleet-speed"></span>
              <span class="mono">${escapeHtml(link.member.nic)}</span>
              <span class="muted mono">${escapeHtml(link.member.address)}</span>
            </div>
            <div class="fleet-nic-rate"></div>
          </div>
        `)).join("")}
      </div>
    `;
    const column = role => machines.filter(machine => (machine.role === "server") === (role === "server")).map(box).join("");
    const clients = column("client");
    const servers = column("server");
    const pills = networks.length ? networks.map(network => `
      <div class="fleet-network">
        <div class="mono">${escapeHtml(network.cidr)}</div>
        <div class="muted">${new Set(network.members.map(member => member.machine)).size} machines</div>
      </div>
    `).join("") : `<div class="fleet-note"></div>`;
    const columns = clients && servers ? [clients, pills, servers] : [clients || servers, pills];
    root.classList.toggle("one-sided", columns.length === 2);
    root.innerHTML = `
      <svg class="fleet-links" aria-hidden="true">${links.map((link, index) => `<path data-link="${index}" data-network="${link.networkIndex}"></path>`).join("")}</svg>
      ${columns.map(html => `<div class="fleet-column">${html}</div>`).join("")}
    `;
    root.onclick = event => {
      const remove = event.target.closest("[data-remove-machine]");
      const node = event.target.closest(".fleet-machine");
      if (remove) removeMachine(remove.dataset.removeMachine);
      else if (node) selectSut(node.dataset.machine);
    };
    root.onkeydown = event => {
      if ((event.key === "Enter" || event.key === " ") && event.target.classList.contains("fleet-machine")) {
        event.preventDefault();
        selectSut(event.target.dataset.machine);
      }
    };
  }

  const set = (node, key, value) => {
    if (node[key] !== value) node[key] = value;
  };
  const byId = new Map(machines.map(machine => [machine.id, machine]));
  // A machine this server connects to has no current numbers the moment its connection is not up.
  const connected = machine => !machine.connection || machine.connection.state === "online";
  const isLive = machine => machine.status === "ONLINE" && connected(machine);
  root.querySelectorAll(".fleet-machine").forEach(node => {
    const machine = byId.get(node.dataset.machine);
    const live = isLive(machine);
    const reported = state.systems.some(item => item.id === machine.id);
    const cpu = machine.cpu_util_avg == null ? "n/a" : `${Number(machine.cpu_util_avg).toFixed(0)}% avg, ${Number(machine.cpu_util_max).toFixed(0)}% max`;
    const irq = machine.irq_rate == null ? "n/a" : `${fmtCount(machine.irq_rate)}/s`;
    let stats = `${machine.status} • last update ${fmtAgo(machine.last_seen)}`;
    if (live) stats = `CPU ${cpu} • IRQ ${irq}`;
    else if (!connected(machine)) stats = machine.connection.state === "connecting" ? "Connecting over SSH…" : "Not connected";
    const problem = connected(machine) ? "" : [machine.connection.message, machine.connection.hint].filter(Boolean).join(" ");
    set(node, "className", `fleet-machine${machine.id === state.host ? " selected" : ""}${live ? "" : " stale"}${reported ? "" : " unreachable"}`);
    set(node.querySelector(".dot"), "className", `dot ${machine.status === "CONNECTING" ? "info" : statusTone(machine.status)}`);
    set(node.querySelector(".fleet-stats"), "textContent", stats);
    set(node.querySelector(".fleet-problem"), "textContent", problem);
  });
  const note = root.querySelector(".fleet-note");
  if (note) set(note, "textContent", machines.filter(isLive).length >= 2 ? "No shared IPv4 network found." : "");
  links.forEach(({ member }, index) => {
    const row = root.querySelector(`.fleet-nic[data-link="${index}"]`);
    const live = isLive(byId.get(member.machine));
    const down = member.state === "down";
    const drops = Number(member.drops_ps || 0);
    const errors = Number(member.errors_ps || 0);
    const busiest = Math.max(Number(member.rx_bps || 0), Number(member.tx_bps || 0));
    const used = member.utilization_pct == null ? null : Number(member.utilization_pct);
    const rate = [`RX ${fmtBitRate(member.rx_bps)}`, `TX ${fmtBitRate(member.tx_bps)}`];
    if (used >= 0.05) rate.push(`${used.toFixed(used < 10 ? 1 : 0)}% of link`);
    if (drops > 0) rate.push(`${fmtCount(drops)} drops/s`);
    if (errors > 0) rate.push(`${fmtCount(errors)} errors/s`);
    set(row.querySelector(".fleet-speed"), "textContent", fmtLinkSpeed(member.speed_mbps));
    set(row.querySelector(".fleet-nic-rate"), "textContent", !live ? "No live data" : (down ? "Link down" : rate.join(" • ")));
    const path = root.querySelector(`path[data-link="${index}"]`);
    const tone = !live ? "stale" : (down || drops + errors > 0 ? "bad" : (busiest * 8 >= 1e6 ? "active" : ""));
    if (path.getAttribute("class") !== tone) path.setAttribute("class", tone);
    // 2 px when idle, up to 10 px on a saturated link.
    path.style.strokeWidth = `${(2 + (live && used ? used * 0.08 : 0)).toFixed(1)}px`;
  });
  drawFleetLinks();
}

function drawFleetLinks() {
  const root = qs("fleet-map");
  const svg = root ? root.querySelector(".fleet-links") : null;
  // Nothing to measure while the map is hidden or stacked in one column.
  if (!svg || !svg.getClientRects().length) return;
  const base = root.getBoundingClientRect();
  const pills = root.querySelectorAll(".fleet-network");
  svg.querySelectorAll("path").forEach(path => {
    const row = root.querySelector(`.fleet-nic[data-link="${path.dataset.link}"]`);
    const box = row.closest(".fleet-machine").getBoundingClientRect();
    const pill = pills[Number(path.dataset.network)].getBoundingClientRect();
    const line = row.getBoundingClientRect();
    const leftOfPill = box.left < pill.left;
    const x1 = (leftOfPill ? box.right : box.left) - base.left;
    const x2 = (leftOfPill ? pill.left : pill.right) - base.left;
    const y1 = line.top + line.height / 2 - base.top;
    const y2 = pill.top + pill.height / 2 - base.top;
    const mid = (x1 + x2) / 2;
    const d = `M${x1.toFixed(1)},${y1.toFixed(1)} C${mid.toFixed(1)},${y1.toFixed(1)} ${mid.toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
    if (path.getAttribute("d") !== d) path.setAttribute("d", d);
  });
}

function renderOverview() {
  renderFleet();
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
      <div class="kpi-note">${escapeHtml(item.note)}</div>
    </div>
  `).join("");

  if (!system) {
    qs("overview-topology").innerHTML = emptyState("No SUT selected", "Start an agent on the Linux SUT and choose it from the SUT selector.");
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
    "TX",
    fmtAxisBytesRate
  );
  renderTopologyMap("overview-topology", { compact: true });
  qs("overview-findings").innerHTML = findings.map(item => `
    <div class="finding ${item.tone}">
      <div class="finding-title">${item.title}</div>
      <div class="finding-text">${escapeHtml(item.text)}</div>
      <div class="button-row" style="margin-top:10px;"><button class="action-button ghost" data-target-view="${item.target}">${item.action}</button></div>
    </div>
  `).join("");
  [...qs("overview-findings").querySelectorAll("[data-target-view]")].forEach(node => {
    node.onclick = () => showView(node.dataset.targetView);
  });
}

function renderSystems() {
  const root = qs("systems-list");
  if (!root) return;
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
  renderIrqHeatmap(heatmapRows(rows));

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
      <td>${row.cross_numa_flag ? `<span class="tag bad">Handled on node ${escapeHtml((row.handler_numa_nodes || []).join(", "))}</span>` : ""}</td>
      <td class="mono">${escapeHtml(row.affinity_list || "N/A")}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="10">No IRQ rows available.</td></tr>`;
}

function heatmapRows(rows) {
  const active = rows
    .filter(row => Number(row.total_rate || 0) > 0)
    .sort((a, b) => Number(b.total_rate || 0) - Number(a.total_rate || 0));
  const top = state.irqTopN === "all" ? active : active.slice(0, Number(state.irqTopN) || 25);
  const field = heatmapGroupField();
  if (!field) return top;
  const groupOf = {};
  (state.topology?.rows || []).forEach(item => {
    groupOf[String(item.cpu_id)] = item[field] == null ? "?" : String(item[field]);
  });
  return top.map(row => {
    const grouped = {};
    for (const [cpu, rate] of Object.entries(row.cpu_rates || {})) {
      const key = groupOf[cpu] ?? "?";
      grouped[key] = (grouped[key] || 0) + Number(rate || 0);
    }
    return { ...row, cpu_rates: grouped };
  });
}

function heatmapGroupField() {
  if (!(state.topology?.rows || []).length) return "";
  if (state.irqAggregate === "numa") return "numa_node";
  if (state.irqAggregate === "socket") return "socket_id";
  return "";
}

function heatmapColumnLabel() {
  const field = heatmapGroupField();
  return field === "numa_node" ? "NUMA" : field === "socket_id" ? "Socket" : "CPU";
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
  // Like the SUT switcher: rebuilding the list on every tick would reset an open dropdown.
  const signature = interfaces.map(item => item.interface).join("\n");
  if (signature !== state.ifaceSignature) {
    state.ifaceSignature = signature;
    const options = [`<option value="ALL">All Interfaces</option>`].concat(interfaces.map(item => `<option value="${escapeHtml(item.interface)}">${escapeHtml(item.interface)}</option>`));
    selector.innerHTML = options.join("");
  }
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
  const global = state.snapshot?.network?.global || {
    rx_bps: 0,
    tx_bps: 0,
    rx_pps: 0,
    tx_pps: 0,
    rx_err_ps: 0,
    tx_err_ps: 0,
    rx_drop_ps: 0,
    tx_drop_ps: 0,
  };
  const title = state.selectedIface === "ALL" ? "All Interfaces" : escapeHtml(state.selectedIface);
  qs("network-current-title").textContent = title;
  if (!iface && state.selectedIface !== "ALL") {
    qs("network-current-cards").innerHTML = emptyState("Interface unavailable", "The selected interface is not present in current telemetry.");
  } else {
    const source = (state.selectedIface === "ALL") ? global : iface;
    qs("network-current-cards").innerHTML = [
      { label: "RX", value: fmtBytesRate(source.rx_bps), note: "Receive throughput" },
      { label: "TX", value: fmtBytesRate(source.tx_bps), note: "Transmit throughput" },
      { label: "RX Packets", value: fmtCount(source.rx_pps), note: "Packets per second" },
      { label: "TX Packets", value: fmtCount(source.tx_pps), note: "Packets per second" },
      { label: "Errors", value: fmtCount(Number(source.rx_err_ps || 0) + Number(source.tx_err_ps || 0)), note: "RX + TX errors" },
      { label: "Drops", value: fmtCount(Number(source.rx_drop_ps || 0) + Number(source.tx_drop_ps || 0)), note: "RX + TX drops" },
    ].concat(softnetCards()).map(item => `
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
  renderDualTrendChart("networkTrend", "chart-network-trend", rx, tx, "RX", "TX", fmtAxisBytesRate);
}

function renderNetworkRelatedIrqs() {
  const allRows = (state.snapshot?.irq?.rows || []).filter(row => row.source_class === "network");
  const iface = state.selectedIface === "ALL" ? null : state.selectedIface;
  const rows = iface ? relatedIrqsForIface(iface) : allRows;
  const root = qs("network-related-irqs");
  if (!rows.length) {
    root.innerHTML = emptyState(
      "IRQ mapping unavailable",
      iface
        ? "Reliable IRQ mapping is not available for this interface on the current SUT."
        : "No network IRQ activity is currently visible across interfaces."
    );
    return;
  }
  root.innerHTML = rows
    .slice()
    .sort((a, b) => Number(b.total_rate || 0) - Number(a.total_rate || 0))
    .slice(0, 12)
    .map(row => `
      <div class="irq-item">
        <div class="item-title">${escapeHtml(row.irq_name || row.irq)}</div>
        <div class="item-text">IRQ ${escapeHtml(row.irq)} • ${escapeHtml(row.nic || "unknown")} • ${escapeHtml(row.direction || "Other")} • ${fmtCount(row.total_rate)}/s • CPU ${topCpuForIrq(row).cpu}</div>
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
          <div class="key-value"><span>IPv6</span><strong>${escapeHtml((ifaceMeta.ipv6 || []).join(", ") || "N/A")}</strong></div>
        </div>
      </div>
    `);
  }
  root.innerHTML = sections.join("") || emptyState("Metadata unavailable", "No interface metadata is available for the selected SUT.");
}

function renderCpu() {
  if (el["cpu-metric-selector"]) {
    el["cpu-metric-selector"].value = state.cpuMetric;
  }
  renderTopologyMap("cpu-topology", { compact: false, interactive: true });
  renderCpuDetail();
  renderCpuHistory();
}

function renderCpuHistory() {
  const rows = filterRowsToRange(state.viz?.series?.cpu_utilization || []).slice().sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0));
  const empty = qs("cpu-history-empty");
  if (!rows.length) {
    clearChart("cpuHistory");
    if (empty) {
      empty.classList.remove("hidden");
      empty.innerHTML = emptyState("No CPU history available", "IRQLENS needs more CPU utilization samples for this time range.");
    }
    return;
  }
  if (empty) empty.classList.add("hidden");
  const avg = rows.map(item => [Number(item.timestamp || 0) * 1000, Number(item.avg || 0)]);
  const peak = rows.map(item => [Number(item.timestamp || 0) * 1000, Number(item.max || 0)]);
  renderDualTrendChart("cpuHistory", "chart-cpu-history", avg, peak, "Average Util %", "Peak Util %");
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
  const compact = !!options.compact;
  const interactive = options.interactive !== false;
  const rows = [...(topo.rows || [])].sort((a, b) => Number(a.cpu_id) - Number(b.cpu_id));
  const cpuMap = buildCpuValueMap(rows);
  const metricKey = state.cpuMetric;
  const metricUnit = metricKey === "util" ? "%" : "/sec";
  const metricLabel = metricKey === "util" ? "CPU Utilization" : (metricKey === "softirq" ? "SoftIRQ Rate" : "IRQ Rate");
  const values = rows.map(row => {
    const raw = cpuMetricValue(cpuMap[String(row.cpu_id)], metricKey);
    return Number(raw ?? 0);
  });
  const max = Math.max(1, ...values);
  const min = Math.min(...values, 0);
  const grouped = groupByNuma(rows);

  if (!compact && el["cpu-legend"]) {
    el["cpu-legend"].innerHTML = `<span>Low (${fmtMetricValue(min, metricKey)})</span><div class="legend-bar"></div><span>High (${fmtMetricValue(max, metricKey)})</span><span class="muted">${escapeHtml(metricLabel)} (${escapeHtml(metricUnit)})</span>`;
  }

  root.innerHTML = `
    <div class="topology-shell">
      <div class="topology-heading">
        <div>
          <h3 class="card-title">${compact ? "CPU / NUMA Activity" : "CPU Heatmap Matrix"}</h3>
          <div class="card-subtitle">Metric: ${metricLabel} • CPUs are real SUT CPU IDs.</div>
        </div>
        <div class="legend"><span>Low</span><div class="legend-bar"></div><span>High</span></div>
      </div>
      <div class="topology-group">${grouped.map(group => {
        const layout = computeMatrixLayout(group.rows.length, root.clientWidth || window.innerWidth);
        const placeholderCount = Math.max(0, (layout.rows * layout.cols) - group.rows.length);
        return `
          <div class="topology-node">
            <div class="topology-heading">
              <div>
                <h4>NUMA ${escapeHtml(String(group.numa))}</h4>
                <div class="card-subtitle">${group.rows.length} CPU(s) • ${layout.rows} × ${layout.cols} matrix • Package(s): ${escapeHtml(group.packages.join(", ") || "Unavailable")}</div>
              </div>
              <span class="tag info">${fmtMetricValue(groupPeakValue(group.rows, cpuMap, metricKey), metricKey)}</span>
            </div>
            <div class="cpu-matrix-grid" style="grid-template-columns: repeat(${layout.cols}, minmax(0, 1fr));">
              ${group.rows.map(cpu => {
                const cpuId = String(cpu.cpu_id);
                const entry = cpuMap[cpuId] || { irq: 0, softirq: 0, util: null };
                const metricValueRaw = cpuMetricValue(entry, metricKey);
                const metricValue = Number(metricValueRaw ?? 0);
                const level = intensityLevel(metricValue, max);
                const active = cpuId === String(state.selectedCpu) ? "active" : "";
                return `
                  <button class="cpu-cell compact ${active}" data-level="${level}" data-cpu="${escapeHtml(cpuId)}" data-tooltip-cpu="${escapeHtml(cpuId)}">
                    <span class="cpu-name">CPU ${escapeHtml(cpuId)}</span>
                    <span class="cpu-metric">${fmtMetricValue(metricValueRaw, metricKey)}</span>
                  </button>
                `;
              }).join("")}
              ${new Array(placeholderCount).fill(0).map(() => `<div class="cpu-cell-placeholder"></div>`).join("")}
            </div>
          </div>
        `;
      }).join("")}</div>
    </div>
  `;
  bindCpuCellInteractions(root, cpuMap, metricKey, interactive);
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

function groupByNuma(rows) {
  const map = new Map();
  rows.forEach(row => {
    const key = row.numa_node ?? "N/A";
    if (!map.has(key)) {
      map.set(key, { numa: key, rows: [], packages: new Set() });
    }
    const item = map.get(key);
    item.rows.push(row);
    item.packages.add(row.socket_id ?? "N/A");
  });
  return [...map.values()]
    .map(item => ({
      numa: item.numa,
      rows: item.rows.slice().sort((a, b) => Number(a.cpu_id) - Number(b.cpu_id)),
      packages: [...item.packages].map(String).sort((a, b) => Number(a) - Number(b)),
    }))
    .sort((a, b) => Number(a.numa) - Number(b.numa));
}

function computeMatrixLayout(cpuCount, containerWidth) {
  const count = Math.max(1, Number(cpuCount || 1));
  const width = Math.max(360, Number(containerWidth || window.innerWidth || 1280));
  const minCell = width >= 1800 ? 62 : width >= 1400 ? 56 : width >= 1100 ? 50 : 44;
  const maxCols = Math.max(2, Math.min(count, Math.floor((width + 8) / (minCell + 8))));
  const preferredRatio = width >= 1500 ? 1.5 : 1.2;

  // First pass: prefer exact factor layouts that keep rows/cols close and readable.
  let best = null;
  for (let rows = 2; rows <= Math.floor(Math.sqrt(count)); rows += 1) {
    if (count % rows !== 0) continue;
    const cols = count / rows;
    const candidates = [
      { rows, cols },
      { rows: cols, cols: rows },
    ];
    candidates.forEach(candidate => {
      if (width >= 1000 && candidate.cols < candidate.rows) return;
      if (candidate.cols > maxCols || candidate.rows < 2) return;
      const aspect = candidate.cols / candidate.rows;
      const score = Math.abs(candidate.cols - candidate.rows) + Math.abs(aspect - preferredRatio) * 2;
      if (!best || score < best.score) {
        best = { ...candidate, score };
      }
    });
  }

  if (best) {
    return { rows: best.rows, cols: best.cols };
  }

  // Fallback for prime or sparse counts.
  let bestCols = Math.min(count, maxCols);
  let bestScore = Number.POSITIVE_INFINITY;
  for (let cols = 2; cols <= maxCols; cols += 1) {
    const rows = Math.ceil(count / cols);
    const blanks = (rows * cols) - count;
    const aspect = cols / Math.max(1, rows);
    const score = Math.abs(aspect - preferredRatio) + (blanks / count);
    if (score < bestScore) {
      bestScore = score;
      bestCols = cols;
    }
  }
  return { rows: Math.ceil(count / bestCols), cols: bestCols };
}

function buildCpuValueMap(rows) {
  const irqSoft = aggregateCpuActivity();
  const cpuUtil = state.viz?.cpu_utilization || {};
  const map = {};
  rows.forEach(row => {
    const cpu = String(row.cpu_id);
    const base = irqSoft[cpu] || { irq: 0, softirq: 0 };
    const hasUtil = Object.prototype.hasOwnProperty.call(cpuUtil, cpu);
    map[cpu] = {
      irq: Number(base.irq || 0),
      softirq: Number(base.softirq || 0),
      util: hasUtil ? Number(cpuUtil[cpu]) : null,
    };
  });
  return map;
}

function cpuMetricValue(entry, metricKey) {
  if (!entry) return 0;
  if (metricKey === "softirq") return Number(entry.softirq || 0);
  if (metricKey === "util") return entry.util == null ? null : Number(entry.util);
  return Number(entry.irq || 0);
}

function fmtMetricValue(value, metricKey) {
  if (metricKey === "util") {
    if (value == null || Number.isNaN(Number(value))) return "N/A";
    return `${Number(value).toFixed(1)}%`;
  }
  const n = Number(value || 0);
  return `${fmtCount(n)}/s`;
}

function groupNetworkIrqsForCpu(cpuId) {
  const rows = (state.snapshot?.irq?.rows || []).filter(row => {
    return row.source_class === "network" && Number((row.cpu_rates || {})[String(cpuId)] || 0) > 0;
  });
  if (!rows.length) return [];
  const grouped = new Map();
  rows.forEach(row => {
    const iface = String(row.nic || "unknown");
    const direction = String(row.direction || "Other");
    const key = `${iface}|${direction}`;
    const current = grouped.get(key) || { iface, direction, rate: 0 };
    current.rate += Number((row.cpu_rates || {})[String(cpuId)] || 0);
    grouped.set(key, current);
  });
  return [...grouped.values()].sort((a, b) => b.rate - a.rate).slice(0, 8);
}

function parseCpuList(rangeText) {
  if (!rangeText) return [];
  const values = [];
  String(rangeText).split(",").forEach(part => {
    const token = part.trim();
    if (!token) return;
    if (token.includes("-")) {
      const [startRaw, endRaw] = token.split("-");
      const start = Number(startRaw);
      const end = Number(endRaw);
      if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
        for (let x = start; x <= end; x += 1) values.push(x);
      }
      return;
    }
    const single = Number(token);
    if (Number.isFinite(single)) values.push(single);
  });
  return [...new Set(values)].sort((a, b) => a - b);
}

function threadIndex(row) {
  const siblings = parseCpuList(row?.thread_siblings_list || "");
  if (!siblings.length) return "Unavailable";
  const idx = siblings.indexOf(Number(row.cpu_id));
  if (idx < 0) return "Unavailable";
  return idx;
}

function groupPeakValue(rows, cpuMap, metricKey) {
  let peak = 0;
  rows.forEach(row => {
    peak = Math.max(peak, Number(cpuMetricValue(cpuMap[String(row.cpu_id)], metricKey) || 0));
  });
  return peak;
}

function cpuTooltipHtml(cpuId, row, cpuMap) {
  const entry = cpuMap[String(cpuId)] || { irq: 0, softirq: 0, util: null };
  const netMap = groupNetworkIrqsForCpu(cpuId);
  const networkHtml = netMap.length
    ? netMap.map(item => `<div class="key-value"><span>${escapeHtml(item.iface)} ${escapeHtml(item.direction)} IRQ</span><strong>${fmtCount(item.rate)}/s</strong></div>`).join("")
    : `<div class="muted">Network IRQ mapping unavailable</div>`;
  return `
    <div class="item-title">CPU ${escapeHtml(String(cpuId))}</div>
    <div class="detail-list">
      <div class="key-value"><span>CPU Utilization</span><strong>${fmtMetricValue(entry.util, "util")}</strong></div>
      <div class="key-value"><span>IRQ Rate</span><strong>${fmtMetricValue(entry.irq, "irq")}</strong></div>
      <div class="key-value"><span>SoftIRQ Rate</span><strong>${fmtMetricValue(entry.softirq, "softirq")}</strong></div>
      <div class="key-value"><span>NUMA Node</span><strong>${row?.numa_node ?? "Unavailable"}</strong></div>
      <div class="key-value"><span>Socket</span><strong>${row?.socket_id ?? "Unavailable"}</strong></div>
      <div class="key-value"><span>Core</span><strong>${row?.core_id ?? "Unavailable"}</strong></div>
      <div class="key-value"><span>Thread</span><strong>${threadIndex(row)}</strong></div>
      <div class="key-value"><span>CBB</span><strong>information unavailable</strong></div>
    </div>
    <div class="item-title" style="margin-top:10px;">Network / IRQ</div>
    <div class="detail-list">${networkHtml}</div>
  `;
}

function bindCpuCellInteractions(root, cpuMap, metricKey, interactive) {
  const tooltip = qs("cpu-hover-tooltip");
  const topoRows = state.topology?.rows || [];
  // A redrawn cell never reports the pointer leaving it, so the matrix itself hides the tooltip.
  if (tooltip) root.onmouseleave = () => tooltip.classList.add("hidden");
  [...root.querySelectorAll("[data-cpu]")].forEach(node => {
    const cpuId = node.dataset.cpu;
    const row = topoRows.find(item => String(item.cpu_id) === String(cpuId));
    if (tooltip) {
      node.onmouseenter = event => {
        tooltip.innerHTML = cpuTooltipHtml(cpuId, row, cpuMap);
        tooltip.classList.remove("hidden");
        positionCpuTooltip(event, tooltip);
      };
      node.onmousemove = event => {
        if (!tooltip.classList.contains("hidden")) {
          positionCpuTooltip(event, tooltip);
        }
      };
      node.onmouseleave = () => {
        tooltip.classList.add("hidden");
      };
    }
    node.onclick = () => {
      state.selectedCpu = cpuId;
      persistSelections();
      if (interactive && state.view !== "cpu") {
        state.view = "cpu";
        storage.set("irqlens:view", state.view);
      }
      render();
    };
    node.title = `CPU ${cpuId} • ${fmtMetricValue(cpuMetricValue(cpuMap[String(cpuId)], metricKey), metricKey)}`;
  });
}

function positionCpuTooltip(event, tooltip) {
  const margin = 14;
  const x = Math.min(window.innerWidth - tooltip.offsetWidth - margin, event.clientX + 14);
  const y = Math.min(window.innerHeight - tooltip.offsetHeight - margin, event.clientY + 14);
  tooltip.style.left = `${Math.max(margin, x)}px`;
  tooltip.style.top = `${Math.max(margin, y)}px`;
}

function renderCpuDetail() {
  const root = qs("cpu-detail");
  const selected = state.selectedCpu;
  if (!selected) {
    root.innerHTML = emptyState("Choose a CPU", "Click a CPU cell in the matrix to inspect activity, topology, and network IRQ mapping.");
    return;
  }
  const topoRows = state.topology?.rows || [];
  const topo = topoRows.find(row => String(row.cpu_id) === String(selected));
  const activityMap = buildCpuValueMap(topoRows);
  const activity = activityMap[String(selected)] || { irq: 0, softirq: 0, util: null };
  const irqRows = (state.snapshot?.irq?.rows || [])
    .filter(row => Number((row.cpu_rates || {})[selected] || 0) > 0)
    .sort((a, b) => Number((b.cpu_rates || {})[selected] || 0) - Number((a.cpu_rates || {})[selected] || 0))
    .slice(0, 10);
  const netMap = groupNetworkIrqsForCpu(selected);
  root.innerHTML = `
    <div class="detail-panel">
      <div class="item-title">CPU ${escapeHtml(selected)}</div>
      <div class="detail-list">
        <div class="key-value"><span>IRQ Rate</span><strong>${fmtCount(activity.irq)}/s</strong></div>
        <div class="key-value"><span>SoftIRQ Rate</span><strong>${fmtCount(activity.softirq)}/s</strong></div>
        <div class="key-value"><span>CPU Utilization</span><strong>${fmtMetricValue(activity.util, "util")}</strong></div>
        <div class="key-value"><span>NUMA Node</span><strong>${topo ? topo.numa_node : "Unavailable"}</strong></div>
        <div class="key-value"><span>Socket</span><strong>${topo ? topo.socket_id : "Unavailable"}</strong></div>
        <div class="key-value"><span>Core</span><strong>${topo ? topo.core_id : "Unavailable"}</strong></div>
        <div class="key-value"><span>Thread</span><strong>${topo ? threadIndex(topo) : "Unavailable"}</strong></div>
        <div class="key-value"><span>CBB</span><strong>information unavailable</strong></div>
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
    <div class="detail-panel" style="margin-top:14px;">
      <div class="item-title">Network / IRQ</div>
      <div class="detail-list">
        ${netMap.length
          ? netMap.map(item => `<div class="key-value"><span>${escapeHtml(item.iface)} ${escapeHtml(item.direction)} IRQ</span><strong>${fmtCount(item.rate)}/s</strong></div>`).join("")
          : `<div class="muted">Network IRQ mapping unavailable</div>`}
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

function softnetCards() {
  const total = state.io?.softnet?.latest?.total;
  if (state.selectedIface !== "ALL" || !total) return [];
  return [
    { label: "Softnet Drops", value: fmtCount(total.dropped), note: "Backlog drops, last sample" },
    { label: "Time Squeeze", value: fmtCount(total.time_squeeze), note: "NAPI budget exhausted, last sample" },
  ];
}

function fmtNumber(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "N/A";
}

function renderStorage() {
  const storageData = state.io?.storage || {};
  const totals = storageData.totals || {};
  const pcie = state.io?.pcie || {};
  qs("storage-kpis").innerHTML = [
    { label: "Total Storage IOPS", value: fmtCount(totals.total_iops), note: "Read + write, all devices" },
    { label: "Read MB/s", value: fmtNumber(totals.read_mb_s || 0), note: "Total read throughput" },
    { label: "Write MB/s", value: fmtNumber(totals.write_mb_s || 0), note: "Total write throughput" },
    { label: "Max Await Latency", value: `${fmtNumber(totals.max_await_ms || 0, 2)} ms`, note: "Slowest device, read or write" },
    { label: "PCIe Link Warnings", value: String(Number(pcie.link_warnings || 0)), note: "Cards below maximum speed or width" },
    { label: "AER Errors", value: String(Number(pcie.aer_errors || 0)), note: Number(pcie.aer_increasing || 0) > 0 ? "Counters still increasing" : "Correctable + non-fatal + fatal" },
  ].map(item => `
    <div class="kpi-card">
      <div class="kpi-label">${item.label}</div>
      <div class="kpi-value">${item.value}</div>
      <div class="kpi-note">${item.note}</div>
    </div>
  `).join("");

  const series = storageData.series || {};
  const names = Object.keys(series).sort();
  const points = (name, key) => filterRowsToRange(series[name]).map(point => [Number(point.timestamp) * 1000, Number(point[key] || 0)]);
  const empty = qs("storage-empty");
  if (!names.length) {
    ["storageIops", "storageThroughput", "storageLatency"].forEach(clearChart);
    empty.classList.remove("hidden");
    empty.innerHTML = emptyState("No block-device samples", "Storage telemetry appears once the selected Linux SUT reports /proc/diskstats.");
  } else {
    empty.classList.add("hidden");
    renderMultiSeriesChart("storageIops", "chart-storage-iops", names.flatMap(name => [
      { name: `${name} read`, data: points(name, "read_iops") },
      { name: `${name} write`, data: points(name, "write_iops") },
    ]), "IOPS");
    renderMultiSeriesChart("storageThroughput", "chart-storage-throughput", names.flatMap(name => [
      { name: `${name} read`, data: points(name, "read_mb_s") },
      { name: `${name} write`, data: points(name, "write_mb_s") },
    ]), "MB/s");
    renderMultiSeriesChart("storageLatency", "chart-storage-latency", names.flatMap(name => [
      { name: `${name} read await`, data: points(name, "avg_read_await_ms") },
      { name: `${name} write await`, data: points(name, "avg_write_await_ms") },
      { name: `${name} in flight`, data: points(name, "queue_depth_in_flight"), axis: 1, dashed: true },
    ]), "ms", "In flight");
  }

  const devices = storageData.devices || [];
  qs("storage-table").innerHTML = devices.length ? devices.map(device => `<tr>
      <td class="mono">${escapeHtml(device.device)}${device.model ? `<div class="muted">${escapeHtml(device.model)}</div>` : ""}</td>
      <td>${fmtCount(device.read_iops)}</td>
      <td>${fmtCount(device.write_iops)}</td>
      <td>${fmtNumber(device.read_mb_s || 0, 2)}</td>
      <td>${fmtNumber(device.write_mb_s || 0, 2)}</td>
      <td>${fmtNumber(device.avg_read_await_ms || 0, 2)} / ${fmtNumber(device.avg_write_await_ms || 0, 2)}</td>
      <td>${fmtNumber(device.queue_depth_in_flight || 0, 0)}</td>
      <td>${fmtNumber(device.util_pct || 0)}%</td>
      <td>${escapeHtml(device.scheduler || "N/A")}</td>
      <td>${device.nr_requests == null ? "N/A" : fmtNumber(device.nr_requests, 0)}</td>
      <td>${device.rotational == null ? "N/A" : (device.rotational ? "Rotational" : "Solid state")}</td>
      <td>${device.numa_node == null ? "N/A" : fmtNumber(device.numa_node, 0)}</td>
      <td>${fmtNumber(device.msi_irq_count || 0, 0)}</td>
    </tr>`).join("") : `<tr><td colspan="13">No block devices reported.</td></tr>`;

  const cards = pcie.devices || [];
  qs("pcie-table").innerHTML = cards.length ? cards.map(card => {
    const current = `${escapeHtml(card.current_link_speed || "N/A")} x${fmtNumber(card.current_link_width || 0, 0)}`;
    const maximum = `${escapeHtml(card.max_link_speed || "N/A")} x${fmtNumber(card.max_link_width || 0, 0)}`;
    const aerText = [card.aer_dev_correctable, card.aer_dev_nonfatal, card.aer_dev_fatal]
      .map(value => (value == null ? "N/A" : fmtNumber(value, 0))).join(" / ");
    const aerTone = Number(card.aer_total || 0) > 0 ? (card.aer_increasing ? "bad" : "warn") : "";
    const interfaces = Array.isArray(card.interfaces) && card.interfaces.length ? ` • ${escapeHtml(card.interfaces.join(", "))}` : "";
    return `<tr>
      <td class="mono">${escapeHtml(card.bdf)}</td>
      <td>${escapeHtml(card.kind || "Device")} <span class="mono">${escapeHtml(card.vendor)}:${escapeHtml(card.device)}</span><div class="muted">${escapeHtml(card.driver || "N/A")}${interfaces}</div></td>
      <td>${card.numa_node == null ? "N/A" : fmtNumber(card.numa_node, 0)}</td>
      <td>${current} <span class="muted">vs ${maximum}</span>${card.link_degraded ? ` <span class="tag bad">Downgraded</span>` : ""}</td>
      <td>${fmtNumber(card.msi_irq_count || 0, 0)}</td>
      <td>${aerTone ? `<span class="tag ${aerTone}">${aerText}</span>` : aerText}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6">No PCIe cards reported.</td></tr>`;
}

function downloadLink(label, url, className = "") {
  return `<a class="${className}" href="${escapeHtml(url)}" data-download-url="${escapeHtml(url)}">${escapeHtml(label)}</a>`;
}

function showToast(message, tone = "info") {
  const root = qs("toast-root");
  if (!root) return;
  const node = document.createElement("div");
  node.className = `toast ${tone}`;
  node.textContent = message;
  root.appendChild(node);
  setTimeout(() => node.remove(), 6000);
}

async function downloadFile(url) {
  let response;
  try {
    response = await fetch(url);
  } catch (_) {
    showToast("Download failed: the server is unreachable.", "bad");
    return false;
  }
  if (response.status !== 200) {
    const detail = await response.json().then(body => body.detail, () => "");
    showToast(`Download failed (HTTP ${response.status})${typeof detail === "string" && detail ? `: ${detail}` : ""}`, "bad");
    return false;
  }
  // blob() keeps binary files such as .xlsx and .zip byte-exact; text() would corrupt them.
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  let name = plain ? plain[1] : "download";
  if (encoded) {
    try {
      name = decodeURIComponent(encoded[1]);
    } catch (_) {
      // Keep the plain filename.
    }
  }
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking in the same task can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

function renderDiagnostics() {
  const system = currentSystem();
  qs("diag-selected-sut").textContent = system ? (system.hostname || system.name || system.id) : "No SUT selected";
  const durationSelect = qs("diag-duration");
  if (durationSelect) {
    durationSelect.value = String(state.diag.duration || 60);
    durationSelect.disabled = state.diag.running || state.diag.starting || state.diag.stopping;
  }
  const sessionInput = qs("diag-session-name");
  if (sessionInput) {
    sessionInput.value = state.diag.sessionName || "";
    sessionInput.disabled = state.diag.running || state.diag.starting || state.diag.stopping;
  }
  qs("diag-checkboxes").innerHTML = [
    { key: "irq", title: "IRQ", text: "Capture current interrupt distribution." },
    { key: "softirq", title: "SoftIRQ", text: "Capture current softirq summary." },
    { key: "network", title: "Network", text: "Capture current interface traffic." },
    { key: "system", title: "System information", text: "Capture system identity and baseline details." },
    { key: "interfaces", title: "Interface metadata", text: "Capture NIC metadata and addressing." },
    { key: "routes", title: "Routes", text: "Capture routing tables and rules." },
    { key: "sockets", title: "Sockets", text: "Capture socket summaries and listeners." },
    { key: "ethtool", title: "Network commands", text: "Capture ethtool output when supported." },
    { key: "storage", title: "Storage", text: "Capture block-device I/O, lsblk, and NVMe details." },
    { key: "pcie", title: "PCIe cards", text: "Capture PCIe link state and AER counters." },
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
  const startBtn = qs("diag-start");
  const stopBtn = qs("diag-stop");
  if (startBtn) {
    startBtn.disabled = state.diag.running || state.diag.starting || state.diag.stopping;
    startBtn.textContent = state.diag.starting ? "Starting..." : (state.diag.running ? "Capturing..." : "Start Capture");
  }
  if (stopBtn) {
    stopBtn.disabled = !state.diag.running || state.diag.starting || state.diag.stopping;
    stopBtn.textContent = state.diag.stopping ? "Stopping..." : "Stop Capture";
  }
}

function renderDiagProgressHtml() {
  if (!state.diag.running) {
    const ready = emptyState("Ready to capture", "Start a capture to collect evidence for the selected SUT.");
    if (!state.diag.error) return ready;
    return `${ready}<div class="detail-panel" style="margin-top:12px; border-color:#efc0c0; background:var(--red-soft);"><div class="item-title">Capture validation</div><div class="item-text">${escapeHtml(state.diag.error)}</div></div>`;
  }
  const remaining = Math.max(0, Math.round(state.diag.endsAt - nowSeconds()));
  const elapsed = Math.max(0, Math.round(nowSeconds() - state.diag.startedAt));
  return `
      <div class="progress-card">
      <div class="item-title">Capturing...</div>
      <div class="progress-timer">${elapsed}s / ${state.diag.duration}s</div>
      <div class="muted">Session: ${escapeHtml(state.diag.sessionId)}</div>
      <div class="muted">Remaining: ${remaining}s</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, (elapsed / Math.max(1, state.diag.duration)) * 100)).toFixed(1)}%"></div></div>
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
  const completed = (state.sessions || []).find(item => item.session_id === state.diag.completedSessionId) || null;
  const isDetailLoaded = state.sessionDetail && state.sessionDetail.session_id === state.diag.completedSessionId;
  const fileCount = isDetailLoaded ? (state.sessionFiles || []).length : 0;
  const outputDir = isDetailLoaded ? (state.sessionDetail.output_dir || "N/A") : (completed?.output_dir || "N/A");
  const status = isDetailLoaded ? (state.sessionDetail.status || "completed") : (completed?.status || "completed");
  return `
    <div class="detail-panel">
      <div class="item-title">Capture complete</div>
      <div class="muted">Session ${escapeHtml(state.diag.completedSessionId)}</div>
      <div class="detail-list" style="margin-top:10px;">
        <div class="key-value"><span>Location</span><strong class="mono">${escapeHtml(outputDir)}</strong></div>
        <div class="key-value"><span>Files</span><strong>${fileCount}</strong></div>
        <div class="key-value"><span>Status</span><strong>${escapeHtml(status)}</strong></div>
      </div>
      <div class="button-row" style="margin-top:14px;">
        <button class="action-button ghost" id="diag-view-session">View Capture</button>
        ${downloadLink("Download Excel", api("/api/sessions/" + encodeURIComponent(state.diag.completedSessionId) + "/workbook"), "action-button ghost")}
        ${downloadLink("Download ZIP", api("/api/sessions/" + encodeURIComponent(state.diag.completedSessionId) + "/download"), "action-button primary")}
      </div>
    </div>
  `;
}

async function startCapture() {
  if (!state.host || state.diag.running || state.diag.starting || state.diag.stopping) {
    return;
  }
  const sessionName = String(qs("diag-session-name")?.value || state.diag.sessionName || "").trim();
  state.diag.sessionName = sessionName;
  if (!sessionName) {
    state.diag.error = "Session name is required.";
    renderDiagnostics();
    bindDiagnosticsActions();
    return;
  }
  if (!state.diag.categories.length) {
    state.diag.error = "Select at least one capture category.";
    renderDiagnostics();
    bindDiagnosticsActions();
    return;
  }
  const body = {
    categories: state.diag.categories,
    sut_id: state.host,
    session_name: sessionName,
    duration_seconds: Number(state.diag.duration || 60),
  };
  state.diag.starting = true;
  state.diag.error = "";
  renderDiagnostics();
  bindDiagnosticsActions();

  try {
    const response = await fetch(api("/api/sessions/start"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      state.diag.error = errorPayload.detail || `Unable to start capture (${response.status}).`;
      return;
    }
    const data = await response.json();
    state.diag.running = true;
    state.diag.sessionId = data.session?.session_id || "";
    state.diag.files = data.files || [];
    state.diag.startedAt = nowSeconds();
    state.diag.endsAt = state.diag.startedAt + Number(state.diag.duration || 60);
    state.diag.completedSessionId = "";
    state.diag.error = "";
    if (state.diag.timer) clearInterval(state.diag.timer);
    state.diag.timer = setInterval(async () => {
      if (state.diag.stopping) return;
      if (nowSeconds() >= state.diag.endsAt) {
        clearInterval(state.diag.timer);
        state.diag.timer = null;
        await stopCapture(true);
        return;
      }
      renderDiagnostics();
      bindDiagnosticsActions();
    }, 1000);
    await loadSessions();
  } catch (_) {
    state.diag.error = "Unable to start capture. Please check backend availability and try again.";
  } finally {
    state.diag.starting = false;
    renderDiagnostics();
    bindDiagnosticsActions();
  }
}

async function stopCapture(autoComplete = false) {
  if (!state.diag.sessionId || state.diag.stopping || state.diag.starting) return;
  state.diag.stopping = true;
  renderDiagnostics();
  bindDiagnosticsActions();
  try {
    const response = await fetch(api("/api/sessions/" + encodeURIComponent(state.diag.sessionId) + "/stop"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: autoComplete ? "duration-complete" : "manual" }),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      state.diag.error = errorPayload.detail || `Unable to stop capture (${response.status}).`;
      return;
    }
    state.diag.running = false;
    state.diag.completedSessionId = state.diag.sessionId;
    state.diag.sessionId = "";
    state.diag.error = "";
    if (state.diag.timer) {
      clearInterval(state.diag.timer);
      state.diag.timer = null;
    }
    await loadSessions();
    if (state.diag.completedSessionId) {
      await loadSessionDetail(state.diag.completedSessionId);
    }
    if (state.selectedSessionId !== state.diag.completedSessionId) {
      state.selectedSessionId = state.diag.completedSessionId;
    }
  } catch (_) {
    state.diag.error = "Unable to stop capture. Please check backend availability and try again.";
  } finally {
    state.diag.stopping = false;
    renderDiagnostics();
    bindDiagnosticsActions();
    renderSessions();
    renderContextBar();
  }
}

function bindDiagnosticsActions() {
  const viewButton = qs("diag-view-session");
  if (viewButton) {
    viewButton.onclick = async () => {
      if (!state.diag.completedSessionId) {
        await loadSessions();
        const latest = (state.sessions || [])[0];
        if (!latest?.session_id) return;
        state.diag.completedSessionId = latest.session_id;
      }
      state.view = "diagnostics";
      storage.set("irqlens:view", state.view);
      try {
        await loadSessions();
        await loadSessionDetail(state.diag.completedSessionId);
        state.expandedSessionId = state.selectedSessionId;
        render();
        qs("session-detail").scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (_) {
        state.diag.error = "Unable to load capture details. Please retry.";
        renderDiagnostics();
        bindDiagnosticsActions();
      }
    };
  }
}

async function loadSessions() {
  state.loading.sessions = true;
  const data = await fetchJson("/api/sessions");
  const filtered = (data.sessions || []).filter(session => {
    if (!state.host) return false;
    if (session.sut_id === state.host) return true;
    // Backward compatibility: legacy captures without sut_id are considered local-only.
    if (!session.sut_id && state.host === "local") return true;
    return false;
  });
  const seen = new Set();
  state.sessions = filtered.filter(session => {
    if (!session?.session_id || seen.has(session.session_id)) return false;
    seen.add(session.session_id);
    return true;
  }).sort((a, b) => Number(b.start_time || 0) - Number(a.start_time || 0));
  if (state.expandedSessionId && !state.sessions.some(x => x.session_id === state.expandedSessionId)) {
    state.expandedSessionId = "";
  }
  if (state.selectedSessionId && !state.sessions.some(x => x.session_id === state.selectedSessionId)) {
    state.selectedSessionId = "";
    state.sessionDetail = null;
    state.sessionFiles = [];
  }
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

// Unchanged markup is left in place, so an open list or a text selection survives the refresh.
function setHtml(node, html) {
  if (node.renderedHtml === html) return false;
  node.renderedHtml = html;
  node.innerHTML = html;
  return true;
}

function foldSection(key, title, note, bodyHtml, openByDefault = false) {
  const open = state.folds.has(key) ? state.folds.get(key) : openByDefault;
  return `
    <details class="file-group" data-fold="${escapeHtml(key)}" ${open ? "open" : ""}>
      <summary><span class="item-title">${escapeHtml(title)}</span><span class="muted">${escapeHtml(note)}</span></summary>
      ${bodyHtml}
    </details>
  `;
}

function qualityChecksTable(checks) {
  return `
    <div class="table-wrap" style="margin-top:8px;">
      <table class="table">
        <thead><tr><th>Check</th><th>Status</th><th>Samples received / expected</th><th>Coverage</th><th>Largest gap</th><th>Findings</th></tr></thead>
        <tbody>${checks.map(check => `<tr>
          <td>${escapeHtml(check.name)}</td><td>${escapeHtml(check.status)}</td>
          <td>${check.unique_samples != null ? `${Number(check.unique_samples)} / ${check.expected_samples == null ? "Unknown" : Number(check.expected_samples)}` : "N/A"}</td>
          <td>${check.coverage_percent == null ? "N/A" : `${Number(check.coverage_percent).toFixed(1)}%`}</td>
          <td>${check.max_gap_seconds == null ? "N/A" : `${Number(check.max_gap_seconds).toFixed(2)}s`}</td>
          <td>${escapeHtml((check.reasons || []).join(" "))}</td>
        </tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function renderSessions() {
  const listRoot = qs("sessions-list");
  const detailRoot = qs("session-detail");
  // A list that is already on screen stays there while it is refreshed.
  if (state.loading.sessions && !state.sessions.length) {
    listRoot.innerHTML = emptyState("Loading sessions", "Fetching diagnostic capture history.");
    setHtml(detailRoot, emptyState("Select a capture", "Choose a capture to inspect summary, files, and download options."));
    return;
  }
  if (!state.sessions.length) {
    listRoot.innerHTML = emptyState("No captures yet.", "Start your first capture from Collect Logs.");
    setHtml(detailRoot, emptyState("No captures yet.", "Start a capture while a workload is running to collect SUT evidence."));
    return;
  }
  const recent = state.sessions.slice(0, 3);
  listRoot.innerHTML = `
    <div class="recent-captures-list">
      ${recent.map(session => {
        const expanded = session.session_id === state.expandedSessionId;
        const duration = session.end_time ? Math.max(0, Math.round(session.end_time - session.start_time)) : 0;
        const statusToneClass = session.status === "completed" ? "good" : (session.status === "running" ? "warn" : "info");
        const reportReady = session.report_status === "ready" && session.report_path;
        const reportPending = session.report_status === "pending" || !!state.reportingSessions[session.session_id];
        const reportFailed = session.report_status === "failed";
        const deleting = !!state.deletingSessions[session.session_id];
        return `
          <div class="recent-session ${expanded ? "expanded" : ""}">
            <div class="recent-session-head ${deleting ? "disabled" : ""}" data-toggle-session="${escapeHtml(session.session_id)}" role="button" tabindex="0" aria-expanded="${expanded ? "true" : "false"}">
              <div class="recent-session-main">
                <span class="status-dot ${statusToneClass}"></span>
                <span class="recent-session-name">${escapeHtml(session.session_id)}</span>
              </div>
              <div class="recent-session-meta">
                <span class="tag ${statusToneClass}">${escapeHtml(session.status)}</span>
                <span class="muted">${fmtDuration(duration)}</span>
                <span class="muted">${fmtTs(session.start_time)}</span>
                <span class="recent-chevron">${expanded ? "v" : ">"}</span>
              </div>
            </div>
            ${expanded ? `
              <div class="recent-session-body">
                <div class="muted">${fmtTs(session.start_time)}${session.end_time ? ` • ${fmtDuration(duration)}` : ""}</div>
                <div class="compact-kv"><span>SUT:</span><strong>${escapeHtml(session.hostname)}${session.sut_id ? ` (${escapeHtml(session.sut_id)})` : ""}</strong></div>
                <div class="compact-kv"><span>Location:</span><strong class="mono">${escapeHtml(session.output_dir || "N/A")}</strong></div>
                <div class="compact-kv"><span>Captured:</span><strong>${(session.categories || []).map(c => escapeHtml(c)).join("  ") || "none"}</strong></div>
                ${reportFailed ? `<div class="muted" style="color:#b23a3a;">${escapeHtml(session.report_error || "Report generation failed. Check the session report logs.")}</div>` : ""}
                <div class="button-row compact-actions">
                  <button class="action-button ghost" data-view-session="${escapeHtml(session.session_id)}" ${deleting ? "disabled" : ""}>View</button>
                  <button class="action-button primary" data-generate-report="${escapeHtml(session.session_id)}" ${reportPending || session.status === "running" ? "disabled" : ""}>${reportPending ? "Generating..." : "Generate Report"}</button>
                  ${reportReady ? `<a class="action-button ghost" target="_blank" rel="noopener" href="${api("/api/sessions/" + encodeURIComponent(session.session_id) + "/report/view")}">View Report</a>` : ""}
                  ${reportReady ? downloadLink("Download Report", api("/api/files?path=" + encodeURIComponent(session.report_path)), "action-button ghost") : ""}
                  <button class="action-button danger" data-delete-session="${escapeHtml(session.session_id)}" ${deleting ? "disabled" : ""}>${deleting ? "Deleting..." : "Delete"}</button>
                </div>
              </div>
            ` : ""}
          </div>
        `;
      }).join("")}
    </div>
  `;

  [...listRoot.querySelectorAll("[data-toggle-session]")].forEach(node => {
    const toggle = async () => {
      const id = node.dataset.toggleSession;
      if (state.deletingSessions[id]) return;
      const closing = state.expandedSessionId === id;
      state.expandedSessionId = closing ? "" : id;
      if (closing) {
        if (state.selectedSessionId === id) {
          state.selectedSessionId = "";
          state.sessionDetail = null;
          state.sessionFiles = [];
        }
      } else {
        state.selectedSessionId = id;
        if (!state.sessionDetail || state.sessionDetail.session_id !== id) {
          await loadSessionDetail(id);
        }
      }
      renderSessions();
      renderContextBar();
    };
    node.onclick = toggle;
    node.onkeydown = event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    };
  });

  [...listRoot.querySelectorAll("[data-view-session]")].forEach(node => {
    node.onclick = async event => {
      event.stopPropagation();
      state.selectedSessionId = node.dataset.viewSession;
      await loadSessionDetail(state.selectedSessionId);
      renderSessions();
      renderContextBar();
      detailRoot.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });

  [...listRoot.querySelectorAll("[data-generate-report]")].forEach(node => {
    node.onclick = async event => {
      event.stopPropagation();
      await startSessionReport(node.dataset.generateReport);
    };
  });

  [...listRoot.querySelectorAll("[data-delete-session]")].forEach(node => {
    node.onclick = async event => {
      event.stopPropagation();
      await deleteSession(node.dataset.deleteSession);
    };
  });

  if (!state.selectedSessionId) {
    setHtml(detailRoot, emptyState("Select a capture", "Choose a capture to inspect summary, files, and download options."));
    return;
  }
  if (!state.sessionDetail || state.sessionDetail.session_id !== state.selectedSessionId) {
    setHtml(detailRoot, emptyState("Loading capture", "Fetching capture detail and generated files."));
    return;
  }
  const grouped = groupFilesByCategory(state.sessionFiles);
  const detail = state.sessionDetail;
  const duration = detail.end_time ? Math.max(0, Math.round(detail.end_time - detail.start_time)) : 0;
  const reportReady = detail.report_status === "ready" && detail.report_path;
  const reportPending = detail.report_status === "pending" || !!state.reportingSessions[detail.session_id];
  const reportFailed = detail.report_status === "failed";
  const quality = detail.capture_quality || { status: "NOT_EVALUATED", checks: [] };
  const qualityBusy = !!state.qualityRequests[detail.session_id];
  const checks = quality.checks || [];
  const issues = checks.filter(check => check.status !== "COMPLETE");
  const unsupported = quality.unsupported_commands || [];
  const foldKey = name => `${detail.session_id}:${name}`;
  const rendered = setHtml(detailRoot, `
    <div class="detail-panel">
      <div class="card-head">
        <div>
          <h3 class="card-title">Session ${escapeHtml(detail.session_id)}</h3>
          <div class="card-subtitle">${escapeHtml(detail.hostname)}${detail.sut_id ? ` (${escapeHtml(detail.sut_id)})` : ""} • ${escapeHtml(detail.status)}</div>
        </div>
        <div class="button-row session-action-bar">
          <button class="action-button primary" id="session-generate-report" ${reportPending || detail.status === "running" ? "disabled" : ""}>${reportPending ? "Generating..." : "Generate Report"}</button>
          ${reportReady ? `<a class="action-button ghost" target="_blank" rel="noopener" href="${api("/api/sessions/" + encodeURIComponent(detail.session_id) + "/report/view")}">View Report</a>` : ""}
          ${reportReady ? downloadLink("Download Report", api("/api/files?path=" + encodeURIComponent(detail.report_path)), "action-button ghost") : ""}
          ${detail.status === "running" ? "" : downloadLink("Download Excel", api("/api/sessions/" + encodeURIComponent(detail.session_id) + "/workbook"), "action-button ghost")}
          ${downloadLink("Download ZIP", api("/api/sessions/" + encodeURIComponent(detail.session_id) + "/download"), "action-button ghost")}
          <button class="action-button danger" id="session-delete">Delete Session</button>
        </div>
      </div>
      <div class="summary-grid">
        <div class="detail-panel"><div class="small">Status</div><div class="item-title">${escapeHtml(detail.status)}</div></div>
        <div class="detail-panel"><div class="small">Start</div><div class="item-title">${fmtTs(detail.start_time)}</div></div>
        <div class="detail-panel"><div class="small">End</div><div class="item-title">${fmtTs(detail.end_time)}</div></div>
        <div class="detail-panel"><div class="small">Duration</div><div class="item-title">${duration}s</div></div>
        <div class="detail-panel"><div class="small">SUT Path</div><div class="item-title mono">${escapeHtml(detail.output_dir || "N/A")}</div></div>
      </div>
      <div class="detail-panel" style="margin-top:12px;">
        <div class="key-value"><span>Report Status</span><strong>${escapeHtml(detail.report_status || "none")}</strong></div>
        ${reportFailed ? `<div class="item-text" style="margin-top:8px;color:#b23a3a;">${escapeHtml(detail.report_error || "Report generation failed. Check the session report logs.")}</div>` : ""}
      </div>
      <div class="detail-panel" style="margin-top:16px;">
        <div class="item-title">Selected Categories</div>
        <div class="button-row" style="margin-top:10px;">${(detail.categories || []).map(cat => `<span class="tag">${escapeHtml(cat)}</span>`).join("")}</div>
      </div>
      <section id="capture-quality" style="margin-top:16px;">
        <div class="card-head">
          <h3 class="card-title">Capture Quality</h3>
          <button class="action-button ghost" id="session-check-quality" ${qualityBusy || detail.status === "running" ? "disabled" : ""}>${qualityBusy ? "Checking..." : "Check Evidence"}</button>
        </div>
        <div class="key-value"><span>Evidence completeness</span><strong class="tag ${quality.status === "COMPLETE" ? "good" : quality.status === "INCOMPLETE" ? "warn" : "info"}">${escapeHtml(quality.status === "NOT_EVALUATED" ? "Not evaluated" : quality.status)}</strong></div>
        <div class="key-value"><span>Workload / silicon verdict</span><strong>NOT EVALUATED</strong></div>
        ${quality.generated_at ? `<div class="key-value"><span>Checked</span><span>${fmtTs(quality.generated_at)}</span></div>` : ""}
        ${state.qualityErrors[detail.session_id] ? `<div role="alert" class="item-text">${escapeHtml(state.qualityErrors[detail.session_id])}</div>` : ""}
        <div class="file-list compact-file-list" style="margin-top:12px;">
          ${issues.length ? foldSection(foldKey("quality-issues"), "Needs attention", `${issues.length} check(s)`, qualityChecksTable(issues), true) : ""}
          ${checks.length ? foldSection(foldKey("quality-all"), "All checks", `${checks.length - issues.length} of ${checks.length} complete`, qualityChecksTable(checks)) : ""}
          ${unsupported.length ? foldSection(foldKey("quality-unsupported"), "Unsupported queries", `${unsupported.length}, not counted`, `
            <div class="stack-list" style="margin-top:8px;">
              ${unsupported.map(item => `<div class="key-value"><span class="mono">${escapeHtml(item.command)}</span><span class="muted">${escapeHtml(item.reason)}</span></div>`).join("")}
            </div>
          `) : ""}
        </div>
      </section>
      ${(detail.remote_diagnostics || []).length ? `
        <div class="detail-panel" style="margin-top:16px;">
          <div class="item-title">Remote Commands</div>
          ${(detail.remote_diagnostics || []).map(job => `<div class="key-value"><span class="mono">${escapeHtml(job.job_id)}</span><strong>${escapeHtml(job.status)}</strong></div>${job.error ? `<div class="item-text">${escapeHtml(job.error)}</div>` : ""}`).join("")}
        </div>
      ` : ""}
      <div class="detail-panel" style="margin-top:16px;">
        <div class="item-title">Session Files</div>
        <div class="file-list compact-file-list" style="margin-top:10px;">
          ${Object.keys(grouped).length ? Object.entries(grouped).map(([category, files]) => foldSection(foldKey(`files-${category}`), category, `${files.length} file(s)`, `
            <div class="stack-list" style="margin-top:8px;">
              ${files.map(file => `<div class="key-value"><span>${escapeHtml(file.name)}</span>${downloadLink("Download", api("/api/files?path=" + encodeURIComponent(file.path)))}</div>`).join("")}
            </div>
          `)).join("") : `<div class="muted">No files were recorded for this session.</div>`}
        </div>
      </div>
    </div>
  `);
  if (!rendered) return;
  detailRoot.querySelectorAll("details[data-fold]").forEach(node => {
    node.ontoggle = () => state.folds.set(node.dataset.fold, node.open);
  });

  const generateBtn = qs("session-generate-report");
  if (generateBtn) {
    generateBtn.onclick = async () => {
      await startSessionReport(detail.session_id);
    };
  }
  const deleteBtn = qs("session-delete");
  if (deleteBtn) {
    deleteBtn.onclick = async () => {
      await deleteSession(detail.session_id);
    };
  }
  const qualityBtn = qs("session-check-quality");
  if (qualityBtn) qualityBtn.onclick = () => checkCaptureEvidence(detail.session_id);
}

async function checkCaptureEvidence(sessionId) {
  if (state.qualityRequests[sessionId]) return;
  state.qualityRequests[sessionId] = true;
  delete state.qualityErrors[sessionId];
  renderSessions();
  try {
    const response = await fetch(api(`/api/sessions/${encodeURIComponent(sessionId)}/quality`), { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : "Evidence check failed.");
    }
    if (state.sessionDetail?.session_id === sessionId) state.sessionDetail.capture_quality = payload;
    const files = await fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}/files`);
    if (state.selectedSessionId === sessionId) state.sessionFiles = files.files || [];
  } catch (error) {
    state.qualityErrors[sessionId] = error.message || "Unable to check evidence. Please retry.";
  } finally {
    delete state.qualityRequests[sessionId];
    renderSessions();
  }
}

function groupFilesByCategory(files) {
  const seenPaths = new Set();
  const unique = (files || []).filter(file => {
    const key = String(file.path || "");
    if (!key || seenPaths.has(key)) return false;
    seenPaths.add(key);
    return true;
  });
  return unique.reduce((acc, file) => {
    const key = file.category || "other";
    acc[key] = acc[key] || [];
    acc[key].push(file);
    return acc;
  }, {});
}

async function startSessionReport(sessionId) {
  if (state.reportingSessions[sessionId]) return;
  state.reportingSessions[sessionId] = true;
  renderSessions();
  try {
    const response = await fetch(api("/api/sessions/" + encodeURIComponent(sessionId) + "/report"), { method: "POST" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      updateSessionReportState(sessionId, { report_status: "failed", report_error: typeof error.detail === "string" ? error.detail : `Report generation failed (${response.status}). Please retry.` });
      return;
    }
    updateSessionReportState(sessionId, { report_status: "pending", report_error: "", report_path: "" });
    renderSessions();
    await pollSessionReportStatus(sessionId, 25);
    if (state.selectedSessionId === sessionId) {
      await loadSessionDetail(sessionId);
    }
  } catch (_) {
    updateSessionReportState(sessionId, { report_status: "failed", report_error: "Unable to generate report. Please retry." });
  } finally {
    delete state.reportingSessions[sessionId];
    renderSessions();
  }
}

function updateSessionReportState(sessionId, patch) {
  state.sessions = (state.sessions || []).map(session => {
    if (session.session_id !== sessionId) return session;
    return { ...session, ...patch };
  });
  if (state.sessionDetail && state.sessionDetail.session_id === sessionId) {
    state.sessionDetail = { ...state.sessionDetail, ...patch };
  }
}

async function pollSessionReportStatus(sessionId, attempts = 20) {
  for (let index = 0; index < attempts; index += 1) {
    try {
      const status = await fetchJson("/api/sessions/" + encodeURIComponent(sessionId) + "/report");
      if (status.status === "ready") {
        updateSessionReportState(sessionId, {
          report_status: "ready",
          report_path: status.report_path || "",
          report_error: "",
        });
        return;
      }
      if (status.status === "failed") {
        updateSessionReportState(sessionId, {
          report_status: "failed",
          report_error: status.error || "Report generation failed. Check the session report logs.",
          report_path: "",
        });
        return;
      }
    } catch (_) {
      // Keep polling within bounded attempts.
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

async function deleteSession(sessionId) {
  if (state.deletingSessions[sessionId]) return;
  const confirmed = window.confirm(`Delete capture ${sessionId}? This removes DB records and generated files.`);
  if (!confirmed) return;
  state.deletingSessions[sessionId] = true;
  renderSessions();

  state.sessions = (state.sessions || []).filter(item => item.session_id !== sessionId);
  if (state.expandedSessionId === sessionId) state.expandedSessionId = "";
  try {
    const response = await fetch(api("/api/sessions/" + encodeURIComponent(sessionId)), { method: "DELETE" });
    if (!response.ok) {
      delete state.deletingSessions[sessionId];
      await loadSessions();
      renderSessions();
      return;
    }
    if (state.selectedSessionId === sessionId) {
      state.selectedSessionId = "";
      state.sessionDetail = null;
      state.sessionFiles = [];
    }
    if (state.diag.completedSessionId === sessionId) {
      state.diag.completedSessionId = "";
    }
    delete state.deletingSessions[sessionId];
    renderSessions();
    renderDiagnostics();
    await loadSessions();
    renderSessions();
  } catch (_) {
    delete state.deletingSessions[sessionId];
    await loadSessions();
    renderSessions();
  }
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
  renderContextBar();
  showSection(state.view);
  renderActiveView();
  resizeCharts();
}

function renderActiveView() {
  if (state.view === "overview") {
    renderOverview();
    return;
  }
  if (state.view === "irq") {
    renderIrq();
    return;
  }
  if (state.view === "network") {
    renderNetwork();
    return;
  }
  if (state.view === "cpu") {
    renderCpu();
    return;
  }
  if (state.view === "storage") {
    renderStorage();
    return;
  }
  if (state.view === "diagnostics") {
    renderDiagnostics();
    bindDiagnosticsActions();
    renderSessions();
  }
}

function applyLiveCpuUtilization(payload) {
  const util = payload?.cpu_utilization;
  if (!util || typeof util !== "object" || !Object.keys(util).length) return false;
  const sampleTs = Number(payload.timestamp || 0);
  if (sampleTs && state.cpuUtilTimestamp && sampleTs < state.cpuUtilTimestamp) {
    return false;
  }
  state.viz = state.viz || {};
  state.viz.cpu_utilization = util;
  state.cpuUtilTimestamp = sampleTs || nowSeconds();
  state.lastDataAt = nowSeconds();
  return true;
}

function renderCpuLiveUpdate() {
  if (!state.topology?.available || !(state.topology?.rows || []).length) return;
  // Update only CPU visuals in-place to keep latency low during live samples.
  if (state.view === "cpu") {
    renderTopologyMap("cpu-topology", { compact: false, interactive: true });
    renderCpuDetail();
  }
  if (state.view === "overview") {
    renderTopologyMap("overview-topology", { compact: true });
  }
}

function pointerIsDown() {
  // A redraw between press and release swallows the click; capped because a release outside the window goes unreported.
  return Date.now() - state.pointerDownAt < 1500;
}

function scheduleTelemetryRefresh(delayMs = LIVE_RENDER_INTERVAL_MS) {
  if (state.refreshTimer) return;
  // Charts redraw at most once per interval, however fast the backend samples.
  const wait = Math.max(delayMs, LIVE_RENDER_INTERVAL_MS - (Date.now() - state.lastRefreshAt));
  state.refreshTimer = setTimeout(async () => {
    state.refreshTimer = null;
    // A tab nobody is looking at does not make the SUT build responses.
    if (state.refreshInFlight || !state.host || document.hidden) return;
    state.refreshInFlight = true;
    // Counted from the start, so one refresh per sample keeps a steady beat whatever the network delay.
    state.lastRefreshAt = Date.now();
    try {
      // Requested together: a remote browser pays the network delay once per refresh, not once per request.
      await Promise.all([
        loadSystems(),
        state.paused ? null : loadSelectedData(),
        state.view === "diagnostics"
          ? loadSessions().then(() => (state.selectedSessionId ? loadSessionDetail(state.selectedSessionId) : null))
          : null,
      ]);
      if (pointerIsDown()) return;
      // While paused, monitoring views keep the frame the user froze; capture lists still update.
      if (state.paused && state.view !== "diagnostics") renderContextBar();
      else render();
    } finally {
      state.refreshInFlight = false;
    }
  }, wait);
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
    // containLabel sizes the plot around its axis labels, so wide values are not cut off.
    grid: { left: 10, right: 28, top: 30, bottom: 8, containLabel: true },
    textStyle: { color: "#1f2430", fontFamily: "Aptos, Segoe UI Variable Display, sans-serif" },
  };
}

// Axis labels: 1,800,000 reads as 1.8M.
function fmtAxisNumber(value) {
  const n = Number(value || 0);
  const abs = Math.abs(n);
  const scaled = (divisor, unit) => `${Number((n / divisor).toFixed(1))}${unit}`;
  if (abs >= 1e9) return scaled(1e9, "G");
  if (abs >= 1e6) return scaled(1e6, "M");
  if (abs >= 1e3) return scaled(1e3, "K");
  return String(Number(n.toFixed(abs < 10 ? 2 : 1)));
}

function fmtAxisBytesRate(value) {
  return `${fmtAxisNumber(value)}B/s`;
}

function timeAxis() {
  return { type: "time", axisLabel: { color: "#6b7280", hideOverlap: true } };
}

function valueAxis(name, formatter = fmtAxisNumber) {
  return { type: "value", name, axisLabel: { color: "#6b7280", formatter } };
}

function renderSimpleTrendChart(key, elementId, data, seriesName, yName, color) {
  const chart = ensureChart(key, elementId);
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "axis" },
    xAxis: timeAxis(),
    yAxis: valueAxis(yName),
    series: [{ name: seriesName, type: "line", showSymbol: false, smooth: true, data, lineStyle: { width: 3, color }, areaStyle: { color: withAlpha(color, 0.12) } }],
  });
}

function renderDualTrendChart(key, elementId, first, second, firstName, secondName, formatter) {
  const chart = ensureChart(key, elementId);
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#6b7280" } },
    xAxis: timeAxis(),
    yAxis: valueAxis("", formatter),
    series: [
      { name: firstName, type: "line", showSymbol: false, smooth: true, data: first, lineStyle: { width: 3, color: "#2368d1" }, areaStyle: { color: "rgba(35,104,209,0.12)" } },
      { name: secondName, type: "line", showSymbol: false, smooth: true, data: second, lineStyle: { width: 3, color: "#237a57" }, areaStyle: { color: "rgba(35,122,87,0.12)" } },
    ],
  });
}

function renderMultiSeriesChart(key, elementId, series, yName, secondAxisName = "") {
  const chart = ensureChart(key, elementId);
  if (!chart) return;
  // replaceMerge swaps only the series: a full reset every second would undo legend toggles and paging.
  chart.setOption({
    ...baseChart(),
    // Room for the axis names below the legend row.
    grid: { ...baseChart().grid, top: 58 },
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { type: "scroll", top: 0, textStyle: { color: "#6b7280" } },
    xAxis: timeAxis(),
    yAxis: secondAxisName ? [valueAxis(yName), { ...valueAxis(secondAxisName), splitLine: { show: false } }] : [valueAxis(yName)],
    series: series.map(item => ({
      id: item.name,
      name: item.name,
      type: "line",
      showSymbol: false,
      data: item.data,
      yAxisIndex: item.axis || 0,
      lineStyle: { width: 2, type: item.dashed ? "dashed" : "solid" },
    })),
  }, { replaceMerge: ["series"] });
}

function renderTopIrqChart(rows) {
  const chart = ensureChart("irqTop", "chart-irq-top");
  if (!chart) return;
  chart.setOption({
    ...baseChart(),
    animation: false,
    tooltip: { trigger: "item" },
    xAxis: { type: "value", axisLabel: { color: "#6b7280", formatter: fmtAxisNumber, hideOverlap: true } },
    // Full source names do not fit beside the bars; the table below has them.
    yAxis: { type: "category", data: rows.map(row => `${row.irq} • ${row.nic || row.source_class || "other"}`), axisLabel: { color: "#6b7280", fontSize: 11, interval: 0 } },
    series: [{ type: "bar", data: rows.map(row => Number(row.total_rate || 0)), itemStyle: { color: "#2368d1", borderRadius: 8 } }],
  });
}

function renderIrqHeatmap(rows) {
  const root = qs("chart-irq-heatmap");
  if (!root) return;
  const cpus = [...new Set(rows.flatMap(row => Object.keys(row.cpu_rates || {})))].sort((a, b) => Number(a) - Number(b));
  if (!rows.length || !cpus.length) {
    root.innerHTML = emptyState("No IRQ/CPU matrix data", "No per-CPU IRQ activity is available in the current selection.");
    return;
  }

  const maxRate = Math.max(1, ...rows.flatMap(row => Object.values(row.cpu_rates || {}).map(v => Number(v || 0))));
  const columnLabel = heatmapColumnLabel();

  const levelFor = (rate) => {
    const ratio = Math.max(0, Math.min(1, Number(rate || 0) / maxRate));
    if (ratio >= 0.85) return 5;
    if (ratio >= 0.65) return 4;
    if (ratio >= 0.45) return 3;
    if (ratio >= 0.25) return 2;
    if (ratio > 0) return 1;
    return 0;
  };

  // Built once per shape and recolored in place: a rebuild every second would reset the scroll position.
  let grid = root.querySelector(".irq-matrix-grid");
  if (!grid) {
    root.innerHTML = `
      <div class="irq-matrix-shell">
        <div class="legend"><span>Low</span><div class="legend-bar"></div><span>High</span><span class="muted"></span></div>
        <div class="irq-matrix-scroll"><div class="irq-matrix-grid"></div></div>
      </div>
    `;
    grid = root.querySelector(".irq-matrix-grid");
  }
  const shape = [columnLabel, rows.length, ...cpus].join("|");
  if (grid.dataset.shape !== shape) {
    grid.dataset.shape = shape;
    const labelStep = cpus.length <= 24 ? 1 : Math.ceil(cpus.length / 18);
    const showCpuLabel = (idx) => idx === 0 || idx === cpus.length - 1 || idx % labelStep === 0;
    // A handful of NUMA/socket columns get wide cells; per-CPU columns stay compact.
    const cellWidth = cpus.length <= 16 ? 48 : 14;
    const header = cpus.map((cpu, idx) => `<div class="irq-cpu-label" title="${columnLabel} ${escapeHtml(cpu)}">${showCpuLabel(idx) ? escapeHtml(cpu) : ""}</div>`).join("");
    const rowHtml = `<div class="irq-row-label"></div>${`<div class="irq-heat-cell" style="width:${cellWidth}px;"></div>`.repeat(cpus.length)}`;
    grid.style.gridTemplateColumns = `minmax(220px, 220px) repeat(${cpus.length}, minmax(${cellWidth}px, ${cellWidth}px))`;
    grid.innerHTML = `<div class="irq-header-label">IRQ Source</div>${header}${rowHtml.repeat(rows.length)}`;
  }
  root.querySelector(".legend .muted").textContent = `Current IRQ/s per (IRQ source, ${columnLabel}) • ${rows.length} active IRQ(s)`;

  const set = (node, key, value) => {
    if (node[key] !== value) node[key] = value;
  };
  const labels = grid.querySelectorAll(".irq-row-label");
  const cells = grid.querySelectorAll(".irq-heat-cell");
  rows.forEach((row, rowIndex) => {
    set(labels[rowIndex], "textContent", `${row.irq} • ${row.nic || row.source_class || "other"}`);
    set(labels[rowIndex], "title", `${row.irq_name || row.irq}`);
    cpus.forEach((cpu, cpuIndex) => {
      const cell = cells[rowIndex * cpus.length + cpuIndex];
      const level = String(levelFor((row.cpu_rates || {})[cpu]));
      if (cell.dataset.level !== level) cell.dataset.level = level;
    });
  });

  // Tooltip for the hovered cell only: rewriting thousands of titles per refresh made Edge deliver responses seconds late.
  const describe = cell => {
    const index = Array.prototype.indexOf.call(cells, cell);
    if (index < 0) return;
    const row = rows[Math.floor(index / cpus.length)];
    const cpu = cpus[index % cpus.length];
    const rate = Number((row.cpu_rates || {})[cpu] || 0);
    cell.title = `${columnLabel} ${cpu}\nIRQ source: ${row.irq_name || row.irq}\nIRQ rate: ${fmtCount(rate)}/s\nTotal: ${fmtCount(row.total_count || 0)}`;
  };
  grid.onmouseover = event => describe(event.target);
  const hovered = grid.querySelector(".irq-heat-cell:hover");
  if (hovered) describe(hovered);
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
  state.sshMachines = data.ssh_machines || [];
  state.canManageMachines = Boolean(data.can_manage_machines);
  if (!state.host || !state.systems.some(item => item.id === state.host)) {
    // No choice made yet: the machine this server runs on when it is the one connecting to the others, else a remote one.
    const own = state.sshMachines.length ? state.systems.find(item => item.mode === "local" && (item.interfaces || []).length) : null;
    const first = own || state.systems.find(item => item.mode === "remote") || state.systems[0];
    state.host = first ? first.id : "";
  }
  persistSelections();
}

// History is what a refresh costs the SUT most: refetched every 1 s up to 2.5 min ranges, 2 s at 5 min, 15 s at 1 h.
function historyIntervalMs(windowSeconds) {
  return Math.min(15000, Math.max(LIVE_RENDER_INTERVAL_MS, (windowSeconds * 1000) / 150));
}

// The machine map exists only on Overview, and only with a second machine: one reporting or one being connected.
// A refresh does not wait for it: one request more than a browser sends at once would cost every refresh a second round trip.
function loadFleet() {
  if (state.systems.length <= 1 && !state.sshMachines.length) {
    state.fleet = null;
    return Promise.resolve();
  }
  if (state.view !== "overview") return Promise.resolve();
  if (!state.fleetRequest) {
    state.fleetRequest = fetchJson("/api/fleet").then(fleet => {
      state.fleet = fleet;
      if (state.view === "overview" && !pointerIsDown()) renderFleet();
    }, () => null).finally(() => {
      state.fleetRequest = null;
    });
  }
  return state.fleetRequest;
}

async function loadSelectedData() {
  const host = state.host;
  if (!host) {
    state.snapshot = null;
    state.viz = null;
    state.io = null;
    state.topology = null;
    state.interfacesMeta = [];
    return;
  }
  const seq = ++state.loadSeq;
  const iface = state.selectedIface;
  const sut = encodeURIComponent(host);
  const bounds = timeBounds();
  const query = state.timeRange === "custom"
    ? `from_ts=${encodeURIComponent(bounds.from)}&to_ts=${encodeURIComponent(bounds.to)}`
    : `window_seconds=${encodeURIComponent(bounds.to - bounds.from)}`;
  const startedAt = Date.now();
  const historyKey = [host, query, iface].join("|");
  // Half a refresh of slack, so timer jitter cannot push a due refetch to the following refresh.
  const historyAge = startedAt - state.historyAt + LIVE_RENDER_INTERVAL_MS / 2;
  const historyDue = historyKey !== state.historyKey || historyAge >= historyIntervalMs(bounds.to - bounds.from);
  const detailsDue = host !== state.detailsHost || startedAt - state.detailsAt >= DETAILS_REFRESH_MS || !state.topology?.available;
  // Current values every time, history and details when due; compact=1 omits history the dashboard does not draw.
  const requests = Promise.all([
    fetchJson(`/api/irq/current?sut_id=${sut}&limit=2000`),
    fetchJson(`/api/softirq/current?sut_id=${sut}`),
    fetchJson(`/api/network/current?sut_id=${sut}`),
    historyDue ? fetchJson(`/api/systems/${sut}/visualization?${query}&top_n=25&compact=1`) : null,
    fetchJson(`/api/systems/${sut}/io?${historyDue ? query : "window_seconds=30"}`).catch(() => null),
    historyDue && iface !== "ALL" ? fetchJson(`/api/network/${encodeURIComponent(iface)}?sut_id=${sut}`).catch(() => null) : null,
    detailsDue ? fetchJson(`/api/interfaces?sut_id=${sut}`) : null,
    detailsDue ? fetchJson(`/api/systems/${sut}/visualization/topology`) : null,
  ]);
  // Sent last, so that it is the map that waits when the browser has no connection to spare.
  loadFleet();
  const [irq, softirq, network, viz, io, ifaceHistory, interfaces, topology] = await requests;
  // A newer request, or another SUT or interface chosen meanwhile, owns the screen.
  if (seq !== state.loadSeq || host !== state.host || iface !== state.selectedIface) return;
  state.snapshot = { irq, softirq, network };
  if (historyDue) {
    state.viz = viz;
    state.cpuUtilTimestamp = Number(viz?.timestamp || state.cpuUtilTimestamp || 0);
    state.interfaceHistory = ifaceHistory;
    state.historyKey = historyKey;
    state.historyAt = startedAt;
  } else if (io?.storage && state.io?.storage) {
    // This response only refreshed the current values; keep the series that is plotted.
    io.storage.series = state.io.storage.series;
  }
  state.io = io;
  if (detailsDue) {
    state.interfacesMeta = interfaces.interfaces || [];
    state.topology = topology;
    state.detailsHost = host;
    state.detailsAt = startedAt;
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

// One path for the SUT switcher and the machine map.
async function selectSut(sutId) {
  if (!sutId || sutId === state.host || !state.systems.some(item => item.id === sutId)) return;
  state.host = sutId;
  state.selectedIface = "ALL";
  state.selectedCpu = "";
  state.selectedSessionId = "";
  state.expandedSessionId = "";
  state.sessionDetail = null;
  state.sessionFiles = [];
  persistSelections();
  // The map marks the new selection at once; the details follow when they are loaded.
  renderFleet();
  await loadSelectedData();
  if (state.view === "diagnostics") await loadSessions();
  render();
}

function bindMachineActions() {
  const dialog = qs("machine-dialog");
  qs("sut-switcher").onchange = event => {
    if (event.target.value !== ADD_MACHINE) {
      selectSut(event.target.value);
      return;
    }
    event.target.value = state.host || "";
    qs("machine-error").textContent = "";
    dialog.showModal();
  };
  qs("machine-cancel").onclick = () => dialog.close();
  qs("machine-form").onsubmit = async event => {
    event.preventDefault();
    const target = qs("machine-target").value.trim();
    qs("machine-submit").disabled = true;
    const response = await fetch(api("/api/machines"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, role: qs("machine-role").value }),
    }).catch(() => null);
    qs("machine-submit").disabled = false;
    if (!response || !response.ok) {
      const detail = response ? await response.json().then(body => body.detail, () => "") : "";
      qs("machine-error").textContent = typeof detail === "string" && detail ? detail : "The machine could not be added.";
      return;
    }
    dialog.close();
    qs("machine-form").reset();
    // Overview is where the new machine shows up, first as connecting.
    state.view = "overview";
    storage.set("irqlens:view", state.view);
    await loadSystems();
    await loadSelectedData();
    await loadFleet();
    render();
  };
}

async function removeMachine(machineId) {
  if (!window.confirm(`Stop monitoring ${machineId}? Its captures are kept.`)) return;
  const response = await fetch(api(`/api/machines/${encodeURIComponent(machineId)}`), { method: "DELETE" }).catch(() => null);
  if (!response || !response.ok) {
    showToast(`${machineId} could not be removed${response ? ` (HTTP ${response.status})` : ""}.`, "bad");
    return;
  }
  // loadSystems moves the selection when the removed machine was the selected one.
  await loadSystems();
  await loadSelectedData();
  await loadFleet();
  render();
}

async function refreshAll() {
  state.loading.global = true;
  try {
    await Promise.all([loadHealth(), loadSystems().then(() => loadSelectedData())]);
    if (state.view === "diagnostics") {
      await loadSessions();
      if (state.selectedSessionId) await loadSessionDetail(state.selectedSessionId);
    }
  } finally {
    state.loading.global = false;
  }
}

function bindGlobalActions() {
  if (el["custom-apply"]) {
    el["custom-apply"].onclick = async () => {
      state.customFrom = el["custom-from"]?.value || "";
      state.customTo = el["custom-to"]?.value || "";
      persistSelections();
      await refreshSelectedHistory();
      render();
    };
  }
  qs("irq-search").oninput = event => {
    state.irqSearch = event.target.value || "";
    renderIrq();
  };
  qs("irq-sort").onchange = event => {
    state.irqSort = event.target.value || "rate";
    renderIrq();
  };
  qs("irq-topn").value = state.irqTopN;
  qs("irq-topn").onchange = event => {
    state.irqTopN = IRQ_TOP_N_OPTIONS.includes(event.target.value) ? event.target.value : "25";
    storage.set("irqlens:irqTopN", state.irqTopN);
    renderIrq();
  };
  qs("irq-aggregate").value = state.irqAggregate;
  qs("irq-aggregate").onchange = event => {
    state.irqAggregate = IRQ_AGGREGATE_OPTIONS.includes(event.target.value) ? event.target.value : "cpu";
    storage.set("irqlens:irqAggregate", state.irqAggregate);
    renderIrq();
  };
  bindMachineActions();
  qs("live-toggle").onclick = () => {
    state.paused = !state.paused;
    renderContextBar();
    if (!state.paused) scheduleTelemetryRefresh(0);
  };
  document.addEventListener("pointerdown", () => { state.pointerDownAt = Date.now(); }, true);
  ["pointerup", "pointercancel"].forEach(type => document.addEventListener(type, () => { state.pointerDownAt = 0; }, true));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleTelemetryRefresh(0);
  });
  document.addEventListener("click", event => {
    const node = event.target.closest ? event.target.closest("[data-download-url]") : null;
    if (!node) return;
    event.preventDefault();
    downloadFile(node.dataset.downloadUrl);
  });
  qs("network-interface-selector").onchange = async event => {
    state.selectedIface = event.target.value || "ALL";
    persistSelections();
    await loadInterfaceHistory();
    render();
  };
  if (el["cpu-metric-selector"]) {
    el["cpu-metric-selector"].onchange = event => {
      state.cpuMetric = event.target.value || "irq";
      persistSelections();
      render();
    };
  }
  qs("diag-duration").onchange = event => {
    state.diag.duration = Number(event.target.value || 60);
  };
  const sessionNameInput = qs("diag-session-name");
  if (sessionNameInput) {
    sessionNameInput.oninput = event => {
      state.diag.sessionName = (event.target.value || "").trim();
      if (state.diag.error) {
        state.diag.error = "";
        renderDiagnostics();
        bindDiagnosticsActions();
      }
    };
  }
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
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    const sutId = payload.sut_id || payload.host || "";
    const isCurrentSutTelemetry = payload.type === "telemetry" && sutId && sutId === state.host;
    if (isCurrentSutTelemetry) {
      if (state.paused) return;
      const updated = applyLiveCpuUtilization(payload);
      if (updated && !pointerIsDown() && Date.now() - state.lastLiveRenderAt >= LIVE_RENDER_INTERVAL_MS) {
        state.lastLiveRenderAt = Date.now();
        renderCpuLiveUpdate();
        renderContextBar();
      }
      // Fetch as soon as the sample exists; refreshes stay at most one per second.
      scheduleTelemetryRefresh(0);
      return;
    }

    if (
      !sutId
      || payload.type === "system_registered"
      || payload.type === "session_started"
      || payload.type === "session_stopped"
      || payload.type === "session_deleted"
      || payload.type === "session_report_ready"
      || payload.type === "session_diagnostics_ready"
    ) {
      scheduleTelemetryRefresh(150);
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
    if (document.hidden) return;
    if (!state.paused && (!state.ws || state.ws.readyState !== WebSocket.OPEN)) {
      await refreshAll();
      if (!pointerIsDown()) render();
      return;
    }
    // A SUT that stopped reporting sends no events; polling lets it turn STALE or OFFLINE.
    scheduleTelemetryRefresh(0);
  }, 5000);
  window.addEventListener("resize", () => {
    resizeCharts();
    drawFleetLinks();
  });
}

boot();
