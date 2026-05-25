from __future__ import annotations

import html


def render_dashboard_html(*, token: str = "") -> str:
    token_js = html.escape(token, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>agent-cli dashboard</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; display: grid; grid-template-columns: 280px 1fr; height: 100vh; }}
aside {{ border-right: 1px solid #e5e7eb; padding: 1rem; overflow: auto; }}
main {{ display: flex; flex-direction: column; height: 100vh; }}
#threads li {{ margin: 0.35rem 0; cursor: pointer; }}
#threads li.active {{ font-weight: 600; color: #2563eb; }}
#transcript {{ flex: 1; overflow: auto; padding: 1rem; background: #fafafa; }}
.msg {{ margin: 0.5rem 0; padding: 0.5rem 0.75rem; border-left: 3px solid #ccc; }}
.msg.user {{ border-color: #2563eb; }}
.msg.assistant {{ border-color: #16a34a; }}
.msg.approval {{ border-color: #ca8a04; background: #fffbeb; }}
footer {{ border-top: 1px solid #e5e7eb; padding: 0.75rem; display: flex; gap: 0.5rem; }}
input[type=text] {{ flex: 1; padding: 0.5rem; }}
button {{ padding: 0.5rem 0.75rem; }}
.badge {{ font-size: 0.75rem; color: #16a34a; }}
</style>
</head>
<body>
<aside>
  <h2>Threads</h2>
  <ul id="threads"></ul>
</aside>
<main>
  <div id="status" style="padding:0.75rem;border-bottom:1px solid #e5e7eb;">Select a thread</div>
  <div id="transcript"></div>
  <footer>
    <input id="prompt" type="text" placeholder="Task prompt..." />
    <button id="runBtn">Run</button>
  </footer>
</main>
<script>
const TOKEN = "{token_js}";
let currentThread = null;
let eventSource = null;

function authHeaders() {{
  const h = {{ "Content-Type": "application/json" }};
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  return h;
}}

function api(path, opts) {{
  const url = TOKEN ? path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : path;
  return fetch(url, Object.assign({{ headers: authHeaders() }}, opts || {{}}));
}}

async function loadThreads() {{
  const res = await api("/threads");
  const threads = await res.json();
  const ul = document.getElementById("threads");
  ul.innerHTML = "";
  threads.forEach(t => {{
    const li = document.createElement("li");
    li.textContent = t.label + (t.active ? " ●" : "");
    li.onclick = () => selectThread(t.id, t.label);
    ul.appendChild(li);
  }});
}}

function appendMsg(role, text) {{
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  document.getElementById("transcript").appendChild(div);
  div.scrollIntoView({{ block: "end" }});
}}

function handleEvent(ev) {{
  const data = JSON.parse(ev.data);
  if (data.type === "agent.delta") appendMsg("assistant", data.data.text || "");
  if (data.type === "turn.completed") document.getElementById("status").textContent = "Turn " + data.data.status;
  if (data.type === "approval.requested") {{
    const id = data.data.approval_id;
    const div = document.createElement("div");
    div.className = "msg approval";
    div.innerHTML = `<div>${{data.data.summary}}</div>
      <button onclick="approve('${{id}}','accept')">Approve</button>
      <button onclick="approve('${{id}}','deny')">Deny</button>`;
    document.getElementById("transcript").appendChild(div);
  }}
}}

function startSSE(threadId) {{
  if (eventSource) eventSource.close();
  const q = TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "";
  eventSource = new EventSource("/threads/" + threadId + "/events" + q);
  eventSource.onmessage = handleEvent;
}}

async function selectThread(id, label) {{
  currentThread = id;
  document.getElementById("status").textContent = "Thread: " + label;
  document.getElementById("transcript").innerHTML = "";
  startSSE(id);
}}

async function approve(id, decision) {{
  await api("/approvals/" + id, {{ method: "POST", body: JSON.stringify({{ decision }}) }});
}}

document.getElementById("runBtn").onclick = async () => {{
  if (!currentThread) return alert("Select a thread first");
  const prompt = document.getElementById("prompt").value;
  if (!prompt) return;
  const res = await api("/threads/" + currentThread + "/run", {{
    method: "POST",
    body: JSON.stringify({{ prompt, auto_approve: false }}),
  }});
  const body = await res.json();
  if (!res.ok) return alert(body.error || "run failed");
  document.getElementById("status").textContent = "Turn started: " + body.turn_id;
  appendMsg("user", prompt);
}};

loadThreads();
setInterval(loadThreads, 5000);
</script>
</body>
</html>"""
