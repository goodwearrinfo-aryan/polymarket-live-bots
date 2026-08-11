"""
Personal Lawyer UI — stdlib only (http.server + json)
Serves on port 8765.

Endpoints:
  GET  /         — chat UI (WhatsApp-style)
  POST /chat     — {question, history:[...]} → {answer, sources, routing, history}
  POST /ask      — legacy single-shot {question} → {answer, sources, routing, live_used}
"""

import sys
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, '/Users/aryanagarwal/Documents/polymarket')
from lawyer import answer, chat as lawyer_chat, courtroom  # noqa: E402
import tools_registry  # noqa: E402

# ---------------------------------------------------------------------------
# Single-page HTML — chat interface (dark theme, no external deps)
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>&#9878;&#65039; Personal Lawyer (India)</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0f1117;
    color: #e0e0e0;
    font-family: 'Segoe UI', system-ui, sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  /* ---- header ---- */
  .header {
    width: 100%;
    max-width: 820px;
    padding: 1.1rem 1rem 0.5rem;
    flex-shrink: 0;
  }
  .header h1 {
    font-size: 1.55rem;
    font-weight: 700;
    color: #fff;
    letter-spacing: -0.4px;
  }
  .header .subtitle {
    color: #888;
    font-size: 0.82rem;
    margin-top: 0.15rem;
  }
  /* ---- chat viewport ---- */
  #chat-viewport {
    flex: 1;
    width: 100%;
    max-width: 820px;
    overflow-y: auto;
    padding: 1rem 1rem 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  /* ---- bubbles ---- */
  .msg-row {
    display: flex;
    flex-direction: column;
    max-width: 82%;
  }
  .msg-row.user { align-self: flex-end; align-items: flex-end; }
  .msg-row.lawyer { align-self: flex-start; align-items: flex-start; }

  .bubble {
    padding: 0.75rem 1rem;
    border-radius: 14px;
    font-size: 0.95rem;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .user .bubble {
    background: #3d5af1;
    color: #fff;
    border-bottom-right-radius: 4px;
  }
  .lawyer .bubble {
    background: #1a1d27;
    border: 1px solid #2a2d3d;
    color: #d8d8e8;
    border-bottom-left-radius: 4px;
  }

  /* sources + routing beneath lawyer bubble */
  .meta-block {
    margin-top: 0.55rem;
    width: 100%;
  }
  .meta-header {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #aaa;
    margin-bottom: 0.4rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid #2a2d3d;
  }
  .source-item {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    background: #12141e;
    margin-bottom: 0.35rem;
    border: 1px solid #23263a;
    font-size: 0.82rem;
  }
  .source-item.live-source { border-color: #2a4a3a; background: #111e18; }
  .source-score {
    flex-shrink: 0;
    font-size: 0.7rem;
    font-weight: 700;
    color: #3d5af1;
    background: #1e2240;
    border-radius: 4px;
    padding: 0.15rem 0.35rem;
    margin-top: 0.1rem;
  }
  .live-badge {
    flex-shrink: 0;
    font-size: 0.67rem;
    font-weight: 700;
    color: #4ade80;
    background: #0d2a1a;
    border: 1px solid #166534;
    border-radius: 4px;
    padding: 0.15rem 0.35rem;
    margin-top: 0.1rem;
  }
  .source-title { color: #c8c8d8; }
  .source-act   { font-size: 0.72rem; color: #888; margin-top: 0.1rem; }

  .routing-header {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #f59e0b;
    margin-top: 0.7rem;
    margin-bottom: 0.4rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid #3d3010;
  }
  .routing-card {
    background: #1c1710;
    border: 1px solid #3d3010;
    border-radius: 7px;
    padding: 0.6rem 0.85rem;
    margin-bottom: 0.45rem;
    font-size: 0.82rem;
  }
  .routing-forum {
    font-weight: 700;
    color: #fbbf24;
    margin-bottom: 0.2rem;
  }
  .routing-guidance { color: #d4b483; line-height: 1.5; }

  /* typing indicator */
  .typing-bubble {
    background: #1a1d27;
    border: 1px solid #2a2d3d;
    border-radius: 14px;
    border-bottom-left-radius: 4px;
    padding: 0.65rem 1rem;
    display: flex;
    gap: 5px;
    align-items: center;
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #5a7cfa;
    animation: bounce 1.1s infinite ease-in-out;
  }
  .dot:nth-child(2) { animation-delay: 0.15s; }
  .dot:nth-child(3) { animation-delay: 0.30s; }
  @keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); opacity:0.4; }
    40%            { transform: translateY(-6px); opacity:1; }
  }

  /* ---- language selector ---- */
  #lang-bar {
    width: 100%;
    max-width: 820px;
    padding: 0.4rem 1rem 0.25rem;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 0.65rem;
  }
  #lang-bar label {
    font-size: 0.78rem;
    color: #888;
    white-space: nowrap;
  }
  #lang-select {
    background: #1a1d27;
    border: 1px solid #2e3148;
    border-radius: 8px;
    color: #c8c8d8;
    font-size: 0.84rem;
    padding: 0.3rem 0.65rem;
    outline: none;
    cursor: pointer;
    max-width: 260px;
  }
  #lang-select:focus { border-color: #5a7cfa; }

  /* ---- input area ---- */
  #input-area {
    width: 100%;
    max-width: 820px;
    padding: 0.65rem 1rem 1rem;
    flex-shrink: 0;
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
    border-top: 1px solid #1e2130;
    background: #0f1117;
  }
  #q {
    flex: 1;
    background: #1a1d27;
    border: 1px solid #2e3148;
    border-radius: 22px;
    color: #e0e0e0;
    font-size: 0.97rem;
    padding: 0.65rem 1.1rem;
    outline: none;
    resize: none;
    height: 46px;
    min-height: 46px;
    max-height: 160px;
    overflow-y: auto;
    font-family: inherit;
    line-height: 1.45;
    transition: border-color 0.2s;
  }
  #q:focus { border-color: #5a7cfa; }
  #send-btn {
    flex-shrink: 0;
    width: 46px; height: 46px;
    border-radius: 50%;
    background: #3d5af1;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.25rem;
    transition: background 0.2s, opacity 0.2s;
    color: #fff;
  }
  #send-btn:hover { background: #5a7cfa; }
  #send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* error */
  .error-msg { color: #f87171; }

  /* live-used note inline */
  .live-note-inline {
    font-size: 0.75rem;
    color: #4ade80;
    background: #0d2a1a;
    border: 1px solid #166534;
    border-radius: 5px;
    padding: 0.3rem 0.65rem;
    margin-bottom: 0.45rem;
    display: inline-block;
  }
  .low-confidence-note {
    color: #fbbf24;
    background: #2a1f0d;
    border: 1px solid #92651a;
  }

  /* tools panel */
  #tools-panel {
    flex: 1;
    width: 100%;
    max-width: 780px;
    margin: 0 auto;
    padding: 1rem;
    overflow-y: auto;
  }
  #tools-select-row { margin-bottom: 0.8rem; }
  #tools-select-row label { color: #999; font-size: 0.85rem; margin-right: 0.5rem; }
  #tool-select {
    background: #1a1d27; color: #e0e0e0; border: 1px solid #2a2d3d;
    border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.85rem; max-width: 100%;
  }
  .tool-doc { color: #888; font-size: 0.8rem; margin-bottom: 0.7rem; }
  .tool-field { margin-bottom: 0.6rem; }
  .tool-field label { display: block; color: #aaa; font-size: 0.78rem; margin-bottom: 0.2rem; }
  .tool-field input {
    width: 100%; background: #1a1d27; color: #e0e0e0; border: 1px solid #2a2d3d;
    border-radius: 6px; padding: 0.5rem 0.7rem; font-size: 0.85rem;
  }
  #tool-run-btn {
    background: #5a7cfa; color: #fff; border: none; border-radius: 8px;
    padding: 0.55rem 1.2rem; font-size: 0.9rem; cursor: pointer; margin-top: 0.4rem;
  }
  #tool-run-btn:hover { background: #4a6cf7; }
  .tool-loading { color: #666; font-style: italic; margin-top: 0.8rem; }
  .tool-error { color: #f87171; margin-top: 0.8rem; font-size: 0.85rem; }
  .tool-output {
    margin-top: 0.8rem; background: #12141e; border: 1px solid #1e2235;
    border-radius: 8px; padding: 0.8rem; color: #d8d8e8; font-size: 0.83rem;
    white-space: pre-wrap; word-break: break-word; max-height: 60vh; overflow-y: auto;
  }
</style>
</head>
<body>

<div class="header">
  <h1>&#9878;&#65039; Personal Lawyer <span style="color:#5a7cfa">(India)</span></h1>
  <p class="subtitle">Grounded in Indian statutes &amp; case law &mdash; session clears on reload</p>
</div>

<div id="lang-bar">
  <label for="lang-select">Language:</label>
  <select id="lang-select">
    <option value="">&#128269; Auto-detect</option>
    <option value="hindi">&#x0939;&#x093F;&#x0928;&#x094D;&#x0926;&#x0940; (Hindi)</option>
    <option value="bengali">&#x09AC;&#x09BE;&#x0982;&#x09B2;&#x09BE; (Bengali)</option>
    <option value="telugu">&#x0C24;&#x0C46;&#x0C32;&#x0C41;&#x0C17;&#x0C41; (Telugu)</option>
    <option value="marathi">&#x092E;&#x0930;&#x093E;&#x0920;&#x0940; (Marathi)</option>
    <option value="tamil">&#x0BA4;&#x0BAE;&#x0BBF;&#x0BB4;&#x0BCD; (Tamil)</option>
    <option value="urdu">&#x0627;&#x0631;&#x062F;&#x0648; (Urdu)</option>
    <option value="gujarati">&#x0A97;&#x0AC1;&#x0A9C;&#x0AB0;&#x0ABE;&#x0AA4;&#x0AC0; (Gujarati)</option>
    <option value="kannada">&#x0C95;&#x0CA8;&#x0CCD;&#x0CA8;&#x0CA1; (Kannada)</option>
    <option value="malayalam">&#x0D2E;&#x0D32;&#x0D2F;&#x0D3E;&#x0D33;&#x0D02; (Malayalam)</option>
    <option value="odia">&#x0B13;&#x0B21;&#x0B3C;&#x0B3F;&#x0B06; (Odia)</option>
    <option value="punjabi">&#x0A2A;&#x0A70;&#x0A1C;&#x0A3E;&#x0A2C;&#x0A40; (Punjabi)</option>
    <option value="assamese">&#x0985;&#x09B8;&#x09AE;&#x09BF;&#x09AF;&#x09BC;&#x09BE; (Assamese)</option>
    <option value="maithili">&#x092E;&#x0948;&#x0925;&#x093F;&#x0932;&#x0940; (Maithili)</option>
    <option value="sanskrit">&#x0938;&#x0902;&#x0938;&#x094D;&#x0915;&#x0943;&#x0924;&#x092E;&#x094D; (Sanskrit)</option>
    <option value="konkani">&#x0915;&#x094B;&#x0902;&#x0915;&#x0923;&#x0940; (Konkani)</option>
    <option value="manipuri">&#x09AE;&#x09C8;&#x09A4;&#x09C8;&#x09B2;&#x09CB;&#x09A8;&#x09CD; (Manipuri)</option>
    <option value="nepali">&#x0928;&#x0947;&#x092A;&#x093E;&#x0932;&#x0940; (Nepali)</option>
    <option value="sindhi">&#x0633;&#x0646;&#x0DA0;&#x064A; (Sindhi)</option>
    <option value="bodo">&#x092C;&#x0921;&#x093C;&#x094B; (Bodo)</option>
    <option value="santali">&#x1C25;&#x1C1F;&#x1C34;&#x1C1B;&#x1C1F;&#x1C36; (Santali)</option>
    <option value="kashmiri">&#x06A9;&#x06B2;&#x0634;&#x064F;&#x0631; (Kashmiri)</option>
    <option value="dogri">&#x0921;&#x094B;&#x0917;&#x0930;&#x0940; (Dogri)</option>
    <option value="english">English</option>
  </select>
  <span style="margin-left:1rem;"></span>
  <label for="mode-select">Mode:</label>
  <select id="mode-select">
    <option value="chat">&#128172; Chat</option>
    <option value="courtroom">&#9878;&#65039; Courtroom (Support vs Opposition vs Judge)</option>
    <option value="tools">&#128295; Tools (draft notices, calculators, guides)</option>
  </select>
</div>

<div id="chat-viewport"></div>

<div id="tools-panel" style="display:none;">
  <div id="tools-select-row">
    <label for="tool-select">Tool:</label>
    <select id="tool-select"></select>
  </div>
  <div id="tool-form"></div>
  <button id="tool-run-btn" onclick="runTool()">Run</button>
  <div id="tool-result"></div>
</div>

<div id="input-area">
  <textarea id="q" placeholder="Ask any Indian legal question&hellip;" rows="1"></textarea>
  <button id="send-btn" onclick="sendMessage()" title="Send">&#10148;</button>
</div>

<script>
// Session state — cleared on page reload
let history = [];
const langSelect = document.getElementById('lang-select');
const modeSelect = document.getElementById('mode-select');

const viewport = document.getElementById('chat-viewport');
const input    = document.getElementById('q');
const sendBtn  = document.getElementById('send-btn');
const inputArea = document.getElementById('input-area');
const toolsPanel = document.getElementById('tools-panel');
const toolSelect = document.getElementById('tool-select');
const toolForm = document.getElementById('tool-form');
const toolResult = document.getElementById('tool-result');

let toolsList = [];

modeSelect.addEventListener('change', async () => {
  const mode = modeSelect.value;
  if (mode === 'tools') {
    viewport.style.display = 'none';
    inputArea.style.display = 'none';
    toolsPanel.style.display = 'block';
    if (!toolsList.length) await loadTools();
  } else {
    viewport.style.display = '';
    inputArea.style.display = '';
    toolsPanel.style.display = 'none';
  }
});

async function loadTools() {
  const res = await fetch('/tools');
  const data = await res.json();
  toolsList = data.tools || [];
  toolSelect.innerHTML = toolsList.map((t, i) =>
    `<option value="${i}">${escHtml(t.label)}</option>`).join('');
  renderToolForm();
}

toolSelect.addEventListener('change', renderToolForm);

function renderToolForm() {
  const tool = toolsList[toolSelect.value];
  toolResult.innerHTML = '';
  if (!tool) { toolForm.innerHTML = ''; return; }
  let html = '';
  if (tool.doc) {
    html += `<div class="tool-doc">${escHtml(tool.doc)}</div>`;
  }
  tool.params.forEach(p => {
    const req = p.required ? '<span style="color:#f87171;">*</span>' : '';
    html += `<div class="tool-field">
      <label>${escHtml(p.name)}${req}</label>
      <input type="text" data-param="${escHtml(p.name)}"
             placeholder="${p.required ? 'required' : 'optional'}">
    </div>`;
  });
  toolForm.innerHTML = html;
}

async function runTool() {
  const tool = toolsList[toolSelect.value];
  if (!tool) return;
  const inputs = toolForm.querySelectorAll('[data-param]');
  const params = {};
  let missingRequired = false;
  inputs.forEach(el => {
    const val = el.value.trim();
    if (val) params[el.dataset.param] = val;
  });
  tool.params.forEach(p => {
    if (p.required && !(p.name in params)) missingRequired = true;
  });
  if (missingRequired) {
    toolResult.innerHTML = '<div class="tool-error">Please fill all required (*) fields.</div>';
    return;
  }

  toolResult.innerHTML = '<div class="tool-loading">Running&hellip;</div>';
  try {
    const res = await fetch('/tools/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: tool.key, params})
    });
    const data = await res.json();
    if (data.error) {
      toolResult.innerHTML = `<div class="tool-error">Error: ${escHtml(data.error)}</div>`;
      return;
    }
    const result = data.result;
    const pretty = (typeof result === 'string') ? result : JSON.stringify(result, null, 2);
    toolResult.innerHTML = `<pre class="tool-output">${escHtml(pretty)}</pre>`;
  } catch (e) {
    toolResult.innerHTML = `<div class="tool-error">Request failed: ${escHtml(String(e))}</div>`;
  }
}

// Auto-resize textarea
input.addEventListener('input', function() {
  this.style.height = '46px';
  this.style.height = Math.min(this.scrollHeight, 160) + 'px';
});

// Enter sends (Shift+Enter = newline)
input.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function appendUserBubble(text) {
  const row = document.createElement('div');
  row.className = 'msg-row user';
  row.innerHTML = '<div class="bubble">' + escHtml(text) + '</div>';
  viewport.appendChild(row);
  scrollBottom();
  return row;
}

function appendTypingIndicator() {
  const row = document.createElement('div');
  row.className = 'msg-row lawyer';
  row.innerHTML = '<div class="typing-bubble"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
  viewport.appendChild(row);
  scrollBottom();
  return row;
}

function buildSourcesHtml(sources, liveUsed, lowConfidence) {
  if (!sources || sources.length === 0) return '';
  let html = '';
  if (liveUsed) {
    html += '<div class="live-note-inline">Low local confidence &mdash; supplemented with live IndianKanoon data</div>';
  } else if (lowConfidence) {
    html += '<div class="live-note-inline low-confidence-note">&#9888; Low confidence &mdash; local sources may not directly cover this question, and live case-law lookup was unavailable. Treat this answer as general guidance only.</div>';
  }
  html += '<div class="meta-header">Sources</div>';
  sources.forEach(s => {
    const isLive = !!s.live;
    const badge = isLive
      ? '<span class="live-badge">LIVE</span>'
      : '<span class="source-score">' + (s.score !== undefined ? Number(s.score).toFixed(3) : '?') + '</span>';
    html += '<div class="source-item' + (isLive ? ' live-source' : '') + '">' +
      badge +
      '<div><div class="source-title">' + escHtml(s.title || '') + '</div>' +
      '<div class="source-act">' + escHtml(s.act_or_court || '') + '</div></div>' +
      '</div>';
  });
  return html;
}

function buildRoutingHtml(routing) {
  if (!routing || routing.length === 0) return '';
  let html = '<div class="routing-header">Where to Go</div>';
  routing.forEach(r => {
    html += '<div class="routing-card">' +
      '<div class="routing-forum">' + escHtml(r.forum || '') + '</div>' +
      '<div class="routing-guidance">' + escHtml(r.guidance || '') + '</div>' +
      '</div>';
  });
  return html;
}

function replacePlaceholderWithAnswer(placeholderRow, data) {
  if (data.error) {
    placeholderRow.innerHTML =
      '<div class="bubble error-msg">Error: ' + escHtml(data.error) + '</div>';
    return;
  }

  const answerText = data.answer || '';
  const detectedLang = data.detected_lang || '';
  const langTag = detectedLang && detectedLang !== 'english'
    ? '<span style="font-size:0.7rem;color:#5a7cfa;background:#1e2240;border-radius:4px;padding:0.1rem 0.4rem;margin-bottom:0.4rem;display:inline-block;">'
      + escHtml(detectedLang) + '</span><br>'
    : '';
  let html = '<div class="bubble">' + langTag + escHtml(answerText) + '</div>';

  const metaParts = buildSourcesHtml(data.sources, data.live_used, data.low_confidence)
                  + buildRoutingHtml(data.routing);
  if (metaParts) {
    html += '<div class="meta-block">' + metaParts + '</div>';
  }

  placeholderRow.innerHTML = html;
  scrollBottom();
}

function scrollBottom() {
  viewport.scrollTop = viewport.scrollHeight;
}

async function sendMessage() {
  const q = input.value.trim();
  if (!q) return;

  const mode = modeSelect ? modeSelect.value : 'chat';

  sendBtn.disabled = true;
  input.value = '';
  input.style.height = '46px';

  appendUserBubble(q);
  const placeholder = appendTypingIndicator();

  try {
    if (mode === 'courtroom') {
      const res = await fetch('/courtroom', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({case: q})
      });
      const data = await res.json();
      placeholder.className = 'msg-row lawyer';
      replaceCourtroomPlaceholder(placeholder, data);
    } else {
      const selectedLang = langSelect ? langSelect.value : '';
      const body = {question: q, history: history};
      if (selectedLang) body.lang = selectedLang;
      const res = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const data = await res.json();

      // Update session history from server response
      if (data.history) {
        history = data.history;
      } else {
        history.push({role:'user', content:q});
        history.push({role:'assistant', content: data.answer || ''});
      }

      // Replace lawyer row (typing → answer)
      placeholder.className = 'msg-row lawyer';
      replacePlaceholderWithAnswer(placeholder, data);
    }
  } catch(e) {
    placeholder.innerHTML = '<div class="bubble error-msg">Request failed: ' + escHtml(String(e)) + '</div>';
  } finally {
    sendBtn.disabled = false;
    input.focus();
    scrollBottom();
  }
}

function replaceCourtroomPlaceholder(placeholderRow, data) {
  if (data.error) {
    placeholderRow.innerHTML =
      '<div class="bubble error-msg">Error: ' + escHtml(data.error) + '</div>';
    return;
  }

  let html = '';
  html += '<div class="bubble" style="border-left:3px solid #4ade80;">'
        + '<div style="font-size:0.72rem;color:#4ade80;font-weight:600;margin-bottom:0.3rem;">&#9878;&#65039; SUPPORT LAWYER</div>'
        + escHtml(data.support_argument || '') + '</div>';
  html += '<div class="bubble" style="border-left:3px solid #f87171;margin-top:0.5rem;">'
        + '<div style="font-size:0.72rem;color:#f87171;font-weight:600;margin-bottom:0.3rem;">&#9878;&#65039; OPPOSITION LAWYER</div>'
        + escHtml(data.opposition_argument || '') + '</div>';
  html += '<div class="bubble" style="border-left:3px solid #5a7cfa;margin-top:0.5rem;">'
        + '<div style="font-size:0.72rem;color:#5a7cfa;font-weight:600;margin-bottom:0.3rem;">&#9878;&#65039; JUDGE&#8217;S VERDICT</div>'
        + escHtml(data.verdict || '') + '</div>';

  const metaParts = buildSourcesHtml(data.sources_used, false, data.low_confidence)
                  + buildRoutingHtml(data.routing);
  if (metaParts) {
    html += '<div class="meta-block">' + metaParts + '</div>';
  }

  placeholderRow.innerHTML = html;
  scrollBottom();
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # quieter logs
        print(fmt % args)

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/tools':
            self.send_json(200, {'tools': tools_registry.list_tools()})
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        return json.loads(raw)

    def do_POST(self):
        if self.path == '/chat':
            self._handle_chat()
        elif self.path == '/ask':
            self._handle_ask()
        elif self.path == '/courtroom':
            self._handle_courtroom()
        elif self.path == '/tools/run':
            self._handle_tool_run()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_tool_run(self):
        try:
            payload = self._read_json()
        except Exception as exc:
            self.send_json(400, {'error': f'bad JSON: {exc}'})
            return

        key = payload.get('key', '').strip()
        params = payload.get('params', {})
        if not key:
            self.send_json(400, {'error': 'key is required'})
            return
        if not isinstance(params, dict):
            self.send_json(400, {'error': 'params must be an object'})
            return

        try:
            result = tools_registry.call(key, params)
            self.send_json(200, {'result': result})
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def _handle_courtroom(self):
        try:
            payload = self._read_json()
        except Exception as exc:
            self.send_json(400, {'error': f'bad JSON: {exc}'})
            return

        case = payload.get('case', '').strip()
        if not case:
            self.send_json(400, {'error': 'case is required'})
            return

        try:
            result = courtroom(case)
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def _handle_chat(self):
        try:
            payload = self._read_json()
        except Exception as exc:
            self.send_json(400, {'error': f'bad JSON: {exc}'})
            return

        question = payload.get('question', '').strip()
        if not question:
            self.send_json(400, {'error': 'question is required'})
            return

        history = payload.get('history', [])
        if not isinstance(history, list):
            history = []

        lang = payload.get('lang') or None  # None → auto-detect

        try:
            result = lawyer_chat(question, history=history, lang=lang)
            self.send_json(200, {
                'answer':        result.get('answer', ''),
                'sources':       result.get('sources_used', []),
                'routing':       result.get('routing', []),
                'live_used':     result.get('live_used', False),
                'low_confidence': result.get('low_confidence', False),
                'history':       result.get('history', []),
                'detected_lang': result.get('detected_lang', 'english'),
            })
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def _handle_ask(self):
        """Legacy single-shot endpoint — preserved for compatibility."""
        try:
            payload = self._read_json()
        except Exception as exc:
            self.send_json(400, {'error': f'bad JSON: {exc}'})
            return

        question = payload.get('question', '').strip()
        if not question:
            self.send_json(400, {'error': 'question is required'})
            return

        try:
            result = answer(question)
            self.send_json(200, {
                'answer':    result.get('answer', ''),
                'sources':   result.get('sources_used', []),
                'routing':   result.get('routing', []),
                'live_used': result.get('live_used', False),
                'low_confidence': result.get('low_confidence', False),
            })
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    port = 8765
    server = HTTPServer(('', port), Handler)
    print(f'Personal Lawyer UI running on http://localhost:{port}')
    server.serve_forever()
