import fs from "node:fs";
import http from "node:http";

interface SquadEvent {
  ts: string;
  kind: string;
  agent: string;
  data: Record<string, unknown>;
}

const argv = process.argv.slice(2);

function argOf(flag: string, fallback: string): string {
  const index = argv.indexOf(flag);
  return index >= 0 && argv[index + 1] !== undefined ? argv[index + 1] : fallback;
}

const logPath = argOf("--log", "");
const port = Number(argOf("--port", "8787"));
const LIMIT = 400;

let events: SquadEvent[] = [];
let offset = 0;
let pending = "";

function loadNew(): void {
  if (!logPath || !fs.existsSync(logPath)) {
    return;
  }
  const stats = fs.statSync(logPath);
  if (stats.size < offset) {
    events = [];
    pending = "";
    offset = 0;
  }
  if (stats.size === offset) {
    return;
  }
  const length = stats.size - offset;
  const handle = fs.openSync(logPath, "r");
  const chunk = Buffer.alloc(length);
  fs.readSync(handle, chunk, 0, length, offset);
  fs.closeSync(handle);
  const atStart = offset === 0;
  offset = stats.size;
  let text = chunk.toString("utf8");
  if (atStart && text.charCodeAt(0) === 0xfeff) {
    text = text.slice(1);
  }
  pending += text;
  const lines = pending.split(/\r?\n/);
  pending = lines.pop() ?? "";
  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    try {
      events.push(JSON.parse(line) as SquadEvent);
    } catch {
      // ignore malformed or partial lines
    }
  }
  if (events.length > LIMIT) {
    events = events.slice(events.length - LIMIT);
  }
}

loadNew();
setInterval(loadNew, 400);

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Squadron — status</title>
<style>
  :root {
    --bg: #0b0f14;
    --panel: #121821;
    --line: #1e2836;
    --text: #d8e2ef;
    --muted: #7d8da3;
    --accent: #4cc2ff;
    --ok: #47d18a;
    --warn: #f2b544;
    --err: #f26d6d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 "Segoe UI", system-ui, sans-serif;
  }
  header {
    display: flex; align-items: baseline; gap: 16px;
    padding: 18px 24px; border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #101722, #0b0f14);
  }
  header h1 { margin: 0; font-size: 18px; letter-spacing: 3px; text-transform: uppercase; }
  header h1 span { color: var(--accent); }
  header .meta { color: var(--muted); font-size: 12px; }
  main { display: grid; grid-template-columns: 260px 1fr; gap: 16px; padding: 16px 24px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; overflow: hidden;
  }
  .panel h2 {
    margin: 0; padding: 10px 14px; font-size: 12px;
    text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted);
    border-bottom: 1px solid var(--line);
  }
  #agents { padding: 8px; min-height: 240px; }
  .agent {
    display: flex; justify-content: space-between; gap: 8px;
    padding: 8px 10px; border-radius: 8px; margin-bottom: 6px;
    background: #0e141d; border: 1px solid var(--line);
  }
  .agent .name { font-weight: 600; }
  .agent .role { color: var(--muted); font-size: 12px; }
  .agent .state { font-size: 11px; align-self: center; color: var(--accent); }
  #feed { max-height: calc(100vh - 140px); overflow-y: auto; }
  .event {
    display: grid; grid-template-columns: 88px 150px 130px 1fr; gap: 10px;
    padding: 8px 14px; border-bottom: 1px solid var(--line); font-size: 13px;
  }
  .event:last-child { border-bottom: 0; }
  .event .time { color: var(--muted); font-variant-numeric: tabular-nums; }
  .event .kind { color: var(--accent); }
  .event .agent { color: var(--warn); }
  .event .detail { color: var(--text); word-break: break-word; }
  .event.err .kind { color: var(--err); }
  .event.good .kind { color: var(--ok); }
  .empty { padding: 24px; color: var(--muted); text-align: center; }
  .stats { display: flex; gap: 24px; }
  .stat b { display: block; font-size: 20px; color: var(--text); }
  .stat i { font-style: normal; font-size: 11px; color: var(--muted); text-transform: uppercase; }
</style>
</head>
<body>
<header>
  <h1>Squa<span>dron</span></h1>
  <div class="stats">
    <div class="stat"><b id="stat-events">0</b><i>events</i></div>
    <div class="stat"><b id="stat-agents">0</b><i>agents</i></div>
  </div>
  <div class="meta" id="logpath">waiting for events…</div>
</header>
<main>
  <section class="panel">
    <h2>Agents</h2>
    <div id="agents"><div class="empty">no agents yet</div></div>
  </section>
  <section class="panel">
    <h2>Event feed</h2>
    <div id="feed"><div class="empty">no events yet</div></div>
  </section>
</main>
<script>
  const agentsEl = document.getElementById("agents");
  const feedEl = document.getElementById("feed");
  const statEvents = document.getElementById("stat-events");
  const statAgents = document.getElementById("stat-agents");
  const logpathEl = document.getElementById("logpath");
  const states = {};
  let rendered = 0;

  function detailText(event) {
    const data = event.data || {};
    if (event.kind === "tool_start") return data.tool + " " + JSON.stringify(data.arguments || {});
    if (event.kind === "tool_end") return data.tool + " → " + (data.ok ? "ok" : "error");
    if (event.kind === "assistant") {
      const calls = (data.tool_calls || []).join(", ");
      return (data.text || "").slice(0, 160) + (calls ? " [" + calls + "]" : "");
    }
    if (event.kind === "subagent_start") return data.subagent + ": " + (data.task || "").slice(0, 120);
    if (event.kind === "subagent_done") return data.subagent + " finished (" + data.chars + " chars)";
    if (event.kind === "agent_start") return data.task || "";
    if (event.kind === "agent_done") return "steps=" + data.steps + " final_chars=" + data.final_chars;
    if (event.kind === "error") return data.message || "";
    return JSON.stringify(data).slice(0, 200);
  }

  function render(events) {
    statEvents.textContent = String(events.length);
    const names = [...new Set(events.map((e) => e.agent))];
    statAgents.textContent = String(names.length);
    logpathEl.textContent = events.length
      ? "last event " + new Date(events[events.length - 1].ts).toLocaleTimeString()
      : "waiting for events…";

    if (events.length !== rendered) {
      feedEl.innerHTML = "";
      if (!events.length) {
        feedEl.innerHTML = '<div class="empty">no events yet</div>';
      }
      for (const event of events.slice(-250)) {
        const row = document.createElement("div");
        const failed = event.kind === "error" ||
          (event.kind === "tool_end" && event.data && event.data.ok === false);
        row.className = "event" + (failed ? " err" : "") +
          (event.kind === "crew_done" || event.kind === "subagent_done" ? " good" : "");
        const time = new Date(event.ts).toLocaleTimeString();
        row.innerHTML =
          '<span class="time">' + time + "</span>" +
          '<span class="kind">' + event.kind + "</span>" +
          '<span class="agent">' + event.agent + "</span>" +
          '<span class="detail"></span>';
        row.querySelector(".detail").textContent = detailText(event);
        feedEl.appendChild(row);
      }
      rendered = events.length;
      feedEl.scrollTop = feedEl.scrollHeight;
    }

    const stateByAgent = {};
    for (const event of events) {
      stateByAgent[event.agent] = event.kind;
      if (event.kind === "subagent_start") stateByAgent[event.data.subagent] = event.kind;
      if (event.kind === "subagent_done") stateByAgent[event.data.subagent] = event.kind;
    }
    agentsEl.innerHTML = "";
    for (const name of Object.keys(stateByAgent).sort()) {
      const row = document.createElement("div");
      row.className = "agent";
      row.innerHTML = '<div><div class="name"></div><div class="role"></div></div>' +
        '<div class="state"></div>';
      row.querySelector(".name").textContent = name;
      row.querySelector(".role").textContent = stateByAgent[name];
      agentsEl.appendChild(row);
    }
    if (!agentsEl.children.length) {
      agentsEl.innerHTML = '<div class="empty">no agents yet</div>';
    }
  }

  async function poll() {
    try {
      const response = await fetch("/api/events", { cache: "no-store" });
      const payload = await response.json();
      render(payload.events || []);
    } catch (error) {
      /* retry on next tick */
    }
  }
  poll();
  setInterval(poll, 700);
</script>
</body>
</html>`;

const server = http.createServer((request, response) => {
  const url = request.url || "/";
  if (url.startsWith("/api/events")) {
    response.writeHead(200, {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    });
    response.end(JSON.stringify({ events }));
    return;
  }
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end(page);
});

server.listen(port, () => {
  console.log(`Squadron dashboard: http://localhost:${port}`);
  if (logPath) {
    console.log(`Watching: ${logPath}`);
  } else {
    console.log("No --log provided; feed will stay empty.");
  }
});
