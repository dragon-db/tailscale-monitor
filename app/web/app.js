const stateColors = {
  DIRECT: "text-emerald-300 bg-emerald-950 border-emerald-800",
  PEER_RELAY: "text-yellow-300 bg-yellow-950 border-yellow-800",
  DERP: "text-orange-300 bg-orange-950 border-orange-800",
  OFFLINE: "text-red-300 bg-red-950 border-red-800",
  UNKNOWN: "text-slate-300 bg-slate-800 border-slate-700",
};

const dotColors = {
  DIRECT: "bg-emerald-400",
  PEER_RELAY: "bg-yellow-400",
  DERP: "bg-orange-400",
  OFFLINE: "bg-red-400 offline-pulse",
  UNKNOWN: "bg-slate-400",
};

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

function stateBadge(state) {
  const palette = stateColors[state] || stateColors.UNKNOWN;
  const dot = dotColors[state] || dotColors.UNKNOWN;
  return `
    <span class="inline-flex items-center gap-2 px-2.5 py-1 rounded-full border text-xs font-semibold ${palette}">
      <span class="w-2 h-2 rounded-full ${dot}"></span>
      ${state}
    </span>
  `;
}

function renderSummary(stats) {
  document.getElementById("statTotal").textContent = stats.total_nodes ?? 0;
  document.getElementById("statOnline").textContent = stats.nodes_online ?? 0;
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
      const tags = (node.tags || []).map((tag) => `<span class="px-2 py-0.5 rounded bg-slate-800 text-xs text-slate-300 border border-slate-700">${tag}</span>`).join(" ");
      const uptime = node.uptime_7d_pct ?? 0;
      return `
        <article class="p-4 rounded-xl bg-slate-900 border border-slate-800">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-lg font-semibold">${node.label}</h3>
              <div class="text-xs text-slate-400">${node.ip}</div>
            </div>
            ${stateBadge(state)}
          </div>

          <div class="mt-3 space-y-1 text-sm text-slate-300">
            <div>Last checked: <span class="text-slate-100">${formatRelative(node.last_checked)}</span></div>
            <div>Confidence: <span class="text-slate-100">${node.confidence || "low"}</span></div>
            <div>DERP region: <span class="text-slate-100">${node.derp_region || "-"}</span></div>
            <div>Ping avg: <span class="text-slate-100">${node.ping_avg_ms ? `${node.ping_avg_ms.toFixed(2)}ms` : "-"}</span></div>
          </div>

          <div class="mt-3">
            <div class="text-xs text-slate-400 mb-1">7d uptime: ${uptime}%</div>
            <div class="w-full h-2 rounded bg-slate-800 overflow-hidden">
              <div class="h-full bg-sky-500" style="width: ${Math.max(0, Math.min(100, uptime))}%"></div>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap gap-1">${tags}</div>

          <div class="mt-4">
            <button data-ip="${node.ip}" class="check-now px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 text-sm font-semibold">Check Now</button>
          </div>
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
          <td class="px-3 py-2">${event.label || event.node_ip}</td>
          <td class="px-3 py-2"><span class="px-2 py-1 rounded border text-xs ${colorClass}">${event.previous_state} -> ${event.current_state}</span></td>
          <td class="px-3 py-2 text-slate-300">${formatDuration(event.duration_previous_seconds)}</td>
        </tr>
      `;
    })
    .join("");
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

document.getElementById("refreshBtn").addEventListener("click", refresh);
setInterval(refresh, 30000);
refresh();
