const stateColors = {
  DIRECT: "text-emerald-300 bg-emerald-950 border-emerald-800",
  PEER_RELAY: "text-yellow-300 bg-yellow-950 border-yellow-800",
  DERP: "text-orange-300 bg-orange-950 border-orange-800",
  INACTIVE: "text-sky-300 bg-sky-950 border-sky-800",
  OFFLINE: "text-red-300 bg-red-950 border-red-800",
  UNKNOWN: "text-slate-300 bg-slate-800 border-slate-700",
};

const dotColors = {
  DIRECT: "bg-emerald-400",
  PEER_RELAY: "bg-yellow-400",
  DERP: "bg-orange-400",
  INACTIVE: "bg-sky-400",
  OFFLINE: "bg-red-400 offline-pulse",
  UNKNOWN: "bg-slate-400",
};

const stateLabels = {
  DIRECT: "DIRECT",
  PEER_RELAY: "SPEED RELAY",
  DERP: "RELAY (DERP)",
  INACTIVE: "INACTIVE",
  OFFLINE: "OFFLINE",
  UNKNOWN: "UNKNOWN",
};

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatState(state) {
  if (!state) return stateLabels.UNKNOWN;
  return stateLabels[state] || state;
}

function formatRelative(iso) {
  if (!iso) return "never";
  const now = Date.now();
  const ts = new Date(iso).getTime();
  const diffSec = Math.max(0, Math.floor((now - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs}h ${rem}m`;
}

function formatMs(value) {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toFixed(2)}ms`;
}

function formatPct(value) {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toFixed(2)}%`;
}

function stateBadge(state) {
  const palette = stateColors[state] || stateColors.UNKNOWN;
  const dot = dotColors[state] || dotColors.UNKNOWN;
  const label = formatState(state);
  return `
    <span class="inline-flex items-center gap-2 px-2.5 py-1 rounded-full border text-xs font-semibold ${palette}">
      <span class="w-2 h-2 rounded-full ${dot}"></span>
      ${escapeHtml(label)}
    </span>
  `;
}

function renderSummary(stats) {
  document.getElementById("statTotal").textContent = stats.total_nodes ?? 0;
  document.getElementById("statOnline").textContent = stats.nodes_online ?? 0;
  document.getElementById("statInactive").textContent = stats.nodes_inactive ?? 0;
  document.getElementById("statOffline").textContent = stats.nodes_offline ?? 0;
  document.getElementById("statDerp").textContent = stats.nodes_on_derp ?? 0;
}

function renderNodes(nodes) {
  const grid = document.getElementById("nodesGrid");
  if (!nodes.length) {
    grid.innerHTML = '<div class="p-4 rounded-xl bg-slate-900 border border-slate-800 text-slate-400">No nodes configured.</div>';
    return;
  }

  grid.innerHTML = nodes
    .map((node) => {
      const state = node.current_state || "UNKNOWN";
      const tags = (node.tags || [])
        .map((tag) => `<span class="px-2 py-0.5 rounded bg-slate-800 text-xs text-slate-300 border border-slate-700">${escapeHtml(tag)}</span>`)
        .join(" ");
      const uptime = node.uptime_7d_pct ?? 0;
      const label = escapeHtml(node.label || node.ip);
      const ip = escapeHtml(node.ip);
      const directEndpoint = escapeHtml(node.cur_addr_endpoint || "-");
      const speedRelayEndpoint = escapeHtml(node.peer_relay_endpoint || "-");
      const relayHint = escapeHtml(node.relay_hint || node.derp_region || "-");
      return `
        <article class="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-lg font-semibold">${label}</h3>
              <div class="text-xs text-slate-400">${ip}</div>
            </div>
            ${stateBadge(state)}
          </div>

          <div class="mt-3 space-y-1 text-sm text-slate-300">
            <div>Last checked: <span class="text-slate-100">${formatRelative(node.last_checked)}</span></div>
            <div>Confidence: <span class="text-slate-100">${escapeHtml(node.confidence || "low")}</span></div>
            <div>Direct path: <span class="text-slate-100">${directEndpoint}</span></div>
            <div>Speed relay: <span class="text-slate-100">${speedRelayEndpoint}</span></div>
            <div>Relay hint: <span class="text-slate-100">${relayHint}</span></div>
            <div>Ping avg: <span class="text-slate-100">${formatMs(node.ping_avg_ms)}</span></div>
          </div>

          <div class="mt-3">
            <div class="text-xs text-slate-400 mb-1">7d uptime: ${uptime}%</div>
            <div class="w-full h-2 rounded bg-slate-800 overflow-hidden">
              <div class="h-full bg-sky-500" style="width: ${Math.max(0, Math.min(100, uptime))}%"></div>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap gap-1">${tags}</div>

          <div class="mt-4 flex gap-2">
            <button data-ip="${ip}" class="check-now px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 text-sm font-semibold">Check Now</button>
            <button data-ip="${ip}" class="ping-test px-3 py-1.5 rounded bg-amber-500 hover:bg-amber-400 text-slate-950 text-sm font-semibold">Ping Test (5)</button>
          </div>

          <div data-ping-result="${ip}" class="hidden mt-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200"></div>
        </article>
      `;
    })
    .join("");

  document.querySelectorAll(".check-now").forEach((button) => {
    button.addEventListener("click", async () => {
      const ip = button.getAttribute("data-ip");
      button.disabled = true;
      try {
        await fetch(`/api/check/${encodeURIComponent(ip)}`, { method: "POST" });
      } finally {
        setTimeout(() => {
          button.disabled = false;
        }, 1200);
      }
    });
  });

  document.querySelectorAll(".ping-test").forEach((button) => {
    button.addEventListener("click", () => runPingTest(button));
  });
}

function renderEvents(events) {
  const body = document.getElementById("eventsTableBody");
  if (!events.length) {
    body.innerHTML = '<tr><td class="px-3 py-3 text-slate-400" colspan="4">No transitions yet.</td></tr>';
    return;
  }

  body.innerHTML = events
    .slice(0, 20)
    .map((event) => {
      const state = event.current_state || "UNKNOWN";
      const colorClass = stateColors[state] || stateColors.UNKNOWN;
      return `
        <tr class="border-b border-slate-800 last:border-0">
          <td class="px-3 py-2 text-slate-300">${formatRelative(event.transitioned_at)}</td>
          <td class="px-3 py-2">${escapeHtml(event.label || event.node_ip)}</td>
          <td class="px-3 py-2"><span class="px-2 py-1 rounded border text-xs ${colorClass}">${escapeHtml(formatState(event.previous_state))} -> ${escapeHtml(formatState(event.current_state))}</span></td>
          <td class="px-3 py-2 text-slate-300">${formatDuration(event.duration_previous_seconds)}</td>
        </tr>
      `;
    })
    .join("");
}

function getPingPanel(ip) {
  return [...document.querySelectorAll("[data-ping-result]")].find((el) => el.getAttribute("data-ping-result") === ip) || null;
}

function renderPingPanel(ip, payload) {
  const panel = getPingPanel(ip);
  if (!panel) return;

  const ok = Boolean(payload.ok);
  panel.classList.remove("hidden");
  panel.className = `mt-3 rounded-lg border p-3 text-xs space-y-2 ${ok ? "border-slate-700 bg-slate-950 text-slate-200" : "border-red-800 bg-red-950 text-red-100"}`;

  const routeCounts = payload.route_counts || {};
  const routeBadges = Object.entries(routeCounts)
    .map(([state, count]) => `<span class="inline-flex items-center rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-[11px]">${escapeHtml(formatState(state))}: ${escapeHtml(count)}</span>`)
    .join(" ");

  const samples = payload.samples || [];
  const sampleRows = samples
    .map((sample, idx) => `
      <tr class="border-b border-slate-800 last:border-0">
        <td class="px-2 py-1">${idx + 1}</td>
        <td class="px-2 py-1">${escapeHtml(formatState(sample.state))}</td>
        <td class="px-2 py-1">${escapeHtml(sample.via || "-")}</td>
        <td class="px-2 py-1 text-right">${formatMs(sample.latency_ms)}</td>
      </tr>
    `)
    .join("");

  const sampleTable = samples.length
    ? `
      <div class="overflow-x-auto rounded border border-slate-800">
        <table class="w-full text-[11px]">
          <thead class="bg-slate-900 text-slate-300">
            <tr>
              <th class="px-2 py-1 text-left">#</th>
              <th class="px-2 py-1 text-left">State</th>
              <th class="px-2 py-1 text-left">Via</th>
              <th class="px-2 py-1 text-right">Latency</th>
            </tr>
          </thead>
          <tbody>${sampleRows}</tbody>
        </table>
      </div>
    `
    : '<div class="text-slate-400">No pong samples parsed.</div>';

  const rawBlock = payload.raw_output
    ? `
      <details class="rounded border border-slate-800 bg-slate-900">
        <summary class="cursor-pointer px-2 py-1 text-slate-300">Raw ping output</summary>
        <pre class="max-h-48 overflow-auto px-2 py-2 whitespace-pre-wrap break-words">${escapeHtml(payload.raw_output)}</pre>
      </details>
    `
    : "";

  panel.innerHTML = `
    <div class="font-semibold text-sm">Ping Test (${escapeHtml(payload.count || 5)} packets)</div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1">
      <div>Status state: <span class="text-slate-100">${escapeHtml(formatState(payload.status_state))}</span></div>
      <div>Detected path: <span class="text-slate-100">${escapeHtml(formatState(payload.ping_state || payload.status_state))}</span></div>
      <div>Direct endpoint: <span class="text-slate-100">${escapeHtml(payload.cur_addr_endpoint || "-")}</span></div>
      <div>Speed relay endpoint: <span class="text-slate-100">${escapeHtml(payload.peer_relay_endpoint || "-")}</span></div>
      <div>Relay hint: <span class="text-slate-100">${escapeHtml(payload.relay_hint || "-")}</span></div>
      <div>DERP region: <span class="text-slate-100">${escapeHtml(payload.derp_region || "-")}</span></div>
      <div>Latency: <span class="text-slate-100">${formatMs(payload.min_ms)} / ${formatMs(payload.avg_ms)} / ${formatMs(payload.max_ms)}</span></div>
      <div>Packet loss: <span class="text-slate-100">${formatPct(payload.packet_loss_pct)}</span></div>
    </div>
    <div class="flex flex-wrap gap-1">${routeBadges || '<span class="text-slate-400">No route samples</span>'}</div>
    ${payload.error ? `<div class="text-red-300">${escapeHtml(payload.error)}</div>` : ""}
    ${sampleTable}
    ${rawBlock}
  `;
}

async function runPingTest(button) {
  const ip = button.getAttribute("data-ip");
  if (!ip) return;

  const panel = getPingPanel(ip);
  if (panel) {
    panel.classList.remove("hidden");
    panel.className = "mt-3 rounded-lg border border-slate-700 bg-slate-950 p-3 text-xs text-slate-200";
    panel.innerHTML = "Running ping test...";
  }

  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "Pinging...";
  try {
    const response = await fetch(`/api/ping/${encodeURIComponent(ip)}`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errorPayload = {
        ok: false,
        count: 5,
        status_state: "UNKNOWN",
        error: payload.detail || "Ping test failed",
        samples: [],
        route_counts: {},
      };
      renderPingPanel(ip, errorPayload);
      return;
    }
    renderPingPanel(ip, payload);
  } catch (error) {
    renderPingPanel(ip, {
      ok: false,
      count: 5,
      status_state: "UNKNOWN",
      error: String(error),
      samples: [],
      route_counts: {},
    });
  } finally {
    setTimeout(() => {
      button.disabled = false;
      button.textContent = originalText;
    }, 600);
  }
}

async function refresh() {
  const loading = document.getElementById("loadingPill");
  loading.classList.remove("hidden");

  try {
    const [nodesRes, transitionsRes, statsRes] = await Promise.all([
      fetch("/api/nodes"),
      fetch("/api/transitions?limit=20"),
      fetch("/api/stats"),
    ]);

    const [nodes, transitions, stats] = await Promise.all([
      nodesRes.json(),
      transitionsRes.json(),
      statsRes.json(),
    ]);

    renderSummary(stats);
    renderNodes(nodes);
    renderEvents(transitions);
    document.getElementById("lastRefresh").textContent = `Last refresh: ${new Date().toLocaleString()}`;
  } catch (error) {
    console.error("Failed to refresh dashboard", error);
  } finally {
    loading.classList.add("hidden");
  }
}

function showDiscordTestStatus(ok, message) {
  const el = document.getElementById("discordTestStatus");
  el.classList.remove("hidden", "bg-emerald-950", "border-emerald-800", "text-emerald-200", "bg-red-950", "border-red-800", "text-red-200");
  if (ok) {
    el.classList.add("bg-emerald-950", "border-emerald-800", "text-emerald-200");
  } else {
    el.classList.add("bg-red-950", "border-red-800", "text-red-200");
  }
  el.textContent = message;
}

async function testDiscord() {
  const button = document.getElementById("testDiscordBtn");
  button.disabled = true;
  try {
    const response = await fetch("/api/test/discord", { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || "Discord test failed";
      showDiscordTestStatus(false, `Discord test failed: ${detail}`);
      return;
    }
    showDiscordTestStatus(true, payload.message || "Discord test sent");
  } catch (error) {
    showDiscordTestStatus(false, `Discord test failed: ${String(error)}`);
  } finally {
    setTimeout(() => {
      button.disabled = false;
    }, 1000);
  }
}

document.getElementById("refreshBtn").addEventListener("click", refresh);
document.getElementById("testDiscordBtn").addEventListener("click", testDiscord);
setInterval(refresh, 30000);
refresh();
