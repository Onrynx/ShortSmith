"use strict";
const STAGES = ["script", "voice", "canvas", "cutter", "herald"];
let TOKEN = localStorage.getItem("sf_token") || "";
let CONFIG = null;
let activeTab = "home";
let pollTimer = null;

// ---------------------------------------------------------------- api ----
async function api(path, opts = {}) {
  const headers = Object.assign({ "Authorization": "Bearer " + TOKEN }, opts.headers || {});
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method: opts.method || "GET", headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401) { openModal(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
const mediaUrl = (id, kind) => `/api/media/${encodeURIComponent(id)}/${kind}?token=${encodeURIComponent(TOKEN)}`;

function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}
const esc = s => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = ts => { try { return new Date(ts).toLocaleString(); } catch { return ts || ""; } };

// -------------------------------------------------------------- modal ----
const modal = document.getElementById("modal");
function openModal() { document.getElementById("tokenInput").value = TOKEN; modal.classList.remove("hidden"); }
document.getElementById("gear").onclick = openModal;
document.getElementById("closeModal").onclick = () => modal.classList.add("hidden");
document.getElementById("saveToken").onclick = () => {
  TOKEN = document.getElementById("tokenInput").value.trim();
  localStorage.setItem("sf_token", TOKEN); modal.classList.add("hidden"); toast("Token saved"); boot();
};
document.getElementById("enablePush").onclick = enablePush;

// --------------------------------------------------------------- tabs ----
document.querySelectorAll(".tab").forEach(btn => btn.onclick = () => setTab(btn.dataset.tab));
function setTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  render();
}

// ------------------------------------------------------------- render ----
const view = document.getElementById("view");
async function render() {
  if (!TOKEN) { view.innerHTML = `<div class="empty">Tap ⚙️ and paste your <b>APP_TOKEN</b> to begin.</div>`; return; }
  try {
    if (activeTab === "home") await renderHome();
    else if (activeTab === "today") await renderToday();
    else if (activeTab === "review") await renderReview();
    else if (activeTab === "feed") await renderFeed();
    else if (activeTab === "history") await renderHistory();
  } catch (e) { view.innerHTML = `<div class="empty">⚠️ ${esc(e.message)}<br><span class="muted">Check the token in ⚙️.</span></div>`; }
}

function stageTrack(st) {
  const stage = (st && st.stage) || "";
  const failed = stage === "failed";
  let cur = STAGES.indexOf(stage);
  const done100 = st && (st.pct === 100 || stage === "herald");
  const dots = STAGES.map((s, i) => {
    let cls = "stage-dot";
    if (done100) cls += " done";
    else if (i < cur) cls += " done";
    else if (i === cur && !failed) cls += " active";
    return `<div class="${cls}" title="${s}"></div>`;
  }).join("");
  const label = failed ? `<span class="sev-red">failed: ${esc(st.message || "")}</span>`
    : (st && st.message ? esc(st.message) : "idle");
  return `<div class="stage-track">${dots}</div><div class="stage-label">${label}</div>`;
}

async function renderHome() {
  CONFIG = CONFIG || await api("/api/config");
  const [ody, status] = await Promise.all([api("/api/odysseus"), api("/api/status")]);
  const brain = ody.storyforge_brain || {};
  const dot = (up) => `<span style="color:${up ? 'var(--green)' : 'var(--red)'}">${up ? '●' : '○'}</span>`;
  const apMode = CONFIG.auto_publish || "off";
  const apColor = apMode === "public" ? "var(--red)" : (apMode === "private" ? "var(--yellow)" : "var(--muted)");
  const brainVia = brain.backend === "odysseus" ? "via Odysseus /v1"
    : (brain.backend === "ollama" ? "Ollama (shared with Odysseus)" : brain.backend);

  view.innerHTML = `
    <div class="card">
      <h3>🧠 Odysseus <span class="muted" style="font-weight:400">— your self-hosted brain</span></h3>
      <div class="row spread"><span>${dot(ody.ui_up)} Workspace ${ody.ui_up ? 'online' : 'offline'}</span>
        <span class="muted">${esc(ody.url)}</span></div>
      <div class="row spread" style="margin-top:6px"><span>${dot(ody.llm_v1_up)} OpenAI /v1 gateway</span>
        <span class="muted">${ody.llm_v1_up ? (ody.models.length + ' models') : 'not exposed (using Ollama)'}</span></div>
      <button class="btn full" id="openOdy">Open Odysseus →</button>
    </div>

    <div class="card">
      <h3>🎬 StoryForge <span class="muted" style="font-weight:400">— the video factory</span></h3>
      <div class="row spread"><span>Crew brain</span><span class="pill">${esc(brain.model || '—')}</span></div>
      <div class="muted" style="margin-top:6px">${esc(brainVia)}</div>
      <div class="row spread" style="margin-top:8px"><span>Awaiting review</span><span class="pill">${status.pending_count}</span></div>
      <div class="row spread" style="margin-top:6px"><span>Cloud cap (day)</span><span class="pill">${status.cloud_cap_remaining}</span></div>
    </div>

    <div class="card">
      <h3>⚡ Automation</h3>
      <div class="row spread"><span>Publish mode</span>
        <span class="pill" style="color:${apColor};border-color:${apColor}">${esc(apMode)}</span></div>
      <div class="muted" style="margin-top:6px">
        ${apMode === 'off' ? 'Videos wait in Review for your approval (recommended).'
          : apMode === 'private' ? 'Auto-uploads PRIVATE — you flip to public in YouTube Studio.'
          : '⚠️ Fully autonomous: auto-uploads PUBLIC. Change AUTO_PUBLISH in .env to revert.'}
      </div>
      <button class="btn primary full" id="runBoth">▶ Run both lanes now (mock)</button>
      <button class="btn full" id="goToday">🌗 Assign today's lanes</button>
    </div>

    <div class="card">
      <h3>🔗 How they work together</h3>
      <ol class="muted" style="margin:0;padding-left:18px;line-height:1.7">
        <li><b>Odysseus</b> is the brain + scheduler you own locally.</li>
        <li>An Odysseus scheduled task fires <b>StoryForge</b> daily (webhook / run_script).</li>
        <li>StoryForge writes the script on the <b>same local model</b> Odysseus uses.</li>
        <li>7 agents build a vertical Short with AI motion, voice & captions.</li>
        <li>It lands in <b>Review</b> → you Approve → (optional) auto-upload with #hashtags.</li>
      </ol>
    </div>`;

  document.getElementById("openOdy").onclick = () => window.open(ody.url, "_blank");
  document.getElementById("goToday").onclick = () => setTab("today");
  document.getElementById("runBoth").onclick = async () => {
    const r = await api("/api/trigger", { method: "POST", body: { daily: true, mock: true } });
    toast(r.ok ? "Running both lanes (mock)…" : "Failed: " + (r.error || "?"));
  };
}

async function renderToday() {
  CONFIG = CONFIG || await api("/api/config");
  const [status, assign] = await Promise.all([api("/api/status"), api("/api/assignment")]);
  const chans = CONFIG.channels;
  const opts = (sel) => chans.map(c => `<option value="${c.key}" ${c.key === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  const nightSel = assign.night || (chans[0] && chans[0].key);
  const daySel = assign.day || (chans[1] && chans[1].key) || (chans[0] && chans[0].key);

  const laneCard = (lane) => {
    const st = (status.lanes && status.lanes[lane]) || {};
    const ch = assign[lane] ? (chans.find(c => c.key === assign[lane]) || {}).name : "—";
    return `<div class="card">
      <div class="row spread"><h3>${lane === "night" ? "🌙 Night · local" : "☀️ Day · cloud"}</h3>
      <span class="pill ${lane}">${esc(ch || "unassigned")}</span></div>
      ${stageTrack(st)}
      ${lane === "day" ? `<div class="muted" style="margin-top:8px">cloud cap remaining: ${status.cloud_cap_remaining}</div>` : ""}
    </div>`;
  };

  view.innerHTML = `
    <div class="card">
      <div class="row spread"><h3>🌗 Assign today's lanes</h3>
      <span class="muted">${assign.date === new Date().toISOString().slice(0,10) ? "set for today" : "not set today"}</span></div>
      <label>Night lane (local Wan2GP)</label><select id="nightSel">${opts(nightSel)}</select>
      <label>Day lane (cloud)</label><select id="daySel">${opts(daySel)}</select>
      <button class="btn primary full" id="assignBtn">Assign lanes</button>
    </div>
    ${laneCard("night")}
    ${laneCard("day")}
    <div class="card">
      <h3>▶️ Run now</h3>
      <label>Channel</label><select id="runChannel">${opts(nightSel)}</select>
      <label>Lane</label><select id="runLane"><option value="night">night</option><option value="day">day</option></select>
      <label class="row" style="margin-top:10px"><input type="checkbox" id="runMock" style="width:auto" checked> &nbsp;<span class="muted">mock (no keys/network/GPU)</span></label>
      <button class="btn full" id="runBtn">Run pipeline</button>
    </div>`;

  document.getElementById("assignBtn").onclick = async () => {
    await api("/api/assignment", { method: "POST", body: {
      night: document.getElementById("nightSel").value, day: document.getElementById("daySel").value } });
    toast("Lanes assigned"); render();
  };
  document.getElementById("runBtn").onclick = async () => {
    const r = await api("/api/run", { method: "POST", body: {
      channel: document.getElementById("runChannel").value,
      lane: document.getElementById("runLane").value,
      mock: document.getElementById("runMock").checked } });
    toast(r.ok ? "Run started (pid " + r.pid + ")" : "Failed: " + (r.error || "?"));
  };
}

async function renderReview() {
  const items = await api("/api/pending");
  if (!items.length) { view.innerHTML = `<div class="empty">No videos awaiting review.<br><span class="muted">Run one from the Today tab.</span></div>`; return; }
  view.innerHTML = items.map(it => `
    <div class="list-item" data-id="${esc(it.id)}">
      <img class="thumb mini" loading="lazy" src="${it.thumbnail ? mediaUrl(it.id, "thumb") : ""}" onerror="this.style.visibility='hidden'"/>
      <div class="meta">
        <div class="title">${esc(it.title)}</div>
        <div class="row" style="margin-top:6px">
          <span class="pill ${it.lane}">${it.lane}</span>
          <span class="pill">${esc(it.channel_name || it.channel)}</span>
          ${it.mock ? '<span class="pill mock">mock</span>' : ""}
        </div>
        <div class="muted" style="margin-top:6px">${Math.round(it.runtime_sec || 0)}s · ${fmtTime(it.created)}</div>
      </div>
    </div>`).join("");
  view.querySelectorAll(".list-item").forEach(el => el.onclick = () => renderDetail(el.dataset.id));
}

async function renderDetail(id) {
  const m = await api("/api/pending/" + encodeURIComponent(id));
  const scenes = (m.scenes || []).map((s, i) =>
    `<div class="scene"><span class="n">${i + 1}</span> ${s.hero ? '<span class="hero-tag">HERO</span> ' : ""}${esc(s.narration)}</div>`).join("");
  view.innerHTML = `
    <button class="btn ghost" id="back">← Back to review</button>
    <div class="card">
      <video controls playsinline poster="${m.thumbnail ? mediaUrl(id, "thumb") : ""}" src="${mediaUrl(id, "video")}"></video>
      <h3>${esc(m.title)}</h3>
      <div class="row"><span class="pill ${m.lane}">${m.lane}</span><span class="pill">${esc(m.channel_name || m.channel)}</span>${m.mock ? '<span class="pill mock">mock</span>' : ""}<span class="pill">${esc(m.captions_kind || "")} captions</span></div>
      <p class="muted" style="margin-top:10px">${esc(m.description || "")}</p>
      <div class="row spread" style="margin-top:8px">
        <button class="btn approve" id="approve">✅ Approve</button>
        <button class="btn reject" id="reject">🗑️ Reject</button>
      </div>
    </div>
    <div class="card"><h3>Script (${(m.scenes || []).length} scenes)</h3>${scenes}</div>
    <div class="card"><h3>Approval report</h3><pre class="report">${esc(m.report_text || "")}</pre></div>`;
  document.getElementById("back").onclick = () => setTab("review");
  document.getElementById("approve").onclick = async () => {
    await api(`/api/pending/${encodeURIComponent(id)}/approve`, { method: "POST" }); toast("Approved → approved/"); setTab("review");
  };
  document.getElementById("reject").onclick = async () => {
    if (!confirm("Delete this video permanently?")) return;
    await api(`/api/pending/${encodeURIComponent(id)}/reject`, { method: "POST" }); toast("Rejected & deleted"); setTab("review");
  };
}

async function renderFeed() {
  const events = await api("/api/events?limit=120");
  if (!events.length) { view.innerHTML = `<div class="empty">No events yet.</div>`; return; }
  const icon = { red: "🔴", yellow: "🟡", green: "🟢", info: "•" };
  view.innerHTML = `<div class="card">` + events.map(e => `
    <div class="feed-item">
      <div class="dot sev-${e.severity}">${icon[e.severity] || "•"}</div>
      <div class="body">
        <div class="msg">${esc(e.message)}</div>
        <div class="sub">${esc(e.lane)}/${esc(e.channel)} · ${esc(e.stage)} · ${fmtTime(e.ts)}</div>
      </div>
    </div>`).join("") + `</div>`;
}

async function renderHistory() {
  const items = await api("/api/history");
  if (!items.length) { view.innerHTML = `<div class="empty">No approved videos yet.</div>`; return; }
  view.innerHTML = items.map(it => `
    <div class="list-item">
      <img class="thumb mini" loading="lazy" src="${it.thumbnail ? mediaUrl(it.id, "thumb") : ""}" onerror="this.style.visibility='hidden'"/>
      <div class="meta">
        <div class="title">${esc(it.title)}</div>
        <div class="row" style="margin-top:6px"><span class="pill ${it.lane}">${it.lane}</span><span class="pill">${esc(it.channel_name || it.channel)}</span>${it.published_youtube_id ? '<span class="pill" style="color:#7ee787">uploaded</span>' : ""}</div>
        <div class="muted" style="margin-top:6px">approved ${fmtTime(it.approved_at || it.created)}</div>
      </div>
    </div>`).join("");
}

// --------------------------------------------------------------- push ----
function urlB64ToUint8(base64) {
  const pad = "=".repeat((4 - base64.length % 4) % 4);
  const b64 = (base64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64); return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
async function enablePush() {
  const hint = document.getElementById("pushHint");
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) { hint.textContent = "Push not supported on this browser."; return; }
    CONFIG = CONFIG || await api("/api/config");
    if (!CONFIG.vapid_public_key) { hint.textContent = "Server has no VAPID key yet — run scripts/gen_vapid.py."; return; }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { hint.textContent = "Permission denied."; return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(CONFIG.vapid_public_key) });
    await api("/api/push/subscribe", { method: "POST", body: { subscription: sub } });
    hint.textContent = "✅ Push enabled on this device.";
    toast("Push enabled");
  } catch (e) { hint.textContent = "Push setup failed: " + e.message; }
}

// --------------------------------------------------------------- boot ----
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => { if (["home", "today", "feed"].includes(activeTab)) render(); }, 5000);
}
async function boot() {
  if ("serviceWorker" in navigator) { try { await navigator.serviceWorker.register("/service-worker.js"); } catch (e) {} }
  if (!TOKEN) openModal();
  await render();
  startPolling();
}
boot();
