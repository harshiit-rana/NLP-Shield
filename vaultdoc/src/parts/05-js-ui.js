/* ══ 6 · utilities ═══════════════════════════════════════════════════════ */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const uid = (p) => p + '_' + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3);
const fmtNum = (n) => (Number(n) || 0).toLocaleString('en-US');
const fmtBytes = (b) => b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(2) + ' MB';
function chatBadge(v) { const s = $('#railChat [data-badge]'); if (s) s.textContent = v || ''; }
const isoAgo = (v, u) => new Date(Date.now() - v * (u === 'h' ? 36e5 : 864e5)).toISOString();
function timeAgo(iso) {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return Math.round(d / 60) + 'm ago';
  if (d < 86400) return Math.round(d / 3600) + 'h ago';
  return Math.round(d / 86400) + 'd ago';
}
function fakeHash(str) {
  let h = 0x811c9dc5, out = '';
  for (let round = 0; round < 8; round++) {
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i) + round; h = Math.imul(h, 0x01000193) >>> 0; }
    out += h.toString(16).padStart(8, '0');
  }
  return out + out.slice(0, 32);
}
const catOf = (token) => (String(token).split('_')[0] || 'ID').toUpperCase();

/* ── icons: real Lucide set + graceful offline shim ───────────────────────── */
const FALLBACK_ICONS = {
  'check': 'M20 6 9 17l-5-5', 'x': 'M18 6 6 6l12 12M6 6l12 12', 'copy': 'M9 9h11v11H9zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  'download': 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3', 'upload': 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  'send-horizontal': 'M22 2 11 13M22 2 15 22l-4-9-9-4 20-7z', 'shield': 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  'lock': 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4', 'eye': 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z',
  'file-text': 'M14 2v6h6M8 13h8M8 17h8M8 9h2', 'sparkles': 'M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z',
  'wand-sparkles': 'M12 3l1.6 3.9L17.5 8.5l-3.9 1.6L12 14l-1.6-3.9L6.5 8.5l3.9-1.6z', 'key-round': 'M2 18a4 4 0 1 0 8 0 4 4 0 0 0-8 0zM10 18h12M18 18v-4',
  'plus': 'M12 5v14M5 12h14', 'trash-2': 'M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15', 'search': 'M11 11m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0M20 20l-4-4',
  'command': 'M18 3a3 3 0 0 1 3 3v12a3 3 0 1 1-3-3H6a3 3 0 1 1 3 3V6a3 3 0 1 1-3 3h12'
};
function icons(root = document) {
  if (window.lucide && lucide.createIcons) { try { lucide.createIcons({ root }); } catch (_) { shim(root); } }
  else shim(root);
}
function shim(root = document) {
  $$('[data-lucide]', root).forEach((el) => {
    if (el.dataset.done) return; el.dataset.done = '1';
    const p = FALLBACK_ICONS[el.dataset.lucide] || FALLBACK_ICONS.shield;
    const svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" style="width:100%;height:100%"><path d="' + p + '"/></svg>';
    const i = document.createElement('i'); i.setAttribute('data-lucide', el.dataset.lucide);
    i.className = el.className; i.innerHTML = svg; el.replaceWith(i);
  });
}

/* ── toasts / clipboard / files ───────────────────────────────────────────── */
function toast(msg, opt = {}) {
  const el = document.createElement('div');
  el.className = 'toast';
  const tone = opt.tone === 'warn' ? 'var(--warn)' : opt.tone === 'bad' ? 'var(--err)' : 'var(--ok-soft)';
  el.innerHTML = '<span class="ic" style="background:color-mix(in srgb,' + tone + ' 16%,transparent);color:' + tone + '">' +
    '<i data-lucide="' + (opt.icon || 'check') + '"></i></span><span class="flex-1">' + esc(msg) + '</span>' +
    (opt.action ? '<button class="btn btn-sm" data-toast-act>' + esc(opt.action) + '</button>' : '');
  $('#toasts').appendChild(el); icons(el);
  if (opt.action && opt.onAction) el.querySelector('[data-toast-act]').addEventListener('click', () => { opt.onAction(); close(); });
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 320); };
  setTimeout(close, opt.ms || 2900);
}
async function copyText(text, label = 'Copied to clipboard') {
  try { await navigator.clipboard.writeText(text); toast(label, { icon: 'clipboard-check' }); }
  catch (_) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast(label, { icon: 'clipboard-check' }); } catch (e) { toast('Clipboard blocked by the browser', { tone: 'bad' }); }
    ta.remove();
  }
}
function download(name, text, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([text], { type: mime }); const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast(name + ' saved', { icon: 'file-down' });
}
const csvFor = (keys, doc) => 'token,category,value,occurrences,document\n' + keys.map((k) =>
  [k.token, k.category, '"' + String(k.value).replace(/"/g, '""') + '"', k.occurrences ?? 1, doc || '']
    .join(',')).join('\n') + '\n';

/* ══ 7 · UI ═══════════════════════════════════════════════════════════════ */
const UI = {
  /* ── chrome ───────────────────────────────────────────────────────────── */
  setView(v) {
    AppState.view = v;
    $$('.view').forEach((s) => s.classList.toggle('on', s.id === 'view-' + v));
    $$('.side-item[data-view]').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
    $('#viewTitle').textContent = { redact: 'Redact', restore: 'Restore', chat: 'AI Chat' }[v];
    if (v === 'chat') { Chat.render(); Chat.focus(); }
    if (v === 'restore') Restore.render();
    revealObserve();
  },

  setMode(mode, announce) {
    AppState.mode = mode; CONFIG.MODE = mode === 'live' ? 'live' : 'mock';
    const modeLabel = $('#modeLabel'), modeDot = $('#modeDot');
    if (modeLabel) modeLabel.textContent = mode === 'live' ? 'Live · FastAPI' : 'Sandbox engine';
    if (modeDot) modeDot.className = 'dot' + (mode === 'live' ? ' live' : ' off');
    const fs = $('#footStatus'), fd = $('#footDot');
    if (fs) fs.textContent = mode === 'live' ? 'FastAPI connected' : 'Sandbox engine';
    if (fd) fd.className = 'dot' + (mode === 'live' ? ' live' : ' off');
    if (announce) toast(mode === 'live' ? 'Live backend attached' : 'Running in sandbox', { icon: mode === 'live' ? 'server' : 'flask-conical' });
  },

  /* ── dropzone ─────────────────────────────────────────────────────────── */
  initDrop() {
    const dz = $('#dropzone'), input = $('#fileInput');
    const pick = () => input.click();
    $('#btnBrowse').addEventListener('click', (e) => { e.stopPropagation(); pick(); });
    dz.addEventListener('click', (e) => { if (e.target.closest('button')) return; pick(); });
    dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    input.addEventListener('change', () => { if (input.files[0]) RedactFlow.run(input.files[0]); input.value = ''; });
    const dragging = (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) return true;
      return false;
    };
    ['dragenter', 'dragover'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (!dragging(e) || e.target.closest('#csvDrop')) return;
      e.preventDefault();
      if (AppState.view === 'redact') {
        dz.classList.add('over');
        if ($('#redactResult').classList.contains('hidden')) $('#redactEmpty').classList.remove('hidden');
      }
    }));
    ['dragleave', 'dragend'].forEach((ev) => document.addEventListener(ev, () => dz.classList.remove('over')));
    document.addEventListener('drop', (e) => {
      if (e.target.closest('#csvDrop')) return;                      // keys.csv has its own handler
      e.preventDefault(); dz.classList.remove('over');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (!f) return;
      if (AppState.view !== 'redact') { UI.setView('redact'); setTimeout(() => RedactFlow.run(f), 240); }
      else RedactFlow.run(f);
    });
    const sample = () => RedactFlow.run(null, true);
    $('#btnSample').addEventListener('click', (e) => { e.stopPropagation(); sample(); });
    const bi = $('#btnSampleInline'); if (bi) bi.addEventListener('click', (e) => { e.stopPropagation(); sample(); });
  },

  /* ── overlays ─────────────────────────────────────────────────────────── */
  openDrawer(id) { $('#' + id).classList.add('on'); $('#overlay').classList.add('on'); },
  closeOverlays() {
    ['vaultDrawer', 'connModal', 'cmdk'].forEach((id) => { const el = $('#' + id); if (el) el.classList.remove('on'); });
    const overlay = $('#overlay'), paletteOverlay = $('#cmdkOverlay');
    if (overlay) overlay.classList.remove('on');
    if (paletteOverlay) paletteOverlay.classList.remove('on');
  },

  /* ── command palette ──────────────────────────────────────────────────── */
  cmdk: {
    open: false, sel: 0, items: [],
    build() {
      return [
        { t: 'Go to · Redact documents', i: 'scan-line', k: '1', run: () => UI.setView('redact') },
        { t: 'Go to · Restore identities', i: 'wand-sparkles', k: '2', run: () => UI.setView('restore') },
        { t: 'Go to · Confidential AI chat', i: 'messages-square', k: '3', run: () => UI.setView('chat') },
        { t: 'Upload a file now', i: 'upload', run: () => { UI.setView('redact'); $('#fileInput').click(); } },
        { t: 'Redact the live sample contract', i: 'sparkles', run: () => RedactFlow.run(null, true) },
        { t: 'Download keys.csv for this run', i: 'file-down', run: () => AppState.keys.length ? download('vaultdoc-keys-' + (AppState.runId || 'run') + '.csv', csvFor(AppState.keys, AppState.document?.name), 'text/csv') : toast('Nothing redacted yet', { tone: 'warn' }) },
        { t: 'Copy scrubbed text', i: 'copy', run: () => AppState.scrubbed ? copyText(AppState.scrubbed, 'Scrubbed text copied') : toast('Nothing to copy yet', { tone: 'warn' }) },
        { t: 'Send scrubbed text to chat', i: 'send-horizontal', run: () => RedactFlow.sendToChat() },
        { t: 'Toggle magic auto-restoration', i: 'sparkles', run: () => Chat.toggleAuto() },
        { t: 'Paste a sample AI answer to restore', i: 'clipboard-paste', run: () => Restore.sample() },
        { t: 'Open key vault', i: 'key-round', run: () => UI.openDrawer('vaultDrawer') },
        { t: 'Backend connection settings', i: 'plug-zap', run: () => { UI.setView('chat'); $('#chatAdvanced').open = true; $('#connectionDetails').classList.remove('hidden'); } },
        { t: 'Reveal originals for 8 seconds', i: 'eye', run: () => RedactFlow.reveal() },
        { t: 'Purge all keys from this browser', i: 'eraser', run: () => RedactFlow.purge() }
      ];
    },
    show() {
      this.open = true; this.sel = 0; this.items = this.build();
      $('#cmdkOverlay').classList.add('on'); $('#cmdk').classList.add('on');
      const inp = $('#cmdkInput'); inp.value = ''; this.list(''); setTimeout(() => inp.focus(), 40); icons($('#cmdk'));
    },
    list(q) {
      const items = this.items.filter((i) => !q || i.t.toLowerCase().includes(q.toLowerCase()));
      this.sel = Math.min(this.sel, Math.max(0, items.length - 1));
      $('#cmdkList').innerHTML = items.length ? items.map((i, n) =>
        '<div class="cmd-row' + (n === this.sel ? ' sel' : '') + '" data-n="' + n + '"><i data-lucide="' + i.i +
        '" style="width:15px;height:15px;color:var(--muted)"></i><span class="flex-1 text-[12.5px]">' + esc(i.t) +
        '</span>' + (i.k ? '<span class="kbd">' + i.k + '</span>' : '') + '</div>').join('')
        : '<p class="p-3 text-[12px] text-[var(--dim)]">No command matches “' + esc(q) + '”.</p>';
      this.filtered = items; icons($('#cmdkList'));
    },
    exec(n) { const it = (this.filtered || [])[n]; if (!it) return; UI.closeOverlays(); it.run(); }
  }
};

/* ── scroll reveal observer ──────────────────────────────────────────────── */
let RO;
function revealObserve() {
  RO = RO || new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); RO.unobserve(e.target); } }), { threshold: .12 });
  $$('[data-reveal]:not(.in)').forEach((n) => RO.observe(n));
}

/* ── highlight painters ─────────────────────────────────────────────────── */
function paintOriginal(text, spans) {
  let out = '', cur = 0;
  for (const s of spans) {
    out += esc(text.slice(cur, s.start));
    out += '<span class="red" data-c="' + s.cat + '" data-tok="' + s.token + '" title="' + esc(s.value) + '  →  ' + s.token + '">' + esc(text.slice(s.start, s.end)) + '</span>';
    cur = s.end;
  }
  return out + esc(text.slice(cur));
}
function paintTokens(text, opts = {}) {
  const re = new RegExp(CONFIG.TOKEN_RE.source, 'g');
  let i = 0;
  return esc(text).replace(re, (t) => {
    const c = catOf(t);
    const meta = CATS[c] || { icon: 'hash', label: c };
    const dim = opts.only && opts.only !== c ? ' dim' : '';
    const delay = Math.min(i++, 34) * 14;
    const known = opts.values && opts.values.has(t) ? 'key in vault' : 'no key in scope';
    return '<span class="tok tok-hit' + dim + '" data-c="' + c + '" data-tok="' + t + '" title="' + esc(meta.label) + ' · ' + t + ' · ' + known +
      '" style="animation-delay:' + delay + 'ms"><i data-lucide="' + meta.icon + '"></i>' + t + '</span>';
  });
}

/* ══ 8 · REDACT flow ══════════════════════════════════════════════════════ */
const STEPS = [
  { i: 'file-search', t: 'Parse & flatten', n: 'Layout-aware text extraction' },
  { i: 'radar', t: 'Detect entities', n: '9 detector classes · regex + NER' },
  { i: 'shuffle', t: 'Tokenise', n: 'Stable mapping, format preserving' },
  { i: 'stamp', t: 'Seal audit trail', n: 'run id, hash, key bundle' }
];
const RedactFlow = {
  async run(file, isSample) {
    if (isSample) file = null;
    const bytes = file ? file.size : SAMPLE_DOC.length;
    if (bytes > CONFIG.MAX_MB * 1048576) return toast('File exceeds ' + CONFIG.MAX_MB + ' MB', { tone: 'bad', icon: 'alert-triangle' });

    $('#redactEmpty').classList.add('hidden'); $('#redactWork').classList.add('active'); $('#redactResult').classList.add('hidden');
    const busy = $('#redactBusy'); busy.classList.remove('hidden');
    $('#busyName').textContent = file ? file.name : 'sample-contract.pdf';
    $('#busySteps').innerHTML = STEPS.map((s, i) =>
      '<div class="step" data-s="' + i + '"><span class="num">' + (i + 1) + '</span><i data-lucide="' + s.i +
      '" style="width:14px;height:14px"></i><div class="min-w-0"><p class="text-[12px] font-semibold truncate">' + s.t +
      '</p><p class="text-[10.5px] text-[var(--dim)] truncate">' + s.n + '</p></div></div>').join('');
    const bar = $('#busyBar'); bar.classList.add('striped'); bar.style.width = '6%';
    icons(busy);

    const job = apiService.uploadAndPseudonymize(file);
    let step = 0;
    const walker = setInterval(() => {
      step = Math.min(step + 1, STEPS.length);
      $$('.step', busy).forEach((el, i) => { el.classList.toggle('done', i < step - 1); el.classList.toggle('active', i === step - 1); });
      $('#busyPct').textContent = Math.min(97, step * 24 + 3) + '%';
      $('#busyNote').textContent = step < STEPS.length ? STEPS[step]?.n || 'Working…' : 'Writing the key bundle…';
      bar.style.width = Math.min(97, step * 24 + 3) + '%';
    }, 430);

    let res;
    try { res = await job; }
    catch (err) {
      clearInterval(walker); busy.classList.add('hidden'); $('#redactEmpty').classList.remove('hidden');
      toast('Backend error — falling back to sandbox: ' + err.message, { tone: 'bad', icon: 'unlink', ms: 4600 });
      AppState.mode = 'mock'; UI.setMode('mock');
      res = await Mock.pseudorun(file);
    }
    clearInterval(walker);
    $$('.step', busy).forEach((el, i) => { el.classList.remove('active'); el.classList.add('done'); });
    bar.style.width = '100%'; $('#busyPct').textContent = '100%';
    await sleep(260);
    busy.classList.add('hidden');
    this.render(res);
  },

  render(res) {
    AppState.document = res.document; AppState.original = res.original_text || '';
    AppState.scrubbed = res.pseudonymized_text || '';
    let spans = [];
    if (res.original_text && (!res.keys || !res.keys.length)) res.keys = [];
    if (res.original_text) {
      // recompute spans locally so the two panes stay perfectly aligned
      const local = Redactor.run(res.original_text);
      spans = local.spans;
      if (!res.pseudonymized_text) { AppState.scrubbed = local.pseudonymized_text; res.keys = local.keys; res.stats = local.stats; }
    }
    AppState.keys = res.keys || []; AppState.spans = spans; AppState.stats = res.stats; AppState.runId = res.run_id;
    AppState.activeCat = null;

    $('#redactEmpty').classList.add('hidden'); $('#redactWork').classList.add('active');
    const out = $('#redactResult'); out.classList.remove('hidden');
    $('#docName').textContent = res.document.name;
    $('#docSize').textContent = fmtBytes(res.document.size || 0);
    const typeEl = $('#docType'), hashEl = $('#docHash'), runEl = $('#runId'), stampEl = $('#stampPill');
    if (typeEl) typeEl.textContent = res.document.type || '';
    if (hashEl) hashEl.textContent = (res.document.sha256 || fakeHash(AppState.scrubbed)).slice(0, 22);
    if (runEl) runEl.textContent = res.run_id;
    if (stampEl) stampEl.innerHTML = '<span class="dot"></span> Sealed';
    this.paintPanes(); this.paintKeys();
    const wrap = $('#paneOriginalWrap');
    wrap.classList.add('masked'); wrap.classList.remove('revealed');
    $('#lockVeil').classList.remove('hide');
    $('#btnReveal').innerHTML = '<i data-lucide="eye"></i> Reveal briefly';

    // shared state → vault → other tabs
    const session = { id: res.run_id, document: res.document.name, created_at: res.created_at || new Date().toISOString(),
      entities: AppState.stats?.entities || 0, keys: AppState.keys };
    AppState.vault = [session, ...AppState.vault.filter((s) => s.id !== session.id)].slice(0, 14);
    persist(); Vault.render(); Restore.renderKeySources();
    chatBadge('');
    toast(fmtNum(AppState.stats?.entities || 0) + ' entities masked in ' + (AppState.stats?.duration_ms || 40) + ' ms', { icon: 'shield-check' });
  },

  paintPanes() {
    $('#paneOriginal').innerHTML = paintOriginal(AppState.original, AppState.spans);
    const values = new Map(AppState.keys.map((k) => [k.token, k.value]));
    const s = $('#paneScrubbed'); s.innerHTML = paintTokens(AppState.scrubbed, { values, only: AppState.activeCat });
    icons(s); this.applyFilter();
  },
  paintStats() {
    const st = AppState.stats || {};
    const words = Math.max(1, Math.round((st.chars || 0) / 6.2));
    const cards = [
      { v: fmtNum(st.entities), l: 'Entities masked', s: st.unique + ' unique keys issued', icon: 'shield-half', c: 'var(--text)' },
      { v: st.categories || 0, l: 'Detector classes fired', s: 'of 9 armed', icon: 'layers-3', c: 'var(--text)' },
      { v: (st.entities ? (st.entities / words * 100).toFixed(1) : '0.0'), l: 'PII density', s: 'per 100 words', icon: 'gauge', c: 'var(--text)' },
      { v: (st.duration_ms || 0) + 'ms', l: 'Scrub time', s: fmtNum(st.chars) + ' chars read', icon: 'timer', c: 'var(--text)' }
    ];
    $('#statStrip').innerHTML = cards.map((c) =>
      '<div class="stat"><div class="flex items-start gap-3"><div class="min-w-0">' +
      '<p class="l">' + c.l + '</p><p class="v mt-1.5">' + c.v + '</p>' +
      '<p class="text-[11px] text-[var(--dim)] mt-1">' + c.s + '</p></div>' +
      '<i data-lucide="' + c.icon + '" style="width:15px;height:15px;color:var(--dim);margin-left:auto;flex:none;margin-top:2px"></i></div></div>').join('');
    icons($('#statStrip'));
  },
  paintChips() {
    const counts = {}; AppState.keys.forEach((k) => { counts[k.category] = (counts[k.category] || 0) + k.occurrences; });
    $('#catChips').innerHTML = Object.keys(CATS).filter((c) => counts[c]).map((c) =>
      '<button class="chip" data-c="' + c + '" style="--tt:' + CATS[c].color + '"><i data-lucide="' + CATS[c].icon +
      '" style="width:13px;height:13px;color:' + CATS[c].color + '"></i>' + CATS[c].label + '<span class="cnt">' + counts[c] + '</span></button>').join('')
      || '<span class="text-[11.5px] text-[var(--dim)]">No entities detected — document looks clean.</span>';
    icons($('#catChips'));
  },
  applyFilter() {
    const only = AppState.activeCat;
    $$('#paneScrubbed .tok').forEach((t) => t.classList.toggle('dim', !!only && t.dataset.c !== only));
    $$('#paneOriginal .red').forEach((t) => { t.style.opacity = only && t.dataset.c !== only ? '.28' : '1'; });
    $$('#catChips .chip').forEach((c) => c.classList.toggle('on', c.dataset.c === only));
  },
  paintKeys(q = '') {
    const rows = AppState.keys.filter((k) => !q || (k.token + k.value + k.category).toLowerCase().includes(q.toLowerCase()));
    $('#keyCount').textContent = rows.length + (q ? ' / ' + AppState.keys.length : '') + ' keys';
    $('#keyBody').innerHTML = rows.map((k) => {
      const meta = CATS[k.category] || { label: k.category, color: '#c4cdd9', icon: 'hash' };
      return '<tr><td><span class="tok" data-c="' + k.category + '"><i data-lucide="' + meta.icon + '"></i>' + k.token + '</span></td>' +
        '<td><span class="text-[11px]" style="color:' + meta.color + '">' + meta.label + '</span></td>' +
        '<td class="mono !text-[11.5px] text-[var(--text)]">' + esc(k.value) + '</td>' +
        '<td class="text-right mono">' + k.occurrences + '</td></tr>';
    }).join('') || '<tr><td colspan="4" class="text-[12px] text-[var(--dim)] py-6 text-center">No mapping row matches that filter.</td></tr>';
    icons($('#keyBody'));
  },

  reveal() {
    if (!AppState.keys.length) return toast('Nothing to reveal yet', { tone: 'warn' });
    const pane = $('#paneOriginalWrap'), veil = $('#lockVeil');
    pane.classList.remove('masked'); pane.classList.add('revealed'); veil.classList.add('hide');
    let left = 8;
    const btn = $('#btnReveal');
    const tick = setInterval(() => {
      left--; btn.innerHTML = '<i data-lucide="timer"></i> Re-mask in ' + left + 's';
      if (left <= 0) { clearInterval(tick); pane.classList.add('masked'); pane.classList.remove('revealed');
        veil.classList.remove('hide'); btn.innerHTML = '<i data-lucide="eye"></i> Reveal briefly'; icons(btn.parentElement); }
    }, 1000);
  },

  sendToChat() {
    if (!AppState.scrubbed) return toast('Redact a document first', { tone: 'warn' });
    AppState.chat.ctx = { name: AppState.document.name, text: AppState.scrubbed, keys: AppState.keys, runId: AppState.runId };
    chatBadge('1');
    $('#chatCtx').textContent = AppState.chat.ctx.name;
    $('#chatCtxTokens').textContent = AppState.keys.length + ' keys in scope';
    $('#btnDetachCtx').classList.remove('hidden');
    AppState.chat.history = [];
    UI.setView('chat'); Chat.render();
    toast('Scrubbed context attached to chat', { icon: 'link-2', action: 'Ask summary', onAction: () => Chat.send('Summarise the document and list the open risks.') });
  },

  purge() {
    AppState.vault = []; AppState.selectedSessions = []; AppState.csvKeys = null; AppState.keys = [];
    AppState.scrubbed = ''; AppState.original = ''; AppState.spans = []; AppState.document = null; AppState.stats = null;
    AppState.chat.ctx = null; AppState.chat.history = [];
    localStorage.removeItem(CONFIG.LS_VAULT);
    $('#redactResult').classList.add('hidden'); $('#redactWork').classList.remove('active'); $('#redactEmpty').classList.remove('hidden');
    Vault.render(); Restore.renderKeySources(); Chat.render();
    toast('Session purged — keys deleted from this browser', { icon: 'eraser', tone: 'warn' });
  }
};

/* ── vault drawer + sessions ────────────────────────────────────────────── */
const Vault = {
  render() {
    const list = AppState.vault;
    $('#vaultCount').textContent = list.length + ' run' + (list.length === 1 ? '' : 's');
    $('#sessionList').innerHTML = list.length ? list.map((s) => {
      const cats = [...new Set(s.keys.map((k) => k.category))];
      const sel = AppState.selectedSessions.includes(s.id);
      return '<div class="card-flat p-3 ' + '' + '" data-ses="' + s.id + '" style="' + (sel ? 'border-color:var(--line3)' : '') + '">' +
        '<div class="flex items-center gap-2"><i data-lucide="' + (sel ? 'key-round' : 'file-lock-2') + '" style="width:15px;height:15px;color:' + (sel ? 'var(--muted)' : 'var(--dim)') + '"></i>' +
        '<p class="font-semibold text-[12.5px] truncate flex-1">' + esc(s.document) + '</p>' +
        '<span class="text-[10.5px] text-[var(--dim)]">' + timeAgo(s.created_at) + '</span></div>' +
        '<div class="flex items-center gap-2 mt-2 text-[11px] text-[var(--muted)]"><span class="mono">' + s.keys.length + '</span> keys ·' +
        '<span>' + cats.slice(0, 3).map((c) => CATS[c]?.label || c).join(', ') + '</span>' +
        (cats.length > 3 ? '<span class="text-[var(--dim)]">+' + (cats.length - 3) + '</span>' : '') + '</div>' +
        '<div class="flex gap-1.5 mt-2.5"><button class="btn btn-sm flex-1 justify-center" data-sel="' + s.id + '">' + (sel ? 'Selected' : 'Use keys') + '</button>' +
        '<button class="btn btn-sm" data-dl="' + s.id + '"><i data-lucide="download"></i></button>' +
        '<button class="btn btn-sm" data-loadchat="' + s.id + '"><i data-lucide="messages-square"></i></button></div></div>';
    }).join('') : '<p class="text-[12px] text-[var(--dim)] py-8 text-center">No stored runs.<br>Redact a document and its key bundle lands here.</p>';
    icons($('#sessionList'));
    Restore.renderKeySources();
  },
  async loadFromBackend() {
    try {
      const list = await apiService.fetchRecentSessions();
      if (Array.isArray(list) && list.length) {
        AppState.vault = list.map((s) => ({ ...s, keys: (s.keys || []).map(normaliseKey) })).concat(AppState.vault).slice(0, 20);
        this.render(); toast(list.length + ' vault sessions synced', { icon: 'cloud-download' });
      }
    } catch (e) { console.info('[vault] using local sessions:', e.message); }
  }
};
