<script>
/* ============================================================================
   VAULTDOC — self-contained frontend runtime (vanilla ES6+, no bundler).
   ----------------------------------------------------------------------------
   ARCHITECTURE
     1. CONFIG        · every integration switch in one place
     2. apiService    · the ONLY module that talks to the FastAPI backend
     3. Redactor      · in-browser detector swarm (powers sandbox mode)
     4. Mock          · fixture responses so the UI is fully clickable offline
     5. AppState      · single reactive store shared by the three workspaces
     6. UI            · render functions + event wiring
   ----------------------------------------------------------------------------
   BACKEND CONTRACT (all four endpoints accept/partial-match these shapes):
     POST /pseudorun     multipart/form-data, field "file"
                         → { document, original_text, pseudonymized_text,
                             keys:[{token,category,value,occurrences}], stats, run_id }
     POST /depseudorun   { text } (+ optional multipart field "keys_file")
                         → { restored_text, resolved[], unresolved[], source_label }
     POST /api/chat      { message, context, model, temperature, max_tokens,
                           system, history[], auto_restore, api_key? }
                         → { reply }   (or text/event-stream OpenAI-style deltas)
     GET  /api/sessions  → { sessions:[{id,document,entities,created_at,keys[]}] }
   Field names are normalised defensively (token|placeholder, value|original|real, …)
   so small naming differences on the Python side need no frontend change.
   ========================================================================== */
'use strict';

/* ══ 1 · CONFIG ═══════════════════════════════════════════════════════════ */
const CONFIG = {
  API_BASE : '',            // 'http://localhost:8000' or '' for same-origin
  MODE     : 'auto',        // 'auto' probes · 'live' forces fetch · 'mock' sandbox
  HEALTH   : '/health',
  PROBE_MS : 1400,
  MAX_MB   : 25,
  TOKEN_RE : /\b[A-Z][A-Z0-9_]*_\d{3}\b/g,
  GROQ_URL : 'https://api.groq.com/openai/v1/chat/completions',
  LS_PREFS : 'vaultdoc.prefs.v2',
  LS_VAULT : 'vaultdoc.vault.v2',
  APP      : 'VaultDoc',
  VERSION  : '1.4.0'
};
const url = (p) => (CONFIG.API_BASE || '') + p;
const TOKEN_ONLY = /^[A-Z][A-Z0-9_]*_\d{3}$/;
const tokenRe = () => new RegExp(CONFIG.TOKEN_RE.source, 'g');

/* ══ 2 · apiService — every backend interaction lives here ════════════════ */
const apiService = {
  /* ── health probe: flips the workspace between Live and Sandbox ───────── */
  async probe() {
    if (CONFIG.MODE === 'mock') return { ok: false, reason: 'sandbox forced' };
    try {
      const r = await fetch(url(CONFIG.HEALTH), {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout ? AbortSignal.timeout(CONFIG.PROBE_MS) : undefined
      });
      const ct = r.headers.get('content-type') || '';
      // a static host answering /health with HTML means "no API here"
      return { ok: r.ok && !/text\/html/i.test(ct), status: r.status };
    } catch (e) { return { ok: false, reason: String(e && e.message || 'network') }; }
  },

  /* ── TAB 1 · upload a document, receive it pseudonymised ────────────────
     @param {File} file
     @returns {Promise<{ok:boolean, source:'live'|'mock',
       document:{name,size,type,sha256}, original_text:string, pseudonymized_text:string,
       keys:Array<{token,category,value,occurrences}>,
       stats:{entities,unique,categories,chars,reduction,duration_ms},
       run_id:string, created_at:string}>} */
  async uploadAndPseudonymize(file) {
    if (AppState.mode !== 'live') return Mock.pseudorun(file);
    const form = new FormData();
    form.append('file', file, file.name);          // FastAPI: async def pseudorun(file: UploadFile = File(...))
    const res = await fetch(url('/pseudorun'), { method: 'POST', body: form });
    if (!res.ok) throw new Error('/pseudorun → HTTP ' + res.status);
    const j = await res.json();
    const keys = (j.keys || j.mapping || j.entities || []).map(normaliseKey);
    const original = j.original_text || j.source_text || '';
    const scrubbed = j.pseudonymized_text || j.redacted_text || '';
    return {
      ok: true, source: 'live',
      document: {
        name: (j.document && j.document.name) || j.filename || (file && file.name) || 'document',
        size: (j.document && j.document.size) ?? (file && file.size) ?? original.length,
        type: (j.document && j.document.type) || (file && file.type) || 'application/octet-stream',
        sha256: (j.document && j.document.sha256) || j.sha256 || fakeHash(original || scrubbed)
      },
      original_text: original, pseudonymized_text: scrubbed, keys,
      run_id: j.run_id || uid('run'), created_at: j.created_at || new Date().toISOString(),
      stats: j.stats || Redactor.stats(original, scrubbed, keys, 0)
    };
  },

  /* ── TAB 2 · resolve privacy tokens back to real identities ─────────────
     @param {string} text          text containing tokens
     @param {File|null} csvFile    optional keys.csv; omit to use vault keys  */
  async depseudonymize(text, csvFile) {
    if (AppState.mode !== 'live') return Mock.depseudorun(text, csvFile);
    let res;
    if (csvFile) {                                        // multipart variant
      const form = new FormData();
      form.append('text', text);
      form.append('keys_file', csvFile, csvFile.name);
      res = await fetch(url('/depseudorun'), { method: 'POST', body: form });
    } else {                                              // json variant, server holds the keys
      res = await fetch(url('/depseudorun'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_ids: AppState.selectedSessions })
      });
    }
    if (!res.ok) throw new Error('/depseudorun → HTTP ' + res.status);
    const j = await res.json();
    return {
      ok: true, source: 'live',
      restored_text: j.restored_html || highlightRestored(j.restored_text || j.depseudonymized_text || '', j.resolved || []),
      plain: j.restored_text || j.depseudonymized_text || '',
      resolved: (j.resolved || j.mapping || []).map(normaliseKey),
      unresolved: j.unresolved || j.unknown_tokens || [],
      source_label: j.source_label || 'FastAPI · /depseudorun'
    };
  },

  /* ── TAB 3 · chat over the redacted context ──────────────────────────── */
  async chatWithDocument({ message, context, apiKey, model, autoRestore, temperature, maxTokens, system, history, signal }) {
    const payload = {
      message, context, model, temperature, max_tokens: maxTokens, system,
      history: (history || []).slice(-8).map((m) => ({ role: m.role, content: m.raw || m.text })),
      auto_restore: !!autoRestore
    };
    /* 3a · dev convenience: call Groq straight from the browser (key never
            reaches our server — also handy for demos without a backend).    */
    if (AppState.prefs.direct && apiKey) return apiService._groq(payload, apiKey, signal);

    /* 3b · normal path: FastAPI owns the key and the egress.                */
    if (AppState.mode === 'live') {
      if (apiKey) payload.api_key = apiKey;
      const res = await fetch(url('/api/chat'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), signal
      });
      if (!res.ok) throw new Error('/api/chat → HTTP ' + res.status);
      const ct = res.headers.get('content-type') || '';
      if (ct.includes('event-stream')) return { ok: true, source: 'live', reply: await readSSE(res.body) };
      const j = await res.json();
      return { ok: true, source: 'live', reply: j.reply || j.content || j.message || '', usage: j.usage };
    }
    /* 3c · sandbox: deterministic local responder so the flow is testable. */
    await sleep(240 + Math.random() * 260);
    return { ok: true, source: 'mock', reply: Mock.chatReply(payload) };
  },

  async _groq(payload, apiKey, signal) {
    const res = await fetch(CONFIG.GROQ_URL, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + apiKey },
      body: JSON.stringify({
        model: payload.model, temperature: payload.temperature, max_tokens: payload.max_tokens, stream: true,
        messages: [
          { role: 'system', content: payload.system + (payload.context ? '\n\nREDACTED DOCUMENT CONTEXT (tokens are intentional):\n' + payload.context : '') },
          ...payload.history, { role: 'user', content: payload.message }
        ]
      })
    });
    if (!res.ok) throw new Error('Groq → HTTP ' + res.status + ' ' + (await res.text()).slice(0, 140));
    return { ok: true, source: 'groq', reply: await readSSE(res.body) };
  },

  /* ── vault list ──────────────────────────────────────────────────────── */
  async fetchRecentSessions() {
    if (AppState.mode !== 'live') return Mock.sessions();
    const res = await fetch(url('/api/sessions'), { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('/api/sessions → HTTP ' + res.status);
    const j = await res.json();
    const list = Array.isArray(j) ? j : (j.sessions || []);
    return list.map((s) => ({
      id: s.id || s.session_id || uid('ses'), document: s.document || s.filename || 'document',
      created_at: s.created_at || s.timestamp || new Date().toISOString(),
      entities: s.entities ?? (s.keys || []).length, keys: (s.keys || s.mapping || []).map(normaliseKey)
    }));
  }
};
const normaliseKey = (k) => ({
  token: (k.token || k.placeholder || '').trim(),
  category: String(k.category || k.type || 'ID').toUpperCase(),
  value: k.value ?? k.original ?? k.real ?? '',
  occurrences: k.occurrences ?? k.count ?? 1
});

/* OpenAI-style SSE reader; feeds the live bubble as tokens arrive. */
async function readSSE(body) {
  const reader = body.getReader(), dec = new TextDecoder();
  let buf = '', out = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n'); buf = lines.pop() || '';
    for (const line of lines) {
      const t = line.trim(); if (!t.startsWith('data:')) continue;
      const d = t.slice(5).trim(); if (!d || d === '[DONE]') continue;
      try { out += JSON.parse(d).choices?.[0]?.delta?.content || ''; } catch (_) { out += d; }
    }
    if (window.Chat) Chat._onStream(out);
  }
  return out;
}

/* ══ 3 · Redactor — deterministic, reversible pseudonymisation ════════════ */
const CATS = {
  PERSON      : { label: 'Person',       icon: 'user-round',      color: '#7dd3fc' },
  ORGANIZATION: { label: 'Organisation', icon: 'building-2',      color: '#a5b4fc' },
  MONEY       : { label: 'Money',        icon: 'banknote',        color: '#fcd34d' },
  EMAIL       : { label: 'Email',        icon: 'at-sign',         color: '#6ee7b7' },
  PHONE       : { label: 'Phone',        icon: 'phone',           color: '#fda4af' },
  DATE        : { label: 'Date',         icon: 'calendar-days',   color: '#bef264' },
  ADDRESS     : { label: 'Place',        icon: 'map-pin',         color: '#93c5fd' },
  URL         : { label: 'Link',         icon: 'link',            color: '#fdba74' },
  ID          : { label: 'Identifier',   icon: 'hash',            color: '#d4d4d8' }
};
const FIRST = ['Elena','Marcus','Priya','Anjalai','Sofia','Daniel','Yuki','Amara','Hugo','Lena','Nadia','Thomas','Aisha','Rafael','Ingrid','Omar','Clara','Viktor','Mei','Jonas','Fatima','Alexander','Sophie','Julian','Nora','Karim','Beatriz','Stefan','Astrid','Ravi','Camille','Lukas','Ilse','Tariq','Greta','Emil','Rosa','Andrei','Marta','Kian','Isabella','Terrence'];
const ORGS  = ['Northgate','Meridian','Halcyon','Vantar','Blackwood','Ashcombe','Lattice','Orpheus','Kestrel','Aurelia','Sable','Ironwood','Quill','Verdant','Cobalt','Pallas'];
const PLACES= ['Zurich','Geneva','Munich','Frankfurt am Main','Amsterdam','Rotterdam','Boston','Singapore','Toronto','Barcelona','Milan','Vienna','Copenhagen','Hamburg','Paris'];

const PATTERNS = [
  { cat:'EMAIL', re:/[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+){1,}/g },
  { cat:'URL',   re:/(?:https?|ftp):\/\/[^\s<>"'\]\)]+/g },
  { cat:'ID',    re:/\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[A-F0-9]{2}:){5}[A-F0-9]{2}\b/gi, min:7 },
  { cat:'ID',    re:/\b(?:VAT|GST|Tax\s?ID|EIN|HRB|SIRET|SIREN|CRN|Company\sNo|Reg(?:istration)?\sNo|Matter\sNo|PO|Invoice\sNo|Contract\sNo|Case\sNo|Account\sNo|Passport\sno\.?|Agreement\sNo|Policy)\.?\s?[:#]?\s*(?=[A-Z0-9][A-Z0-9\-\/\.]{2,23}\d)[A-Z0-9][A-Z0-9\-\/\.]{3,24}\b/gi, min:6 },
  { cat:'ID',    re:/\b[A-Z]{2}\s?\d{7,9}\b|\b\d{3}-\d{2}-\d{4}\b/g },
  { cat:'ADDRESS', re:/\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b/g },                  // IBAN
  { cat:'ID',    re:/\b(?:\d[ \-]?){13,16}\b/g, digits:[13, 16] },                     // card numbers
  { cat:'PHONE', re:/(?:\+\d{1,3}[ .\-]?)?(?:\(\d{1,5}\)[ .\-]?)?\d{2,5}[ .\-]?\d{3,4}[ .\-]?\d{3,4}(?:\s?x\d{1,5})?\b/g, digits:[7, 15] },
  { cat:'MONEY', re:/(?:€|\$|£|¥|CHF|EUR|USD|GBP|JPY|AUD|CAD|SEK|NOK|CNY)\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,3})?|\d{1,3}(?:[.,]\d{3}){1,}(?:[.,]\d{1,3})?\s?(?:EUR|USD|GBP|JPY|CHF|AUD|CAD|SEK|NOK|CNY)/gi, min:2 },
  { cat:'DATE',  re:/\b(?:\d{1,2}[\/.\-]\d{1,2}[\/.\-]\d{2,4}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|(?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}|\d{4}-\d{2}-\d{2})\b/gi },
  { cat:'ADDRESS', re:/\b\d{1,5}[A-Za-z]?\s+[A-Z][A-Za-z0-9.\-'’]+(?:\s+[A-Z][A-Za-z0-9.\-'’]+){0,3}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Square|Sq|Place|Pl|Terrace|Court|Ct|Parkway|Pkwy|Rue|Quai|Straße|Strasse|Weg|Allee|Gracht|Plein)\.?/g, min:6 },
  { cat:'ADDRESS', re:/\b\d{4}\s+[A-Z][a-z]+(?:[ ,]\w+)?\b(?=,|\n|\s+(?:Switzerland|Netherlands|Germany|France|USA|United States|Suisse)\b)/g, min:5 },
  { cat:'ADDRESS', re:new RegExp('\\b(?:' + PLACES.join('|') + ')\\b', 'g') },
  { cat:'PERSON', re:/\b(?:Dr|Prof|Mr|Ms|Mrs|Sir|Madam|Judge|Attorney|Hon)\.?\s+[A-Z][a-z’']{1,14}(?:\s+[A-Z][a-z’']{1,14}){1,2}\b/g, min:4 },
  { cat:'PERSON', re:/\b[A-Z][A-Z’'\-]{2,14},\s+[A-Z][a-z’']{1,14}\b/g, min:4 },
  { cat:'PERSON', re:new RegExp('\\b(?:' + FIRST.join('|') + ')(?:\\s+(?:[A-Z][a-z’\\-]{1,16}|[A-Z]\\.?)){1,2}\\b', 'g'), min:4 },
  { cat:'PERSON', re:/\b[A-Z][a-z’']{1,14}\s+[A-Z][a-z’']{1,14}(?=,?\s+(?:Chief|Head of|Director|Managing|Senior|VP|President|General Counsel|Partner|Counsel|Founder|Analyst|Minister|Auditor))/g, min:4 },
  { cat:'PERSON', re:/\bName:\s*([A-Z][a-z’']+\s+[A-Z][a-z’']+)/g, group:1, min:4 },
  { cat:'ORGANIZATION', re:/\b(?:[A-Z][A-Za-z0-9&.'’\-]+\s){0,4}[A-Z][A-Za-z0-9&.'’\-]*\s+(?:Inc|Incorporated|LLC|Ltd|Limited|GmbH|AG|SE|SA|NV|KG|Corp|Corporation|Company|Co|Group|Holdings|Partners|Capital|Bank|Trust|Fund|Technologies|Technology|Systems|Solutions|Labs|Industries|Enterprises|LLP|PLC)\b\.?/g, min:4 },
  { cat:'ORGANIZATION', re:new RegExp('\\b(?:' + ORGS.join('|') + ')(?:\\s+(?:[A-Z][A-Za-z0-9&\\-]+\\s+)){0,2}(?:Bank|Holdings|Group|Capital|Labs|Technologies|Technologies Inc\\.|Systems|Industries|Trust|Partners|Mutual)?\\b', 'g'), min:4 },
  { cat:'ORGANIZATION', re:/\b(?:Ministry|Agency|Authority|Council|Foundation|Institute|University|Hospital|Chamber|Committee|Commission|Board|Tribunal)\s+(?:of|for|de|des|der)\s+[A-ZÀ-Þ][A-Za-zÀ-ÿ'’\- ]{3,32}/g, min:8 },
  { cat:'ORGANIZATION', re:/\b(?:[A-Z][A-Z&\-]{2,11})\s+(?:N\.?\s?V\.?|S\.?\s?A\.?|B\.?\s?V\.?|A\.?\s?S\.?)\b/g, min:4 }
];

const Redactor = {
  run(text) {
    text = String(text || '');
    const t0 = performance.now();
    const taken = new Uint8Array(text.length);
    const found = [];
    for (const p of PATTERNS) this._scan(text, p, taken, found);
    found.sort((a, b) => a.start - b.start || b.end - a.end);

    const counters = Object.create(null), byValue = new Map(), keys = [], index = new Map();
    const parts = [], spans = []; let cursor = 0;
    for (const f of found) {
      const norm = f.cat + '::' + f.value.trim().replace(/\s+/g, ' ').toLowerCase();
      let token = byValue.get(norm);
      if (!token) {
        counters[f.cat] = (counters[f.cat] || 0) + 1;
        token = f.cat + '_' + String(counters[f.cat]).padStart(3, '0');
        byValue.set(norm, token);
        keys.push({ token, category: f.cat, value: f.value.trim(), occurrences: 0 });
        index.set(token, keys[keys.length - 1]);
      }
      index.get(token).occurrences++;
      parts.push(text.slice(cursor, f.start), token);
      spans.push({ start: f.start, end: f.end, token, cat: f.cat, value: text.slice(f.start, f.end) });
      cursor = f.end;
    }
    parts.push(text.slice(cursor));
    const pseudonymized_text = parts.join('');
    const duration = Math.max(1, Math.round(performance.now() - t0));
    return { keys, spans, found, duration, pseudonymized_text, original: text,
             stats: this.stats(text, pseudonymized_text, keys, duration) };
  },

  _scan(text, p, taken, out) {
    p.re.lastIndex = 0; let m, guard = 0;
    while ((m = p.re.exec(text)) !== null && guard++ < 5000) {
      if (m[0] === '') { p.re.lastIndex++; continue; }
      let s = m.index, e = m.index + m[0].length;
      if (p.group && m[p.group] !== undefined) {                       // capture-group patterns
        const idx = m.index + m[0].indexOf(m[p.group]);
        s = idx; e = idx + m[p.group].length;
      }
      while (e > s && /[\s.,;:!?)\]'"’”\-]/.test(text[e - 1])) e--;
      while (s < e && /[\s(“"]/.test(text[s])) s++;
      const v = text.slice(s, e);
      if (!v || v.length < (p.min || 2)) continue;
      if (p.digits) { const d = v.replace(/\D/g, '').length; if (d < p.digits[0] || d > p.digits[1]) continue; }
      if (/^\d+$/.test(v) && v.length < 5) continue;
      if (p.cat === 'PHONE' && /^\d{1,3}[.\-]\d{1,3}[.\-]\d{1,3}$/.test(v)) continue;   // version/id triples
      let clash = false; for (let i = s; i < e; i++) if (taken[i]) { clash = true; break; }
      if (clash) continue;
      for (let i = s; i < e; i++) taken[i] = 1;
      out.push({ start: s, end: e, value: v, cat: p.cat });
    }
  },

  stats(original, scrubbed, keys, duration) {
    const chars = (original || '').length;
    return {
      entities: keys.reduce((a, k) => a + k.occurrences, 0), unique: keys.length,
      categories: new Set(keys.map((k) => k.category)).size, chars,
      reduction: chars ? Math.max(0, Math.round((1 - (scrubbed || '').length / chars) * 1000) / 10) : 0,
      duration_ms: duration || 0
    };
  }
};

/* Live-mode fallback highlighter when the API returns plain text only. */
function highlightRestored(text, resolved) {
  const map = new Map((resolved || []).map((k) => [k.token, k]));
  return esc(text).replace(tokenRe(), (t) => map.has(t)
    ? '<mark class="restored" data-tok="' + t + '" data-c="' + map.get(t).category + '">' + esc(map.get(t).value) + '</mark>'
    : '<span class="tok tok-unresolved" data-tok="' + t + '">' + t + '</span>');
}

/* ══ 4 · Mock fixtures (instant preview, zero setup) ══════════════════════ */
const SAMPLE_DOC = `MASTER DATA PROCESSING AND PROFESSIONAL SERVICES AGREEMENT

Agreement No. MDA-2024-0417 — executed this 14th day of March, 2024 (the "Effective Date").

BETWEEN:
(1) NORTHGATE HOLDINGS AG, a corporation organised under the laws of Switzerland, whose principal
    place of business is Feldstrasse 42, 8001 Zurich, Switzerland ("Client"); and
(2) MERIDIAN LATTICE TECHNOLOGIES INC., a Delaware corporation, whose principal place of business
    is 88 Harbor Boulevard, Boston, MA 02110, United States ("Provider").

RECITALS
Dr. Elena Vasquez, Chief Privacy Officer of Northgate Holdings AG, and Marcus Whitfield, General
Counsel of Meridian Lattice Technologies Inc., confirm that the parties intend to process personal
data on the terms below. Client's tax identifier is CHE-114.902.331 and Provider's VAT number is
US 84-2910773.

1. SCOPE OF SERVICES
1.1 Provider shall deliver the Platform Migration Programme described in Exhibit A between
    01/04/2024 and 30/09/2024, staffed by no fewer than eleven (11) senior engineers.
1.2 Client shall provide access to the internal repository at https://git.northgate-internal.example/
    core-banking and to the staging host 10.42.17.88.
1.3 Any sub-processor listed in Exhibit C requires prior written approval from Priya Raghunathan,
    Head of Vendor Risk (priya.raghunathan@northgate.example, +41 44 912 8830).

2. FEES AND PAYMENT
2.1 Client shall pay Provider a fixed fee of EUR 2,450,000.00, invoiced quarterly in arrears.
2.2 A success bonus of $1,250,000 is payable if cutover completes before 30 September 2024.
2.3 Late amounts accrue interest at 1.5% per month. Travel is reimbursed up to GBP 18,500.
2.4 Invoices are payable within thirty (30) days to IBAN CH93 0076 2011 6238 5295 7,
    BIC UBSWCHZH80A, reference MDA-2024-0417.

3. CONFIDENTIALITY AND DATA PROTECTION
3.1 Each party shall treat the other's Confidential Information as strictly confidential.
3.2 Provider shall notify Client of any Personal Data Breach without undue delay and in any event
    within 24 hours, to privacy@meridian-lattice.example or to +1 (617) 555-0142.
3.3 Client personnel listed in Schedule 4 — including Daniel Okonkwo, Sofia Lindqvist and
    Hugo Beaumont — are authorised to receive Personal Data.
3.4 Audit reports are held at 14 Rue de la Paix, 75002 Paris and at the Rotterdam archive.

4. LIABILITY AND INDEMNITY
4.1 Aggregate liability is capped at EUR 4,900,000 during any twelve-month period.
4.2 Provider shall indemnify Client for third-party claims arising from a Data Incident, capped at
    USD 7,500,000. Nothing excludes liability for fraud or wilful misconduct.
4.3 Client's insurer is Ashcombe Mutual PLC, policy AM-77-2211-04, with a deductible of £250,000.

5. TERM AND TERMINATION
5.1 The Initial Term runs for thirty-six (36) months from the Effective Date.
5.2 Either party may terminate for convenience on 90 days' written notice to legal@meridian-lattice.example.

IN WITNESS WHEREOF the parties have executed this Agreement as of the Effective Date.

SIGNED for and on behalf of NORTHGATE HOLDINGS AG
Name: Dr. Elena Vasquez            Title: Chief Privacy Officer
Date: 14.03.2024                   Passport no. ZH 4419287

SIGNED for and on behalf of MERIDIAN LATTICE TECHNOLOGIES INC.
Name: Marcus Whitfield             Title: General Counsel
Date: 14/03/2024                   Contact: m.whitfield@meridian-lattice.example`;


const Mock = {
  async pseudorun(file) {
    const text = await Mock.extract(file);
    await sleep(140);
    const r = Redactor.run(text);
    return {
      ok: true, source: 'mock',
      document: file
        ? { name: file.name, size: file.size, type: file.type || 'application/octet-stream', sha256: fakeHash(text) }
        : { name: 'sample-contract.pdf', size: text.length, type: 'application/pdf', sha256: fakeHash(text) },
      original_text: text, pseudonymized_text: r.pseudonymized_text,
      keys: r.keys, stats: r.stats, run_id: uid('run'), created_at: new Date().toISOString()
    };
  },

  /* Real builds hand the raw bytes to FastAPI (OCR + layout parsing there).
     Sandbox mode reads plain-text files for real and simulates extraction
     for binary types so every code path stays testable offline.              */
  async extract(file) {
    if (!file) return SAMPLE_DOC;
    if (/^text\//.test(file.type) || /\.(txt|md|csv|json|eml)$/i.test(file.name)) {
      try { const t = await file.text(); if (t.trim().length > 40) return t; } catch (_) {}
      return SAMPLE_DOC;
    }
    return SAMPLE_DOC.replace('Agreement No. MDA-2024-0417',
      'Agreement No. MDA-2024-0417   [extracted from ' + file.name + ' · OCR confidence 98.4%]');
  },

  async depseudorun(text, csvFile) {
    await sleep(180);
    let keys = [], label = 'Session vault keys';
    if (csvFile) { keys = Mock.parseCsv(await csvFile.text()); label = 'Uploaded ' + (csvFile.name || 'keys.csv'); }
    else if (AppState.keySource === 'csv' && AppState.csvKeys && AppState.csvKeys.length) { keys = AppState.csvKeys; label = 'Loaded keys.csv'; }
    else { keys = Restore.keys(); label = AppState.selectedSessions.length ? AppState.selectedSessions.length + ' vault sessions' : 'Entire session vault'; }
    const out = RedactorRestore(text, keys, label);
    out.source = 'mock';
    return out;
  },

  /* VaultDoc CSV layout: token,category,value,occurrences,document */
  parseCsv(src) {
    const rows = String(src).replace(/\r/g, '').split('\n').filter((r) => r.trim());
    const out = [];
    for (let i = (/^\s*token\s*,/i.test(rows[0] || '') ? 1 : 0); i < rows.length; i++) {
      const cells = []; let cur = '', q = false;
      for (let c = 0; c < rows[i].length; c++) {
        const ch = rows[i][c];
        if (ch === '"') { if (q && rows[i][c + 1] === '"') { cur += '"'; c++; } else q = !q; }
        else if (ch === ',' && !q) { cells.push(cur); cur = ''; }
        else cur += ch;
      }
      cells.push(cur);
      const rec = normaliseKey({ token: (cells[0] || '').trim(), category: (cells[1] || 'ID').trim(),
        value: (cells[2] || '').trim(), occurrences: parseInt(cells[3], 10) || 1 });
      if (TOKEN_ONLY.test(rec.token) && rec.value) out.push(rec);
    }
    return out;
  },

  chatReply(payload) {
    const keys = (AppState.chat.ctx && AppState.chat.ctx.keys.length ? AppState.chat.ctx.keys : AppState.keys) || [];
    const g = (cat, i = 0) => keys.filter((k) => k.category === cat)[i]?.token || (cat + '_001');
    const org = g('ORGANIZATION'), org2 = g('ORGANIZATION', 1), per = g('PERSON'), per2 = g('PERSON', 1);
    const money = g('MONEY'), date = g('DATE'), id = g('ID'), mail = g('EMAIL'), ph = g('PHONE');
    const q = (payload.message || '').toLowerCase();
    if (!keys.length)
      return 'No document is attached to this session, so I have nothing grounded to reason over.\n\n• Redact a file in tab **01 · Redact** and press *Send to confidential chat*, or load a key bundle from the vault.\n• I only ever see tokenised text — that is by design.';
    if (/summar|overview|tl;?dr|brief/.test(q))
      return '**Summary of the redacted brief**\n\n1. Parties — ' + org + ' engages ' + (org2 || org) + ' under agreement ' + id + ', with an initial term of thirty-six (36) months measured from ' + date + '.\n2. Money — fixed consideration ' + money + ', a time-boxed success bonus, and interest of 1.5% per month on late amounts.\n3. People — ' + per + ' executes for the client; ' + per2 + ' executes for the provider and is the notice contact for incident escalation.\n4. Open items — sub-processor approval, the breach-notice channel, and the audit-archive location should be confirmed before cutover.';
    if (/risk|red flag|concern|issue|problem/.test(q))
      return '**Risk read**\n\n• Indemnity exposure (§4.2) is capped in absolute terms, while the bonus in §2.2 is time-bound — that asymmetry favours ' + (org2 || org) + '.\n• Notice obligations run to a named individual via ' + mail + ' / ' + ph + ' rather than a role mailbox: a single-person dependency if they leave.\n• §1.2 grants repository and staging-host access that sits outside the confidentiality framing of §3.1.\n\nRecommended: re-point notices to a functional address, and mirror the bonus conditions into the liability cap.';
    if (/date|deadline|when|term|timeline|schedule/.test(q))
      return '**Timeline pulled from the document**\n\n• Effective date: ' + date + '\n• Delivery window and cutover milestone: §1.1, measured against the bonus trigger in §2.2.\n• Termination for convenience: ninety (90) days written notice (§5.2), served on the notice address tied to ' + per2 + '.\n• Personal data breach notification: without undue delay, never later than 24 hours (§3.2).';
    if (/amount|fee|cost|pay|money|price|invoice|cap|liabilit/.test(q))
      return '**Monetary terms**\n\n• Fixed fee: ' + money + ' (§2.1), invoiced quarterly in arrears.\n• Performance bonus: a second amount, contingent on early cutover (§2.2); travel reimbursed to a separate ceiling (§2.3).\n• Liability ceilings: §4.1 general, §4.2 data incidents, with the deductible borne by ' + org + '.\n• Settlement reference ' + id + ' via the account in §2.4.\n\nBecause values keep their shape, you can still do arithmetic with your model — only the identity is removed.';
    if (/who|people|party|parties|counterpart|sign|contact/.test(q))
      return '**Counterparties and signatories**\n\n• ' + org + ' — client; signed by ' + per + ', Chief Privacy Officer, on ' + date + '.\n• ' + (org2 || 'ORGANIZATION_002') + ' — provider; signed by ' + (per2 || 'PERSON_002') + ', General Counsel.\n• Sub-processor approvals escalate to the Head of Vendor Risk named in §1.3 (' + mail + ').\n• Authorised recipients of personal data appear in Schedule 4 (§3.3).';
    if (/quote|verbatim|clause|section/.test(q))
      return 'Verbatim, tokens intact so you can restore it in tab 02:\n\n> §4.2 — ' + (org2 || org) + ' shall indemnify ' + org + ' for third-party claims arising from a Data Incident, capped at the figure recorded in §4.2 and subject to the deductible held by ' + id + '.\n\n§3.2 routes notification to ' + mail + ' or ' + ph + ' within 24 hours of ' + per + '’s awareness.';
    return 'Working from the redacted brief attached to this session:\n\n• The clause you asked about is expressed entirely in privacy tokens — ' + per + ', ' + org + ' and ' + money + ' are resolvable from your vault, never by me.\n• Structurally the obligation runs from ' + (org2 || org) + ' to ' + per + ' inside the notice window of §3.2, subject to the cap in §4.2 and the milestone on ' + date + '.\n• Ask me to "quote §4.2 verbatim" and I will reproduce it with tokens intact for restoration in tab 02.\n\nRun reference: ' + id + '.';
  },

  sessions() {
    const k = (token, category, value, occurrences) => ({ token, category, value, occurrences });
    const mk = (document, at, keys) => ({ id: uid('ses'), document, created_at: at, entities: keys.reduce((a, x) => a + x.occurrences, 0), keys });
    return [
      mk('vendor-deposition-v3.pdf', isoAgo(2, 'h'), [
        k('PERSON_001', 'PERSON', 'Isabella Kroon', 6), k('PERSON_002', 'PERSON', 'Terrence Abe', 3),
        k('ORGANIZATION_001', 'ORGANIZATION', 'Vantar Capital LLP', 5), k('MONEY_001', 'MONEY', '€1,100,000', 2),
        k('EMAIL_001', 'EMAIL', 'i.kroon@vantar.example', 1), k('DATE_001', 'DATE', '22 Nov 2023', 2)]),
      mk('q3-invoice-batch.pdf', isoAgo(1, 'd'), [
        k('ORGANIZATION_001', 'ORGANIZATION', 'Halcyon Systems GmbH', 9), k('MONEY_001', 'MONEY', 'USD 486,000', 4),
        k('ID_001', 'ID', 'DE89 3704 0044 0532 0130 00', 1), k('DATE_001', 'DATE', '2024-01-15', 3)]),
      mk('board-minutes-2024-02.docx', isoAgo(4, 'd'), [
        k('PERSON_001', 'PERSON', 'Amara Nwosu', 11), k('PERSON_002', 'PERSON', 'Stefan Devereux', 7),
        k('ORGANIZATION_001', 'ORGANIZATION', 'Blackwood Partners', 4), k('ADDRESS_001', 'ADDRESS', '12 Quay Lane', 2)])
    ];
  }
};

function RedactorRestore(text, keys, label) {
  const map = new Map(); (keys || []).forEach((k) => { if (k && k.token) map.set(k.token, k); });
  const resolved = []; const unresolved = new Set(); const html = [];
  let last = 0, m; const re = tokenRe();
  while ((m = re.exec(text)) !== null) {
    const tok = m[0];
    html.push(esc(text.slice(last, m.index)));
    if (map.has(tok)) {
      const rec = map.get(tok);
      resolved.push({ token: tok, value: rec.value, category: rec.category || 'ID', occurrences: rec.occurrences });
      html.push('<mark class="restored" data-tok="' + tok + '" data-c="' + (rec.category || 'ID') + '" title="' + esc(tok) + ' → restored locally">' + esc(rec.value) + '</mark>');
    } else {
      unresolved.add(tok);
      html.push('<span class="tok tok-unresolved" data-tok="' + tok + '" title="No key loaded for this token">' + esc(tok) + '</span>');
    }
    last = m.index + tok.length;
  }
  html.push(esc(text.slice(last)));
  return {
    ok: true, source: 'mock', source_label: label || 'vault',
    restored_text: html.join(''), resolved, unresolved: [...unresolved],
    plain: text.replace(tokenRe(), (t) => (map.has(t) ? map.get(t).value : t))
  };
}

/* ══ 5 · AppState — one reactive store shared by all three workspaces ══════ */
const AppState = {
  view: 'redact',
  mode: 'mock',
  document: null, original: '', scrubbed: '', keys: [], spans: [], stats: null, runId: null,
  activeCat: null,
  vault: [], selectedSessions: [],
  keySource: 'vault', csvKeys: null, csvName: null, csvFile: null,
  restore: { result: null, mode: 'split' },
  chat: { ctx: null, history: [], busy: false, lastHits: 0 },
  prefs: { groqKey: '', persist: false, direct: false, model: 'openai/gpt-oss-120b',
           temp: 0.2, maxTok: 3072, autoRestore: false },
  get active() { return { name: this.document && this.document.name, keys: this.keys }; }
};
