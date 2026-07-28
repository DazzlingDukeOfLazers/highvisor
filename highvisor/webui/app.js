"use strict";
// highvisor web cockpit — talks to the daemon over POST /rpc and streams the
// event log over GET /events (SSE). No framework, no build step.

const $ = (id) => document.getElementById(id);
let selected = null;   // currently selected window target id

async function rpc(op, extra = {}) {
  const r = await fetch("/rpc", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op, ...extra }),
  });
  return r.json();
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

// ---------------------------------------------------------------- windows
async function refreshWindows() {
  const res = await rpc("list_targets");
  const ul = $("windows");
  ul.innerHTML = "";
  if (!res.ok) { ul.innerHTML = `<li class="bad">${res.error || "error"}</li>`; return; }
  for (const t of res.targets) {
    const li = document.createElement("li");
    if (t.focused) li.classList.add("foc");
    if (t.id === selected) li.classList.add("sel");
    li.innerHTML = `${escapeHtml(t.title || t.class_name || t.id)}`
      + `<div class="sub">${t.id} · ${t.w}×${t.h}</div>`;
    li.onclick = () => shoot(t.id, t.title || t.id);
    ul.appendChild(li);
  }
}

async function shoot(target, label) {
  selected = target;
  document.querySelectorAll("#windows li").forEach(li =>
    li.classList.toggle("sel", li.textContent.includes(target)));
  $("shotlabel").textContent = label || target;
  const wrap = $("shotwrap");
  wrap.innerHTML = `<span class="muted">capturing ${escapeHtml(target)}…</span>`;
  const res = await rpc("screenshot", { target });
  if (!res.ok) { wrap.innerHTML = `<span class="bad">${res.error}</span>`; return; }
  wrap.innerHTML = "";
  const img = new Image();
  img.src = "data:image/png;base64," + res.png_b64;
  wrap.appendChild(img);
}

// ------------------------------------------------------------------ peers
async function refreshPeers() {
  let res;
  try { res = await (await fetch("/bridge/peers")).json(); } catch { return; }
  if (res.self) $("self").textContent = res.self.name + "@" + res.self.host;
  const ul = $("peers");
  if (!res.peers || !res.peers.length) {
    ul.innerHTML = `<li class="muted">${res.self ? "no peers yet" : "bridge offline"}</li>`;
    return;
  }
  ul.innerHTML = "";
  for (const p of res.peers) {
    const li = document.createElement("li");
    li.innerHTML = `${escapeHtml(p.name)}<div class="sub">${p.host}:${p.port}</div>`;
    ul.appendChild(li);
  }
}

// -------------------------------------------------------------- handoff
async function sendContext() {
  const ta = $("ctx");
  const text = ta.value.trim();
  if (!text) return;
  $("send").disabled = true;
  const res = await post("/bridge/send", { text });
  $("send").disabled = false;
  if (res && res.ok) { ta.value = ""; }
  else { addRow({ kind: "error", t: Date.now() / 1000, msg: (res && res.error) || "bridge offline — is it running?" }); }
}

// ---------------------------------------------------------------- layouts
let layoutCache = {};
async function refreshLayouts() {
  const res = await rpc("layout_list");
  const sel = $("layoutsel");
  sel.innerHTML = "";
  layoutCache = {};
  if (!res.ok) return;
  for (const l of res.layouts) {
    layoutCache[l.name] = l;
    const o = document.createElement("option");
    o.value = l.name;
    o.textContent = `${l.name} (${l.placements})`;
    sel.appendChild(o);
  }
  showLayoutDesc();
}
function showLayoutDesc() {
  const l = layoutCache[$("layoutsel").value];
  $("layoutdesc").textContent = l ? l.description : "";
}
async function applyLayout() {
  const name = $("layoutsel").value;
  if (!name) return;
  $("applylayout").disabled = true;
  await rpc("layout_apply", { name });   // result streams into the log via the bus
  $("applylayout").disabled = false;
  refreshWindows();
}
async function saveLayout() {
  const name = $("savename").value.trim();
  if (!name) return;
  const res = await rpc("layout_save", { name });
  if (res.ok) { $("savename").value = ""; await refreshLayouts(); $("layoutsel").value = name; showLayoutDesc(); }
}

// ------------------------------------------------------ pending (agent loop)
async function refreshPending() {
  let res;
  try { res = await (await fetch("/orch/pending")).json(); } catch { return; }
  $("lanes").textContent = (res.lanes && res.lanes.length) ? "auto: " + res.lanes.join(" · ") : "";
  const el = $("pending");
  if (!res.pending || !res.pending.length) { el.innerHTML = `<div class="muted">nothing pending</div>`; return; }
  el.innerHTML = "";
  for (const p of res.pending) {
    const d = document.createElement("div");
    d.className = "pend";
    d.innerHTML =
      `<div class="pend-hd"><b>${escapeHtml(p.verb)}</b> → ${escapeHtml(p.target)} `
      + `<span class="muted">from ${escapeHtml(p.src || "")}</span></div>`
      + `<div class="pend-body">${escapeHtml(p.body || "(no body)")}</div>`
      + `<div class="pend-btns">`
      + `<button class="ok" data-fp="${p.fp}" data-a="approve">approve</button>`
      + `<button data-fp="${p.fp}" data-a="approve-all">approve lane</button>`
      + `<button class="no" data-fp="${p.fp}" data-a="deny">deny</button></div>`;
    el.appendChild(d);
  }
  el.querySelectorAll("button[data-fp]").forEach(b =>
    b.onclick = () => actOpcode(b.dataset.fp, b.dataset.a));
}
async function actOpcode(fp, action) {
  await post("/orch/act", { fp, action });
  refreshPending();
}

// ----------------------------------------------------------------- log
const logEl = () => $("log");
function fmtTime(t) {
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function addRow(ev) {
  const el = logEl();
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  const row = document.createElement("div");
  row.className = "row";
  let msg = renderEvent(ev);
  row.innerHTML = `<span class="ts">${fmtTime(ev.t)}</span>`
    + `<span class="k k-${ev.kind}">${ev.kind}</span>`
    + `<span class="msg">${msg}</span>`;
  el.appendChild(row);
  while (el.childElementCount > 1000) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}
function renderEvent(ev) {
  if (ev.kind === "op") {
    const okc = ev.ok ? "ok" : "bad";
    let s = `<b>${escapeHtml(ev.op)}</b> <span class="${okc}">${ev.ok ? "ok" : "fail"}</span>`;
    if (ev.tier != null) s += ` <span class="muted">tier${ev.tier}</span>`;
    if (ev.target) s += ` ${escapeHtml(ev.target)}`;
    if (ev.detail) s += ` <span class="muted">${escapeHtml(ev.detail)}</span>`;
    if (ev.error) s += ` <span class="bad">${escapeHtml(ev.error)}</span>`;
    return s;
  }
  if (ev.kind === "context") {
    const from = ev.from ? `<span class="k-peer">${escapeHtml(ev.from)}</span> ` : "";
    const body = escapeHtml(ev.text || "");
    return `${from}${body} <span class="copy" onclick="copyText(this)" data-t="${encodeURIComponent(ev.text || "")}">copy</span>`;
  }
  if (ev.kind === "opcode") {
    const st = ev.status || "";
    const cls = st === "denied" ? "bad" : "k-peer";
    return `<span class="${cls}">${escapeHtml(st)}</span> <b>${escapeHtml(ev.verb || "")}</b> → `
      + `${escapeHtml(ev.target || "")} <span class="muted">${escapeHtml((ev.body || "").slice(0, 60))}</span>`;
  }
  if (ev.kind === "peer") return `${escapeHtml(ev.event || "")} <b>${escapeHtml(ev.name || "")}</b> <span class="muted">${escapeHtml(ev.host || "")}</span>`;
  if (ev.msg) return escapeHtml(ev.msg);
  return escapeHtml(JSON.stringify(ev));
}
function copyText(el) {
  navigator.clipboard.writeText(decodeURIComponent(el.dataset.t || ""));
  el.textContent = "copied";
  setTimeout(() => (el.textContent = "copy"), 1200);
}
window.copyText = copyText;

// -------------------------------------------------------------- wire-up
function connectEvents() {
  const es = new EventSource("/events");
  es.onopen = () => $("conn").classList.add("on");
  es.onerror = () => $("conn").classList.remove("on");
  es.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data);
      addRow(ev);
      if (ev.kind === "opcode" || ev.kind === "orch") refreshPending();
    } catch {}
  };
}

async function init() {
  const p = await rpc("ping");
  if (p.ok) $("backend").textContent = "backend: " + p.backend;
  $("refresh").onclick = refreshWindows;
  $("send").onclick = sendContext;
  $("applylayout").onclick = applyLayout;
  $("savelayout").onclick = saveLayout;
  $("layoutsel").onchange = showLayoutDesc;
  $("clearlog").onclick = () => (logEl().innerHTML = "");
  $("off").onclick = async () => {
    if (!confirm("Shut down the highvisor daemon? You'll restart it from a terminal.")) return;
    try { await fetch("/shutdown", { method: "POST" }); } catch {}
    document.body.classList.add("down");   // dim + show the stopped overlay
    $("conn").classList.remove("on");
  };
  $("ctx").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendContext();
  });
  await refreshWindows();
  await refreshLayouts();
  await refreshPeers();
  await refreshPending();
  connectEvents();
  setInterval(refreshPeers, 4000);
  setInterval(refreshPending, 3000);
}
init();
