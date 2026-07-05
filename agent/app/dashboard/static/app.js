/* SREagent dashboard.
 *
 * One render path for history and live: cold loads replay stored events
 * through the same per-event-type renderers the SSE stream feeds.
 * All payload text lands via textContent; markdown goes through md.js only.
 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  incidents: new Map(),   // id -> {id, title, status, service, severity, created_at}
  selectedId: null,
  lastImpact: null,       // impact may arrive before the diagnosis card exists
  pendingTools: new Map(), // `${turn}:${tool}` -> timeline row (spinner -> check)
  renderedEventIds: new Set(), // dedup: cold-load replay overlaps the live SSE stream
  coldLoadBuffer: null,   // live events held back while a history fetch is in flight
};

/* ---------- boot ---------- */

async function init() {
  try {
    const health = await (await fetch("/healthz")).json();
    $("mode-badge").textContent = health.mode + (health.llm_reachable ? "" : " · LLM offline");
  } catch { /* non-fatal */ }

  // Cursor first: the stream later replays everything after this seq, so
  // events emitted while we fetch the initial state are never lost.
  let cursor = 0;
  try {
    cursor = (await (await fetch("/api/cursor")).json()).seq || 0;
  } catch { /* stream falls back to live-only */ }

  const incidents = await (await fetch("/api/incidents")).json();
  for (const inc of incidents) state.incidents.set(inc.id, inc);
  renderSidebar();
  if (incidents.length) await selectIncident(incidents[0].id);

  const source = new EventSource(`/api/stream?after=${cursor}`);
  source.onmessage = (msg) => routeEvent(JSON.parse(msg.data));
}

/* ---------- sidebar ---------- */

function renderSidebar() {
  const list = $("incident-list");
  list.textContent = "";
  const incidents = [...state.incidents.values()]
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  $("sidebar-empty").hidden = incidents.length > 0;

  for (const inc of incidents) {
    const row = document.createElement("div");
    row.className = "inc-row" + (inc.id === state.selectedId ? " selected" : "");
    row.onclick = () => selectIncident(inc.id);

    const title = document.createElement("div");
    title.className = "inc-row-title";
    title.textContent = inc.title || inc.id;

    const meta = document.createElement("div");
    meta.className = "inc-row-meta";
    const dot = document.createElement("span");
    dot.className = "dot " + (inc.status || "");
    const status = document.createElement("span");
    status.textContent = (inc.status || "").replace("_", " ");
    const when = document.createElement("span");
    when.textContent = relTime(inc.created_at);
    meta.append(dot, status, when);

    row.append(title, meta);
    list.append(row);
  }
}

function relTime(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

/* ---------- incident selection / cold load ---------- */

async function selectIncident(id) {
  state.selectedId = id;
  state.lastImpact = null;
  state.pendingTools.clear();
  state.renderedEventIds.clear();
  // Live events arriving while the history fetch is in flight are buffered
  // and applied after the replay — otherwise they'd render out of order
  // (and stale state_changed replays would regress the status pill).
  state.coldLoadBuffer = [];

  $("empty-state").hidden = true;
  const view = $("incident-view");
  view.hidden = false;

  $("timeline").textContent = "";
  for (const cardId of ["diagnosis-card", "slack-card", "postmortem-card"]) {
    $(cardId).hidden = true;
  }
  document.querySelectorAll("#phase-strip li").forEach((li) =>
    li.classList.remove("done", "active"));

  let response = null;
  try {
    response = await fetch(`/api/incidents/${id}`);
  } catch { /* handled below */ }
  if (state.selectedId !== id) return; // a newer selection won the race

  if (!response || !response.ok) {
    // Stale sidebar entry (e.g. agent restarted with a fresh DB) — drop it
    // instead of poisoning the incident map and bricking the renderers.
    state.incidents.delete(id);
    state.selectedId = null;
    state.coldLoadBuffer = null;
    view.hidden = true;
    $("empty-state").hidden = false;
    renderSidebar();
    return;
  }

  const { incident, events } = await response.json();
  if (state.selectedId !== id) return;
  state.incidents.set(id, incident);
  renderHeader(incident);
  renderSidebar();
  for (const envelope of events) renderEvent(envelope);

  const buffered = state.coldLoadBuffer || [];
  state.coldLoadBuffer = null;
  for (const envelope of buffered) renderEvent(envelope);
}

function renderHeader(incident) {
  $("inc-title").textContent = incident.title || incident.id;
  $("inc-id").textContent = incident.id;
  $("inc-service").textContent = incident.service || "";
  $("inc-started").textContent = incident.created_at
    ? "started " + new Date(incident.created_at).toLocaleTimeString() : "";
  const severity = $("inc-severity");
  severity.textContent = incident.severity || "unknown";
  severity.className = "badge " + (incident.severity || "");
  setStatus(incident.status);
}

function setStatus(status) {
  const pill = $("inc-status");
  pill.textContent = (status || "").replace("_", " ");
  pill.className = "pill " + status;

  const reached = {
    detected: ["alert"],
    investigating: ["alert", "investigate"],
    diagnosed: ["alert", "investigate", "diagnose"],
    resolved: ["alert", "investigate", "diagnose", "notify"],
    postmortem_published: ["alert", "investigate", "diagnose", "notify", "postmortem"],
  }[status] || [];
  document.querySelectorAll("#phase-strip li").forEach((li) => {
    const done = reached.includes(li.dataset.phase);
    li.classList.toggle("done", done);
    li.classList.toggle("active", done && li.dataset.phase === reached[reached.length - 1]);
  });
}

/* ---------- live event routing ---------- */

function routeEvent(envelope) {
  const { incident_id: id, type, payload } = envelope;

  if (!state.incidents.has(id)) {
    state.incidents.set(id, {
      id,
      title: payload.alertname ? `${payload.alertname} on ${payload.endpoint || payload.service}` : id,
      status: "detected",
      service: payload.service || "",
      severity: payload.severity || "",
      created_at: envelope.ts,
    });
  }
  if (type === "state_changed") {
    state.incidents.get(id).status = payload.to;
  }
  renderSidebar();

  if (type === "alert_received" && state.selectedId !== id) {
    selectIncident(id);          // a new incident steals focus — zero-click demo
  }
  if (id === state.selectedId) {
    // A history fetch in flight? Hold live events back so replay stays ordered.
    if (state.coldLoadBuffer) state.coldLoadBuffer.push(envelope);
    else renderEvent(envelope);
  }
}

/* ---------- per-event renderers ---------- */

const renderers = {
  alert_received(env) {
    timelineRow(env.ts, "🚨", (body) => {
      const title = el("div", "tl-title");
      title.append(
        text(`Alert `), bold(env.payload.alertname),
        text(` firing on `), bold(env.payload.endpoint || env.payload.service || "?"),
      );
      const detail = el("div", "tl-args");
      detail.textContent = env.payload.summary || "";
      body.append(title, detail);
    });
  },

  state_changed(env) {
    // The pill/phase strip follow the authoritative current status, not this
    // (possibly replayed, possibly stale) transition event.
    const current = state.incidents.get(state.selectedId);
    setStatus((current && current.status) || env.payload.to);
    timelineRow(env.ts, "•", (body) => {
      const title = el("div", "tl-args");
      title.textContent = `state: ${env.payload.from} → ${env.payload.to}`;
      body.append(title);
    });
  },

  llm_turn(env) {
    if (!env.payload.assistant_text) return;
    timelineRow(env.ts, "💭", (body) => {
      const thought = el("div", "tl-thought");
      thought.textContent = env.payload.assistant_text;
      body.append(thought);
    });
  },

  tool_call(env) {
    const row = timelineRow(env.ts, "", (body, icon) => {
      icon.append(el("span", "spinner"));
      const title = el("div", "tl-title");
      const tool = el("span", "tool-name");
      tool.textContent = env.payload.tool;
      title.append(text("Running "), tool);
      const args = el("div", "tl-args");
      args.textContent = JSON.stringify(env.payload.args);
      body.append(title, args);
    });
    state.pendingTools.set(`${env.payload.turn}:${env.payload.tool}`, row);
  },

  tool_result(env) {
    const key = `${env.payload.turn}:${env.payload.tool}`;
    const row = state.pendingTools.get(key);
    const failed = (env.payload.result_preview || "").startsWith("(tool error");
    if (row) {
      state.pendingTools.delete(key);
      const icon = row.querySelector(".tl-icon");
      icon.textContent = failed ? "✗" : "✓";
      icon.className = "tl-icon " + (failed ? "tl-fail" : "tl-ok");
      const details = el("details");
      const summary = el("summary");
      summary.textContent = "evidence" + (env.payload.truncated ? " (truncated)" : "");
      const pre = el("pre");
      pre.textContent = env.payload.result_preview;
      details.append(summary, pre);
      row.querySelector(".tl-body").append(details);
    }
  },

  diagnosis_ready(env) {
    renderDiagnosis(env.payload.diagnosis);
  },

  impact_computed(env) {
    state.lastImpact = env.payload.impact;
    if (!$("diagnosis-card").hidden) renderImpact(env.payload.impact);
  },

  slack_brief_sent(env) {
    const card = $("slack-card");
    card.hidden = false;
    $("slack-text").textContent = env.payload.text_fallback || "";
    const tag = $("slack-delivery");
    const delivered = env.payload.delivered_to === "slack";
    tag.textContent = delivered ? "delivered to Slack ✓" : "console fallback";
    tag.className = "tag" + (delivered ? " ok" : "");
  },

  alert_resolved(env) {
    timelineRow(env.ts, "✅", (body) => {
      const title = el("div", "tl-title");
      title.textContent = "Alert resolved — metrics back to baseline";
      body.append(title);
    });
  },

  async postmortem_published(env) {
    const card = $("postmortem-card");
    card.hidden = false;
    $("postmortem-raw").href = env.payload.url;
    const forId = env.incident_id;
    try {
      const markdown = await (await fetch(env.payload.url)).text();
      if (state.selectedId !== forId) return; // selection changed mid-fetch
      $("postmortem-body").innerHTML = renderMarkdown(markdown);
    } catch {
      if (state.selectedId !== forId) return;
      $("postmortem-body").textContent = "(failed to load postmortem)";
    }
  },

  agent_error(env) {
    timelineRow(env.ts, "⚠️", (body) => {
      const row = el("div", "tl-error");
      const title = el("div", "tl-title");
      title.textContent = `agent error in ${env.payload.stage}` +
        (env.payload.recovered ? " (recovered — continuing with fallback)" : "");
      const detail = el("div", "tl-args");
      detail.textContent = env.payload.message || "";
      row.append(title, detail);
      body.append(row);
    });
  },
};

function renderEvent(envelope) {
  if (envelope.event_id) {
    if (state.renderedEventIds.has(envelope.event_id)) return;
    state.renderedEventIds.add(envelope.event_id);
  }
  const renderer = renderers[envelope.type];
  if (renderer) renderer(envelope);
}

/* ---------- diagnosis card ---------- */

function renderDiagnosis(diagnosis) {
  const card = $("diagnosis-card");
  card.hidden = false;

  $("diag-summary").textContent = diagnosis.summary || "";
  $("diag-root-cause").textContent = diagnosis.root_cause || "";

  const meter = card.querySelector(".conf-meter");
  meter.className = "conf-meter " + (diagnosis.confidence || "low");
  $("confidence-label").textContent = diagnosis.confidence || "";

  const commit = $("diag-commit");
  commit.textContent = "";
  if (diagnosis.suspect_commit && diagnosis.suspect_commit.sha) {
    const sha = el("div", "commit-sha");
    sha.textContent = diagnosis.suspect_commit.sha;
    const msg = el("div", "commit-msg");
    msg.textContent = diagnosis.suspect_commit.message || "";
    const author = el("div", "commit-author");
    author.textContent = diagnosis.suspect_commit.author || "";
    commit.append(sha, msg, author);
  } else {
    commit.textContent = "unknown";
  }

  const runbook = $("diag-runbook");
  runbook.textContent = "";
  const slug = el("div", "runbook-slug");
  slug.textContent = diagnosis.runbook_slug || "none";
  const remediation = el("div", "remediation");
  remediation.textContent = diagnosis.remediation || "";
  runbook.append(slug, remediation);

  const evidenceList = $("diag-evidence-list");
  evidenceList.textContent = "";
  for (const item of diagnosis.evidence || []) {
    const li = el("li");
    li.textContent = item;
    evidenceList.append(li);
  }

  if (state.lastImpact) renderImpact(state.lastImpact);
}

function renderImpact(impact) {
  const target = $("diag-impact");
  target.textContent = "";
  const entries = [
    [`${(impact.error_rate_pct ?? 0).toFixed(1)}%`, `error rate (baseline ${(impact.baseline_error_rate_pct ?? 0).toFixed(1)}%)`, (impact.error_rate_pct ?? 0) >= 5],
    [`~${impact.est_failed_requests ?? 0}`, `requests failed over ${(impact.duration_min ?? 0).toFixed(1)} min`, false],
    [impact.p95_ms != null ? `${Math.round(impact.p95_ms)}ms` : "n/a", "p95 latency", (impact.p95_ms ?? 0) > 500],
  ];
  for (const [value, label, bad] of entries) {
    const num = el("div", "impact-num" + (bad ? " bad" : ""));
    num.textContent = value;
    const lbl = el("div", "impact-label");
    lbl.textContent = label;
    target.append(num, lbl);
  }
}

/* ---------- DOM helpers ---------- */

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}
function text(value) { return document.createTextNode(value); }
function bold(value) {
  const node = document.createElement("strong");
  node.textContent = value ?? "";
  return node;
}

function timelineRow(ts, iconText, build) {
  const row = el("div", "tl-row");
  const time = el("div", "tl-time");
  time.textContent = ts ? new Date(ts).toLocaleTimeString() : "";
  const icon = el("div", "tl-icon");
  if (iconText) icon.textContent = iconText;
  const body = el("div", "tl-body");
  build(body, icon);
  row.append(time, icon, body);
  $("timeline").append(row);
  row.scrollIntoView({ block: "nearest" });
  return row;
}

init();
