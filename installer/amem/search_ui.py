#!/usr/bin/env python3
"""A-MEM Search Web UI - Flask app that talks to A-MEM via MCP SSE protocol."""

import json
import threading
import queue
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

AMEM_BASE = "http://localhost:8020"


class MCPSession:
    """Persistent MCP SSE session with proper handshake."""

    def __init__(self):
        self._lock = threading.Lock()
        self._endpoint = None
        self._sse_resp = None
        self._result_queue = queue.Queue()
        self._ready = threading.Event()
        self._initialized = False
        self._msg_id = 0
        self._reader_thread = None

    def _start_sse(self):
        """Start SSE reader thread and wait for endpoint."""
        self._ready.clear()
        self._initialized = False
        self._endpoint = None

        def reader():
            try:
                self._sse_resp = requests.get(
                    f"{AMEM_BASE}/sse", stream=True, timeout=300
                )
                for line in self._sse_resp.iter_lines(decode_unicode=True):
                    if line is None:
                        continue
                    if line.startswith("data: ") and "session_id=" in line:
                        self._endpoint = line[6:].strip()
                        self._ready.set()
                    elif line.startswith("data: "):
                        try:
                            parsed = json.loads(line[6:].strip())
                            self._result_queue.put(parsed)
                        except (json.JSONDecodeError, ValueError):
                            pass
            except Exception as e:
                self._result_queue.put({"error": f"SSE reader error: {e}"})
                self._ready.set()

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

        if not self._ready.wait(timeout=10):
            raise RuntimeError("Timeout waiting for SSE endpoint")

    def _send(self, method, params=None):
        """Send JSON-RPC message and get response."""
        self._msg_id += 1
        payload = {"jsonrpc": "2.0", "id": self._msg_id, "method": method}
        if params is not None:
            payload["params"] = params

        # Drain any stale messages
        while not self._result_queue.empty():
            try:
                self._result_queue.get_nowait()
            except queue.Empty:
                break

        requests.post(
            f"{AMEM_BASE}{self._endpoint}", json=payload, timeout=30
        )
        return self._result_queue.get(timeout=30)

    def _ensure_initialized(self):
        """Start SSE + MCP handshake if not done yet."""
        if self._initialized and self._endpoint:
            return

        self._start_sse()

        # MCP initialize handshake
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "amem-search-ui", "version": "1.0"}
        })

        if "error" in result and "result" not in result:
            raise RuntimeError(f"MCP initialize failed: {result}")

        # Send initialized notification (no response expected)
        self._msg_id += 1
        requests.post(
            f"{AMEM_BASE}{self._endpoint}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=10
        )
        time.sleep(0.3)
        self._initialized = True

    def call_tool(self, tool_name, arguments=None):
        """Call an MCP tool and return parsed result."""
        with self._lock:
            try:
                self._ensure_initialized()
                raw = self._send("tools/call", {
                    "name": tool_name,
                    "arguments": arguments or {}
                })
            except Exception:
                # Session broken, reset and retry once
                self._initialized = False
                self._endpoint = None
                try:
                    if self._sse_resp:
                        self._sse_resp.close()
                except Exception:
                    pass
                self._ensure_initialized()
                raw = self._send("tools/call", {
                    "name": tool_name,
                    "arguments": arguments or {}
                })

        return self._parse_result(raw)

    @staticmethod
    def _parse_result(raw):
        """Extract content from MCP tool response."""
        if "error" in raw and "result" not in raw:
            return {"_error": raw["error"]}

        try:
            content = raw.get("result", {}).get("content", [])
            if content and isinstance(content, list):
                text = content[0].get("text", "")
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    return text
            return raw.get("result", raw)
        except Exception:
            return raw


# Global MCP session
mcp = MCPSession()


# ─── Routes ───

@app.route("/")
def index():
    return HTML_PAGE


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.json
    q = data.get("query", "").strip()
    mode = data.get("mode", "hybrid")
    limit = int(data.get("limit", 10))

    if not q:
        return jsonify({"error": "Query required"}), 400

    tool = "amem_search" if mode == "hybrid" else "amem_search_agentic"
    result = mcp.call_tool(tool, {"query": q, "k": limit})

    return jsonify({"results": result, "mode": mode, "query": q})


@app.route("/api/read/<memory_id>")
def api_read(memory_id):
    result = mcp.call_tool("amem_read", {"memory_id": memory_id})
    return jsonify({"note": result})


@app.route("/api/stats")
def api_stats():
    result = mcp.call_tool("amem_stats")
    return jsonify({"stats": result})


# ─── HTML ───

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A-MEM Search</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242836;
    --border: #2e3344;
    --text: #e1e4ed;
    --text2: #8b90a0;
    --accent: #6c8cff;
    --accent2: #4a6adf;
    --green: #4ade80;
    --orange: #fb923c;
    --tag-bg: #2a2f42;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .container { max-width: 900px; margin: 0 auto; padding: 24px 16px; }

  /* Header */
  .header { text-align: center; margin-bottom: 32px; }
  .header h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }
  .header h1 span { color: var(--accent); }
  .header .sub { color: var(--text2); font-size: 0.9rem; }
  .stats-bar {
    display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;
    margin-top: 12px; font-size: 0.82rem; color: var(--text2);
  }
  .stats-bar .stat { background: var(--surface); padding: 4px 12px; border-radius: 6px; }
  .stats-bar .stat b { color: var(--green); }

  /* Search */
  .search-box {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 20px;
  }
  .search-row { display: flex; gap: 8px; }
  .search-row input[type="text"] {
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
    color: var(--text);
    font-size: 1rem;
    outline: none;
    transition: border-color 0.2s;
  }
  .search-row input:focus { border-color: var(--accent); }
  .search-row button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 1rem;
    cursor: pointer;
    font-weight: 600;
    transition: background 0.2s;
  }
  .search-row button:hover { background: var(--accent2); }
  .search-row button:disabled { opacity: 0.5; cursor: wait; }

  .options {
    display: flex; gap: 16px; align-items: center;
    margin-top: 12px; font-size: 0.85rem;
  }
  .toggle-group {
    display: flex; background: var(--surface2); border-radius: 6px; overflow: hidden;
  }
  .toggle-group label {
    padding: 5px 14px; cursor: pointer; color: var(--text2);
    transition: all 0.2s; user-select: none;
  }
  .toggle-group input { display: none; }
  .toggle-group input:checked + label {
    background: var(--accent); color: #fff;
  }
  .options .limit-group { display: flex; align-items: center; gap: 6px; color: var(--text2); }
  .options .limit-group input {
    width: 50px; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 8px; color: var(--text); text-align: center;
    font-size: 0.85rem;
  }

  /* Results */
  .info { text-align: center; color: var(--text2); padding: 40px 0; font-size: 0.95rem; }
  .result-count { color: var(--text2); font-size: 0.85rem; margin-bottom: 12px; }

  .result-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .result-card:hover { border-color: var(--accent); }
  .result-card .top { display: flex; justify-content: space-between; align-items: start; gap: 12px; margin-bottom: 8px; }
  .result-card .score {
    font-size: 0.75rem; background: var(--tag-bg); color: var(--orange);
    padding: 2px 8px; border-radius: 4px; white-space: nowrap; flex-shrink: 0;
  }
  .result-card .content {
    font-size: 0.9rem; color: var(--text);
    max-height: 120px; overflow: hidden;
    line-height: 1.5; white-space: pre-wrap; word-break: break-word;
  }
  .result-card .meta { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
  .result-card .meta .tag {
    font-size: 0.72rem; background: var(--tag-bg); color: var(--accent);
    padding: 2px 8px; border-radius: 4px;
  }
  .result-card .meta .ctx {
    font-size: 0.72rem; background: var(--tag-bg); color: var(--green);
    padding: 2px 8px; border-radius: 4px;
  }

  /* Detail Modal */
  .modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.7); z-index: 100;
    align-items: center; justify-content: center;
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; padding: 24px; max-width: 700px; width: 95%;
    max-height: 85vh; overflow-y: auto;
  }
  .modal .close {
    float: right; background: none; border: none; color: var(--text2);
    font-size: 1.4rem; cursor: pointer; line-height: 1;
  }
  .modal .close:hover { color: var(--text); }
  .modal h2 { font-size: 1.1rem; margin-bottom: 16px; color: var(--accent); }
  .modal .field { margin-bottom: 12px; }
  .modal .field-label { font-size: 0.78rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .modal .field-value {
    background: var(--surface2); border-radius: 8px; padding: 10px 14px;
    font-size: 0.88rem; white-space: pre-wrap; word-break: break-word; line-height: 1.5;
  }
  .modal .field-value.id-val { font-family: monospace; font-size: 0.78rem; color: var(--text2); }

  .spinner { display: inline-block; width: 18px; height: 18px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite;
    vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1><span>A-MEM</span> Search</h1>
    <p class="sub">Zettelkasten-Wissensdatenbank durchsuchen</p>
    <div class="stats-bar" id="statsBar">
      <span class="stat"><span class="spinner"></span></span>
    </div>
  </div>

  <div class="search-box">
    <div class="search-row">
      <input type="text" id="queryInput" placeholder="Suchbegriff eingeben..." autofocus>
      <button id="searchBtn" onclick="doSearch()">Suchen</button>
    </div>
    <div class="options">
      <div class="toggle-group">
        <input type="radio" name="mode" id="modeHybrid" value="hybrid" checked>
        <label for="modeHybrid">Hybrid</label>
        <input type="radio" name="mode" id="modeAgentic" value="agentic">
        <label for="modeAgentic">Agentic</label>
      </div>
      <div class="limit-group">
        <span>Max:</span>
        <input type="number" id="limitInput" value="10" min="1" max="50">
      </div>
    </div>
  </div>

  <div id="results">
    <div class="info">Suchbegriff eingeben und Enter drücken</div>
  </div>
</div>

<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="modalContent">
    <button class="close" onclick="document.getElementById('modalOverlay').classList.remove('active')">&times;</button>
    <h2>Note Details</h2>
    <div id="modalBody">Loading...</div>
  </div>
</div>

<script>
const queryInput = document.getElementById('queryInput');
const searchBtn = document.getElementById('searchBtn');
const resultsDiv = document.getElementById('results');

queryInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

// Load stats
fetch('/api/stats').then(r => r.json()).then(data => {
  const s = data.stats;
  if (s && typeof s === 'object' && s.total_notes !== undefined) {
    document.getElementById('statsBar').innerHTML =
      `<span class="stat"><b>${Number(s.total_notes).toLocaleString('de-DE')}</b> Notes</span>` +
      `<span class="stat">LLM: <b>${s.llm_model || '?'}</b></span>` +
      `<span class="stat">Embedding: <b>${s.embedding_model || '?'}</b></span>`;
  } else {
    document.getElementById('statsBar').innerHTML = '<span class="stat">Stats: error</span>';
  }
}).catch(() => {
  document.getElementById('statsBar').innerHTML = '<span class="stat">Stats unavailable</span>';
});

function getMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

async function doSearch() {
  const query = queryInput.value.trim();
  if (!query) return;

  searchBtn.disabled = true;
  searchBtn.innerHTML = '<span class="spinner"></span>';
  resultsDiv.innerHTML = '<div class="info"><span class="spinner"></span> Suche läuft...</div>';

  try {
    const resp = await fetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        query,
        mode: getMode(),
        limit: parseInt(document.getElementById('limitInput').value) || 10
      })
    });
    const data = await resp.json();

    if (data.error) {
      resultsDiv.innerHTML = `<div class="info">Fehler: ${esc(data.error)}</div>`;
      return;
    }

    let items = data.results;
    if (typeof items === 'string') {
      try { items = JSON.parse(items); } catch(e) {}
    }

    if (!Array.isArray(items) || items.length === 0) {
      resultsDiv.innerHTML = '<div class="info">Keine Ergebnisse gefunden</div>';
      return;
    }

    const modeLabel = data.mode === 'hybrid' ? 'Hybrid (BM25+Vektor)' : 'Agentic (Vektor)';
    let html = `<div class="result-count">${items.length} Ergebnis${items.length !== 1 ? 'se' : ''} — ${modeLabel}</div>`;

    for (const item of items) {
      const id = item.id || item.memory_id || '';
      const content = item.content || item.text || JSON.stringify(item);
      const score = item.score !== undefined ? item.score : '';
      const context = item.context || '';
      const tags = item.tags || item.keywords || '';

      html += `<div class="result-card" onclick="showDetail('${escAttr(id)}')">`;
      html += `<div class="top">`;
      html += `<div class="content">${esc(truncate(content, 300))}</div>`;
      if (score !== '') html += `<div class="score">${typeof score === 'number' ? score.toFixed(3) : esc(String(score))}</div>`;
      html += `</div>`;

      const metaParts = [];
      if (context) metaParts.push(`<span class="ctx">${esc(context)}</span>`);
      if (tags) {
        const tagList = typeof tags === 'string' ? tags.split(',') : (Array.isArray(tags) ? tags : []);
        tagList.forEach(t => { if(t.trim()) metaParts.push(`<span class="tag">${esc(t.trim())}</span>`); });
      }
      if (metaParts.length) html += `<div class="meta">${metaParts.join('')}</div>`;
      html += `</div>`;
    }

    resultsDiv.innerHTML = html;
  } catch (err) {
    resultsDiv.innerHTML = `<div class="info">Fehler: ${esc(err.message)}</div>`;
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = 'Suchen';
  }
}

async function showDetail(memoryId) {
  if (!memoryId) return;
  const overlay = document.getElementById('modalOverlay');
  const body = document.getElementById('modalBody');
  overlay.classList.add('active');
  body.innerHTML = '<div class="info"><span class="spinner"></span> Lade Details...</div>';

  try {
    const resp = await fetch(`/api/read/${encodeURIComponent(memoryId)}`);
    const data = await resp.json();
    const note = data.note;

    if (!note || typeof note === 'string') {
      body.innerHTML = `<div class="field"><div class="field-value">${esc(note || 'Keine Daten')}</div></div>`;
      return;
    }

    let html = '';
    const fields = [
      ['ID', note.id || memoryId, 'id-val'],
      ['Content', note.content || ''],
      ['Context', note.context || ''],
      ['Keywords', Array.isArray(note.keywords) ? note.keywords.join(', ') : (note.keywords || '')],
      ['Tags', Array.isArray(note.tags) ? note.tags.join(', ') : (note.tags || '')],
      ['Links', Array.isArray(note.links) ? note.links.join(', ') : (note.links || '')],
      ['Created', note.created_at || note.timestamp || ''],
      ['Retrieval Count', note.retrieval_count !== undefined ? String(note.retrieval_count) : ''],
    ];

    for (const [label, value, cls] of fields) {
      if (!value) continue;
      html += `<div class="field">
        <div class="field-label">${esc(label)}</div>
        <div class="field-value${cls ? ' ' + cls : ''}">${esc(String(value))}</div>
      </div>`;
    }

    body.innerHTML = html || '<div class="info">Keine Details verfügbar</div>';
  } catch (err) {
    body.innerHTML = `<div class="info">Fehler: ${esc(err.message)}</div>`;
  }
}

function closeModal(e) {
  if (e.target === document.getElementById('modalOverlay'))
    document.getElementById('modalOverlay').classList.remove('active');
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function escAttr(s) {
  return String(s).replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function truncate(s, max) {
  return s.length > max ? s.substring(0, max) + '...' : s;
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8021, debug=False)
