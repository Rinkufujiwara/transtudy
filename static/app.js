// Transtudy front end (vanilla JS). User text is always rendered with textContent.
'use strict';
const $ = (id) => document.getElementById(id);
const frame = $('frame');
const S = { tl: 'es', state: 'idle', busy: false, capture: null, commit: null, carouselCards: [], lessonInclude: [] };

// ---------- helpers ----------
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function fillIcons(root = document) { root.querySelectorAll('[data-icon]').forEach((n) => { if (!n.querySelector('svg')) n.prepend(icon(n.dataset.icon, 20)); }); }
function today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function dayStart() { const d = new Date(); d.setHours(0, 0, 0, 0); return d.toISOString(); }
function daysAgo(n) { return new Date(Date.now() - n * 864e5).toISOString(); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  } catch (e) { throw new Error("Can't reach the app server."); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error((data && typeof data.detail === 'string' && data.detail) || 'Something went wrong.');
  return data;
}
let toastT;
function toast(msg, ms = 2600) { const t = $('toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), ms); }
function storeGet(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function storeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
function relDay(iso) {
  const d = new Date(iso); const t0 = new Date(); t0.setHours(0, 0, 0, 0);
  const diff = Math.round((t0 - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  if (diff <= 0) return 'Today'; if (diff === 1) return 'Yesterday'; return `${diff} days ago`;
}
const DIR = { couldnt_say: "Couldn't say", didnt_catch: "Didn't catch" };
const langCode = () => S.tl.toUpperCase();

// ---------- toggle group (sliding clip-path highlight) ----------
function toggleGroup(options, value, onChange) {
  const g = el('div', 'tg'); g.setAttribute('role', 'group');
  const hl = el('div', 'tg-hl'); hl.setAttribute('aria-hidden', 'true');
  const n = options.length; let cur = Math.max(0, options.findIndex((o) => o[0] === value));
  const btns = options.map(([v, label], i) => {
    const b = el('button', null, label); b.type = 'button';
    b.addEventListener('click', () => { if (i !== cur) { set(i); onChange(v); } });
    g.append(b); hl.append(el('span', null, label)); return b;
  });
  function set(i) {
    cur = i; btns.forEach((b, j) => b.setAttribute('aria-pressed', String(j === i)));
    hl.style.clipPath = `inset(0 ${((n - 1 - i) * 100) / n}% 0 ${(i * 100) / n}% round 7px)`;
  }
  g.append(hl); set(cur);
  g.setValue = (v) => set(Math.max(0, options.findIndex((o) => o[0] === v)));
  return g;
}

// ---------- tabs ----------
const TAB_ICONS = { home: 'house', wordlist: 'list', lesson: 'layers', wordbook: 'book-open' };
function renderTabs(active) {
  document.querySelectorAll('#tabbar button').forEach((b) => {
    const t = b.dataset.tab; b.textContent = '';
    b.append(icon(TAB_ICONS[t], 26, { fill: t === active && t !== 'list' && t !== 'wordlist' }));
    b.classList.toggle('active', t === active);
  });
}
function showTab(t) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('active', s.id === t));
  renderTabs(t);
  if (t === 'home') loadHome();
  if (t === 'wordlist') loadWordlist();
  if (t === 'lesson') loadRecent();
  if (t === 'wordbook') loadShelf();
}
document.querySelectorAll('#tabbar button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
document.querySelectorAll('.screen.scroll').forEach((s) => s.addEventListener('scroll', () => s.classList.toggle('collapsed', s.scrollTop > 48)));

// ---------- layers: panels, sheets ----------
function setLayer(node, open) {
  node.classList.toggle('open', open);
  if (open) node.removeAttribute('inert'); else node.setAttribute('inert', '');
}
function openPanel(id) {
  const p = $(id);
  p.classList.add('noanim'); p.classList.remove('open'); void p.offsetWidth; p.classList.remove('noanim');
  requestAnimationFrame(() => setLayer(p, true));
}
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => setLayer($(b.dataset.close), false)));
let openSheetId = null;
function openSheet(id) { openSheetId = id; $('backdrop').classList.add('on'); setLayer($(id), true); }
function closeSheet() {
  if (!openSheetId) return; const id = openSheetId; openSheetId = null;
  $('backdrop').classList.remove('on'); setLayer($(id), false);
  if (id === 'cardSheet') lessonClosed();
}
$('backdrop').addEventListener('click', closeSheet);
document.querySelectorAll('.sheet').forEach((sh) => {
  const g = sh.querySelector('.grabber'); let y0 = null;
  g.addEventListener('click', closeSheet);
  sh.addEventListener('pointerdown', (e) => { if (e.target === g || sh.querySelector('.sheet-body').scrollTop <= 0) y0 = e.clientY; });
  sh.addEventListener('pointerup', (e) => { if (y0 != null && e.clientY - y0 > 80) closeSheet(); y0 = null; });
});

// ---------- settings ----------
function renderLangs() {
  const g = $('langGroup'); g.textContent = '';
  [['es', 'Spanish'], ['de', 'German']].forEach(([v, label]) => {
    const r = el('button', 'grow-row'); r.append(el('span', null, label));
    if (v === S.tl) r.append(icon('check', 20));
    r.addEventListener('click', async () => {
      try { await api('/api/settings', { method: 'PUT', body: JSON.stringify({ target_lang: v }) }); S.tl = v; renderLangs(); } catch (e) { toast(e.message); }
      setLayer($('settingsPanel'), false); loadHome();
    });
    g.append(r);
  });
}
$('btnMenu').addEventListener('click', () => { renderLangs(); openPanel('settingsPanel'); });

// ---------- agent log ----------
function localTime(iso) { const d = new Date(iso); return d.toLocaleTimeString('en-GB', { hour12: false }); }
async function loadLog() {
  const [pol, rows] = await Promise.all([api('/api/policy'), api('/api/audit-log')]);
  const pt = $('policyTable'); pt.textContent = '';
  Object.entries(pol).forEach(([a, tools]) => { const d = el('div'); d.append(el('b', null, a + ': ')); d.append(document.createTextNode(tools.join(', '))); pt.append(d); });
  const box = $('logRows'); box.textContent = '';
  rows.forEach((r) => {
    const row = el('div', 'log-row' + (r.decision === 'blocked' ? ' blocked' : ''));
    row.append(document.createTextNode(`${localTime(r.ts)} ${r.agent} ${r.tool}`));
    row.append(el('span', 'pill ' + r.decision, r.decision));
    row.append(el('div', null, r.args));
    if (r.reason) row.append(el('div', null, 'reason: ' + r.reason));
    if (r.why) row.append(el('div', null, 'why: ' + r.why));
    box.append(row);
  });
  if (!rows.length) box.append(el('div', 'muted small', 'No tool calls yet.'));
}
$('btnLog').addEventListener('click', async () => { openSheet('logSheet'); try { await loadLog(); } catch (e) { toast(e.message); } });

// =====================================================================
// HOME
// =====================================================================
const home = $('home');
function setHome(state) { S.state = state; home.dataset.state = state; $('btnLog').classList.toggle('hidden', state === 'result'); $('btnMenu').classList.toggle('hidden', state === 'result'); $('btnClose').classList.toggle('hidden', state !== 'result'); $('btnLog').classList.toggle('hidden', state !== 'idle'); $('btnMenu').classList.toggle('hidden', state !== 'idle'); }
function micLine(text, cls = 'tap') { const m = $('micLine'); m.className = 'mic-line ' + cls; m.textContent = text || ''; return m; }

// ----- carousel sizes -----
function sizeCards() {
  const W = frame.clientWidth, H = frame.clientHeight;
  const hdr = Math.min(44, Math.max(14, H * 0.04)) + 50;
  const free = H * 0.66 - 48 - hdr;
  let w, h;
  if (H <= 624) { h = Math.min(237, free * 0.7); w = h * 307 / 237; } else { w = W * 0.76; h = 237; if (h > free - 20) { h = free - 20; w = h * 307 / 237; } }
  const k = Math.min(1, w / 307);
  home.style.setProperty('--cw', w + 'px'); home.style.setProperty('--ch', h + 'px');
  document.querySelectorAll('.hcard').forEach((c) => c.style.setProperty('--k', k));
  S.cardK = k; fitCardWords();
}
function fitCardWords() {
  document.querySelectorAll('.hcard-word').forEach((wEl) => {
    const base = Number(wEl.dataset.base || 42); const k = S.cardK || 1;
    let size = Math.max(26, base * k); wEl.style.fontSize = size + 'px';
    const max = wEl.parentElement.clientWidth - 8;
    while (wEl.scrollWidth > max && size > 26) { size -= 1; wEl.style.fontSize = size + 'px'; }
  });
}
new ResizeObserver(() => { sizeCards(); if (S.state === 'result') fitResult(); }).observe(frame);

function minutesLine(n) { const m = Math.ceil((n * 25) / 60); return m <= 1 ? 'About 1 minute' : `About ${m} minutes`; }
function buildCard({ word, next, empty, kicker, count, sub, onPlay, base }) {
  const c = el('div', 'hcard'); c.style.setProperty('--k', S.cardK || 1);
  const body = el('div', 'hcard-body');
  if (empty) body.append(el('div', 'hcard-empty', empty));
  else { const w = el('div', 'hcard-word', word); w.dataset.base = base || 42; body.append(w); if (next) body.append(el('div', 'hcard-next', next)); }
  const strip = el('div', 'hcard-strip'); const st = el('div', 'strip-text');
  st.append(el('span', 'strip-k', kicker), el('span', 'strip-n', count)); if (sub) st.append(el('span', 'strip-m', sub));
  strip.append(st);
  if (onPlay) { const p = el('button', 'play44'); p.setAttribute('aria-label', 'Play'); const pc = el('span', 'play-c'); pc.append(icon('play', 20, { fill: true })); p.append(pc); strip.append(p); c.onPlay = onPlay; }
  c.append(body, strip); return c;
}
let reviewData = null;
function reviewCard(d) {
  const words = d.words || [];
  if (!words.length) return buildCard({ empty: d.removed > 0 ? 'All done for today.' : 'Nothing to review yet.', kicker: "TODAY'S REVIEW", count: '0 words' });
  const shown = words.slice(1, 3).map((w) => w.target_text); const more = words.length - 1 - shown.length;
  const next = shown.concat(more > 0 ? [`${more} more`] : []).join(' · ');
  return buildCard({ word: words[0].target_text, next, kicker: "TODAY'S REVIEW", count: `${words.length} word${words.length === 1 ? '' : 's'}`, sub: minutesLine(words.length), onPlay: () => startLesson({ today: true, day: today() }, "Today's review") });
}
async function loadHome() {
  if (S.state !== 'idle') return;
  try {
    const [rev, recent] = await Promise.all([api(`/api/review/today?day=${today()}&day_start=${encodeURIComponent(dayStart())}`), api(`/api/lesson/recent-filters?day_start=${encodeURIComponent(dayStart())}`)]);
    reviewData = rev; renderCarousel(rev, recent.slice(0, 2));
    if (rev.updating || rev.stale) pollReview();
  } catch (e) { micLine(e.message, 'tap'); }
}
function renderCarousel(rev, recent) {
  const car = $('carousel'); const keep = car.scrollLeft; car.textContent = '';
  const cards = [reviewCard(rev)];
  recent.forEach((f) => {
    const dirLabel = f.direction === 'all' ? 'Both' : DIR[f.direction];
    const kind = { words: 'Words', sentences: 'Sentences', both: 'Words & sentences' }[f.kind];
    const period = f.period === 'all' ? 'All dates' : f.period === 'today' ? 'Today' : `${f.period} days`;
    cards.push(buildCard({ word: dirLabel, base: 34, next: `${kind} · ${period}`, kicker: 'RECENT LESSON', count: `${f.count} ${f.kind === 'sentences' ? 'sentences' : 'words'}`, sub: relDay(f.last_used_at),
      onPlay: () => startLesson({ kind: f.kind, direction: f.direction, period: f.period }, f.label) }));
  });
  cards.forEach((c) => car.append(c));
  car.classList.toggle('single', cards.length === 1);
  sizeCards(); car.scrollLeft = keep;
}
let reviewPolling = false;
async function pollReview() {
  if (reviewPolling) return; reviewPolling = true;
  const t0 = Date.now();
  try {
    while (Date.now() - t0 < 20000) {
      await sleep(1500);
      const rev = await api(`/api/review/today?day=${today()}&day_start=${encodeURIComponent(dayStart())}`);
      if (S.state === 'idle' && JSON.stringify(rev.words) !== JSON.stringify(reviewData && reviewData.words)) {
        reviewData = rev;
        const car = $('carousel'); const first = car.querySelector('.hcard');
        if (first) { const body = first.querySelector('.hcard-body'); body.style.opacity = 0; await sleep(300); const nc = reviewCard(rev); first.replaceWith(nc); sizeCards(); const nb = nc.querySelector('.hcard-body'); nb.style.opacity = 0; requestAnimationFrame(() => { nb.style.opacity = 1; }); }
      }
      if (!rev.updating && !rev.stale) break;
    }
  } catch (e) { /* ignore */ }
  reviewPolling = false;
}

// ----- carousel: mouse drag, wheel, keys, tap -----
(function carouselInput() {
  const car = $('carousel'); let down = null; let moved = false; let wheelLock = 0;
  const cardsOf = () => [...car.querySelectorAll('.hcard')];
  const snapLeft = (c) => c.offsetLeft - (car.clientWidth - c.offsetWidth) / 2;
  const centered = () => { const mid = car.scrollLeft + car.clientWidth / 2; let best = null, bd = 1e9; cardsOf().forEach((c) => { const d = Math.abs(c.offsetLeft + c.offsetWidth / 2 - mid); if (d < bd) { bd = d; best = c; } }); return best; };
  const go = (c) => c && car.scrollTo({ left: snapLeft(c), behavior: 'smooth' });
  const step = (dir) => { const cs = cardsOf(); const i = cs.indexOf(centered()); go(cs[Math.max(0, Math.min(cs.length - 1, i + dir))]); };
  car.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') { down = { x: e.clientX, touch: true }; moved = false; return; } down = { x: e.clientX, left: car.scrollLeft }; moved = false; car.setPointerCapture(e.pointerId); });
  car.addEventListener('pointermove', (e) => {
    if (!down) return; if (Math.abs(e.clientX - down.x) > 6) moved = true;
    if (!down.touch && moved) { car.classList.add('dragging'); car.scrollLeft = down.left - (e.clientX - down.x); }
  });
  const end = (e) => {
    if (!down) return; const wasTouch = down.touch; down = null;
    if (!wasTouch && car.classList.contains('dragging')) { car.classList.remove('dragging'); go(centered()); }
    if (!moved && e.type === 'pointerup') tap(e);
  };
  car.addEventListener('pointerup', end); car.addEventListener('pointercancel', end);
  function tap(e) {
    const c = document.elementFromPoint(e.clientX, e.clientY)?.closest('.hcard'); if (!c) return;
    if (c !== centered()) { go(c); return; }
    if (c.onPlay) c.onPlay();
  }
  car.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return; // trackpad sideways swipe: native
    e.preventDefault(); const now = Date.now(); if (now - wheelLock < 350) return; wheelLock = now; step(e.deltaY > 0 ? 1 : -1);
  }, { passive: false });
  car.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight') { step(1); e.preventDefault(); } if (e.key === 'ArrowLeft') { step(-1); e.preventDefault(); } });
})();

// ----- text input -----
const textIn = $('textIn');
function updatePasteBtn() {
  const b = $('btnPaste'); const has = textIn.value.trim().length > 0; b.classList.toggle('send', has);
  b.setAttribute('aria-label', has ? 'Send' : 'Paste'); const c = b.querySelector('.circle-btn'); c.textContent = ''; c.append(icon(has ? 'arrow-up' : 'clipboard-paste', 20));
}
textIn.addEventListener('input', updatePasteBtn);
$('btnPaste').addEventListener('click', async () => {
  if (textIn.value.trim()) { submitText(); return; }
  try { const t = await navigator.clipboard.readText(); textIn.value = (t || '').slice(0, 300); updatePasteBtn(); textIn.focus(); } catch (e) { toast('Clipboard is not available.'); }
});
$('inputRow').addEventListener('submit', (e) => { e.preventDefault(); submitText(); });
async function submitText() {
  const text = textIn.value.trim(); if (!text || S.busy) return;
  S.busy = true;
  try { const cap = await api('/api/capture/text', { method: 'POST', body: JSON.stringify({ text }) }); textIn.value = ''; updatePasteBtn(); textIn.blur(); await showResult(cap); }
  catch (e) { micLine(e.message, 'tap'); S.busy = false; }
}

// ----- result -----
let resultToken = 0;
async function showResult(cap) {
  const token = ++resultToken; S.capture = cap; S.commit = null; S.kept = new Set();
  setHome('result');
  $('tags').textContent = ''; $('tutorLine').classList.add('hidden');
  const t = $('trans'); t.textContent = cap.translated_text;
  const o = $('other'); o.textContent = '';
  const learnLine = cap.source_lang === 'en' ? null : cap.source_text; // Didn't catch: learning-language text is the 17px line
  if (cap.granularity === 'sentence') {
    if (learnLine) { o.append(...keepWords(learnLine)); } else { t.textContent = ''; t.append(...keepWords(cap.translated_text)); o.textContent = cap.source_text; }
  } else o.textContent = cap.source_text;
  micLine('Capture Agent is working…', 'status');
  fitResult();
  S.busy = true;
  const state = { pending: true };
  try {
    const res = await api('/api/capture/commit', { method: 'POST', body: JSON.stringify({ capture_id: cap.capture_id, day: today() }) });
    if (token !== resultToken) return;
    S.commit = res; renderTags(cap, res);
    if (res.action === 'none') micLine("Couldn't save", 'status');
    else if (res.action === 'saved') { const m = micLine('Saved', 'status'); const u = el('button', 'undo', 'Undo'); u.addEventListener('click', undo); m.append(u); }
    else micLine('Counted', 'status');
    if (res.handed_to_tutor) watchTutor(res.entry_id, token);
    if (res.review_updating) pollReview();
  } catch (e) { if (token === resultToken) micLine(e.message, 'status'); }
  finally { S.busy = false; state.pending = false; }
}
function keepWords(line) {
  const parts = []; line.split(/(\s+)/).forEach((tok) => {
    if (!tok) return; if (/^\s+$/.test(tok)) { parts.push(document.createTextNode(tok)); return; }
    const b = el('button', 'kw', tok); b.type = 'button'; b.addEventListener('click', () => toggleKeep(b, tok)); parts.push(b);
  });
  return parts;
}
async function toggleKeep(b, tok) {
  if (!S.commit || !S.commit.entry_id) { toast('Wait until it is saved.'); return; }
  const id = S.commit.entry_id; const kept = b.classList.contains('kept');
  try {
    await api(`/api/entries/${id}/${kept ? 'kept-word' : 'keep-word'}`, { method: kept ? 'DELETE' : 'POST', body: JSON.stringify({ word: tok }) });
    b.classList.toggle('kept', !kept);
  } catch (e) { toast(e.message); }
}
function renderTags(cap, res) {
  const box = $('tags'); box.textContent = ''; box.className = 'tags';
  const tags = [[cap.granularity === 'word' ? 'Word' : 'Sentence', 'keep'], [DIR[cap.direction], 'keep']];
  if (res.is_weak) tags.push(['Weak word', 'keep weak']);
  if (res.action !== 'none') tags.push(res.first_time ? ['First time', 'first'] : [`Looked up ${res.lookup_count}×`, 'keep']);
  if (res.action === 'counted') tags.push(['Not saved again', 'nsa']);
  tags.forEach(([label, cls]) => { const s = el('span', 'tag ' + (cls.includes('weak') ? 'weak' : ''), label); s.dataset.k = cls; box.append(s); });
  const fits = () => box.scrollWidth <= box.clientWidth + 0.5;
  for (const lvl of ['', 'l1', 'l2', 'l3', 'l4']) { box.className = 'tags ' + lvl; if (fits()) return; }
  for (const k of ['nsa', 'first']) { const n = box.querySelector(`[data-k="${k}"]`); if (n) { n.remove(); if (fits()) return; } }
}
function fitResult() {
  const area = $('result'), t = $('trans'), o = $('other');
  [t, o].forEach((n) => { n.classList.remove('clamp'); n.style.webkitLineClamp = ''; });
  let size = 32; t.style.fontSize = size + 'px';
  const over = () => area.scrollHeight > area.clientHeight + 1;
  while (over() && size > 22) { size -= 2; t.style.fontSize = size + 'px'; }
  if (!over()) return;
  o.classList.add('clamp'); o.style.webkitLineClamp = 2;
  const lh = size * 1.2; let lines = Math.max(1, Math.floor(t.clientHeight / lh));
  t.classList.add('clamp');
  while (lines > 1) { t.style.webkitLineClamp = lines; if (!over()) return; lines -= 1; }
  t.style.webkitLineClamp = 1;
}
async function watchTutor(entryId, token) {
  const line = $('tutorLine'); line.textContent = ''; line.append(icon('bot', 15), el('span', null, 'Tutor Agent is reviewing this word…')); line.classList.remove('hidden'); fitResult();
  const t0 = Date.now(); await sleep(600);
  while (Date.now() - t0 < 30000) {
    try { const p = await api('/api/wordbook/pending'); if (!p.pending.includes(entryId)) break; } catch (e) { break; }
    await sleep(500);
  }
  if (token !== resultToken || S.state !== 'result') return;
  try { const e = await api(`/api/entries/${entryId}`); line.lastChild.textContent = 'Tutor Agent: ' + (e.tutor_note || 'reviewed'); fitResult(); } catch (e) { /* ignore */ }
}
async function undo() {
  const id = S.commit && S.commit.entry_id; if (!id) return;
  try { await api(`/api/entries/${id}`, { method: 'DELETE' }); toIdle(); } catch (e) { toast(e.message); }
}
function toIdle() { resultToken++; setHome('idle'); micLine('Tap to speak', 'tap'); loadHome(); }
$('btnClose').addEventListener('click', toIdle);
function learnText() { const c = S.capture; if (!c) return ''; return c.source_lang === 'en' ? c.translated_text : c.source_text; }
$('btnListen').addEventListener('click', () => {
  try {
    const u = new SpeechSynthesisUtterance(learnText()); u.lang = S.tl === 'de' ? 'de-DE' : 'es-ES';
    const voices = speechSynthesis.getVoices(); if (voices.length && !voices.some((v) => v.lang.startsWith(S.tl))) toast('No voice for this language on this computer.');
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch (e) { toast('Listening is not available.'); }
});
$('btnShow').addEventListener('click', () => {
  const st = $('showText'); st.textContent = learnText(); setLayer($('showLayer'), true);
  let size = 120; st.style.fontSize = size + 'px'; const L = $('showLayer');
  while ((st.scrollHeight > L.clientHeight - 48 || st.scrollWidth > L.clientWidth - 48) && size > 16) { size -= 4; st.style.fontSize = size + 'px'; }
});
$('showLayer').addEventListener('click', () => setLayer($('showLayer'), false));

// ----- microphone -----
const REC = { on: false };
$('mic').addEventListener('click', () => { if (REC.on) stopRec(); else if (S.state !== 'listening' && !S.busy) startRec(); });
function renderLive(finalText, interim) {
  const box = $('liveText'); box.textContent = ''; const inner = el('span', 'live-inner');
  inner.append(document.createTextNode(finalText)); if (interim) inner.append(el('span', 'interim', interim)); inner.append(el('span', 'caret'));
  box.append(inner); box.scrollTop = box.scrollHeight;
}
async function startRec() {
  if (S.state === 'result') toIdle();
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } }); }
  catch (e) { noSound(); return; }
  Object.assign(REC, { on: true, stream, chunks: [], peak: 0, t0: performance.now(), ws: null, queue: [], done: false, result: null, fallback: false, liveText: '' });
  setHome('listening'); renderLive('', ''); micLine('Listening… 0.0 s / 15 s', 'status');
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 }); REC.ctx = ctx;
  const src = ctx.createMediaStreamSource(stream); const proc = ctx.createScriptProcessor(2048, 1, 1); REC.proc = proc;
  proc.onaudioprocess = (ev) => {
    if (!REC.on) return; const f = ev.inputBuffer.getChannelData(0); const pcm = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) { const s = Math.max(-1, Math.min(1, f[i])); pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff; const a = Math.abs(pcm[i]); if (a > REC.peak) REC.peak = a; }
    REC.chunks.push(pcm);
    if (REC.ws && REC.ws.readyState === 1) REC.ws.send(pcm.buffer); else REC.queue.push(pcm.buffer);
  };
  src.connect(proc); proc.connect(ctx.destination);
  REC.timer = setInterval(() => { const s = (performance.now() - REC.t0) / 1000; micLine(`Listening… ${Math.min(15, s).toFixed(1)} s / 15 s`, 'status'); if (s >= 15) stopRec(); }, 100);
  try {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/capture/live`); ws.binaryType = 'arraybuffer'; REC.ws = ws;
    const openT = setTimeout(() => { if (ws.readyState !== 1) REC.fallback = true; }, 8000);
    ws.onopen = () => { clearTimeout(openT); REC.queue.forEach((b) => ws.send(b)); REC.queue = []; };
    ws.onmessage = (m) => {
      let d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (d.type === 'interim' && REC.on) { REC.liveText = d.text; renderLive('', d.text); }
      else if (d.type === 'result') REC.result = d;
      else if (d.type === 'fallback' || d.type === 'error') { REC.fallback = true; if (d.type === 'error') REC.error = d.detail; }
    };
    ws.onclose = () => { if (!REC.result) REC.fallback = true; };
    ws.onerror = () => { REC.fallback = true; };
  } catch (e) { REC.fallback = true; }
}
async function stopRec() {
  if (!REC.on) return; REC.on = false; clearInterval(REC.timer);
  try { REC.proc.disconnect(); REC.ctx.close(); REC.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* ignore */ }
  if (REC.peak < 300) { try { REC.ws && REC.ws.close(); } catch (e) { /* */ } noSound(); return; }
  S.busy = true; micLine('Translating…', 'status');
  if (REC.ws && REC.ws.readyState === 1) REC.ws.send('stop');
  const t0 = Date.now();
  while (!REC.result && !REC.fallback && Date.now() - t0 < 8000) await sleep(50);
  try { REC.ws && REC.ws.close(); } catch (e) { /* */ }
  if (REC.result) { await showResult(REC.result); return; }
  if (REC.error) { S.busy = false; setHome('idle'); micLine(REC.error, 'tap'); loadHome(); return; }
  try {
    const wav = toWav(REC.chunks);
    const r = await fetch('/api/capture/audio', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Could not transcribe.');
    await showResult(d);
  } catch (e) { S.busy = false; setHome('idle'); micLine(e.message === 'Failed to fetch' ? "Can't reach the app server." : e.message, 'tap'); loadHome(); }
}
function noSound() {
  S.busy = false; setHome('idle'); loadHome();
  micLine('No sound from the microphone. Check the mic permission.', 'tap nosound');
  setTimeout(() => { if (S.state === 'idle' && $('micLine').classList.contains('nosound')) micLine('Tap to speak', 'tap'); }, 4000);
}
function toWav(chunks) {
  const n = chunks.reduce((a, c) => a + c.length, 0); const max = 16000 * 15; const len = Math.min(n, max);
  const buf = new ArrayBuffer(44 + len * 2); const v = new DataView(buf);
  const w = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + len * 2, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, len * 2, true);
  let o = 44, k = 0; for (const c of chunks) for (let i = 0; i < c.length && k < len; i++, k++, o += 2) v.setInt16(o, c[i], true);
  return buf;
}

// =====================================================================
// WORDLIST
// =====================================================================
const WL = { dir: 'couldnt_say', kind: 'all', period: 'any', q: '' };
(function initWordlist() {
  const box = $('wlToggles');
  box.append(toggleGroup([['couldnt_say', "Couldn't say"], ['didnt_catch', "Didn't catch"]], WL.dir, (v) => { WL.dir = v; loadWordlist(); }));
  box.append(toggleGroup([['all', 'All'], ['word', 'Words'], ['sentence', 'Sentences']], WL.kind, (v) => { WL.kind = v; loadWordlist(); }));
  box.append(toggleGroup([['any', 'Any date'], ['today', 'Today'], ['7', '7 days'], ['30', '30 days']], WL.period, (v) => { WL.period = v; loadWordlist(); }));
  let t; $('wlSearch').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { WL.q = e.target.value.trim(); loadWordlist(); }, 200); });
})();
async function loadWordlist() {
  const p = new URLSearchParams({ direction: WL.dir }); if (WL.kind !== 'all') p.set('granularity', WL.kind); if (WL.q) p.set('q', WL.q);
  if (WL.period === 'today') p.set('from', dayStart()); else if (WL.period !== 'any') p.set('from', daysAgo(Number(WL.period)));
  let rows = []; try { rows = await api('/api/entries?' + p); } catch (e) { toast(e.message); }
  const list = $('wlList'); list.textContent = '';
  rows.forEach((r) => list.append(entryRow(r)));
  $('wlEmpty').classList.toggle('hidden', rows.length > 0);
}
function entryRow(r) {
  const row = el('div', 'row has-count'); const th = el('div', 'thumb', r.target_lang.toUpperCase());
  const tx = el('div', 'row-text'); tx.append(el('div', 'row-title', r.target_text), el('div', 'row-sub', r.english_text));
  const cnt = el('div', 'row-count'); if (r.is_weak) cnt.append(icon('flame', 16)); cnt.append(document.createTextNode(`${r.lookup_count}×`));
  row.append(th, tx, cnt); longPress(row, () => openCtx(r)); return row;
}
function longPress(node, fn) {
  let t = null; let x0, y0;
  node.addEventListener('pointerdown', (e) => { x0 = e.clientX; y0 = e.clientY; node.classList.add('pressed'); t = setTimeout(() => { t = null; node.classList.remove('pressed'); fn(); }, 450); });
  const cancel = () => { if (t) clearTimeout(t); t = null; node.classList.remove('pressed'); };
  node.addEventListener('pointermove', (e) => { if (Math.abs(e.clientX - x0) > 8 || Math.abs(e.clientY - y0) > 8) cancel(); });
  node.addEventListener('pointerup', cancel); node.addEventListener('pointerleave', cancel); node.addEventListener('pointercancel', cancel);
  node.addEventListener('contextmenu', (e) => e.preventDefault());
}
function openCtx(r) {
  const pv = $('ctxPreview'); pv.textContent = ''; pv.append(el('div', 't', r.target_text), el('div', 's', r.english_text));
  const m = $('ctxMenu'); m.textContent = '';
  const item = (label, ic, fn, cls = '') => { const b = el('button', 'ctx-item ' + cls); b.append(el('span', null, label), icon(ic, 20)); b.addEventListener('click', (e) => { e.stopPropagation(); closeCtx(); fn(); }); return b; };
  m.append(item('Add to lesson', 'list-plus', () => { if (!S.lessonInclude.includes(r.id)) S.lessonInclude = S.lessonInclude.concat(r.id).slice(-8); toast('Added to the next lesson.'); }));
  m.append(item('Copy', 'copy', async () => { try { await navigator.clipboard.writeText(r.target_text); toast('Copied.'); } catch (e) { toast('Clipboard is not available.'); } }));
  m.append(el('div', 'ctx-gap'));
  m.append(item('Delete', 'trash-2', () => confirmDelete(r), 'destructive'));
  setLayer($('ctx'), true);
}
function closeCtx() { setLayer($('ctx'), false); }
$('ctx').addEventListener('click', (e) => { if (e.target.closest('.ctx-menu')) return; closeCtx(); });
function confirmDelete(r) {
  setLayer($('alert'), true);
  $('alertCancel').onclick = () => setLayer($('alert'), false);
  $('alertDelete').onclick = async () => { setLayer($('alert'), false); try { await api(`/api/entries/${r.id}`, { method: 'DELETE' }); loadWordlist(); } catch (e) { toast(e.message); } };
}

// =====================================================================
// LESSON
// =====================================================================
const LS = { kind: 'words', direction: 'all', period: '7' };
(function initLesson() {
  const box = $('lsToggles');
  box.append(toggleGroup([['words', 'Words'], ['sentences', 'Sentences'], ['both', 'Both']], LS.kind, (v) => { LS.kind = v; }));
  box.append(toggleGroup([['all', 'All'], ['couldnt_say', "Couldn't say"], ['didnt_catch', "Didn't catch"]], LS.direction, (v) => { LS.direction = v; }));
  box.append(toggleGroup([['today', 'Today'], ['7', '7 days'], ['30', '30 days'], ['all', 'All']], LS.period, (v) => { LS.period = v; }));
  $('btnStartLesson').addEventListener('click', () => startLesson({ ...LS }, null, true));
})();
async function loadRecent() {
  let rows = []; try { rows = await api(`/api/lesson/recent-filters?day_start=${encodeURIComponent(dayStart())}`); } catch (e) { toast(e.message); }
  const list = $('recentList'); list.textContent = '';
  rows.forEach((f) => {
    const row = el('div', 'row'); const th = el('div', 'thumb'); th.append(icon('circle-play', 26)); th.style.background = 'transparent';
    const tx = el('div', 'row-text'); tx.append(el('div', 'row-title', f.label), el('div', 'row-sub', `${f.count} · ${relDay(f.last_used_at)}`));
    row.append(th, tx); row.addEventListener('click', () => startLesson({ kind: f.kind, direction: f.direction, period: f.period }, f.label)); list.append(row);
  });
  if (!rows.length) list.append(el('div', 'empty', 'Nothing here yet.'));
}
const L = {};
async function startLesson(filters, label, fromTab = false) {
  const body = { ...filters };
  if (body.today) body.day = today();
  else if (body.period === 'today') body.since = dayStart();
  else if (body.period !== 'all') body.since = daysAgo(Number(body.period));
  if (fromTab && S.lessonInclude.length) body.include_ids = S.lessonInclude;
  let d; try { d = await api('/api/lesson/start', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { toast(e.message); return; }
  if (fromTab) S.lessonInclude = [];
  if (!d.cards.length) { toast('No cards for these choices.'); return; }
  Object.assign(L, { cards: d.cards.map((c) => ({ ...c, requeued: false })), i: 0, pending: 0, score: 0, answered: 0, fromToday: !!filters.today, open: true, flipped: false });
  $('lessonTitle').textContent = label || d.label; $('lessonEnd').classList.add('hidden'); $('lessonMain').classList.remove('hidden');
  showCard(); openSheet('cardSheet'); if (S.state === 'idle') loadHome();
}
function showCard() {
  const c = L.cards[L.i]; const left = L.cards.length - L.i;
  if (!c) { finishLesson(); return; }
  $('lessonCount').textContent = `Card ${L.i + 1} · ${left - 1} left`;
  L.flipped = false; const f = $('flash'); f.classList.remove('back'); $('flashText').textContent = c.english_text; $('flashHint').classList.remove('hidden');
  const r = $('flashReason'); r.textContent = '';
  if (L.fromToday && c.reason) { r.append(icon('bot', 15), el('span', null, c.reason)); r.classList.remove('hidden'); } else r.classList.add('hidden');
  $('answerPair').style.visibility = 'hidden';
}
$('flash').addEventListener('click', () => {
  const c = L.cards[L.i]; if (!c) return; L.flipped = !L.flipped; $('flash').classList.toggle('back', L.flipped);
  $('flashText').textContent = L.flipped ? c.target_text : c.english_text; $('flashHint').classList.add('hidden'); $('answerPair').style.visibility = 'visible';
});
async function answer(remembered) {
  const c = L.cards[L.i]; if (!c) return; L.i += 1;
  if (!c.requeued) { L.answered += 1; if (remembered) L.score += 1; }
  L.pending += 1; showCard();
  try {
    const d = await api('/api/lesson/result', { method: 'POST', body: JSON.stringify({ entry_id: c.entry_id, remembered, already_requeued: c.requeued, from_today: L.fromToday, day: today() }) });
    if (L.open) {
      toast(d.why || 'Recorded');
      if (d.requeue && !c.requeued) { L.cards.push({ ...c, requeued: true }); if (L.i < L.cards.length) $('lessonCount').textContent = `Card ${L.i + 1} · ${L.cards.length - L.i - 1} left`; }
    }
  } catch (e) { toast(e.message); }
  L.pending -= 1;
  if (L.open && L.i >= L.cards.length) finishLesson();
}
$('btnForgot').addEventListener('click', () => answer(false));
$('btnRemembered').addEventListener('click', () => answer(true));
function finishLesson() {
  $('lessonMain').classList.add('hidden'); $('lessonEnd').classList.remove('hidden');
  if (L.pending > 0) { $('lessonEnd').querySelector('.sheet-title').textContent = 'Checking your answers…'; $('lessonScore').textContent = ''; $('btnLessonDone').classList.add('hidden'); $('lessonCount').textContent = ''; return; }
  $('lessonEnd').querySelector('.sheet-title').textContent = 'Lesson complete'; $('btnLessonDone').classList.remove('hidden');
  $('lessonScore').textContent = `${L.score} / ${L.answered}`; $('lessonCount').textContent = '';
}
$('btnLessonDone').addEventListener('click', closeSheet);
function lessonClosed() { L.open = false; if (home.classList.contains('active') && S.state === 'idle') loadHome(); }

// =====================================================================
// WORDBOOK
// =====================================================================
const BOOKS = {
  couldnt_say: { vc: '#FF0056', dark: '#A80039', soft: '#FFE5EE', kicker: 'SPEAKING', t: ["Couldn't", 'say'] },
  didnt_catch: { vc: '#5C0A2B', dark: '#2B0413', soft: '#F6E7ED', kicker: 'LISTENING', t: ["Didn't", 'catch'] },
};
async function loadShelf() {
  let rows = []; try { rows = await api('/api/entries?granularity=word'); } catch (e) { toast(e.message); }
  const shelf = $('shelf'); shelf.textContent = '';
  Object.entries(BOOKS).forEach(([dir, b]) => {
    const words = rows.filter((r) => r.direction === dir); const weak = words.filter((r) => r.is_weak).length;
    const card = el('div', 'shelf-card'); card.style.setProperty('--vc', b.vc); card.style.setProperty('--dark', b.dark); card.style.setProperty('--soft', b.soft);
    const cover = el('button', 'cover'); cover.setAttribute('aria-label', `Open ${b.t.join(' ')}`);
    const title = el('div', 'cover-t'); title.append(document.createTextNode(b.t[0]), el('br'), document.createTextNode(b.t[1]));
    cover.append(el('div', 'cover-k', b.kicker), title, el('div', 'cover-stripe'), el('div', 'cover-n', `${words.length} entries`), el('div', 'cover-lang', langCode()));
    cover.addEventListener('click', () => openBook(dir));
    const info = el('div', 'shelf-info');
    const stats = el('div', 'stats');
    [[words.length, 'Words'], [weak, 'Weak']].forEach(([n, l]) => { const s = el('div', 'stat'); s.append(el('b', null, String(n)), el('span', null, l)); stats.append(s); });
    const btns = el('div', 'shelf-btns');
    const bo = el('button', 'b-open'); bo.append(icon('book-open', 15), document.createTextNode('Open')); bo.addEventListener('click', () => openBook(dir));
    const bl = el('button', 'b-lesson'); bl.append(icon('layers', 15), document.createTextNode('Lesson')); bl.addEventListener('click', () => startLesson({ kind: 'words', direction: dir, period: 'all' }, null));
    btns.append(bo, bl);
    info.append(el('div', 'shelf-title', DIR[dir]), stats, btns);
    card.append(cover, info); shelf.append(card);
  });
}

// ----- progress bar loader (spring toward 90%, jump to 100%) -----
const PB = { target: 0, x: 0, v: 0, raf: 0, timer: 0 };
function loaderStart() {
  setLayer($('loader'), true); Object.assign(PB, { target: 0.15, x: 0, v: 0 });
  clearInterval(PB.timer); PB.timer = setInterval(() => { PB.target = Math.min(0.9, PB.target + (0.9 - PB.target) * 0.25); }, 250);
  const tick = () => { const f = (PB.target - PB.x) * 170 - PB.v * 26; PB.v += f / 60; PB.x += PB.v / 60; $('pbBar').style.transform = `scaleX(${Math.max(0, Math.min(1, PB.x))})`; PB.raf = requestAnimationFrame(tick); };
  cancelAnimationFrame(PB.raf); tick();
}
async function loaderEnd() { clearInterval(PB.timer); PB.target = 1; await sleep(260); cancelAnimationFrame(PB.raf); setLayer($('loader'), false); $('pbBar').style.transform = 'scaleX(0)'; }

const BK = { dir: null, entries: [], pages: [], page: 0, sheet: 'off', opening: false, edge: 0 };
const ORDERS = [['newest', 'Newest first'], ['oldest', 'Oldest first'], ['az', 'A to Z'], ['weak', 'Weak words first']];
async function waitCapturing() { const t0 = Date.now(); while (Date.now() - t0 < 30000) { try { const p = await api('/api/wordbook/pending'); if (!p.capturing) return p; } catch (e) { return null; } await sleep(400); } return null; }
function sortEntries(list) {
  const o = storeGet('wbOrder', 'newest'); const a = list.slice();
  if (o === 'newest') a.sort((x, y) => (y.created_at.localeCompare(x.created_at)) || y.id - x.id);
  if (o === 'oldest') a.sort((x, y) => (x.created_at.localeCompare(y.created_at)) || x.id - y.id);
  if (o === 'az') a.sort((x, y) => x.target_text.localeCompare(y.target_text, S.tl));
  if (o === 'weak') a.sort((x, y) => (y.is_weak - x.is_weak) || y.lookup_count - x.lookup_count || y.created_at.localeCompare(x.created_at));
  return a;
}
async function fetchBook() { await waitCapturing(); const rows = await api(`/api/entries?granularity=word&direction=${BK.dir}`); BK.entries = sortEntries(rows); }
async function openBook(dir) {
  if (BK.opening) return; BK.opening = true; BK.dir = dir; BK.page = 0;
  const b = BOOKS[dir]; const layer = $('book'); layer.style.setProperty('--vc', b.vc);
  $('bookTitle').textContent = DIR[dir];
  loaderStart();
  try {
    await fetchBook(); setLayer(layer, true); layer.style.opacity = 0; paginate(); await ensureExamples(); paginate(); renderPage();
  } catch (e) { toast(e.message); }
  await loaderEnd(); layer.style.opacity = ''; BK.opening = false;
}
$('bookBack').addEventListener('click', () => { setLayer($('book'), false); loadShelf(); });
async function ensureExamples() {
  const page = BK.pages[BK.page] || []; const need = page.filter((e) => !e.example_checked).map((e) => e.id).slice(0, 8);
  if (!need.length) return;
  const t0 = Date.now();
  try { await api('/api/wordbook/examples', { method: 'POST', body: JSON.stringify({ entry_ids: need }) }); } catch (e) { return; }
  await sleep(600);
  while (Date.now() - t0 < 30000) { const p = await api('/api/wordbook/pending'); if (!need.some((i) => p.pending.includes(i))) break; await sleep(400); }
  await fetchBook();
}
function entryNode(e, n) {
  const d = el('div', 'entry'); d.append(el('div', 'entry-n', String(n)));
  const c = el('div'); const w = el('div', 'entry-w lang-t'); w.append(el('span', 'hidable', e.target_text)); if (e.is_weak) w.append(icon('flame', 15));
  c.append(w, el('div', 'entry-e lang-e hidable', e.english_text));
  if (e.note) c.append(el('div', 'entry-note', e.note));
  if (e.example_target) {
    const ex = el('div', 'entry-ex'); const t = el('div', 'ex-t lang-t hidable'); t.append(...emphasize(e.example_target, e.target_text));
    ex.append(t, el('div', 'ex-e lang-e hidable', e.example_english || '')); c.append(ex);
  }
  d.append(c); return d;
}
function emphasize(sentence, word) {
  const low = sentence.toLowerCase(); const cands = [word, word.replace(/^(el|la|los|las|un|una|der|die|das|ein|eine)\s+/i, '')];
  const toks = word.split(/\s+/).sort((a, b) => b.length - a.length); cands.push(toks[0]);
  for (const c of cands) {
    const i = low.indexOf(c.toLowerCase()); if (c && i >= 0) return [document.createTextNode(sentence.slice(0, i)), el('b', null, sentence.slice(i, i + c.length)), document.createTextNode(sentence.slice(i + c.length))];
  }
  return [document.createTextNode(sentence)];
}
function paginate() {
  const paper = $('paper'); paper.textContent = ''; const pages = []; let cur = [];
  const maxH = paper.clientHeight - 8;
  BK.entries.forEach((e, i) => {
    const node = entryNode(e, i + 1); paper.append(node);
    if (paper.scrollHeight > maxH + 8 && cur.length) { pages.push(cur); cur = []; paper.textContent = ''; paper.append(node); }
    cur.push(e);
  });
  if (cur.length || !pages.length) pages.push(cur);
  BK.pages = pages; BK.page = Math.min(BK.page, pages.length - 1);
}
function renderPage() {
  const paper = $('paper'); paper.textContent = '';
  const page = BK.pages[BK.page] || []; const start = BK.pages.slice(0, BK.page).reduce((a, p) => a + p.length, 0);
  if (!page.length) paper.append(el('div', 'empty', 'Nothing here yet.'));
  page.forEach((e, i) => paper.append(entryNode(e, start + i + 1)));
  $('pgNum').textContent = `${BK.page + 1} / ${BK.pages.length}`;
  $('pgPrev').disabled = BK.page === 0; $('pgNext').disabled = BK.page >= BK.pages.length - 1;
  resetEdge(); applySheet();
}
async function turn(d) {
  const n = BK.page + d; if (n < 0 || n >= BK.pages.length) return;
  const p = await api('/api/wordbook/pending').catch(() => null);
  BK.page = n;
  if (p && p.capturing) { await fetchBook(); paginate(); }
  const page = BK.pages[BK.page] || [];
  if (page.some((e) => !e.example_checked)) { loaderStart(); await ensureExamples(); paginate(); await loaderEnd(); }
  renderPage();
}
$('pgPrev').addEventListener('click', () => turn(-1));
$('pgNext').addEventListener('click', () => turn(1));
new ResizeObserver(() => { if ($('book').classList.contains('open') && !BK.opening) { paginate(); renderPage(); } }).observe($('paperWrap'));

// ----- red sheet -----
function initSheetToggle() {
  const box = $('sheetToggle'); box.textContent = '';
  box.append(toggleGroup([['off', 'Sheet off'], ['t', `Hide ${langCode()}`], ['e', 'Hide EN']], BK.sheet, (v) => { BK.sheet = v; resetEdge(); applySheet(); }));
}
function resetEdge() { const first = $('paper').querySelector('.entry'); BK.edge = first ? first.offsetTop - 3 : 8; }
function applySheet() {
  const paper = $('paper'); const rs = $('redSheet'); paper.classList.remove('ink-t', 'ink-e');
  paper.querySelectorAll('.hid').forEach((n) => n.classList.remove('hid'));
  if (BK.sheet === 'off') { rs.classList.add('hidden'); return; }
  rs.classList.remove('hidden'); rs.style.top = BK.edge + 'px'; paper.classList.add(BK.sheet === 't' ? 'ink-t' : 'ink-e');
  const sel = BK.sheet === 't' ? '.lang-t' : '.lang-e';
  paper.querySelectorAll(sel).forEach((n) => {
    const target = n.classList.contains('hidable') ? n : n.querySelector('.hidable'); if (!target) return;
    const mid = target.offsetTop + target.offsetHeight / 2 + (target.offsetParent === paper ? 0 : relTop(target, paper) - target.offsetTop);
    target.classList.toggle('hid', mid > BK.edge);
  });
}
function relTop(node, root) { let y = 0; let n = node; const rr = root.getBoundingClientRect(); y = n.getBoundingClientRect().top - rr.top; return y; }
(function dragHandle() {
  const h = $('redHandle'); let y0 = null, e0 = 0;
  h.addEventListener('pointerdown', (e) => { y0 = e.clientY; e0 = BK.edge; h.setPointerCapture(e.pointerId); e.preventDefault(); });
  h.addEventListener('pointermove', (e) => { if (y0 == null) return; const H = $('paper').clientHeight; BK.edge = Math.max(0, Math.min(H - 10, e0 + e.clientY - y0)); applySheet(); });
  const up = () => { y0 = null; }; h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
})();
$('paper').addEventListener('click', (e) => { const n = e.target.closest('.hid'); if (!n) return; n.classList.add('peek'); setTimeout(() => n.classList.remove('peek'), 1400); });

// ----- wordbook settings -----
function renderOrders() {
  const g = $('orderGroup'); g.textContent = ''; const cur = storeGet('wbOrder', 'newest');
  ORDERS.forEach(([v, label]) => {
    const r = el('button', 'grow-row'); r.append(el('span', null, label)); if (v === cur) r.append(icon('check', 20));
    r.addEventListener('click', () => { storeSet('wbOrder', v); setLayer($('wbSettingsPanel'), false); BK.entries = sortEntries(BK.entries); BK.page = 0; paginate(); renderPage(); });
    g.append(r);
  });
}
$('bookGear').addEventListener('click', () => { renderOrders(); openPanel('wbSettingsPanel'); });

// ---------- boot ----------
(async function boot() {
  fillIcons(); renderTabs('home');
  try { const s = await api('/api/settings'); S.tl = s.target_lang; } catch (e) { /* offline */ }
  initSheetToggle(); sizeCards(); loadHome();
})();
