import fs from 'fs';
import { JSDOM, VirtualConsole } from 'jsdom';

const html = fs.readFileSync('index.html', 'utf8');
const errs = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errs.push('jsdomError: ' + e.message + '\n' + (e.stack || '').split('\n').slice(1, 4).join('\n')));
vc.on('error', (...a) => errs.push('console.error: ' + a.map(String).join(' ')));

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://localhost:8000/', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.IntersectionObserver = class { constructor(cb) { this.cb = cb; } observe(el) { this.cb([{ isIntersecting: true, target: el }], this); } unobserve() {} disconnect() {} };
    w.Element.prototype.scrollIntoView = function () {};
    w.navigator.clipboard = { readText: async () => 'copied', writeText: async () => {} };
    w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
  }
});
const w = dom.window, d = w.document;
const ev = (id, type = 'click') => { const el = d.getElementById(id); if (!el) throw new Error('missing #' + id); el.dispatchEvent(new w.Event(type, { bubbles: true })); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);

await wait(700);
log('· boot errors:', errs.length ? errs : 'none');

/* ── view 1: redact the sample ─────────────────────────────────────────── */
ev('btnSample');
await wait(2600);
let s = w.eval('({keys:AppState.keys.length, ent:AppState.stats&&AppState.stats.entities, cats:[...new Set(AppState.keys.map(k=>k.category))].join(","), vault:AppState.vault.length})');
log('· redaction →', JSON.stringify(s));
log('· sample keys →', w.eval("AppState.keys.slice(0,9).map(k=>k.token+'='+k.value).join(' | ')"));
log('· tok chips rendered →', d.querySelectorAll('#paneScrubbed .tok').length, '| original spans →', d.querySelectorAll('#paneOriginal .red').length);
log('· stat cards →', d.querySelectorAll('#statStrip .stat').length, '| table rows →', d.querySelectorAll('#keyBody tr').length);

/* ── view 2: restore ───────────────────────────────────────────────────── */
w.eval("UI.setView('restore')");
ev('btnSampleAnswer');
await wait(1400);
log('· restore →', w.eval('JSON.stringify({resolved:AppState.restore.result?AppState.restore.result.resolved.length:0, unresolved:AppState.restore.result?AppState.restore.result.unresolved.length:0, keys:Restore.keys().length})'));
log('· restored marks →', d.querySelectorAll('#paneRestored mark.restored').length, '| stats →', d.querySelectorAll('#restoreStats .stat').length);
log('· scan line →', d.getElementById('restoreScan').textContent.trim());

/* ── view 3: chat + magic switch ───────────────────────────────────────── */
ev('btnSendToChat');
await wait(400);
w.eval("Chat.send('summarise the risks')");
await wait(2800);
log('· chat ctx →', w.eval('AppState.chat.ctx && AppState.chat.ctx.name'));
log('· history →', w.eval('AppState.chat.history.length'), '| answer chars →', w.eval('(AppState.chat.history[1]||{}).text.length'));
log('· tokens shown in bubbles →', d.querySelectorAll('#chatLog .tok').length);
ev('autoRestoreSwitch');
await wait(200);
log('· after magic switch → marks:', d.querySelectorAll('#chatLog mark.restored').length, 'tokens:', d.querySelectorAll('#chatLog .tok').length, '| state:', d.getElementById('autoRestoreState').textContent);

/* ── misc surfaces ─────────────────────────────────────────────────────── */
ev('btnDownloadCsv'); ev('btnToRestore'); ev('btnVault'); await wait(200);
log('· vault sessions →', d.querySelectorAll('#sessionList [data-ses]').length, '| toasts →', d.querySelectorAll('.toast').length);
w.eval('UI.cmdk.show()'); w.eval("UI.cmdk.list('vault')");
log('· palette rows →', d.querySelectorAll('#cmdkList .cmd-row').length);
log('· payload preview →', JSON.stringify(d.getElementById('payloadPreview').textContent.slice(0, 72)));
log('· topbar crumbs →', JSON.stringify(d.getElementById('viewTitle').textContent));
log('');
log('ERRORS AFTER FULL FLOW:', errs.length ? errs : 'none');
dom.window.close();
