import threading
import time
import subprocess
import json
import io
import secrets
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import qrcode

_flask_thread = None
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
_jarvis = None
_ngrok_process = None

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def get_remote_password():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("remote_password", "jarvis")
    except Exception:
        return "jarvis"

LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>JARVIS Login</title>
    <style>
        body { background:#000; color:#0ff; font-family:monospace; text-align:center; padding:40px; }
        input { padding:10px; font-size:18px; background:#111; color:#0ff; border:1px solid #0ff; width:80%%; max-width:300px; }
        button { margin-top:15px; padding:10px 30px; background:#0ff; color:#000; border:none; font-weight:bold; }
        .error { color:red; }
    </style>
</head>
<body>
    <h2>J.A.R.V.I.S Remote</h2>
    <form method="POST">
        <input type="password" name="password" placeholder="Password"><br>
        <button type="submit">Authenticate</button>
    </form>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
</body>
</html>
"""

INDEX_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>JARVIS Remote</title>
    <style>
        * { box-sizing:border-box; }
        body { background:#000; color:#fff; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:0; height:100vh; display:flex; flex-direction:column; }
        #header { background:#0a0a0a; padding:12px; border-bottom:1px solid #0ff; display:flex; align-items:center; justify-content:space-between; }
        #header h1 { font-size:18px; margin:0; color:#0ff; }
        #header a { color:#f44; font-size:12px; text-decoration:none; }
        #messages { flex:1; overflow-y:auto; padding:15px; display:flex; flex-direction:column; gap:8px; }
        .msg { max-width:80%%; padding:10px 14px; border-radius:12px; word-wrap:break-word; animation:fadeIn 0.3s; }
        @keyframes fadeIn { from{opacity:0;transform:translateY(5px);} to{opacity:1;transform:translateY(0);} }
        .msg-you { align-self:flex-end; background:#0ff; color:#000; border-bottom-right-radius:4px; }
        .msg-jarvis { align-self:flex-start; background:#1a1a2e; color:#0ff; border-bottom-left-radius:4px; border:1px solid #0ff44; }
        .msg-status { align-self:center; color:#666; font-size:12px; font-style:italic; }
        #input-area { display:flex; padding:10px; background:#0a0a0a; border-top:1px solid #333; gap:8px; }
        #cmd { flex:1; padding:12px; font-size:16px; background:#111; color:#fff; border:1px solid #0ff; border-radius:20px; outline:none; }
        #cmd:focus { border-color:#0ff; box-shadow:0 0 8px #0ff44; }
        button { padding:12px 20px; background:#0ff; color:#000; border:none; border-radius:20px; font-weight:bold; cursor:pointer; }
        button:disabled { opacity:0.5; cursor:not-allowed; }
        .dot-flashing { display:inline-block; width:6px; height:6px; border-radius:50%%; background:#0ff; animation:flash 0.8s infinite alternate; margin:0 2px; }
        .dot-flashing:nth-child(2) { animation-delay:0.2s; }
        .dot-flashing:nth-child(3) { animation-delay:0.4s; }
        @keyframes flash { to{opacity:0.3;} }
    </style>
</head>
<body>
    <div id="header">
        <h1>◈ J.A.R.V.I.S</h1>
        <a href="/logout">Logout</a>
    </div>
    <div id="messages"></div>
    <div id="input-area">
        <input id="cmd" type="text" placeholder="Type a command..." autocomplete="off">
        <button id="sendBtn" onclick="send()">Send</button>
    </div>
    <script>
        let waiting = false;

        function addMessage(text, cls) {
            const div = document.createElement('div');
            div.className = 'msg ' + cls;
            div.textContent = text;
            document.getElementById('messages').appendChild(div);
            document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
        }

        function addTyping() {
            const div = document.createElement('div');
            div.className = 'msg msg-jarvis';
            div.id = 'typing-indicator';
            div.innerHTML = '<span class="dot-flashing"></span><span class="dot-flashing"></span><span class="dot-flashing"></span>';
            document.getElementById('messages').appendChild(div);
            document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
        }

        function removeTyping() {
            const el = document.getElementById('typing-indicator');
            if (el) el.remove();
        }

        async function send() {
            if (waiting) return;
            const input = document.getElementById('cmd');
            const cmd = input.value.trim();
            if (!cmd) return;
            input.value = '';
            addMessage(cmd, 'msg-you');
            waiting = true;
            document.getElementById('sendBtn').disabled = true;
            addTyping();

            // Poll for response
            let reply = null;
            let attempts = 0;
            const maxAttempts = 600; // 600 × 1s = 10 minutes max
            try {
                // Fire the command
                await fetch('/command', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({command:cmd})
                });

                // Poll for result
                while (attempts < maxAttempts) {
                    await new Promise(r => setTimeout(r, 1000));
                    const res = await fetch('/result');
                    const data = await res.json();
                    if (data.reply) {
                        reply = data.reply;
                        break;
                    }
                    attempts++;
                }
            } catch(e) {
                reply = 'Connection error. Please try again.';
            }

            removeTyping();
            if (reply) {
                addMessage(reply, 'msg-jarvis');
            } else {
                addMessage('No response received. Jarvis may still be working.', 'msg-status');
            }
            waiting = false;
            document.getElementById('sendBtn').disabled = false;
            input.focus();
        }

        document.getElementById('cmd').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') send();
        });
    </script>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == get_remote_password():
            session["authenticated"] = True
            return redirect(url_for("index"))
        return render_template_string(LOGIN_PAGE, error="Wrong password")
    return render_template_string(LOGIN_PAGE, error=None)

@app.route("/index")
def index():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return render_template_string(INDEX_PAGE)

@app.route("/command", methods=["POST"])
def command():
    if not session.get("authenticated"):
        return jsonify({"reply": "Unauthorised"}), 403
    data = request.get_json()
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify({"reply": "No command provided."})
    if _jarvis:
        with _jarvis._response_lock:
            _jarvis._last_response = ""
        _jarvis.speak(cmd)
        return jsonify({"status": "sent"})
    return jsonify({"reply": "JARVIS not available."})

@app.route("/result")
def result():
    if not session.get("authenticated"):
        return jsonify({"reply": "Unauthorised"}), 403
    if _jarvis:
        with _jarvis._response_lock:
            reply = _jarvis._last_response
        return jsonify({"reply": reply or ""})
    return jsonify({"reply": ""})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def generate_qr(url):
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def start_server(jarvis):
    global _jarvis, _flask_thread
    _jarvis = jarvis
    if _flask_thread and _flask_thread.is_alive():
        return
    _flask_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=5050, debug=False),
        daemon=True
    )
    _flask_thread.start()
    print("[Server] 🌐 Remote dashboard started on port 5050")

def stop_server():
    global _flask_thread
    # We cannot gracefully stop Flask's built‑in server, but we can prevent new requests
    # by setting _jarvis to None and letting the thread die when the app exits.
    # For a clean toggle, we simply mark the tunnel as closed.
    print("[Server] 🌐 Remote dashboard stopped")

def start_ngrok():
    global _ngrok_process
    if _ngrok_process is not None:
        return None
    import requests
    ngrok_path = Path(__file__).resolve().parent / "tools" / "ngrok.exe"
    if not ngrok_path.exists():
        # Fallback to old location
        ngrok_path = Path(__file__).resolve().parent / "ngrok.exe"
    if not ngrok_path.exists():
        print("[Server] ❌ ngrok.exe not found in tools/ folder")
        return None

    _ngrok_process = subprocess.Popen(
        [str(ngrok_path), "http", "5050", "--log=stdout"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    for _ in range(15):
        time.sleep(1)
        try:
            r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
            tunnels = r.json().get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    return t["public_url"]
        except Exception:
            pass
    return None

def stop_ngrok():
    global _ngrok_process
    if _ngrok_process:
        _ngrok_process.terminate()
        _ngrok_process = None
        print("[Server] 🔌 Remote tunnel closed.")