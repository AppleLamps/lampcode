from __future__ import annotations

import html


def render_login_html(*, oidc_enabled: bool = False) -> str:
    sso_block = ""
    if oidc_enabled:
        sso_block = """
<p><a href="/auth/oidc/login"><button type="button">Sign in with SSO</button></a></p>
<hr/>
<p>Or use a break-glass access token:</p>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>agent-cli login</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 4rem auto; padding: 1rem; }}
label {{ display: block; margin: 0.5rem 0 0.25rem; }}
input {{ width: 100%; padding: 0.5rem; box-sizing: border-box; }}
button {{ margin-top: 1rem; padding: 0.5rem 1rem; }}
.error {{ color: #b91c1c; margin-top: 0.5rem; }}
</style>
</head>
<body>
<h1>agent-cli</h1>
{sso_block}
<label for="token">Token</label>
<input id="token" type="password" autocomplete="current-password"/>
<button id="loginBtn">Login</button>
<div id="err" class="error"></div>
<script>
document.getElementById("loginBtn").onclick = async () => {{
  const token = document.getElementById("token").value;
  const res = await fetch("/auth/login", {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{ token }}),
  }});
  const body = await res.json();
  if (!res.ok) {{
    document.getElementById("err").textContent = body.error || "Login failed";
    return;
  }}
  sessionStorage.setItem("agent_session", body.session_id);
  sessionStorage.setItem("agent_role", body.role);
  sessionStorage.setItem("agent_user", body.name);
  window.location.href = "/";
}};
</script>
</body>
</html>"""


def render_dashboard_html(
    *,
    token: str = "",
    role: str = "admin",
    user_name: str = "legacy",
    session_mode: bool = False,
    oidc_enabled: bool = False,
) -> str:
    token_js = html.escape(token, quote=True)
    role_js = html.escape(role, quote=True)
    user_js = html.escape(user_name, quote=True)
    can_control = role in ("operator", "admin")
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
button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
.badge {{ font-size: 0.75rem; color: #64748b; }}
</style>
</head>
<body>
<aside>
  <h2>Threads</h2>
  <div class="badge">User: {user_js} · Role: {role_js}</div>
  <ul id="threads"></ul>
</aside>
<main>
  <div id="status" style="padding:0.75rem;border-bottom:1px solid #e5e7eb;">Select a thread</div>
  <div id="transcript"></div>
  <footer>
    <input id="prompt" type="text" placeholder="Task prompt..." {"disabled" if not can_control else ""}/>
    <button id="runBtn" {"disabled" if not can_control else ""}>Run</button>
  </footer>
</main>
<script>
const TOKEN = "{token_js}";
const SESSION_MODE = {"true" if session_mode else "false"};
const USER_ROLE = "{role_js}";
const CAN_CONTROL = {"true" if can_control else "false"};
let currentThread = null;
let eventSource = null;

function sessionId() {{
  return sessionStorage.getItem("agent_session") || "";
}}

function authHeaders() {{
  const h = {{ "Content-Type": "application/json" }};
  if (SESSION_MODE && sessionId()) {{
    h["Authorization"] = "Session " + sessionId();
  }} else if (TOKEN) {{
    h["Authorization"] = "Bearer " + TOKEN;
  }}
  return h;
}}

function api(path, opts) {{
  let url = path;
  if (!SESSION_MODE && TOKEN) {{
    url = path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
  }}
  return fetch(url, Object.assign({{ headers: authHeaders() }}, opts || {{}}));
}}

async function loadThreads() {{
  const res = await api("/threads");
  if (res.status === 401) {{ window.location.href = "/login"; return; }}
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
  if (data.type === "approval.requested" && CAN_CONTROL) {{
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
  let q = "";
  if (SESSION_MODE && sessionId()) {{
    // EventSource cannot set headers; rely on cookie-less session via query in future
    q = "?token=" + encodeURIComponent(sessionId());
  }} else if (TOKEN) {{
    q = "?token=" + encodeURIComponent(TOKEN);
  }}
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
  if (!CAN_CONTROL) return;
  await api("/approvals/" + id, {{ method: "POST", body: JSON.stringify({{ decision }}) }});
}}

const runBtn = document.getElementById("runBtn");
if (runBtn) runBtn.onclick = async () => {{
  if (!CAN_CONTROL) return alert("Insufficient role");
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

if (SESSION_MODE && !sessionId() && !TOKEN) {{
  window.location.href = "/login";
}} else {{
  loadThreads();
  setInterval(loadThreads, 5000);
}}
</script>
</body>
</html>"""
