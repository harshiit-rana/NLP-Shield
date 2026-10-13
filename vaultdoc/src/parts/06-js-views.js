/* ══ 9 · RESTORE flow ═════════════════════════════════════════════════════ */
const Restore = {
  keys() {
    if (AppState.keySource === 'csv' && AppState.csvKeys && AppState.csvKeys.length) return AppState.csvKeys;
    const sel = AppState.selectedSessions;
    const src = sel.length ? AppState.vault.filter((s) => sel.includes(s.id)) : AppState.vault;
    const map = new Map();
    src.forEach((s) => (s.keys || []).forEach((k) => {
      if (!map.has(k.token)) map.set(k.token, { ...k }); else map.get(k.token).occurrences += k.occurrences || 1;
    }));
    if (!map.size && AppState.keys.length) AppState.keys.forEach((k) => map.set(k.token, k));
    return [...map.values()];
  },
  render() { this.renderKeySources(); this.renderResult(); },

  renderKeySources() {
    const n = this.keys().length;
    const loaded = $('#loadedCount'), summary = $('#vaultKeySummary'), vaultMode = $('#vaultMode');
    if (loaded) loaded.textContent = n + (n === 1 ? ' key' : ' keys');
    if (summary) summary.textContent = n
      ? n + ' stored key' + (n === 1 ? '' : 's') + ' available for this response.'
      : 'No stored keys yet. Redact a document or upload a CSV.';
    if (vaultMode) vaultMode.textContent = AppState.prefs.persist ? 'localStorage' : 'in-memory';

    if ($('#csvInfo') && AppState.csvKeys && AppState.csvKeys.length) {
      const cats = new Set(AppState.csvKeys.map((k) => k.category));
      $('#csvInfo').classList.remove('hidden');
      $('#csvInfo').innerHTML = '<span class="mono text-[var(--ok-soft)]">' + esc(AppState.csvName) + '</span> parsed · ' +
        AppState.csvKeys.length + ' keys · ' + cats.size + ' categories · never transmitted';
    } else if ($('#csvInfo')) $('#csvInfo').classList.add('hidden');

    this.scan();
  },

  scan() {
    const text = ($('#restoreInput').value || '');
    const toks = text.match(tokenRe()) || [];
    const uniq = [...new Set(toks)];
    const have = new Set(this.keys().map((k) => k.token));
    const ok = uniq.filter((t) => have.has(t)).length;
    const miss = uniq.length - ok;
    const box = $('#restoreScan');
    box.innerHTML = !text.trim()
      ? '<span>No tokens detected yet.</span>'
      : '<i data-lucide="scan-search" style="width:14px;height:14px;color:var(--muted)"></i><span><b class="mono text-[var(--text)]">' + toks.length +
        '</b> token' + (toks.length === 1 ? '' : 's') + ' · <b class="mono text-[var(--ok-soft)]">' + ok + '</b> resolvable' +
        (miss ? ' · <b class="mono text-[var(--warn)]">' + miss + '</b> unknown</span>' : '</span>');
    if (box) icons(box);
    return toks.length;
  },

  async run() {
    const text = ($('#restoreInput').value || '').trim();
    if (!text) return toast('Paste text that contains privacy tokens', { tone: 'warn' });
    const keys = this.keys();
    if (!keys.length) { toast('No keys loaded — select a vault session or upload keys.csv', { tone: 'warn', icon: 'key-round' }); return; }
    const btn = $('#btnRestoreRun2');
    btn.disabled = true; btn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> Restoring'; icons(btn);
    let res;
    try { res = await apiService.depseudonymize(text, AppState.keySource === 'csv' ? AppState.csvFile : null); }
    catch (e) {
      toast('Backend error — resolved locally instead: ' + e.message, { tone: 'bad', ms: 4200 });
      AppState.mode = 'mock'; UI.setMode('mock');
      res = RedactorRestore(text, keys, 'Sandbox fallback');
    }
    res.input = text;
    AppState.restore.result = res;
    this.renderResult(true);
    btn.disabled = false; btn.innerHTML = '<i data-lucide="wand-sparkles"></i> Restore'; icons(btn);
    toast(res.resolved.length + ' identities restored from ' + (res.source_label || 'vault'), { icon: 'wand-sparkles' });
  },

  renderResult(animate) {
    const res = AppState.restore.result;
    if (!res) {
      $('#paneRestored').innerHTML = '<span class="dim">Your restored response will appear here.</span>';
      $('#unresolvedNote').textContent = 'Your text stays on this device.';
      $('#btnCopyRestored').classList.add('hidden');
      $('#btnDlRestored').classList.add('hidden');
      return;
    }
    $('#paneRestored').innerHTML = res.restored_text;
    $('#btnCopyRestored').classList.remove('hidden');
    $('#btnDlRestored').classList.remove('hidden');
    $('#unresolvedNote').innerHTML = res.unresolved.length
      ? res.resolved.length + ' ' + (res.resolved.length === 1 ? 'identity' : 'identities') + ' restored · ' + res.unresolved.length + ' token' + (res.unresolved.length === 1 ? '' : 's') + ' unresolved.'
      : res.resolved.length + ' ' + (res.resolved.length === 1 ? 'identity' : 'identities') + ' restored on this device.';
    icons($('#paneRestored'));
  },

  async sample() {
    if (!AppState.keys.length && !AppState.vault.length) { RedactFlow.render(await Mock.pseudorun(null)); await sleep(180); }
    const keys = AppState.keys.length ? AppState.keys : (AppState.vault[0] && AppState.vault[0].keys) || [];
    const g = (c, i = 0) => (keys.filter((k) => k.category === c)[i] || {}).token || (c + '_001');
    $('#restoreInput').value =
      'Clause review — run summary\n\n' +
      '1. §4.2 obliges ' + g('ORGANIZATION', 1) + ' to indemnify ' + g('ORGANIZATION') + ' for data-incident claims; the ceiling sits in §4.1 next to the deductible carried by ' + g('ORGANIZATION') + '.\n' +
      '2. Breach notice must reach ' + g('EMAIL') + ' or ' + g('PHONE') + ' within 24 hours of awareness; the escalation owner is ' + g('PERSON') + '.\n' +
      '3. Payment runs 30 days from ' + g('DATE') + ', referenced to ' + g('ID') + ' and settled to the account in §2.4.\n' +
      '4. Authorised recipients of personal data (Schedule 4): ' + g('PERSON', 1) + ', ' + g('PERSON', 2) + ', ' + g('PERSON', 3) + '.\n\n' +
      'Open question for counsel: keep the notice channel tied to ' + g('PERSON') + ', or move it to ' + g('EMAIL', 1) + '?';
    this.scan();
    toast('Sample AI answer loaded', { icon: 'wand-sparkles' });
    this.run();
  }
};
function syncScroll() {
  const a = $('#paneMasked'), b = $('#paneRestored');
  if (!a || a._synced) return;
  a._synced = true;
  const link = (from, to) => from.addEventListener('scroll', () => {
    const r = from.scrollTop / Math.max(1, from.scrollHeight - from.clientHeight);
    to.scrollTop = r * (to.scrollHeight - to.clientHeight);
  }, { passive: true });
  link(a, b); link(b, a);
}

/* ══ 10 · CHAT flow ═══════════════════════════════════════════════════════ */
const MODELS = [
  { id: 'llama-3.3-70b-versatile',  label: 'Llama 3.3 · 70B',  note: 'best for long legal context', ctx: '128k' },
  { id: 'llama-3.1-8b-instant',     label: 'Llama 3.1 · 8B',   note: 'fast drafts & extraction',    ctx: '128k' },
  { id: 'gpt-oss-120b',             label: 'GPT-OSS · 120B',   note: 'deep reasoning',              ctx: '131k' },
  { id: 'qwen-2.5-32b-instruct',    label: 'Qwen 2.5 · 32B',   note: 'multilingual contracts',      ctx: '131k' }
];
const SUGGESTIONS = ['Summarise the obligations of each party', 'List every deadline with its clause reference',
  'What are the liability caps?', 'Quote §4.2 verbatim, tokens intact', 'Which named individuals appear here?'];

const Chat = {
  ctrl: null, _stream: '', _streaming: false, _id: null,
  render() {
    $('#modelGrid').innerHTML = MODELS.map((m) => {
      const on = AppState.prefs.model === m.id;
      return '<button class="model-card' + (on ? ' on' : '') + '" data-model="' + m.id + '">' +
        '<span class="flex items-center gap-2"><i data-lucide="brain-circuit" style="width:14px;height:14px;color:' + (on ? 'var(--ok-soft)' : 'var(--dim)') + '"></i>' +
        '<span class="mono text-[10px] text-[var(--dim)] ml-auto">' + m.ctx + '</span></span>' +
        '<b class="block text-[12px] font-semibold mt-1.5">' + m.label + '</b>' +
        '<span class="block text-[10.5px] text-[var(--dim)]">' + m.note + '</span>' +
        '<code class="block text-[9.5px] text-[var(--muted)] opacity-70 mt-1 truncate">' + m.id + '</code></button>';
    }).join('');
    $('#modelBadge').textContent = AppState.prefs.model;
    $('#chatEngineLabel').textContent = (AppState.mode === 'live' ? 'FastAPI /api/chat · ' : 'Sandbox engine · ') + AppState.prefs.model;
    this.renderLog(); this.payload();
    icons($('#modelGrid'));
  },
  renderLog() {
    const log = $('#chatLog'), h = AppState.chat.history;
    log.innerHTML = h.length ? h.map((m) => this.bubble(m)).join('')
      : '<div class="py-12 text-center"><div class="dz-ic" style="margin:0 auto 14px;width:36px;height:36px">' +
        '<i data-lucide="messages-square" style="width:16px;height:16px"></i></div>' +
        '<p class="text-[13.5px] font-semibold">Ask a question about your document</p>' +
        '<p class="dim mt-1.5" style="font-size:12px">Only tokenised text is sent to the model.</p></div>';
    icons(log);
    log.scrollTop = log.scrollHeight;
    const c = AppState.chat.ctx;
    $('#chatCtx').textContent = c ? c.name : 'No document attached';
    $('#chatCtxTokens').textContent = c ? (c.keys.length + ' keys') : '';
    $('#chatCtxTokens').classList.toggle('hidden', !c);
    $('#btnDetachCtx').classList.toggle('hidden', !c);
    chatBadge(c ? String(Math.max(1, Math.floor(h.length / 2))) : '');
  },
  bubble(m) {
    const who = m.role === 'user' ? 'You' : 'VaultDoc AI';
    const body = m.role === 'user' ? '<p>' + esc(m.text).replace(/\n/g, '<br>') + '</p>' : this.body(m.text);
    const foot = m.role === 'assistant'
      ? '<div class="flex items-center gap-2 mt-2.5 pt-2 border-t border-[var(--line)] text-[10px] text-[var(--dim)] flex-wrap">' +
        '<i data-lucide="' + (m.restored ? 'eye' : 'shield') + '" style="width:11px;height:11px"></i>' +
        (m.restored ? 'auto-restored locally · ' + (m.hits || 0) + ' identities resolved' : 'placeholder view · model never saw the originals') +
        '<span class="mono ml-auto mr-1">' + esc(m.model || '') + '</span>' +
        '<button class="btn btn-sm btn-ghost" data-msg-copy="' + m.id + '" title="Copy response text"><i data-lucide="copy" style="width:12px;height:12px"></i> Copy</button>' +
        '<button class="btn btn-sm btn-primary ml-1" data-msg-restore="' + m.id + '" title="Send this response to Restore tab" style="font-size:11px;padding:3px 9px;gap:5px;display:inline-flex;align-items:center"><i data-lucide="wand-sparkles" style="width:12px;height:12px"></i> Send to restore</button></div>' : '';
    return '<div class="flex gap-2.5' + (m.role === 'user' ? ' justify-end' : '') + '" data-msg="' + m.id + '">' +
      (m.role === 'assistant' ? '<span class="ava"><i data-lucide="shield"></i></span>' : '') +
      '<div class="bubble ' + m.role + (m.role === 'assistant' ? ' flex-1' : ' max-w-[88%]') + '"><p class="eyebrow mb-1.5">' + who + (m.error ? ' · <span class="txt-err">error</span>' : '') + '</p>' +
      '<div class="msg-body">' + body + '</div>' + foot + '</div></div>';
  },
  body(text) {
    const pool = (AppState.chat.ctx && AppState.chat.ctx.keys && AppState.chat.ctx.keys.length) ? AppState.chat.ctx.keys : AppState.keys;
    const keys = new Map((pool || []).map((k) => [k.token, k]));
    const auto = AppState.prefs.autoRestore;
    let hits = 0;
    const html = String(text || '').split('\n').map((line) => {
      let l = esc(line).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      if (/^\s*[•*\-]\s+/.test(line)) return '<div class="flex gap-2 my-1"><span class="text-[var(--muted)] flex-none">•</span><span>' + l.replace(/^\s*[•*\-]\s+/, '') + '</span></div>';
      const num = line.match(/^\s*(\d+)\.\s+/);
      if (num) return '<div class="flex gap-2 my-1"><span class="mono text-[var(--muted)] flex-none">' + num[1] + '.</span><span>' + l.replace(/^\s*\d+\.\s+/, '') + '</span></div>';
      if (/^\s*&gt;\s?/.test(l)) return '<div class="card-flat p-2.5 my-1 text-[12px] text-[#c9d8e8]">' + l.replace(/^\s*&gt;\s?/, '') + '</div>';
      return l ? '<p>' + l + '</p>' : '';
    }).join('');
    const out = html.replace(tokenRe(), (t) => {
      const c = catOf(t), meta = CATS[c] || { icon: 'hash', label: c };
      if (auto && keys.has(t)) {
        hits++;
        return '<mark class="restored restoring" data-tok="' + t + '" title="' + t + ' → restored from your vault">' + esc(keys.get(t).value) + '</mark>';
      }
      return '<span class="tok" data-c="' + c + '" data-tok="' + t + '"><i data-lucide="' + meta.icon + '"></i>' + t + '</span>';
    });
    AppState.chat.lastHits = hits;
    return out;
  },
  _onStream(text) {
    if (!this._streaming) return;
    this._stream = text;
    const host = $('#chatLog [data-msg="' + this._id + '"] .msg-body');
    if (host) { host.innerHTML = this.body(text); icons(host); const log = $('#chatLog'); log.scrollTop = log.scrollHeight; }
  },
  async typeOut(text) {
    this._streaming = true;
    const step = Math.max(4, Math.ceil(text.length / 150));
    for (let i = 0; i < text.length; i += step) { this._onStream(text.slice(0, i + step)); await sleep(13); }
    this._onStream(text);
    this._streaming = false;
  },
  focus() { const i = $('#chatInput'); if (i && !i.value) setTimeout(() => i.focus(), 120); },
  setAuto(v) {
    AppState.prefs.autoRestore = !!v;
    const sw = $('#autoRestoreSwitch');
    sw.classList.toggle('on', AppState.prefs.autoRestore);
    sw.setAttribute('aria-checked', String(AppState.prefs.autoRestore));
    $('#autoRestoreState').textContent = AppState.prefs.autoRestore ? 'ON' : 'OFF';
    $('#autoRestoreState').style.color = AppState.prefs.autoRestore ? 'var(--ok-soft)' : 'var(--dim)';
    this.renderLog();
    if (AppState.prefs.autoRestore) $$('mark.restored').forEach((n) => { n.classList.remove('restoring'); void n.offsetWidth; n.classList.add('restoring'); });
    persist();
  },
  toggleAuto() {
    this.setAuto(!AppState.prefs.autoRestore);
    if (AppState.chat.history.some((m) => m.role === 'assistant'))
      toast(AppState.prefs.autoRestore ? 'Magic switch on — tokens now read as real identities' : 'Magic switch off — placeholders only',
        { icon: AppState.prefs.autoRestore ? 'sparkles' : 'eye-off', tone: AppState.prefs.autoRestore ? 'ok' : 'warn' });
  },
  async send(forced) {
    const inp = $('#chatInput');
    const text = (forced || inp.value || '').trim();
    if (!text || AppState.chat.busy) return;
    if (!AppState.chat.ctx) toast('No document attached — the answer will be generic', { tone: 'warn', icon: 'link-off' });
    inp.value = ''; inp.style.height = 'auto'; $('#chatChars').textContent = '0 chars';
    const push = (o) => { o.id = uid('m'); AppState.chat.history.push(o); return o; };
    push({ role: 'user', text });
    const ai = push({ role: 'assistant', text: '', model: AppState.prefs.model });
    this._id = ai.id;
    AppState.chat.busy = true;
    this.renderLog();
    const host = $('#chatLog [data-msg="' + ai.id + '"] .msg-body');
    if (host) host.innerHTML = '<span class="typing flex items-center gap-1"><i></i><i></i><i></i></span>';
    this._streaming = true;
    const ctrl = new AbortController(); this.ctrl = ctrl;
    try {
      const res = await apiService.chatWithDocument({
        message: text, context: (AppState.chat.ctx && AppState.chat.ctx.text) || '', apiKey: AppState.prefs.groqKey,
        model: AppState.prefs.model, autoRestore: AppState.prefs.autoRestore,
        temperature: AppState.prefs.temp, maxTokens: AppState.prefs.maxTok,
        system: $('#systemPrompt').value, history: AppState.chat.history.slice(0, -2), signal: ctrl.signal
      });
      const reply = (res.reply || '').trim() || 'The model returned an empty response.';
      if (res.source === 'mock') await this.typeOut(reply); else this._streaming = false;
      ai.text = reply; ai.raw = reply;
      ai.restored = AppState.prefs.autoRestore; ai.hits = AppState.chat.lastHits || 0;
      ai.model = (res.source === 'live' ? 'FastAPI · ' : res.source === 'groq' ? 'Groq direct · ' : 'sandbox · ') + AppState.prefs.model;
    } catch (err) {
      ai.error = true;
      ai.text = (err && err.name === 'AbortError') ? 'Request cancelled.'
        : 'Request failed: ' + err.message + '\n\nSandbox mode keeps working — if you expected the FastAPI proxy, open **Backend connection** and switch mode to Live.';
    }
    this._streaming = false; this.ctrl = null; AppState.chat.busy = false;
    this.renderLog(); this.payload(); persist();
  },
  payload() {
    const body = {
      message: '<user prompt>',
      context: ((AppState.chat.ctx && AppState.chat.ctx.text) || '').slice(0, 40) + (AppState.chat.ctx ? '…' : 'none'),
      model: AppState.prefs.model, temperature: AppState.prefs.temp, max_tokens: AppState.prefs.maxTok,
      auto_restore: AppState.prefs.autoRestore,
      api_key: AppState.prefs.groqKey ? 'gsk_••' + AppState.prefs.groqKey.slice(-4) : null
    };
    $('#payloadPreview').innerHTML =
      '<span class="text-[10px] text-[var(--dim)]">body of </span><span class="text-[var(--ok-soft)]">POST</span> ' +
      esc(AppState.mode === 'live' ? url('/api/chat') : '/api/chat · sandbox') +
      '<br>' + esc(JSON.stringify(body, null, 1)).replace(/\n/g, '<br>').replace(/ {2}/g, '&nbsp;&nbsp;');
    const keyChip = $('#keyChipLabel');
    if (keyChip) {
      keyChip.textContent = AppState.prefs.groqKey ? 'Key ••' + AppState.prefs.groqKey.slice(-4) : 'No key';
      keyChip.style.color = AppState.prefs.groqKey ? 'var(--ok-soft)' : 'var(--muted)';
    }
    $('#tempVal').textContent = Number(AppState.prefs.temp).toFixed(2);
    $('#maxTokVal').textContent = AppState.prefs.maxTok;
    if (document.activeElement !== $('#groqKey')) $('#groqKey').value = AppState.prefs.groqKey || '';
  }
};

/* ══ 11 · persistence ═════════════════════════════════════════════════════ */
function persist() {
  const p = { ...AppState.prefs };
  if (!p.persist) delete p.groqKey;
  try {
    localStorage.setItem(CONFIG.LS_PREFS, JSON.stringify({ p, apiBase: CONFIG.API_BASE, mode: CONFIG.MODE }));
    if (p.persist) localStorage.setItem(CONFIG.LS_VAULT, JSON.stringify(AppState.vault.slice(0, 8)));
  } catch (_) {}
}
function restorePrefs() {
  let j = {};
  try { j = JSON.parse(localStorage.getItem(CONFIG.LS_PREFS) || '{}'); } catch (_) {}
  if (j.p) Object.assign(AppState.prefs, j.p);
  if (j.apiBase !== undefined) CONFIG.API_BASE = j.apiBase;
  if (j.mode) { CONFIG.MODE = j.mode; if (j.mode !== 'auto') UI.setMode(j.mode); }
  $$('#modeTabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === CONFIG.MODE));
  try {
    const v = JSON.parse(localStorage.getItem(CONFIG.LS_VAULT) || '[]');
    if (Array.isArray(v) && v.length && AppState.prefs.persist) AppState.vault = v.map((s) => ({ ...s, keys: (s.keys || []).map(normaliseKey) }));
  } catch (_) {}
  $('#apiBaseInput').value = CONFIG.API_BASE || '';
  $('#tempRange').value = AppState.prefs.temp;
  $('#maxTokRange').value = AppState.prefs.maxTok;
  $('#persistSwitch').classList.toggle('on', !!AppState.prefs.persist);
  $('#directSwitch').classList.toggle('on', !!AppState.prefs.direct);
  $('#groqKey').value = AppState.prefs.groqKey || '';
  $('#groqKey').type = 'password';
}

/* ══ 12 · wiring ══════════════════════════════════════════════════════════ */
function bind() {
  $$('.side-item[data-view]').forEach((b) => b.addEventListener('click', () => UI.setView(b.dataset.view)));
  $('#btnVault').addEventListener('click', () => UI.openDrawer('vaultDrawer'));
  $('#btnCloseVault').addEventListener('click', UI.closeOverlays);
  $('#overlay').addEventListener('click', UI.closeOverlays);
  $('#cmdkOverlay').addEventListener('click', UI.closeOverlays);
  $$('[data-close]').forEach((b) => b.addEventListener('click', UI.closeOverlays));
  $('#btnSettings').addEventListener('click', () => {
    const panel = $('#connectionDetails');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });

  $('#sessionList').addEventListener('click', (e) => {
    const sel = e.target.closest('[data-sel]'), dl = e.target.closest('[data-dl]'), lc = e.target.closest('[data-loadchat]');
    if (sel) {
      const id = sel.dataset.sel;
      AppState.selectedSessions = AppState.selectedSessions.includes(id) ? AppState.selectedSessions.filter((x) => x !== id) : [...AppState.selectedSessions, id];
      Vault.render(); Restore.renderKeySources();
      toast(AppState.selectedSessions.length ? 'Keys scoped for restoration' : 'No session selected — the whole vault is used', { icon: 'key-round' });
    }
    if (dl) { const s = AppState.vault.find((x) => x.id === dl.dataset.dl); download('keys-' + s.document.replace(/\.[^.]+$/, '') + '.csv', csvFor(s.keys, s.document), 'text/csv'); }
    if (lc) {
      const s = AppState.vault.find((x) => x.id === lc.dataset.loadchat);
      AppState.chat.ctx = { name: s.document + ' · keys only', text: '', keys: s.keys, runId: s.id, keysOnly: true };
      AppState.selectedSessions = [s.id];
      UI.closeOverlays(); UI.setView('chat');
      toast('Vault keys scoped to the chat session', { icon: 'key-round' });
    }
  });
  $('#btnVaultPurge').addEventListener('click', () => {
    AppState.vault = []; AppState.selectedSessions = []; AppState.csvKeys = null;
    try { localStorage.removeItem(CONFIG.LS_VAULT); } catch (_) {}
    Vault.render(); Restore.renderKeySources(); UI.closeOverlays();
    toast('Vault emptied — keys deleted from this browser', { tone: 'warn', icon: 'trash-2' });
  });

  /* backend connection */
  $('#modeTabs').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-mode]'); if (!b) return;
    $$('#modeTabs button').forEach((x) => x.classList.toggle('on', x === b));
    CONFIG.MODE = b.dataset.mode;
    if (b.dataset.mode === 'auto') { const r = await apiService.probe(); UI.setMode(r.ok ? 'live' : 'mock'); setProbe(r); }
    else UI.setMode(b.dataset.mode);
    persist();
  });
  $('#btnProbe').addEventListener('click', async () => {
    CONFIG.API_BASE = $('#apiBaseInput').value.trim().replace(/\/+$/, '');
    $('#probeResult').innerHTML = '<span class="dot live"></span> Probing ' + esc(url(CONFIG.HEALTH)) + ' …';
    setProbe(await apiService.probe());
    persist();
  });
  $('#btnSaveConn').addEventListener('click', () => {
    CONFIG.API_BASE = $('#apiBaseInput').value.trim().replace(/\/+$/, '');
    persist(); UI.closeOverlays();
    toast('Connection applied · base “' + (CONFIG.API_BASE || 'same origin') + '”', { icon: 'plug-zap' });
    Vault.loadFromBackend();
  });

  /* redact workspace */
  $('#btnNewDoc').addEventListener('click', () => {
    $('#redactResult').classList.add('hidden'); $('#redactWork').classList.remove('active'); $('#redactEmpty').classList.remove('hidden');
    AppState.keys = []; AppState.spans = []; AppState.scrubbed = ''; AppState.original = ''; AppState.document = null; AppState.stats = null;
    AppState.chat.ctx = null;
    Restore.renderKeySources();
    Chat.renderLog();
    toast('Workspace reset — key bundle stays in the vault', { icon: 'rotate-ccw' });
  });
  $('#btnReveal').addEventListener('click', () => RedactFlow.reveal());
  $('#redactResult').addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]'), peek = e.target.closest('[data-peek]'), chip = e.target.closest('#catChips .chip');
    if (act && act.dataset.act === 'reveal') RedactFlow.reveal();
    if (act && act.dataset.act === 'copy-scrubbed') copyText(AppState.scrubbed, 'Scrubbed text copied');
    if (act && act.dataset.act === 'dl-txt') download(((AppState.document && AppState.document.name) || 'document').replace(/\.[^.]+$/, '') + '.redacted.txt', AppState.scrubbed);
    if (chip) { AppState.activeCat = AppState.activeCat === chip.dataset.c ? null : chip.dataset.c; RedactFlow.applyFilter(); }
    if (peek) {
      const t = $('#paneScrubbed [data-tok="' + peek.dataset.peek + '"]');
      if (t) { t.scrollIntoView({ block: 'center', behavior: 'smooth' }); t.classList.remove('tok-hit'); void t.offsetWidth; t.classList.add('tok-hit'); }
    }
  });
  $('#keySearch').addEventListener('input', (e) => RedactFlow.paintKeys(e.target.value));
  $('#btnDownloadCsv').addEventListener('click', () => AppState.keys.length
    ? download('vaultdoc-keys-' + (AppState.runId || 'run') + '.csv', csvFor(AppState.keys, AppState.document && AppState.document.name), 'text/csv')
    : toast('Nothing redacted yet', { tone: 'warn' }));
  $('#btnSendToChat').addEventListener('click', RedactFlow.sendToChat);

  /* restore workspace */
  $('#srcTabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-src]'); if (!b) return;
    AppState.keySource = b.dataset.src;
    $$('#srcTabs button').forEach((x) => x.classList.toggle('on', x === b));
    $('#srcVault').classList.toggle('hidden', AppState.keySource !== 'vault');
    $('#srcCsv').classList.toggle('hidden', AppState.keySource !== 'csv');
    Restore.renderKeySources();
  });
  $('#btnCsvPick').addEventListener('click', (e) => { e.preventDefault(); $('#csvInput').click(); });
  $('#csvInput').addEventListener('change', () => { const f = $('#csvInput').files[0]; if (f) RestoreCsv.load(f); $('#csvInput').value = ''; });
  const csvz = $('#csvDrop');
  ['dragenter', 'dragover'].forEach((ev) => csvz.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); csvz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => csvz.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); csvz.classList.remove('over'); }));
  csvz.addEventListener('drop', (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) RestoreCsv.load(f); });
  $('#restoreInput').addEventListener('input', debounce(() => Restore.scan(), 200));
  $('#btnRestoreRun2').addEventListener('click', () => Restore.run());
  $('#btnClearRestore').addEventListener('click', () => { $('#restoreInput').value = ''; AppState.restore.result = null; Restore.render(); });
  $('#btnPaste').addEventListener('click', async () => {
    try { $('#restoreInput').value = await navigator.clipboard.readText(); Restore.scan(); toast('Pasted from clipboard', { icon: 'clipboard-paste' }); }
    catch (_) { toast('Clipboard read blocked — paste manually', { tone: 'warn' }); }
  });
  $('#restoreResult').addEventListener('mouseover', (e) => hit(e.target.closest('[data-tok]') && e.target.closest('[data-tok]').dataset.tok));
  $('#restoreResult').addEventListener('mouseleave', () => hit(null));
  $('#btnCopyRestored').addEventListener('click', () => {
    const r = AppState.restore.result; if (!r) return toast('Nothing restored yet', { tone: 'warn' });
    copyText(r.plain || '', 'Restored text copied — handle it as confidential');
  });
  $('#btnDlRestored').addEventListener('click', () => {
    const r = AppState.restore.result; if (!r) return toast('Nothing restored yet', { tone: 'warn' });
    download('restored-answer.txt', r.plain || '');
  });

  /* chat workspace */
  $('#autoRestoreSwitch').addEventListener('click', () => Chat.toggleAuto());
  $('#autoRestoreSwitch').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); Chat.toggleAuto(); } });
  const inp = $('#chatInput');
  inp.addEventListener('input', () => {
    inp.style.height = 'auto'; inp.style.height = Math.min(132, inp.scrollHeight) + 'px';
    $('#chatChars').textContent = inp.value.length + ' chars';
  });
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); Chat.send(); } });
  $('#btnSend').addEventListener('click', () => Chat.send());
  $('#chatLog').addEventListener('click', (e) => {
    const c = e.target.closest('[data-msg-copy]');
    if (c) {
      const m = AppState.chat.history.find((x) => x.id === c.dataset.msgCopy);
      if (m) copyText(m.raw || m.text, 'Answer copied');
      return;
    }
    const r = e.target.closest('[data-msg-restore]');
    if (r) {
      const m = AppState.chat.history.find((x) => x.id === r.dataset.msgRestore);
      if (m && (m.raw || m.text)) {
        $('#restoreInput').value = m.raw || m.text;
        UI.setView('restore');
        Restore.scan();
        Restore.run();
        toast('Sent to Restore tab — restoring identities', { icon: 'wand-sparkles' });
      }
      return;
    }
  });
  $('#modelGrid').addEventListener('click', (e) => {
    const b = e.target.closest('[data-model]'); if (!b) return;
    AppState.prefs.model = b.dataset.model; Chat.render(); persist();
    toast('Model set to ' + b.dataset.model, { icon: 'cpu' });
  });
  $('#groqKey').addEventListener('input', debounce((e) => { AppState.prefs.groqKey = e.target.value.trim(); Chat.payload(); persist(); }, 350));
  $('#btnPeekKey').addEventListener('click', () => { const k = $('#groqKey'); k.type = k.type === 'password' ? 'text' : 'password'; });
  [['#persistSwitch', 'persist'], ['#directSwitch', 'direct']].forEach(([sel, key]) => {
    $(sel).addEventListener('click', () => {
      AppState.prefs[key] = !AppState.prefs[key];
      $(sel).classList.toggle('on', AppState.prefs[key]);
      $(sel).setAttribute('aria-checked', String(AppState.prefs[key]));
      if (key === 'direct' && AppState.prefs.direct) toast('Direct Groq mode — the key travels from your browser', { tone: 'warn', icon: 'alert-triangle' });
      persist();
    });
  });
  $('#tempRange').addEventListener('input', (e) => { AppState.prefs.temp = parseFloat(e.target.value); Chat.payload(); });
  $('#tempRange').addEventListener('change', persist);
  $('#maxTokRange').addEventListener('input', (e) => { AppState.prefs.maxTok = parseInt(e.target.value, 10); Chat.payload(); });
  $('#maxTokRange').addEventListener('change', persist);
  $('#btnCopyPayload').addEventListener('click', () => copyText(JSON.stringify({
    endpoint: AppState.mode === 'live' ? url('/api/chat') : '/api/chat', model: AppState.prefs.model,
    temperature: AppState.prefs.temp, max_tokens: AppState.prefs.maxTok, auto_restore: AppState.prefs.autoRestore,
    context_chars: ((AppState.chat.ctx && AppState.chat.ctx.text) || '').length, system: $('#systemPrompt').value
  }, null, 2), 'Request body copied'));
  $('#btnDetachCtx').addEventListener('click', () => { AppState.chat.ctx = null; Chat.renderLog(); Chat.payload(); toast('Context detached', { icon: 'link-off' }); });

  /* command palette */
  $('#cmdkInput').addEventListener('input', (e) => { UI.cmdk.sel = 0; UI.cmdk.list(e.target.value); });
  $('#cmdkList').addEventListener('click', (e) => { const r = e.target.closest('[data-n]'); if (r) UI.cmdk.exec(+r.dataset.n); });

  document.addEventListener('keydown', (e) => {
    const ae = document.activeElement || {};
    const typing = /INPUT|TEXTAREA/.test(ae.tagName || '') || ae.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); UI.cmdk.open ? UI.closeOverlays() : UI.cmdk.show(); return; }
    if (UI.cmdk.open) {
      if (e.key === 'Escape') { UI.closeOverlays(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); const n = (UI.cmdk.filtered || []).length || 1;
        UI.cmdk.sel = (UI.cmdk.sel + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
        UI.cmdk.list($('#cmdkInput').value); return;
      }
      if (e.key === 'Enter') { e.preventDefault(); UI.cmdk.exec(UI.cmdk.sel); return; }
    }
    if (e.key === 'Escape') UI.closeOverlays();
    if (!typing && ['1', '2', '3'].includes(e.key)) UI.setView(['redact', 'restore', 'chat'][+e.key - 1]);
    if (!typing && e.key.toLowerCase() === 'r' && AppState.view === 'redact' && AppState.keys.length) RedactFlow.reveal();
    if (!typing && e.key === '/') { e.preventDefault(); UI.cmdk.show(); }
  });
}
function setProbe(r) {
  const el = $('#probeResult'); if (!el) return;
  el.innerHTML = '<span class="dot' + (r.ok ? '' : ' warn') + '"></span> ' +
    (r.ok ? 'OK · HTTP ' + r.status + ' at ' + esc(url(CONFIG.HEALTH)) : 'Unreachable · ' + esc(r.reason || 'no API on this origin'));
  icons(el);
}
const RestoreCsv = {
  async load(file) {
    if (!/\.csv$/i.test(file.name) && !/csv/i.test(file.type || '')) return toast('That is not a CSV file', { tone: 'bad' });
    const keys = Mock.parseCsv(await file.text());
    if (!keys.length) return toast('No valid “token,category,value” rows found', { tone: 'bad', ms: 4200 });
    AppState.csvKeys = keys; AppState.csvName = file.name; AppState.csvFile = file;
    AppState.keySource = 'csv';
    $$('#srcTabs button').forEach((b) => b.classList.toggle('on', b.dataset.src === 'csv'));
    $('#srcVault').classList.add('hidden'); $('#srcCsv').classList.remove('hidden');
    Restore.renderKeySources();
    toast(keys.length + ' keys loaded from ' + file.name, { icon: 'key-round' });
  }
};
function hit(tok) { $$('#restoreResult [data-tok]').forEach((n) => n.classList.toggle('on-hit', !!tok && n.dataset.tok === tok)); }
function debounce(fn, ms = 200) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* ══ 13 · boot ════════════════════════════════════════════════════════════ */
(async function boot() {
  restorePrefs();
  bind();
  UI.initDrop();
  if (AppState.prefs.autoRestore) Chat.setAuto(true);
  UI.setView('redact');
  icons();
  const probe = await apiService.probe();
  if (CONFIG.MODE === 'auto') UI.setMode(probe.ok ? 'live' : 'mock');
  setProbe(probe);
  await Vault.loadFromBackend();
  Vault.render();
  Restore.render();
  Chat.render();
  console.info('%cVaultDoc ' + CONFIG.VERSION, 'color:#fafafa;font-weight:700',
    'ready · set CONFIG.API_BASE (or use the connection modal) to attach FastAPI');
})();
</script>
</body>
</html>
