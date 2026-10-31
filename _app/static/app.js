'use strict';
/* Chuleta: interfaz (vanilla JS, sin dependencias). Habla con server.py en /api/* */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const enc = encodeURIComponent;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nfc = s => (s || '').normalize('NFC');
const pad = n => String(n).padStart(2, '0');
const sum = (arr, f) => arr.reduce((t, x) => t + (f(x) || 0), 0);

/* minusculas y sin acentos, conservando la longitud (para resaltar coincidencias en el texto original) */
function foldMap(s) {
  let o = '';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c.charCodeAt(0) < 128) { o += c.toLowerCase(); continue; }
    const d = (c.normalize('NFD')[0] || c).toLowerCase();
    o += d.length === 1 ? d : c;
  }
  return o;
}
const fold = s => foldMap(nfc(s));
const termsOf = q => fold(q || '').split(/\s+/).filter(Boolean);

const I = {
  caret: '<svg class="caret" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  doc: '<svg class="fi" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  cloud: '<svg class="cloudmark" viewBox="0 0 24 24"><path d="M7 18a4.5 4.5 0 1 1 .6-8.96A6 6 0 0 1 19 11a3.5 3.5 0 0 1-.5 7z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/></svg>',
  wave: '<svg viewBox="0 0 24 24"><path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/></svg>',
  move: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M10 13h6m-2-2 2 2-2 2"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 3h7v7M21 3l-9 9"/><path d="M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z"/><path d="M19 17v4M17 19h4"/></svg>',
  text: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
  /* iconos de linea (monocromos, currentColor) que sustituyen a los emojis de la interfaz */
  home: '<svg viewBox="0 0 24 24"><path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v10h14V10"/></svg>',
  list: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M8 10v9"/></svg>',
  table: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 10v10"/></svg>',
  calendar: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/></svg>',
  micOff: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6M4 4l16 16"/></svg>',
  volume: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/></svg>',
  chat: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z"/></svg>',
  notes: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  nube: '<svg viewBox="0 0 24 24"><path d="M7 18a4.5 4.5 0 1 1 .6-8.96A6 6 0 0 1 19 11a3.5 3.5 0 0 1-.5 7z"/></svg>',
  monitor: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  book: '<svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>',
  package: '<svg viewBox="0 0 24 24"><path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/></svg>',
  voice: '<svg viewBox="0 0 24 24"><circle cx="9" cy="9" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M18 8a4 4 0 0 1 0 6M20.5 5.5a8 8 0 0 1 0 11"/></svg>',
  users: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M17.5 14a6 6 0 0 1 3.5 6"/></svg>',
  bolt: '<svg viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
  scale: '<svg viewBox="0 0 24 24"><path d="M12 3v18M7 21h10M4 7h16"/><path d="M7 7l-3.5 8a3.5 3.5 0 0 0 7 0zM17 7l-3.5 8a3.5 3.5 0 0 0 7 0z"/></svg>',
  gauge: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/></svg>',
  target: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="m12 3 2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.4l-5.7 3.1 1.2-6.4L2.8 9.7l6.4-.8z"/></svg>',
  starOn: '<svg viewBox="0 0 24 24"><path d="m12 3 2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.4l-5.7 3.1 1.2-6.4L2.8 9.7l6.4-.8z" fill="currentColor"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  tag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l9 9-8 8z"/><circle cx="7.5" cy="8.5" r="1.2" fill="currentColor"/></svg>',
  topic: '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2h6"/></svg>',
  state: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/></svg>',
  clipboard: '<svg viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4a2 2 0 0 1 6 0M9 11h6M9 15h4"/></svg>',
  keyboard: '<svg viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/></svg>',
  phone: '<svg viewBox="0 0 24 24"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/></svg>',
  power: '<svg viewBox="0 0 24 24"><path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>',
  face: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0"/><path d="M9 10h.01M15 10h.01"/></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
};
/* iconos de los presets de transcripcion por id; el emoji que manda engines.py queda solo de reserva */
const PRESET_ICONS = { rapido: 'bolt', turbo: 'scale', medium: 'gauge', calidad: 'target', nube: 'nube' };
const presetIcon = p => !p ? '' : PRESET_ICONS[p.id] ? I[PRESET_ICONS[p.id]] : (p.icon ? esc(p.icon) + ' ' : '');

const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const DIA = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
const LANGS = [['es-en', 'Español + inglés (spanglish)'], ['', 'Detectar (cualquier idioma)'], ['es', 'Solo español'], ['en', 'Solo inglés'],
  ['pt', 'Português'], ['fr', 'Français'], ['it', 'Italiano'], ['de', 'Deutsch'], ['ca', 'Català'],
  ['es-pt', 'Español + portugués (mezcla)'], ['en-pt', 'Inglés + portugués (mezcla)']];
const langSelect = (id, sel) => `<select id="${id}" title="Idioma de la reunión">${LANGS.map(([v, l]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const MODEL_GB = { 'small': '0,5 GB', 'medium': '1,5 GB', 'large-v3-turbo': '1,6 GB', 'large-v3': '3 GB' };

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = (sec / 3600) | 0, m = ((sec % 3600) / 60) | 0, s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
function fmtDur(sec) {
  if (!sec) return '—';
  const m = Math.round(sec / 60);
  return m < 60 ? `${m} min` : `${(m / 60) | 0} h ${pad(m % 60)}`;
}
function fmtHours(sec) { return (sec / 3600).toFixed(sec < 36000 ? 1 : 0).replace('.', ','); }
function fmtLeft(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return `${sec} s`;
  if (sec < 3600) return `${(sec / 60) | 0} min ${pad(sec % 60)} s`;
  return `${(sec / 3600) | 0} h ${pad(((sec % 3600) / 60) | 0)} min`;
}
function fmtEst(sec) {
  if (sec < 55) return `~${Math.max(5, Math.round(sec / 5) * 5)} s`;
  const m = Math.round(sec / 60);
  return m < 90 ? `~${m} min` : `~${(sec / 3600).toFixed(1).replace('.', ',')} h`;
}
function fmtSize(b) { return b >= 1e9 ? (b / 1e9).toFixed(1).replace('.', ',') + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB'; }
const aDate = a => a.date ? new Date(a.date) : null;
function shortTitle(a) {
  const d = aDate(a);
  if (!d) return nfc(a.stem);
  const y = d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : '';
  return `${d.getDate()} ${MES[d.getMonth()]}${y} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function longTitle(a) {
  const d = aDate(a);
  if (!d) return nfc(a.stem);
  return `${DIA[d.getDay()]} ${d.getDate()} ${MES[d.getMonth()]} ${d.getFullYear()} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const fullTitle = a => (a.props && a.props.titulo) ? `${nfc(a.props.titulo)} · ${shortTitle(a)}` : longTitle(a) + (a.label ? ` · ${nfc(a.label)}` : '');
const provName = () => S.config?.providers?.[S.config.cloud?.provider]?.label || 'la nube';
const estDuration = a => a.duration || (a.ext === '.wav' ? a.size / 192000 : a.size / 16000);
/* nombres de carpeta: las vinculadas (fuera de Audios) van por dentro como ":<id>/…" y se ven con su nombre */
const nodeName = p => nfc(S.folders.get(p)?.name ?? p.split('/').pop());
const folderName = p => p ? nodeName(p) : `${APP_NAME} (raíz)`;
const pathLabel = p => p.split('/').map((_, i, parts) => nodeName(parts.slice(0, i + 1).join('/'))).join(' › ');

function hlTerms(text, terms) {
  if (!terms || !terms.length) return esc(text);
  const f = foldMap(text), ranges = [];
  for (const t of terms) { let k = 0; while ((k = f.indexOf(t, k)) !== -1) { ranges.push([k, k + t.length]); k += t.length; } }
  if (!ranges.length) return esc(text);
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges) { const l = merged[merged.length - 1]; if (l && r[0] <= l[1]) l[1] = Math.max(l[1], r[1]); else merged.push([...r]); }
  let out = '', i = 0;
  for (const [a, b] of merged) { out += esc(text.slice(i, a)) + '<mark>' + esc(text.slice(a, b)) + '</mark>'; i = b; }
  return out + esc(text.slice(i));
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Audios': '1' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let j;
  try { j = await r.json(); } catch { j = { error: r.statusText } }
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

/* ---------------------------------------------------------------- estado */
// Nombre de la app: el servidor lo pone en <title> (common.APP_NAME) y lo confirma /api/config (setConfig)
let APP_NAME = document.title;
const S = {
  config: null, presetsById: {}, tree: null, version: '',
  audios: new Map(), folders: new Map(), docs: new Map(),
  jobs: [], jobsById: new Map(), jobsSig: '',
  route: { kind: 'home', path: '', params: new URLSearchParams() },
  exp: new Set(JSON.parse(localStorage.getItem('exp') || '[]')),
  mode: localStorage.getItem('txmode') || 'para',
  follow: localStorage.getItem('follow') !== '0',
  rate: +localStorage.getItem('rate') || 1,
  selPreset: null, selLang: null, boxes: {}, txOpen: false,
  tx: null, tq: '', marks: [], markIdx: -1, curIdx: -1, curEl: null, userScroll: 0,
  playing: null, dragging: false,
};
const main = $('#main');
const audio = $('#audio');
const saveExp = () => localStorage.setItem('exp', JSON.stringify([...S.exp]));

function indexTree(tree) {
  S.audios.clear(); S.folders.clear(); S.docs.clear();
  (function walk(n, parent) {
    n.parent = parent; S.folders.set(n.path, n);
    let total = 0, done = 0, secs = 0;
    for (const f of n.folders) { walk(f, n); total += f.total; done += f.done; secs += f.secs; }
    for (const a of n.audios) { a.parent = n; S.audios.set(a.path, a); total++; if ((a.txt || a.srt) && !a.partial) done++; secs += estDuration(a); }
    for (const d of n.docs) { d.parent = n; S.docs.set(d.path, d); }
    n.total = total; n.done = done; n.secs = secs;
  })(tree, null);
  S.tree = tree;
}
const allAudios = () => [...S.audios.values()];
const audiosUnder = f => { const out = []; (function w(n) { out.push(...n.audios); n.folders.forEach(w); })(f); return out; };
const isActive = j => j.status === 'queued' || j.status === 'running';
const jobFor = path => S.jobs.find(j => j.path === path && isActive(j) && j.kind !== 'summary');  // los resúmenes van aparte
const lastJobFor = path => { for (let i = S.jobs.length - 1; i >= 0; i--) if (S.jobs[i].path === path) return S.jobs[i]; };
function statusOf(a) {
  const j = jobFor(a.path);
  if (j && j.live) return 'rec';
  if (j) return j.status;
  if (a.recording) return 'rec';
  if (a.txt || a.srt) return a.partial ? 'partial' : 'done';
  const l = lastJobFor(a.path);
  return l && l.status === 'error' ? 'error' : 'pending';
}
function statusPill(a) {
  const j = jobFor(a.path);
  if (j && j.live) return `<span class="pill rec"><i class="recdot"></i>En directo</span>`;
  if (a.recording) return '<span class="pill rec"><i class="recdot"></i>Grabando</span>';
  if (j && j.status === 'running') return `<span class="pill run">Transcribiendo ${Math.round(j.progress * 100)} %</span>`;
  if (j) return '<span class="pill run">En cola</span>';
  const st = statusOf(a);
  if (st === 'done') return `<span class="pill ok">${I.check}Transcrito</span>`;
  if (st === 'partial') return `<span class="pill pending" title="La transcripción se cortó antes del final">A medias · hasta ${fmtClock(a.tx_end)}</span>`;
  if (st === 'error') return '<span class="pill err">Error</span>';
  return '<span class="pill pending">Sin transcribir</span>';
}

/* ---------------------------------------------------------------- en directo */
const liveJob = a => { const j = jobFor(a.path); return j && j.live ? j : null; };
/* "en vivo" = OBS sigue grabando (lo dice el servidor o la propia sesion en directo) */
const isLive = a => { const j = liveJob(a); return j ? !!j.recording : !!a.recording; };
const liveAudios = () => allAudios().filter(isLive);
const recordedOf = a => { const j = liveJob(a); return j ? j.recorded : (a.duration || 0); };

/* reloj de lo grabado: el servidor solo lo actualiza cada pocos segundos, aqui se interpola */
const clocks = {};
function syncClock(a) {
  const r = recordedOf(a), c = clocks[a.path];
  if (!c || Math.abs(r - c.r) > 0.05) clocks[a.path] = { r, at: Date.now() };
}
function clockNow(path) {
  const c = clocks[path], a = S.audios.get(path);
  if (!c) return a ? recordedOf(a) : 0;
  return c.r + (a && isLive(a) ? Math.min(8, (Date.now() - c.at) / 1000) : 0);
}
setInterval(() => {
  $$('[data-rec-clock]').forEach(el => { const p = el.dataset.recClock || el.closest('[data-live-path]')?.dataset.livePath; if (p) el.textContent = fmtClock(clockNow(p)); });
}, 500);

function liveFit(p) {
  const s = p.live_speed || 1;
  return s >= 1.6 ? ['ok', 'va al día'] : s >= 1.05 ? ['warn', 'va justo'] : ['bad', 'se irá retrasando'];
}
function liveModels(sel, j) {
  return `<div class="lmodels">${S.config.presets.map(p => {
    const off = p.engine === 'cloud' && !S.config.cloud.has_key;
    const [cls, txt] = liveFit(p);
    return `<button class="lmodel${p.id === sel ? ' on' : ''}${off ? ' off' : ''}" data-act="live-model" data-preset="${p.id}"${j ? ` data-id="${j.id}"` : ''} title="${esc(p.live_note || p.hint || '')}">`
      + `<div class="lm-n">${presetIcon(p)}${esc(p.live_label || p.label)}${p.id === 'medium' ? ' <span class="lm-rec">★</span>' : ''}</div>`
      + `<div class="lm-s">${off ? 'falta API key' : `<i class="fit ${cls}"></i>~${String(p.live_speed).replace('.', ',')}× · ${txt}`}</div></button>`;
  }).join('')}</div>`;
}
function liveStatus(a, j) {
  if (!j) return a.recording ? 'La transcripción en directo está parada. Elige modelo y pulsa «Transcribir en directo».' : '';
  if (/Cargando|Descargando|Reinici|Rehaciendo|Empezando/.test(j.stage)) return esc(j.stage);
  const lag = Math.max(0, j.recorded - j.transcribed);
  const p = S.presetsById[j.preset] || {};
  if (lag <= 75) return `<b>Al día</b> · el texto va unos ${Math.max(5, Math.round(lag / 5) * 5)} s por detrás de lo que se dice`;
  const slow = (p.live_speed || 1) < 1.05 && j.recording;
  return `<b>Poniéndose al día</b> · ${fmtLeft(lag)} por detrás${slow ? ' · este modelo no da abasto en directo: terminará después de la grabación' : ''}`;
}
function renderLiveHero(a) {
  const el = $('#lHero');
  if (!el) return;
  const j = liveJob(a);
  const sel = j ? j.preset : (S.livePreset || S.config.live_preset || 'medium');
  const mine = !!(S.rec.recording && S.rec.path === a.path);  // la esta grabando esta app (no OBS)
  syncClock(a);
  const muted = !!(mine && S.rec.mic_muted);
  el.dataset.sig = (j ? j.id + j.preset : 'none') + sel + mine + muted;
  el.innerHTML = `<div class="live-hero card" data-live-path="${esc(a.path)}">
    <div class="lh-row">
      <span class="rec-badge"><i class="recdot"></i>EN VIVO</span>
      <span class="lh-clock" data-rec-clock>${fmtClock(clockNow(a.path))}</span>
      <span class="lh-sub">${mine ? `grabando desde ${APP_NAME}` : 'grabado hasta ahora'}</span>
      <span class="sp"></span>
      ${mine && S.rec.mic ? `<button class="btn sm${muted ? ' muted-on' : ''}" data-act="rec-mute" title="Deja de grabar tu voz; el audio del ordenador se sigue grabando">${muted ? I.micOff + 'Micro silenciado · reactivar' : I.mic + 'Silenciar mi micro'}</button>` : ''}
      ${mine ? `<button class="btn sm rec-stop" data-act="rec-stop">${I.stop}Parar grabación</button>` : ''}
      ${j ? `<button class="btn sm danger" data-act="cancel-job" data-id="${j.id}" title="${mine ? 'La grabación sigue; la podrás transcribir después' : ''}">${mine ? 'Pausar transcripción' : 'Detener transcripción'}</button>`
        : `<button class="btn primary" data-act="live" data-path="${esc(a.path)}">${I.spark}Transcribir en directo</button>`}
    </div>
    ${mine ? `<div class="lh-meters">${meterHtml('mic', I.mic + 'Micro')}${meterHtml('sys', I.volume + 'Ordenador')}</div>` : ''}
    ${crumbs(a.folder)}
    <h1>${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(pageTitle(a))}${!titleOf(a) && a.label ? `<span class="sub"> · ${esc(nfc(a.label))}</span>` : ''}</h1>
    <div class="lh-status" data-lh-status>${liveStatus(a, j)}</div>
    <div id="directoBar"></div>
    ${S.vocab.length ? `<div class="lh-ws">${I.topic}Tema ${wsSelect(a)}</div>` : ''}
    ${typeof perfilLiveHtml === 'function' ? perfilLiveHtml(a) : ''}
    ${j ? `<div class="lh-bar" title="Transcrito / grabado"><i data-lh-bar style="width:${(100 * Math.min(1, j.transcribed / Math.max(1, j.recorded))).toFixed(1)}%"></i></div>` : ''}
    <div class="lh-label">Modelo ${j ? '<span class="muted">(el cambio se aplica desde el siguiente trozo)</span>' : ''}</div>
    ${liveModels(sel, j)}
    ${S.presetsById[sel]?.live_note ? `<div class="lh-note">${esc(S.presetsById[sel].live_note)}</div>` : ''}
    <div class="lh-actions">
      ${j ? `<button class="btn sm" data-act="live-restart" data-id="${j.id}">${I.refresh}Rehacer desde el principio con este modelo</button>` : ''}
      <button class="btn sm" data-act="play-live" data-path="${esc(a.path)}">${I.play}Escuchar lo grabado</button>
      <button class="btn sm" data-act="consent" title="Texto para pegar en el chat de Teams">${I.clipboard}Copiar aviso de grabación</button>
      <button class="btn sm" data-act="reveal" data-path="${esc(a.path)}">${I.ext}Mostrar en carpeta</button>
    </div></div>`;
  if (typeof renderDirectoBar === 'function') renderDirectoBar(a, j);
}
function updateLiveHero(a) {
  const el = $('#lHero');
  if (!el) return;
  const j = liveJob(a);
  const sel = j ? j.preset : (S.livePreset || S.config.live_preset || 'medium');
  const mine = !!(S.rec.recording && S.rec.path === a.path);
  if (el.dataset.sig !== (j ? j.id + j.preset : 'none') + sel + mine + !!(mine && S.rec.mic_muted)) return renderLiveHero(a);
  syncClock(a);
  const st = $('[data-lh-status]', el);
  if (st) st.innerHTML = liveStatus(a, j);
  const bar = $('[data-lh-bar]', el);
  if (bar && j) bar.style.width = (100 * Math.min(1, j.transcribed / Math.max(1, j.recorded))).toFixed(1) + '%';
}
function renderLiveChip() {
  const lives = liveAudios(), chip = $('#liveChip');
  chip.classList.toggle('hidden', !lives.length);
  if (!lives.length) return;
  const a = lives[0];
  syncClock(a);
  chip.href = href('a', a.path);
  $('[data-rec-clock]', chip).dataset.recClock = a.path;
  chip.title = lives.length > 1 ? `${lives.length} grabaciones en curso` : 'Grabación en curso: ' + fullTitle(a);
}
function liveBanner(a) {
  const j = liveJob(a);
  syncClock(a);
  return `<div class="card live-banner" data-go="a" data-path="${esc(a.path)}" data-live-path="${esc(a.path)}">
    <span class="rec-badge"><i class="recdot"></i>EN VIVO</span>
    <div class="lb-t"><b>${esc(fullTitle(a))}</b><div class="muted">${j ? liveStatus(a, j) : 'Grabando · sin transcripción en directo'}</div></div>
    <span class="lh-clock sm" data-rec-clock>${fmtClock(clockNow(a.path))}</span>
    <span class="btn sm primary">Abrir directo →</span></div>`;
}

/* ---------------------------------------------------------------- grabadora propia */
/* graba el audio del ordenador + el micro (como OBS). El WAV que escribe es igual que el de OBS,
   asi que el resto de la app lo trata como cualquier grabacion en vivo */
S.rec = { recording: false };
let recTimer = null, recDialog = false;
async function pollRec() {
  clearTimeout(recTimer);
  if (S.off) return;
  const was = !!S.rec.recording;
  try { S.rec = await api('/api/rec/status' + (recDialog ? '?monitor=1' : '')); } catch { /* reiniciando */ }
  // la app se ha actualizado y reiniciado: se recarga sola (sin cortar un dialogo ni un audio que suena)
  if (S.rec.build) {
    S.build = S.build || S.rec.build;
    if (S.rec.build !== S.build && $('#modal').classList.contains('hidden') && audio.paused) return location.reload();
  }
  if (was !== !!S.rec.recording) onRecChange();
  renderRecBtn(); updateMeters(); renderMeetChip();
  recTimer = setTimeout(pollRec, S.rec.recording || recDialog ? 300 : 1500);
}
/* empezo o paro la grabacion (tambien con Ctrl+Alt+R desde otro programa) */
function onRecChange() {
  refreshTree(true); pollJobs(true);
  if (S.recLocal) { S.recLocal = false; return; }  // lo ha pedido esta misma pagina: ya avisa ella
  if (S.rec.recording) toast('● Grabando con el perfil por defecto · <b>Ctrl+Alt+R</b> para parar · clic para ajustar idioma, tema y contexto', '', () => go('a', S.rec.path));
  else toast('■ Grabación guardada · la transcripción termina sola');
}
async function toggleRec() {
  try { S.recLocal = true; await api('/api/rec/toggle', {}); pollRec(); }
  catch (err) { S.recLocal = false; toast(esc(err.message), 'err'); }
}
function renderRecBtn() {
  const b = $('#btnRec'), on = !!S.rec.recording;
  b.classList.toggle('on', on);
  b.title = on ? 'Parar la grabación (Ctrl+Alt+R)' : 'Grabar el audio del ordenador y el micrófono (Ctrl+Alt+R)';
  $('span', b).textContent = on ? `Parar · ${fmtClock(S.rec.t || 0)}` : 'Grabar';
}
function meterHtml(kind, label) {
  return `<div class="meter" data-meter="${kind}"><span class="ml">${label}</span><div class="mbar"><i></i></div><small class="mdb">conectando…</small>`
    + `<small class="mdev"></small></div>`;
}
function updateMeters() {
  $$('[data-meter]').forEach(m => {
    const s = S.rec[m.dataset.meter], i = $('.mbar i', m), t = $('.mdb', m);
    if (!s) { i.style.width = '0%'; t.textContent = m.dataset.off || 'conectando…'; m.classList.remove('err'); return; }
    const pct = Math.max(0, Math.min(100, (s.db + 60) / 60 * 100));
    i.style.width = pct + '%';
    i.className = s.db > -3 ? 'hot' : s.db > -50 ? 'ok' : '';
    m.classList.toggle('err', !!s.error);
    t.textContent = s.error ? 'sin señal: ' + s.error.split(':')[0] : s.db <= -99 ? 'silencio' : `${Math.round(s.db)} dB`;
    if (m.dataset.meter === 'mic' && S.rec.mic_muted) { t.textContent = '🔇 silenciado'; i.className = ''; }
    const dev = $('.mdev', m);  // que se vea que dispositivo se esta usando de verdad
    if (dev) dev.textContent = (s.name ? `Usando: ${nfc(s.name)}${s.note ? ` · ${s.note}` : ''}` : (s.note || ''))
      // audio que llego tarde y se perdio: el portatil no da abasto (cierra programas o quita el vídeo)
      + (s.lost >= 0.5 ? ` · ⚠ ${String(s.lost.toFixed(1)).replace('.', ',')} s de audio perdidos: el portátil va muy cargado` : '');
    m.classList.toggle('lost', s.lost >= 0.5);
  });
}
async function stopRec() {
  if (!await ask('¿Parar la grabación?', 'Se guarda el archivo y la transcripción en directo termina sola con lo que falte.', 'Parar')) return;
  try {
    S.recLocal = true;
    const r = await api('/api/rec/stop', {});
    toast(`Grabación guardada · ${fmtLeft(r.seconds)}`);
    pollRec(); refreshTree(true); pollJobs(true);
  } catch (err) { S.recLocal = false; toast(esc(err.message), 'err'); }
}
/* carpetas donde grabar: las de la app, las vinculadas y "otra carpeta…" (se vincula al elegirla) */
const folderOpts = sel => flatFolders().filter(f => fold(f.name) !== 'vocabulario')
  .map(f => `<option value="${esc(f.path)}"${f.path === sel ? ' selected' : ''}>${'  '.repeat(f.depth)}${esc(f.name)}</option>`).join('')
  + '<option value="__pick">Otra carpeta (fuera de Audios)…</option>';
async function openRec() {
  if (S.rec.recording) return stopRec();
  recDialog = true;
  // desde una carpeta: ahi; si no, la fija del perfil; si no, donde se grabo la ultima vez (con tema, la de su proyecto: applyVocab)
  const pf = S.config.perfil || {};
  const folder = S.route.kind === 'f' ? S.route.path : (pf.folder != null && S.folders.has(pf.folder)) ? pf.folder
    : (S.folders.has(S.config.rec_folder) ? S.config.rec_folder : '');
  openModal(`<h3><i class="recdot"></i>Nueva grabación</h3>
    <div class="muted">Graba lo que suena en el ordenador (Teams, Zoom, el navegador…) y tu micrófono, mezclados, como OBS.</div>
    <div class="notice info" style="margin-top:10px">${S.config.hotkey?.ok
      ? I.keyboard + 'Atajo: <b>Ctrl+Alt+R</b> empieza a grabar con esta misma configuración desde cualquier programa (aunque estés en Teams) y vuelve a pulsarlo para parar. Suena un pitido corto al empezar y al parar.'
      : I.keyboard + `El atajo Ctrl+Alt+R solo funciona con ${esc(APP_NAME)} en primer plano: ${esc(S.config.hotkey?.error || 'no se pudo registrar en Windows')}.`}</div>
    <div id="rForm"><div class="empty">Buscando dispositivos de audio…</div></div>
    <div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary rec-go" id="rGo" disabled><i class="recdot"></i>Empezar a grabar</button></div>`, async d => {
    d.addEventListener('click', e => { if (e.target.closest('[data-m="no"]')) closeModal(false); });
    let devs, voc;
    try { [devs, voc] = await Promise.all([api('/api/rec/devices'), loadVocab(S.config.language ?? 'es-en')]); }
    catch (err) { $('#rForm', d).innerHTML = `<div class="notice">${esc(err.message)}</div>`; return; }
    // tema: el que se acaba de crear desde este dialogo; si no, el fijo del perfil; si no, el ultimo usado
    const want = S.vocabJustSaved || (pf.vocab != null ? pf.vocab : voc.last);
    S.vocabJustSaved = null;
    const lastWs = S.vocab.some(v => v.id === want) ? want : '';
    // arriba, las opciones que siguen solas a los cascos; debajo, cada dispositivo por si hace falta fijarlo
    const o = (v, label, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(label)}</option>`;
    const defName = list => nfc((list.find(x => x.default) || {}).name || '—');
    const mics = o('default', `Predeterminado de Windows (ahora: ${defName(devs.mics)})`, devs.mic)
      + `<optgroup label="Fijar un micrófono">${devs.mics.map(x => o(x.id, nfc(x.name), devs.mic)).join('')}</optgroup>`
      + o('none', 'Sin micrófono', devs.mic);
    const outs = o('all', 'Todo lo que suena en el ordenador (todas las salidas) · recomendado', devs.sys)
      + o('default', `Salida predeterminada de Windows (ahora: ${defName(devs.outputs)})`, devs.sys)
      + `<optgroup label="Fijar una salida">${devs.outputs.map(x => o(x.id, nfc(x.name), devs.sys)).join('')}</optgroup>`
      + o('none', 'No grabar el audio del ordenador', devs.sys);
    const presets = S.config.presets.map(p => `<option value="${p.id}"${p.id === (S.config.live_preset || 'medium') ? ' selected' : ''}>${esc(p.live_label)} · ${liveFit(p)[1]}</option>`).join('');
    $('#rForm', d).innerHTML = `
      ${typeof perfilTemaHtml === 'function' ? perfilTemaHtml(lastWs) : `<label>${I.topic}Tema</label>${S.vocab.length
        ? `<div class="row" style="gap:6px;flex-wrap:nowrap"><select id="rVocab" style="flex:1">${vocabOptions(lastWs)}</select><button class="btn sm" type="button" id="rVocabNew">${I.plus}Nuevo</button></div><div class="help" id="rVocabFit"></div>`
        : `<div class="row" style="gap:8px"><button class="btn sm" type="button" id="rVocabNew">${I.plus}Crear un proyecto con su vocabulario</button></div>`}`}
      <label class="chk"><input type="checkbox" id="rMicOn" checked><span>${I.mic}Grabar también mi micrófono <span class="muted" style="font-weight:400">· desmárcalo para grabar solo el audio del ordenador</span></span></label>
      <div id="rMicBox"><select id="rMic">${mics}</select>
      ${meterHtml('mic', 'Nivel')}</div>
      <label>${I.volume}Audio del ordenador</label><select id="rSys">${outs}</select>
      ${meterHtml('sys', 'Nivel')}
      <div class="help">Con las opciones de arriba da igual que conectes o quites los cascos, incluso a mitad de la reunión: la grabación sigue con el dispositivo nuevo. Pon algo a sonar para ver que la barra se mueve.</div>
      <div class="grid2">
        <div><label>Nombre (opcional)</label><input class="input" id="rName" placeholder="p. ej. Reunión semanal del equipo"></div>
        <div><label title="Donde se guardan el audio y la transcripción">Carpeta de destino</label><select id="rFolder" title="Donde se guardan el audio y la transcripción">${folderOpts(folder)}</select></div>
      </div>
      ${typeof perfilCamposHtml === 'function' ? perfilCamposHtml(S.config.language ?? 'es-en', pf.context) : langSelect('rLang', S.config.language ?? 'es-en')}
      <label>${I.chat}Transcripción</label>
      <div class="seg2" id="rMode">
        <label class="on"><input type="radio" name="rMode" value="live" checked><b>Transcribir en directo</b><span>El texto va apareciendo mientras se habla</span></label>
        <label><input type="radio" name="rMode" value="rec"><b>Solo grabar</b><span>Si el portátil va lento: la transcribes al terminar</span></label>
      </div>
      <div id="rLiveOpts"><select id="rPreset">${presets}</select></div>
      <div class="row" style="margin-top:12px"><button class="btn sm" data-m="consent" type="button">${I.clipboard}Copiar aviso de grabación</button><span class="muted" style="font-size:12px">para pegarlo en el chat de la reunión (español, inglés y portugués)</span></div>`;
    if (typeof perfilBindRec === 'function') perfilBindRec(d);  // Proyecto/Tema, importar listas y «Guardar como perfil»
    const micVal = () => $('#rMicOn', d).checked ? $('#rMic', d).value : 'none';
    const monitor = () => api('/api/rec/monitor', { mic: micVal(), sys: $('#rSys', d).value }).catch(() => { });
    $('#rMic', d).onchange = $('#rSys', d).onchange = monitor;
    $('#rMicOn', d).onchange = () => { $('#rMicBox', d).classList.toggle('off', !$('#rMicOn', d).checked); monitor(); };
    $('#rMode', d).onchange = () => { const live = d.querySelector('[name=rMode]:checked').value === 'live'; $$('#rMode label', d).forEach(l => l.classList.toggle('on', l.querySelector('input').checked)); $('#rLiveOpts', d).classList.toggle('off', !live); };
    d.querySelector('[data-m="consent"]').onclick = () => copy(CONSENT_TEXT, 'Aviso copiado: pégalo en el chat de la reunión');
    // workstream: cuanto le cabe a Whisper y, al elegirlo, se graba en la carpeta de su proyecto
    const vocabSel = () => S.vocab.find(v => v.id === $('#rVocab', d)?.value);
    const fsel = $('#rFolder', d);
    fsel.dataset.prev = fsel.value;
    const applyVocab = initial => {
      const v = vocabSel(), fit = $('#rVocabFit', d);
      if (fit) fit.innerHTML = vocabFit(v);
      if (initial && S.route.kind === 'f') return;  // se abrio desde una carpeta: esa manda
      if (pf.folder != null) return;  // carpeta fija en el perfil: el tema no la cambia
      // la ultima carpeta donde grabaste reuniones de ese proyecto; si aun no hay, se queda la elegida
      const want = v ? S.config.project_folders?.[v.project] : (initial ? S.config.rec_folder || '' : undefined);
      if (want != null && [...fsel.options].some(o => o.value === want)) fsel.value = fsel.dataset.prev = want;
    };
    fsel.onchange = async () => {
      if (fsel.value !== '__pick') { fsel.dataset.prev = fsel.value; return; }
      fsel.value = fsel.dataset.prev || '';
      const rel = await linkFolder();
      if (rel && document.body.contains(fsel)) { fsel.innerHTML = folderOpts(rel); fsel.dataset.prev = rel; }
    };
    $('#rVocabNew', d).onclick = () => openVocabEditor(null, openRec, vocabSel()?.project || '');
    if ($('#rVocab', d)) {
      $('#rVocab', d).onchange = () => applyVocab(false);
      $('#rLang', d).onchange = async () => { await loadVocab($('#rLang', d).value); applyVocab(true); };  // la frase de estilo cambia con el idioma
      applyVocab(true);
    }
    monitor();
    pollRec();
    window.reunionPrefill?.(d);  // nombre, tema y participantes de la reunión en curso (reunion.js)
    $('#rGo', d).disabled = false;
    $('#rGo', d).onclick = async () => {
      $('#rGo', d).disabled = true;
      try {
        S.recLocal = true;
        const r = await api('/api/rec/start', { mic: micVal(), sys: $('#rSys', d).value, name: $('#rName', d).value.trim(),
          folder: $('#rFolder', d).value, live: d.querySelector('[name=rMode]:checked').value === 'live', preset: $('#rPreset', d).value,
          language: $('#rLang', d).value, vocab: $('#rVocab', d)?.value || '', ...(window.reunionExtra?.(d) || {}),
          ...(typeof perfilRecExtra === 'function' ? perfilRecExtra(d) : {}) });
        S.config.live_preset = $('#rPreset', d).value;
        recDialog = false;
        closeModal(true);
        toast((r.workstream ? `🗂 <b>${esc(r.workstream)}</b> · ` : '') + 'Grabando · el texto irá apareciendo en directo');
        await refreshTree(true);
        pollRec(); pollJobs(true);
        go('a', r.path);
      } catch (err) { S.recLocal = false; toast(esc(err.message), 'err'); $('#rGo', d).disabled = false; }
    };
  });
  S.modalCleanup = () => { if (recDialog) { recDialog = false; api('/api/rec/monitor', { stop: true }).catch(() => { }); } };
}
$('#btnRec').onclick = openRec;

/* ---------------------------------------------------------------- router */
function parseHash() {
  const h = location.hash.slice(1) || '/';
  const qi = h.indexOf('?');
  const p = qi < 0 ? h : h.slice(0, qi);
  const params = new URLSearchParams(qi < 0 ? '' : h.slice(qi + 1));
  const m = p.match(/^\/(a|f|d|s|r|c)\/(.*)$/);
  return m ? { kind: m[1], path: decodeURIComponent(m[2]), params } : { kind: 'home', path: '', params };
}
function href(kind, path, params) {
  if (kind === 'home') return '#/';
  const q = params ? '?' + new URLSearchParams(params) : '';
  return `#/${kind}/${enc(path)}${q}`;
}
function go(kind, path, params, replace) {
  const h = href(kind, path, params);
  if (replace) { history.replaceState(null, '', h); route(); } else location.hash = h;
}
window.addEventListener('hashchange', route);

function route() {
  flushEdits();  // si se estaba corrigiendo un texto, se guarda antes de cambiar de pagina
  flushNotes();
  const prev = S.route;
  S.route = parseHash();
  if (prev.kind !== S.route.kind || prev.path !== S.route.path) { main.scrollTop = 0; S.txOpen = false; S.notes = null; S.sum = null; S.tab = null; closePop(); closeSlash(); pushRecent(S.route.kind, S.route.path); }
  const r = S.route;
  if (r.kind === 'a' || r.kind === 'd' || r.kind === 'f') {
    let n = (r.kind === 'f' ? S.folders.get(r.path) : (S.audios.get(r.path) || S.docs.get(r.path)));
    n = n && (r.kind === 'f' ? n.parent : n.parent);
    let changed = false;
    while (n && n.path) { if (!S.exp.has(n.path)) { S.exp.add(n.path); changed = true; } n = n.parent; }
    if (changed) saveExp();
  }
  if (r.kind !== 's' && document.activeElement !== $('#gq')) $('#gq').value = '';
  renderView();
  renderTree();
  renderSideNav();
  requestAnimationFrame(() => { const sel = $('.trow.sel'); if (sel) sel.scrollIntoView({ block: 'nearest' }); });
}

function renderView() {
  const r = S.route;
  if (r.kind === 'a') { const a = S.audios.get(r.path); return !a ? viewMissing() : isLive(a) ? viewLive(a) : viewAudio(a); }
  if (r.kind === 'f') { const f = S.folders.get(r.path); return f ? viewFolder(f) : viewMissing(); }
  if (r.kind === 'd') { const d = S.docs.get(r.path); return d ? viewDoc(d) : viewMissing(); }
  if (r.kind === 's') return viewSearch(r.path);
  if (r.kind === 'r') return viewTable();
  if (r.kind === 'c') return viewCalendar(r.path);
  return viewHome();
}

/* re-pintado suave cuando cambian archivos o trabajos (sin perder la posicion) */
function refreshView() {
  const r = S.route;
  if (r.kind === 'home' || r.kind === 'f' || r.kind === 'r' || r.kind === 'c') {
    if (main.contains(document.activeElement) && /INPUT|SELECT/.test(document.activeElement.tagName)) return;  // escribiendo un filtro
    const st = main.scrollTop; renderView(); main.scrollTop = st;
  } else if (r.kind === 'a') {
    const a = S.audios.get(r.path);
    if (!a) return viewMissing();
    if ((isLive(a) ? 'live' : 'normal') !== S.viewMode) return renderView();  // empezo o termino la grabacion
    if (S.viewMode === 'live') renderLiveHero(a);
    else { renderAudioHead(a); renderAudioJob(a); }
    if ($('#aTabs') && !$('#aTabs').contains(document.activeElement)) $('#aTabs').innerHTML = tabsHtml(a);
    if (!S.tx || S.tx.path !== a.path || S.tx.mtime !== a.tx_mtime) loadTranscript(a);
    if (!S.spk || S.spk.path !== a.path || S.spk.mtime !== a.spk_mtime) loadSpeakers(a); else renderVoicesBar(a);
  }
}

/* ---------------------------------------------------------------- arbol */
function renderTree() {
  if (!S.tree || S.dragging) return;
  const q = fold($('#filter').value.trim());
  const root = S.tree, html = [];
  if (!q) html.push(favHtml());
  const lives = liveAudios();
  if (lives.length) {  // arriba del todo y en rojo: lo que se esta grabando ahora
    html.push(`<div class="tsec live"><i class="recdot"></i> En directo</div>`);
    html.push(lives.map(a => audioRow(a, 0)).join(''));
  }
  const loose = root.audios.filter(a => matchA(a, q) && !isLive(a));
  if (loose.length || !q) {
    html.push(`<div class="tsec" data-drop="">Sin clasificar ${loose.length ? `· ${loose.length}` : ''}</div>`);
    html.push(loose.length ? loose.map(a => audioRow(a, 0)).join('') : '<div class="trow muted" data-drop="" style="padding-left:16px;cursor:default">Nada suelto</div>');
  }
  const folders = root.folders.map(f => folderNode(f, 0, q)).join('');
  if (folders) html.push('<div class="tsec">Carpetas</div>' + folders);
  root.docs.filter(d => matchD(d, q)).forEach(d => html.push(docRow(d, 0)));
  if (!html.length) html.push('<div class="empty">Sin resultados</div>');
  $('#tree').innerHTML = html.join('');
}
function matchA(a, q) {
  const p = a.props || {};
  return !q || fold([a.name, shortTitle(a), longTitle(a), p.titulo, (p.etiquetas || []).join(' '), p.participantes].join(' ')).includes(q);
}
function matchD(d, q) { return !q || fold(d.name).includes(q); }
function folderNode(f, depth, q) {
  const self = !q || fold(f.name).includes(q);
  const cq = self ? '' : q;
  const kids = [
    ...f.folders.map(c => folderNode(c, depth + 1, cq)),
    ...f.audios.filter(a => matchA(a, cq)).map(a => audioRow(a, depth + 1)),
    ...f.docs.filter(d => matchD(d, cq)).map(d => docRow(d, depth + 1)),
  ].filter(Boolean);
  if (q && !self && !kids.length) return '';
  const open = q ? true : S.exp.has(f.path);
  const sel = S.route.kind === 'f' && S.route.path === f.path;
  const full = f.total && f.done === f.total;
  return `<div class="trow folder${open ? ' open' : ''}${sel ? ' sel' : ''}" data-folder="${esc(f.path)}" data-drop="${esc(f.path)}" style="padding-left:${6 + depth * 14}px" title="${esc(nfc(f.name))}">`
    + `${I.caret}${fIcon(f) ? `<span class="ticon">${fIcon(f)}</span>` : ''}<span class="tl">${esc(nfc(f.name))}</span><span class="tc${full ? ' full' : ''}">${f.total ? `${f.done}/${f.total}` : ''}</span></div>`
    + `<div class="tkids${open ? ' open' : ''}">${kids.join('')}</div>`;
}
function audioRow(a, depth) {
  const j = jobFor(a.path);
  const sel = S.route.kind === 'a' && S.route.path === a.path;
  const prog = j && j.status === 'running' ? `<div class="tprog"><i style="width:${(j.progress * 100).toFixed(1)}%"></i></div>` : '';
  const live = isLive(a);
  return `<div class="trow audio${sel ? ' sel' : ''}${live ? ' live' : ''}" draggable="${!live}" data-audio="${esc(a.path)}" data-drag="${esc(a.path)}" style="padding-left:${14 + depth * 14}px" title="${esc(nfc(a.name))}">`
    + `<span class="dot ${statusOf(a)}"></span>${iconOf(a) ? `<span class="ticon">${iconOf(a)}</span>` : ''}`
    + `<span class="tl">${titleOf(a) ? `${esc(titleOf(a))} <small>${esc(shortTitle(a))}</small>` : `${esc(shortTitle(a))}${a.label ? ` <small>${esc(nfc(a.label))}</small>` : ''}`}</span>`
    + `${a.cloud ? I.cloud : ''}${live ? '<span class="tlive">EN VIVO</span>' : `<span class="tc">${a.duration ? fmtDur(a.duration) : ''}</span>`}${prog}</div>`;
}
function docRow(d, depth) {
  const sel = S.route.kind === 'd' && S.route.path === d.path;
  return `<div class="trow doc${sel ? ' sel' : ''}" draggable="true" data-doc="${esc(d.path)}" data-drag="${esc(d.path)}" style="padding-left:${12 + depth * 14}px" title="${esc(nfc(d.name))}">${I.doc}<span class="tl">${esc(nfc(d.name))}</span></div>`;
}
function toggleExp(path) {
  S.exp.has(path) ? S.exp.delete(path) : S.exp.add(path);
  saveExp(); renderTree();
}
$('#tree').addEventListener('click', e => {
  const row = e.target.closest('.trow');
  if (!row) return;
  if (row.dataset.folder !== undefined) {
    const p = row.dataset.folder;
    if (e.target.closest('.caret')) return toggleExp(p);
    if (S.route.kind === 'f' && S.route.path === p) return toggleExp(p);
    if (!S.exp.has(p)) { S.exp.add(p); saveExp(); }
    go('f', p);
  } else if (row.dataset.audio !== undefined) go('a', row.dataset.audio);
  else if (row.dataset.doc !== undefined) go('d', row.dataset.doc);
});
$('#filter').addEventListener('input', renderTree);

/* arrastrar y soltar para mover audios entre carpetas */
let dragPath = null, hoverTimer = null, hoverPath = null;
document.addEventListener('dragstart', e => {
  const el = e.target.closest && e.target.closest('[data-drag]');
  if (!el) return;
  dragPath = el.dataset.drag; S.dragging = true;
  e.dataTransfer.setData('text/plain', dragPath);
  e.dataTransfer.effectAllowed = 'move';
  el.classList.add('dragging');
});
document.addEventListener('dragend', () => {
  S.dragging = false; dragPath = null; clearTimeout(hoverTimer);
  $$('.dragging, .drop').forEach(x => x.classList.remove('dragging', 'drop'));
});
document.addEventListener('dragover', e => {
  if (!dragPath) return;
  const t = e.target.closest && e.target.closest('[data-drop]');
  $$('.drop').forEach(x => x !== t && x.classList.remove('drop'));
  if (!t) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  t.classList.add('drop');
  if (t.dataset.folder !== undefined && !t.classList.contains('open') && hoverPath !== t.dataset.folder) {
    hoverPath = t.dataset.folder; clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => { t.classList.add('open'); t.nextElementSibling?.classList.add('open'); S.exp.add(hoverPath); saveExp(); }, 650);
  }
});
document.addEventListener('drop', e => {
  if (!dragPath) return;
  const t = e.target.closest && e.target.closest('[data-drop]');
  if (!t) return;
  e.preventDefault();
  const path = dragPath;
  S.dragging = false;
  moveItem(path, t.dataset.drop);
});

async function moveItem(path, folder, name) {
  const item = S.audios.get(path) || S.docs.get(path);
  if (!name && item && item.parent && item.parent.path === folder) return;
  const wasPlaying = S.playing === path, t = audio.currentTime, playingNow = !audio.paused;
  if (wasPlaying) { audio.pause(); audio.removeAttribute('src'); audio.load(); }  // suelta el archivo para poder moverlo
  try {
    const r = await api('/api/move', { path, folder, name: name || undefined });
    toast(name ? 'Renombrado' : `Movido a <b>${esc(folderName(folder))}</b>`);
    await refreshTree(true);
    if (wasPlaying) { const a = S.audios.get(r.path); S.playing = null; if (a) loadAudio(a, t, playingNow); }
    const rp = Object.keys(localStorage).find(k => k === 'pos:' + path);
    if (rp) { localStorage.setItem('pos:' + r.path, localStorage.getItem(rp)); localStorage.removeItem(rp); }
    if (S.route.path === path) go(S.route.kind, r.path, null, true);
  } catch (err) {
    toast(esc(err.message), 'err');
    if (wasPlaying) { const a = S.audios.get(path); S.playing = null; if (a) loadAudio(a, t, playingNow); }
  }
}

/* ---------------------------------------------------------------- componentes */
function crumbs(path, last) {
  const parts = path ? path.split('/') : [];
  const out = [`<a href="#/">${esc(APP_NAME)}</a>`];
  parts.forEach((p, i) => out.push(`<span class="sep">›</span><a href="${href('f', parts.slice(0, i + 1).join('/'))}">${esc(nodeName(parts.slice(0, i + 1).join('/')))}</a>`));
  if (last) out.push(`<span class="sep">›</span><span>${esc(last)}</span>`);
  return `<div class="crumbs">${out.join('')}</div>`;
}
function alist(list, opts = {}) {
  if (!list.length) return '<div class="card empty">No hay audios aquí</div>';
  return `<div class="card alist">${list.map(a => {
    const j = jobFor(a.path);
    const right = j && j.status === 'running'
      ? `<div class="minibar" data-bar="${esc(a.path)}"><i style="width:${(j.progress * 100).toFixed(1)}%"></i></div>`
      : statusPill(a);
    const where = opts.folder && a.folder ? `<div class="f">${esc(pathLabel(a.folder))}</div>` : '';
    return `<div class="aitem" data-go="a" data-path="${esc(a.path)}" draggable="true" data-drag="${esc(a.path)}">`
      + `<span class="p"><span class="dot ${statusOf(a)}"></span></span>`
      + `<span class="t">${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(pageTitle(a))}${titleOf(a) ? `<small>${esc(shortTitle(a))}</small>` : (a.label ? `<small>${esc(nfc(a.label))}</small>` : '')}${where}</span>`
      + `<span>${right}</span><span class="d">${fmtDur(estDuration(a))}</span>`
      + `<button class="btn ghost sm go" data-act="play-item" data-path="${esc(a.path)}" title="Reproducir">${I.play}</button></div>`;
  }).join('')}</div>`;
}
function fcard(f) {
  const pct = f.total ? Math.round(100 * f.done / f.total) : 0;
  return `<div class="card fcard" data-go="f" data-path="${esc(f.path)}" data-drop="${esc(f.path)}">`
    + `<div class="n">${fIcon(f) || I.folder}${esc(nfc(f.name))}</div>`
    + `<div class="s"><span>${f.total} audio${f.total === 1 ? '' : 's'} · ${fmtDur(f.secs)}</span><span>${f.done}/${f.total}</span></div>`
    + `<div class="bar"><i style="width:${pct}%"></i></div></div>`;
}

/* selector de velocidad + idioma para transcribir una lista de audios */
let boxSeq = 0;
/* resume: las que se quedaron a medias se continuan desde donde acaba el texto (se estima solo lo que falta) */
const remaining = (a, resume) => resume && a.partial ? Math.max(1, estDuration(a) - a.tx_end) : estDuration(a);
function txBox(list, title, opts = {}) {
  const id = 'b' + (++boxSeq), resume = !!opts.resume;
  S.boxes[id] = { paths: list.map(a => a.path), resume };
  const total = sum(list, a => remaining(a, resume));
  const sel = S.selPreset || S.config.preset;
  const cards = S.config.presets.map(p => {
    const cloudOff = p.engine === 'cloud' && !S.config.cloud.has_key;
    const load = p.engine === 'local' ? (p.cached ? 8 : 60) * Math.min(list.length, 1) : 5;
    const est = total / p.speed + load;
    const tag = p.id === 'rapido' ? '<span class="tag">Recomendado</span>'
      : (p.engine === 'local' && !p.cached ? `<span class="tag dl">descarga ${MODEL_GB[p.model] || ''}</span>` : '');
    return `<button class="preset${p.id === sel ? ' on' : ''}${cloudOff ? ' off' : ''}" data-preset="${p.id}" data-box="${id}">${tag}`
      + `<div class="pn">${presetIcon(p)}${esc(p.label)}</div>`
      + `<div class="pe">${cloudOff ? 'Configurar' : fmtEst(est)}</div>`
      + `<div class="pm">${p.engine === 'cloud' ? (cloudOff ? 'falta API key' : esc(S.config.providers[S.config.cloud.provider]?.label || 'nube')) : esc(p.model)}</div></button>`;
  }).join('');
  const lang = list.length === 1 && typeof perfilLangOf === 'function' ? perfilLangOf(list[0]) : (S.selLang ?? S.config.language);
  const hint = S.presetsById[sel]?.hint || '';
  return `<div class="card txbox" data-box="${id}">
    <div class="head"><b>${esc(title)}</b><span class="muted">${list.length > 1 ? `${list.length} audios · ` : ''}${fmtDur(total)} de audio${resume && list.some(a => a.partial) ? ' por transcribir' : ''}</span></div>
    <div class="presets">${cards}</div>
    <div class="preset-hint" data-hint="${id}">${esc(hint)}</div>
    <div class="foot">
      <select data-lang="${id}" title="Idioma de la reunión">${LANGS.map(([v, l]) => `<option value="${v}"${v === lang ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <button class="btn primary" data-act="tx-go" data-box="${id}">${I.spark}${opts.button || (list.length > 1 ? `Transcribir ${list.length} audios` : 'Transcribir')}</button>
      <span class="muted" style="font-size:12.5px">Se guarda como .txt y .srt junto al audio. Puedes seguir usando la app mientras tanto.</span>
    </div></div>`;
}

async function submitTx(paths, preset, language, resume = false) {
  const p = S.presetsById[preset];
  if (p.engine === 'cloud' && !S.config.cloud.has_key) return openSettings();
  // continuar una a medias no sobrescribe nada: solo anade lo que falta
  const existing = paths.filter(x => { const a = S.audios.get(x); return a && (a.txt || a.srt) && !(resume && a.partial); });
  if (existing.length && !await ask('¿Sobrescribir la transcripción?',
    `${existing.length === 1 ? 'Este audio ya tiene' : `${existing.length} audios ya tienen`} .txt/.srt. Se reemplazarán por la nueva transcripción.`, 'Sobrescribir')) return;
  if (p.engine === 'cloud' && !localStorage.getItem('cloudOk')) {
    const prov = S.config.providers[S.config.cloud.provider]?.label || 'la nube';
    if (!await ask('Enviar audio a ' + prov,
      `El audio saldrá de tu portátil hacia <b>${esc(prov)}</b>. Si son reuniones de trabajo, asegúrate de que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay.`,
      'Entendido, enviar')) return;
    localStorage.setItem('cloudOk', '1');
  }
  try {
    const r = await api('/api/transcribe', { paths, preset, language, resume });
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    toast(r.added.length ? `${r.added.length === 1 ? 'Añadido' : r.added.length + ' añadidos'} a la cola · ${presetIcon(p)}${esc(p.label)}` : 'Ya estaba en la cola');
    S.txOpen = false;
    pollJobs(true);
  } catch (err) { toast(esc(err.message), 'err'); }
}

function jobSub(j) {
  if (j.kind === 'voces') return `${I.voice}Quién habla (en este PC)${j.status === 'running' ? ` · ${Math.round(j.progress * 100)} %` : ''}${j.elapsed && j.status === 'done' ? ` · hecho en ${fmtLeft(j.elapsed)}` : ''}`;
  if (j.kind === 'summary') return `${I.spark}Resumen con IA (${provName()})${j.status === 'running' ? ` · ${Math.round(j.progress * 100)} %` : ''}${j.elapsed && j.status === 'done' ? ` · hecho en ${fmtLeft(j.elapsed)}` : ''}`;
  const p = S.presetsById[j.preset] || { icon: '', label: j.preset };
  const parts = [`${presetIcon(p)}${esc(j.live ? (p.live_label || p.label) : p.label)}`];
  if (j.live) {
    parts.push(`transcrito hasta ${fmtClock(j.transcribed)} de ${fmtClock(j.recorded)}${j.recording ? ' grabados' : ''}`);
    const lag = j.recorded - j.transcribed;
    if (j.status === 'running' && j.recording) parts.push(lag > 90 ? `poniéndose al día (${fmtLeft(lag)} por detrás)` : 'al día');
    if (j.status === 'done' && j.elapsed) parts.push(`terminado`);
    return parts.join(' · ');
  }
  if (j.resume_from) parts.push(`continuando desde ${fmtClock(j.resume_from)}`);
  if (j.status === 'running') {
    parts.push(`${Math.round(j.progress * 100)} %`);
    if (j.eta != null && j.progress > 0.03) parts.push(`quedan ~${fmtLeft(j.eta)}`);
    if (j.elapsed) parts.push(`${fmtLeft(j.elapsed)} transcurridos`);
  } else if (j.status === 'done' && j.elapsed) {
    parts.push(`hecho en ${fmtLeft(j.elapsed)}` + (j.duration ? ` (${(j.duration / j.elapsed).toFixed(1).replace('.', ',')}× tiempo real)` : ''));
  } else if (j.status === 'queued') {
    const ahead = S.jobs.filter(x => x.engine === j.engine && isActive(x) && x.created < j.created).length;
    parts.push(ahead ? `${ahead} por delante` : 'empieza enseguida');
  }
  return parts.join(' · ');
}
function jobStage(j) {
  if (j.kind === 'voces') return j.status === 'error' ? 'No se pudieron identificar las voces' : j.status === 'queued' ? 'En cola para identificar voces' : j.status === 'cancelled' ? 'Cancelado' : j.stage;
  if (j.kind === 'summary') return j.status === 'done' ? 'Resumen listo' : j.status === 'error' ? 'No se pudo resumir' : j.status === 'queued' ? 'En cola para resumir' : j.stage;
  if (j.status === 'queued') return 'En cola';
  if (j.status === 'done') return j.live ? 'Grabación transcrita' : 'Transcripción lista';
  if (j.status === 'cancelled') return j.live ? 'Directo detenido' : 'Cancelado';
  if (j.live && j.status === 'running') return `● En directo · ${j.stage}`;
  if (j.status === 'error') return 'No se pudo transcribir';
  return j.stage;
}

/* ---------------------------------------------------------------- vistas */
function viewMissing() {
  main.innerHTML = `<div class="page"><div class="card empty">Esto ya no está aquí (¿se ha movido o renombrado?).<br><br><a class="btn" href="#/">Volver al inicio</a></div></div>`;
}

function viewHome() {
  const t = S.tree, all = allAudios();
  const pending = all.filter(a => (!(a.txt || a.srt) || a.partial) && !jobFor(a.path) && !a.recording);
  const done = all.filter(a => a.txt || a.srt).length;
  const secs = sum(all, estDuration);
  const recent = [...all].sort((a, b) => (b.date || '').localeCompare(a.date || '') || b.mtime - a.mtime).slice(0, 8);
  const pct = all.length ? Math.round(100 * done / all.length) : 0;
  const lives = liveAudios(), loose = t.audios.filter(a => !isLive(a));
  main.innerHTML = `<div class="page">
    ${lives.map(liveBanner).join('')}
    <h1>Tus notas de reunión</h1>
    <div class="meta">${esc(S.config.root)} · <a href="#" data-act="palette">Ctrl+P para buscar</a></div>
    ${welcomeCard(all)}
    ${recentCards()}
    <div class="stats">
      <div class="card stat"><div class="v">${all.length}</div><div class="l">reuniones</div></div>
      <div class="card stat"><div class="v">${fmtHours(secs)} h</div><div class="l">de audio</div></div>
      <div class="card stat"><div class="v">${pct} %</div><div class="l">transcritas (${done})</div><div class="bar"><i style="width:${pct}%"></i></div></div>
      <div class="card stat"><div class="v">${pending.length}</div><div class="l">sin transcribir</div></div>
    </div>
    ${loose.length ? `<h2>Sin clasificar <span class="count">${loose.length}</span><span class="muted" style="font-weight:400;font-size:12.5px">· arrástralos a una carpeta de la izquierda</span></h2>${alist(loose)}` : ''}
    ${pending.length ? `<h2>Pendientes de transcribir <span class="count">${pending.length}</span></h2>${txBox(pending, 'Transcribir todos los pendientes', { resume: true })}<div style="height:12px"></div>${alist(pending, { folder: true })}` : ''}
    <h2>Carpetas <span class="count">${t.folders.length}</span>
      <button class="btn sm" data-act="new-folder" data-path="" style="margin-left:auto">${I.plus}Nueva carpeta</button></h2>
    <div class="fgrid">${t.folders.map(fcard).join('')}</div>
    <h2>Recientes</h2>${alist(recent, { folder: true })}
  </div>`;
}

function viewFolder(f) {
  const list = audiosUnder(f);
  const pending = list.filter(a => (!(a.txt || a.srt) || a.partial) && !jobFor(a.path) && !a.recording);
  main.innerHTML = `<div class="page">
    ${crumbs(f.path.split('/').slice(0, -1).join('/'))}
    <div class="ptitle-row"><button class="picon" data-act="folder-icon" data-path="${esc(f.path)}" title="Cambiar icono">${fIcon(f) || '<span class="picon-add">' + I.face + '</span>'}</button>
      <h1 class="ptitle static">${esc(nfc(f.name))}</h1><button class="pfav${isFav('f', f.path) ? ' on' : ''}" data-act="fav" data-kind="f" data-path="${esc(f.path)}">${isFav('f', f.path) ? I.starOn : I.star}</button></div>
    <div class="meta"><span><b>${f.total}</b> audio${f.total === 1 ? '' : 's'}</span><span><b>${fmtDur(f.secs)}</b> de audio</span><span><b>${f.done}</b> transcritos</span></div>
    <div class="actions">
      <button class="btn" data-act="new-folder" data-path="${esc(f.path)}">${I.plus}Subcarpeta</button>
      <button class="btn" data-act="rename-folder" data-path="${esc(f.path)}">${I.edit}Renombrar</button>
      <button class="btn" data-act="reveal" data-path="${esc(f.path)}">${I.ext}Abrir en el Explorador</button>
      ${f.linked ? `<button class="btn" data-act="link-remove" data-path="${esc(f.path)}" title="Deja de verse en ${esc(APP_NAME)}; no se borra nada">Quitar de ${esc(APP_NAME)}</button>` : ''}
    </div>
    ${f.linked ? `<div class="help">${I.link}Carpeta vinculada: <code>${esc(f.linked)}</code>${f.missing ? ' · <span class="warn-txt">ahora no se encuentra (¿disco o OneDrive desconectado?)</span>' : ''}. «Renombrar» solo cambia cómo se llama en ${esc(APP_NAME)}.</div>` : ''}
    ${pending.length ? `<h2>Pendientes <span class="count">${pending.length}</span></h2>${txBox(pending, pending.length === 1 ? 'Transcribir el pendiente' : `Transcribir los ${pending.length} pendientes`, { resume: true })}` : ''}
    ${f.folders.length ? `<h2>Carpetas <span class="count">${f.folders.length}</span></h2><div class="fgrid">${f.folders.map(fcard).join('')}</div>` : ''}
    ${f.audios.length || !f.folders.length ? `<h2>Grabaciones <span class="count">${f.audios.length}</span></h2>${alist(f.audios)}` : ''}
    ${f.docs.length ? `<h2>Documentos <span class="count">${f.docs.length}</span></h2><div class="related">${f.docs.map(d => `<a href="${href('d', d.path)}">${I.doc}${esc(nfc(d.name))}</a>`).join('')}</div>` : ''}
  </div>`;
}

function viewLive(a) {
  S.viewMode = 'live';
  main.innerHTML = `<div class="page live-page"><div id="lHero"></div><div id="aVoz"></div>
    <div id="aTabs"></div><section class="sum hidden" id="aSum"></section><section class="tx live-feed" id="aTx"></section><section class="notes hidden" id="aNotes"></section></div>
    <button class="to-live hidden" id="toLive" data-act="to-live">↓ Ir al directo</button>`;
  S.tx = null; S.curIdx = -1; S.curEl = null; S.liveRev = undefined; S.seenSegs = 0;
  S.tq = S.route.params.get('q') || '';
  renderLiveHero(a);
  applyTab(a);  // en directo tambien se pueden ir tomando notas (como en Notion)
  loadTranscript(a);
  loadSpeakers(a);
}

function viewAudio(a) {
  S.viewMode = 'normal';
  main.innerHTML = `<div class="page">
    <div id="aHead"></div><div id="aJob"></div><div id="aVoz"></div><div id="aBox"></div>
    <div id="aTabs"></div><section class="sum hidden" id="aSum"></section><section class="tx" id="aTx"></section><section class="notes hidden" id="aNotes"></section></div>`;
  S.tx = null; S.curIdx = -1; S.curEl = null; S.liveRev = undefined;
  S.tq = S.route.params.get('q') || '';
  renderAudioHead(a); renderAudioJob(a);
  applyTab(a);
  loadTranscript(a);
  loadSpeakers(a);
}

function renderAudioHead(a, force) {
  const el = $('#aHead');
  if (!el) return;
  // si se esta escribiendo el titulo o una propiedad, no se repinta (se perderia el cursor)
  const ae = document.activeElement;
  if (!force && el.contains(ae) && (ae.isContentEditable || /INPUT|SELECT/.test(ae.tagName))) return;
  const st = statusOf(a), has = a.txt || a.srt;
  const docs = (a.parent?.docs || []);
  el.innerHTML = `${crumbs(a.folder)}
    ${titleRowHtml(a)}
    ${propsHtml(a)}
    <div class="actions">
      <button class="btn primary" data-act="play" data-path="${esc(a.path)}">${I.play}${S.playing === a.path && !audio.paused ? 'Pausa' : 'Reproducir'}</button>
      ${jobFor(a.path) ? '' : a.recording
        ? `<button class="btn primary" data-act="live" data-path="${esc(a.path)}">${I.spark}Transcribir en directo</button>`
        : `<button class="btn${has ? '' : ' primary'}" data-act="tx-toggle">${I.spark}${has ? 'Volver a transcribir' : 'Transcribir'}</button>`}
      <button class="btn" data-act="move" data-path="${esc(a.path)}">${I.move}Mover / renombrar</button>
      <button class="btn" data-act="reveal" data-path="${esc(a.path)}">${I.ext}Mostrar en carpeta</button>
      ${a.txt ? `<button class="btn" data-act="open-txt" data-path="${esc(a.path)}">${I.text}Abrir .txt</button>` : ''}
      <button class="btn ghost danger" data-act="delete" data-path="${esc(a.path)}" title="A la papelera de Windows">${I.trash}Eliminar</button>
    </div>
    ${docs.length ? `<div class="related">${docs.map(d => `<a href="${href('d', d.path)}">${I.doc}${esc(nfc(d.name))}</a>`).join('')}</div>` : ''}`;
  fillTagList();
  const box = $('#aBox');
  // a medias: se ofrece continuar desde donde se corto (con "Volver a transcribir" se rehace entera)
  const resume = a.partial && !S.txOpen;
  const showBox = !jobFor(a.path) && !a.recording && (S.txOpen || a.partial || (!has && st !== 'error'));
  const want = showBox ? a.path + (resume ? '|resume' : '') : '';
  if (box.dataset.for !== want) {
    box.dataset.for = want;
    box.innerHTML = !showBox ? '' : resume
      ? txBox([a], `La transcripción se cortó en ${fmtClock(a.tx_end)}: continuar desde ahí`,
        { resume: true, button: `Continuar desde ${fmtClock(a.tx_end)}` })
      : txBox([a], has ? 'Volver a transcribir (entera)' : 'Transcribir esta grabación');
  }
}

function renderAudioJob(a) {
  const el = $('#aJob');
  if (!el) return;
  let j = jobFor(a.path);
  if (!j) { const l = lastJobFor(a.path); if (l && l.status === 'error') j = l; }
  if (!j) { el.innerHTML = ''; el.dataset.id = ''; return; }
  if (el.dataset.id === j.id + j.status) return updateJobLine(el, j);
  el.dataset.id = j.id + j.status;
  if (j.status === 'error') {
    el.innerHTML = `<div class="card jobline error"><div><div class="st">No se pudo transcribir</div><div class="sub">${esc(j.error)}</div></div><button class="btn sm" data-act="tx-toggle">Reintentar</button></div>`;
    return;
  }
  el.innerHTML = `<div class="card jobline${j.live ? ' live' : ''}"><div><div class="st" data-st></div><div class="sub" data-sub></div></div>`
    + `<button class="btn sm danger" data-act="cancel-job" data-id="${j.id}">${j.live ? 'Detener directo' : 'Cancelar'}</button><div class="pbar" data-pb><i></i></div></div>`;
  updateJobLine(el, j);
}
function updateJobLine(el, j) {
  const st = $('[data-st]', el), sub = $('[data-sub]', el), pb = $('[data-pb]', el);
  if (!st) return;
  st.textContent = jobStage(j);
  sub.innerHTML = jobSub(j);
  pb.classList.toggle('indet', j.status === 'running' && j.progress < 0.004 && !(j.live && j.recorded < 30));
  $('i', pb).style.width = (j.progress * 100).toFixed(1) + '%';
}

/* ---------------------------------------------------------------- transcripcion */
/* corrigiendo en directo: lo nuevo se anade debajo sin repintar (ni mover el cursor de) lo que editas */
async function appendLive(a) {
  let t;
  try { t = await api('/api/transcript?path=' + enc(a.path)); } catch { return; }
  if (!S.editing || !S.tx || S.tx.path !== a.path) return;
  const segs = S.tx.segs, last = segs.length ? (segs[segs.length - 1].start ?? -1) : -1;
  const add = t.segments.filter(s => (s.start ?? -1) > last + 0.005);
  const body = $('#txBody');
  if (!add.length || !body) return;
  syncEdits();
  const i0 = segs.length, terms = termsOf(S.tq);
  segs.push(...add);
  S.tx.starts = segs.map(s => s.start ?? 0);
  const span = (s, i) => `<span class="s fresh" data-i="${i}" contenteditable="plaintext-only" spellcheck="true">${hlTerms(s.text, terms)}</span>`;
  const html = S.mode === 'lines' && S.tx.kind === 'srt'
    ? add.map((s, k) => `<div class="ln"><span class="ts" data-i="${i0 + k}">${fmtClock(s.start)}</span>${span(s, i0 + k)}</div>`).join('')
    : `<p><span class="ts" data-i="${i0}">${fmtClock(add[0].start)}</span>${add.map((s, k) => span(s, i0 + k)).join(' ')}</p>`;
  const typing = $('.typing', body);
  typing ? typing.insertAdjacentHTML('beforebegin', html) : body.insertAdjacentHTML('beforeend', html);
  S.marks = $$('mark', body);
}

async function loadTranscript(a) {
  if (S.editing && S.tx && S.tx.path === a.path) return liveJob(a) ? appendLive(a) : undefined;  // no pisar lo que se esta corrigiendo
  const want = a.path;
  let t;
  try { t = await api('/api/transcript?path=' + enc(a.path)); } catch (err) { t = { kind: null, segments: [], error: err.message }; }
  if (S.route.kind !== 'a' || S.route.path !== want) return;
  const keepScroll = S.tx && S.tx.path === want;
  const st = main.scrollTop;
  const atBottom = main.scrollHeight - main.scrollTop - main.clientHeight < 160;
  const typing = document.activeElement && document.activeElement.id === 'tq';
  S.tx = { path: want, kind: t.kind, file: t.file, segs: t.segments, mtime: a.tx_mtime, starts: t.segments.map(s => s.start ?? 0), has_backup: !!t.has_backup };
  if (keepScroll && typing) { renderTxBody(); main.scrollTop = st; return; }  // no robar el foco del buscador
  renderTxSection();
  const lj = jobFor(want);
  if (keepScroll) main.scrollTop = lj && lj.live && atBottom ? main.scrollHeight : st;
  else if (S.viewMode === 'live') main.scrollTop = main.scrollHeight;  // el directo empieza abajo, en lo ultimo
  updateToLive();
  const t0 = parseFloat(S.route.params.get('t'));
  if (!isNaN(t0) && !keepScroll) {
    if (!a.cloud && S.playing !== a.path) loadAudio(a, t0, false);
    const i = segAt(t0);
    const el = i >= 0 && $(`#txBody .s[data-i="${i}"]`);
    if (el) { el.classList.add('cur'); S.curEl = el; S.curIdx = i; requestAnimationFrame(() => scrollToEl(el, true)); }
  } else if (S.tq && S.marks.length) gotoMark(0);
}

function renderTxSection() {
  syncEdits();  // lo escrito a mano no se pierde al repintar
  const sec = $('#aTx'), tx = S.tx;
  if (!sec || !tx) return;
  if (!tx.segs.length) {
    const a = S.audios.get(tx.path), lj = a && liveJob(a);
    sec.innerHTML = S.viewMode === 'live'
      ? `<div class="card empty tx-empty">${lj ? `<div class="typing"><i></i><i></i><i></i></div><div>Escuchando… el texto aparecerá aquí en cuanto se hable (${lj.engine === 'cloud' || S.presetsById[lj.preset]?.engine === 'cloud' ? 'unos 5–10 s' : 'unos 20–40 s'} de retraso).</div>`
        : '<div>La transcripción en directo está parada.</div>'}</div>`
      : `<div class="card empty tx-empty">${I.text}<div>Aún no hay transcripción.</div></div>`;
    return;
  }
  const words = sum(tx.segs, s => s.text.split(/\s+/).length);
  sec.innerHTML = `<div class="tx-tools">
      <div class="tx-search">${I.search}<input id="tq" placeholder="Buscar en esta transcripción  (Ctrl F)" value="${esc(S.tq)}" autocomplete="off" spellcheck="false">
        <span class="n" id="tqn"></span><button data-act="mark-prev" title="Anterior (Shift Enter)">${I.up}</button><button data-act="mark-next" title="Siguiente (Enter)">${I.down}</button></div>
      ${tx.kind === 'srt' ? `<div class="seg-toggle"><button data-mode="para" class="${S.mode === 'para' ? 'on' : ''}">Párrafos</button><button data-mode="lines" class="${S.mode === 'lines' ? 'on' : ''}">Con tiempos</button></div>
      <label class="btn ghost sm" title="El texto se desplaza solo mientras escuchas"><input type="checkbox" id="follow" ${S.follow ? 'checked' : ''}> Seguir el audio</label>` : '<span class="muted" style="font-size:12.5px">Solo .txt (sin tiempos)</span>'}
      <span class="sp"></span>
      <span class="muted" style="font-size:12.5px">${words.toLocaleString('es-ES')} palabras</span>
      ${S.editing ? `<span class="save-state" id="saveState">${S.dirty ? 'Sin guardar…' : 'Guardado ✓'}</span>
        <button class="btn sm primary" data-act="tx-done">${I.check}Listo</button>`
        : `${!jobFor(tx.path) || jobFor(tx.path).live ? `<button class="btn sm" data-act="tx-edit" title="Corregir lo que Whisper haya entendido mal${jobFor(tx.path) ? ' (también mientras se transcribe en directo)' : ''}">${I.edit}Editar</button>` : ''}
        <button class="btn sm" data-act="copy-tx">${I.copy}Copiar texto</button>
        ${tx.kind === 'srt' ? `<button class="btn sm" data-act="seg-to-notes" title="Añade la frase que suena (o la última que pulsaste) a tus notas, con su minuto">${I.plus}A mis notas</button>` : ''}`}
    </div>
    ${S.editing ? `<div class="tx-editbar">
      <span>${I.edit}Haz clic en cualquier frase para corregirla. Se guarda solo.${liveJob(S.audios.get(tx.path) || {}) ? ' Lo nuevo sigue llegando debajo.' : ''}</span>
      <span class="sp"></span>
      <span class="muted">Cambiar lo buscado por</span>
      <input class="input" id="trep" placeholder="p. ej. nombre del cliente" autocomplete="off" spellcheck="false">
      <button class="btn sm" data-act="tx-replace-all">Reemplazar todo</button>
      <button class="btn sm ghost${tx.has_backup && !jobFor(tx.path) ? '' : ' hidden'}" data-act="tx-restore" title="Volver a lo que transcribió Whisper">${I.refresh}Restaurar original</button>
    </div>` : ''}
    <div class="tx-body${S.mode === 'lines' && tx.kind === 'srt' ? ' lines' : ''}${S.editing ? ' editing' : ''}" id="txBody"></div>`;
  renderTxBody();
}

/* ---------------------------------------------------------------- edicion del texto */
/* cada frase es editable; se guarda sola (.txt y .srt) al dejar de escribir. La primera vez el
   servidor guarda una copia de lo que escribio Whisper, para poder restaurarlo */
function syncEdits() {
  if (!S.editing || !S.tx) return;
  $$('#txBody .s[data-i]').forEach(el => {
    const s = S.tx.segs[+el.dataset.i], t = el.innerText.replace(/\s+/g, ' ').trim();
    if (s && s.text !== t) { s.text = t; S.dirty = true; }
  });
}
function setSaveState(txt) { const el = $('#saveState'); if (el) el.textContent = txt; }
function scheduleSave() { clearTimeout(S.saveTimer); setSaveState('Sin guardar…'); S.saveTimer = setTimeout(saveEdits, 1200); }
function editPayload() {
  return { path: S.tx.path, segments: S.tx.segs.filter(s => s.text.trim()).map(s => ({ start: s.start, end: s.end, text: s.text })) };
}
async function saveEdits() {
  clearTimeout(S.saveTimer);
  if (!S.tx) return;
  syncEdits();
  if (!S.dirty) return;
  S.dirty = false;
  setSaveState('Guardando…');
  try {
    const r = await api('/api/transcript/save', editPayload());
    S.tx.mtime = r.tx_mtime;
    const a = S.audios.get(S.tx.path);
    if (a) a.tx_mtime = r.tx_mtime;
    if (!r.live) {  // en directo la copia del original se guarda al terminar
      S.tx.has_backup = true;
      $('[data-act="tx-restore"]')?.classList.remove('hidden');
    }
    setSaveState(S.dirty ? 'Sin guardar…' : 'Guardado ✓');
  } catch (err) { S.dirty = true; setSaveState('No se ha podido guardar'); toast(esc(err.message), 'err'); }
}
/* al salir de la pagina o cambiar de reunion: se guarda sin esperar */
function flushEdits() {
  if (!S.editing || !S.tx) return;
  syncEdits();
  clearTimeout(S.saveTimer);
  if (S.dirty) {
    fetch('/api/transcript/save', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-Audios': '1' }, body: JSON.stringify(editPayload()) });
    S.dirty = false;
  }
  S.editing = false;
}
function startEdit() {
  if (S.tx && S.tx.kind === 'txt') toast('Esta transcripción no tiene tiempos (.txt): se edita igual.');
  S.editing = true; S.dirty = false;
  renderTxSection();
  $('#txBody .s')?.focus();
}
async function finishEdit() {
  await saveEdits();
  S.editing = false;
  S.tx.segs = S.tx.segs.filter(s => s.text.trim());
  S.tx.starts = S.tx.segs.map(s => s.start ?? 0);
  renderTxSection();
  toast('Cambios guardados en el .txt y el .srt');
}
function replaceAll() {
  syncEdits();
  const find = (S.tq || '').trim(), rep = $('#trep').value;
  if (!find) { toast('Escribe primero en el buscador (arriba) lo que quieres cambiar'); $('#tq')?.focus(); return; }
  const ff = fold(find);
  let n = 0;
  for (const s of S.tx.segs) {
    const f = foldMap(s.text);
    let out = '', i = 0, k;
    while ((k = f.indexOf(ff, i)) !== -1) { out += s.text.slice(i, k) + rep; i = k + ff.length; n++; }
    if (i) s.text = out + s.text.slice(i);
  }
  if (!n) return toast(`«${esc(find)}» no aparece en el texto`);
  S.dirty = true;
  S.tq = rep; $('#tq').value = rep;  // ahora resalta lo nuevo, para revisarlo
  renderTxBody(true);
  scheduleSave();
  toast(`${n} ${n === 1 ? 'cambio' : 'cambios'}: «${esc(find)}» → «${esc(rep)}»`);
}
async function restoreOriginal() {
  if (!await ask('¿Restaurar el original?', 'Se descartan todas tus correcciones y vuelve el texto tal como lo transcribió Whisper.', 'Restaurar')) return;
  clearTimeout(S.saveTimer);
  try {
    await api('/api/transcript/restore', { path: S.tx.path });
    S.editing = false; S.dirty = false;
    const a = S.audios.get(S.tx.path);
    S.tx = null;
    if (a) loadTranscript(a);
    toast('Restaurado el texto original de Whisper');
  } catch (err) { toast(esc(err.message), 'err'); }
}

function paragraphs(segs, who) {
  const paras = [];
  let cur = null, len = 0;
  segs.forEach((s, i) => {
    const prev = segs[i - 1];
    const gap = prev && s.start != null ? s.start - prev.end : 0;
    const endsSentence = prev ? /[.?!…]["»”)]?$/.test(prev.text) : true;
    const turn = who && i > 0 && who[i] !== who[i - 1];  // cambia quien habla: parrafo nuevo
    if (!cur || turn || gap > 3 || (len > 420 && endsSentence) || len > 1100 || (s.start == null && cur.length >= 5)) { cur = []; paras.push(cur); len = 0; }
    cur.push(i); len += s.text.length;
  });
  return paras;
}

function renderTxBody(fromModel) {
  if (!fromModel) syncEdits();  // fromModel: los datos ya traen cambios hechos por codigo (reemplazar)
  const body = $('#txBody'), tx = S.tx;
  if (!body || !tx) return;
  const terms = termsOf(S.tq);
  const live = S.viewMode === 'live';
  const fresh = live && S.seenSegs ? S.seenSegs : Infinity;  // lo recien llegado se resalta un momento
  const edit = S.editing ? ' contenteditable="plaintext-only" spellcheck="true"' : '';
  const span = i => `<span class="s${i >= fresh ? ' fresh' : ''}" data-i="${i}"${edit}>${hlTerms(tx.segs[i].text, terms)}</span>`;
  // quien habla (voces.js): etiqueta al cambiar de hablante, parrafo nuevo por turno
  const who = S.spk && S.spk.path === tx.path && S.spk.exists && tx.kind === 'srt' && S.spk.who && S.spk.who.length === tx.segs.length ? S.spk.who : null;
  const chip = i => who && (i === 0 || who[i] !== who[i - 1]) ? spkChip(who[i]) : '';
  if (S.mode === 'lines' && tx.kind === 'srt') {
    body.innerHTML = tx.segs.map((s, i) => `<div class="ln"><span class="ts" data-i="${i}">${fmtClock(s.start)}</span><span>${chip(i)}${span(i)}</span></div>`).join('');
  } else {
    body.innerHTML = paragraphs(tx.segs, who).map(p => {
      const s0 = tx.segs[p[0]];
      return `<p>${chip(p[0])}${s0.start != null ? `<span class="ts" data-i="${p[0]}">${fmtClock(s0.start)}</span>` : ''}${p.map(span).join(' ')}</p>`;
    }).join('');
  }
  if (live) {
    S.seenSegs = tx.segs.length;
    const a = S.audios.get(tx.path), lj = a && liveJob(a);
    if (lj) body.insertAdjacentHTML('beforeend', `<div class="typing" title="${esc(lj.stage)}"><i></i><i></i><i></i><span>escuchando…</span></div>`);
    if (typeof directoAfterRender === 'function') directoAfterRender(tx, lj);
  }
  S.marks = $$('mark', body); S.markIdx = -1;
  const n = $('#tqn');
  if (n) n.textContent = terms.length ? (S.marks.length ? `${S.marks.length}` : '0') : '';
  S.curIdx = -1; S.curEl = null;
  syncHighlight();
}

function gotoMark(i) {
  if (!S.marks.length) return;
  S.marks[S.markIdx]?.classList.remove('now');
  S.markIdx = (i + S.marks.length) % S.marks.length;
  const m = S.marks[S.markIdx];
  m.classList.add('now');
  scrollToEl(m, true);
  const n = $('#tqn');
  if (n) n.textContent = `${S.markIdx + 1}/${S.marks.length}`;
}

function segAt(t) {
  const st = S.tx?.starts || [];
  let lo = 0, hi = st.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (st[mid] <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}

function syncHighlight() {
  if (!S.tx || S.tx.kind !== 'srt' || S.playing !== S.tx.path) {
    if (S.curEl && S.playing && S.tx && S.playing !== S.tx.path) { S.curEl.classList.remove('cur'); S.curEl = null; S.curIdx = -1; }
    return;
  }
  const i = segAt(audio.currentTime);
  if (i === S.curIdx && S.curEl && S.curEl.isConnected) return;
  S.curIdx = i;
  S.curEl?.classList.remove('cur');
  S.curEl = i >= 0 ? $(`#txBody .s[data-i="${i}"]`) : null;
  if (S.curEl) {
    S.curEl.classList.add('cur');
    if (S.follow && !audio.paused && Date.now() - S.userScroll > 4000) scrollToEl(S.curEl);
  }
}

function scrollToEl(el, force) {
  const r = el.getBoundingClientRect(), mr = main.getBoundingClientRect();
  if (force || r.top < mr.top + 80 || r.bottom > mr.bottom - 40) {
    main.scrollTo({ top: main.scrollTop + r.top - mr.top - mr.height * 0.35, behavior: 'smooth' });
  }
}
['wheel', 'touchmove'].forEach(ev => main.addEventListener(ev, () => { S.userScroll = Date.now(); }, { passive: true }));
function updateToLive() {
  const b = $('#toLive');
  if (b) b.classList.toggle('hidden', S.viewMode !== 'live' || main.scrollHeight - main.scrollTop - main.clientHeight < 160);
}
main.addEventListener('scroll', updateToLive, { passive: true });

function transcriptText() {
  const tx = S.tx;
  if (!tx) return '';
  return paragraphs(tx.segs).map(p => p.map(i => tx.segs[i].text).join(' ')).join('\n\n');
}

/* ---------------------------------------------------------------- documentos */
async function viewDoc(d) {
  main.innerHTML = `<div class="page">${crumbs(d.parent?.path || '')}
    <h1>${esc(nfc(d.name))}</h1>
    <div class="meta"><span>${fmtSize(d.size)}</span><span>${new Date(d.mtime * 1000).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' })}</span>
      ${d.cloud ? `<span class="pill cloud">${I.cloud}Solo en OneDrive</span>` : ''}</div>
    <div class="actions">
      <button class="btn" data-act="copy-doc">${I.copy}Copiar texto</button>
      <button class="btn" data-act="open-doc" data-path="${esc(d.path)}">${I.ext}Abrir</button>
      <button class="btn" data-act="move" data-path="${esc(d.path)}">${I.move}Mover / renombrar</button>
      <button class="btn" data-act="reveal" data-path="${esc(d.path)}">${I.ext}Mostrar en carpeta</button>
    </div>
    <div class="card docview" id="docBody"><div class="muted">Cargando…</div></div></div>`;
  try {
    const r = await api('/api/doc?path=' + enc(d.path));
    if (S.route.kind !== 'd' || S.route.path !== d.path) return;
    S.docText = r.text;
    const terms = termsOf(S.route.params.get('q'));
    let html;
    if (r.ext === '.md') html = md(r.text);
    else if (r.ext === '.srt') html = r.segments.map(s => `<p><code>${fmtClock(s.start)}</code> ${hlTerms(s.text, terms)}</p>`).join('');
    else html = `<pre>${hlTerms(r.text, terms)}</pre>`;
    $('#docBody').innerHTML = html;
    const m = $('#docBody mark');
    if (m) scrollToEl(m, true);
  } catch (err) { $('#docBody').innerHTML = `<div class="muted">${esc(err.message)}</div>`; }
}

function md(src) {
  const lines = src.replace(/\r/g, '').split('\n'), out = [];
  let list = null, para = [];
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const flushP = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } };
  const flushL = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const cells = r => r.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if (/^\s*\|/.test(l)) {
      flushP(); flushL();
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      i--;
      const body = rows.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r));
      out.push('<table>' + body.map((r, k) => `<tr>${cells(r).map(c => k ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`).join('')}</tr>`).join('') + '</table>');
    } else if ((m = l.match(/^(#{1,4})\s+(.*)/))) { flushP(); flushL(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
    else if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { flushP(); flushL(); out.push('<hr>'); }
    else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { flushP(); if (list !== 'ul') { flushL(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { flushP(); if (list !== 'ol') { flushL(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(m[1])}</li>`); }
    else if (!l.trim()) { flushP(); flushL(); }
    else { flushL(); para.push(l.trim()); }
  }
  flushP(); flushL();
  return out.join('\n');
}

/* ---------------------------------------------------------------- busqueda global */
let searchSeq = 0;
async function viewSearch(q) {
  const seq = ++searchSeq;
  if ($('#gq').value !== q && document.activeElement !== $('#gq')) $('#gq').value = q;
  main.innerHTML = `<div class="page"><h1>Resultados <span class="sub">“${esc(q)}”</span></h1><div class="meta" id="sMeta">Buscando…</div><div id="sRes" style="margin-top:18px"></div></div>`;
  let r;
  try { r = await api('/api/search?q=' + enc(q)); } catch (err) { $('#sMeta').textContent = err.message; return; }
  if (seq !== searchSeq) return;
  const terms = termsOf(q);
  $('#sMeta').textContent = r.results.length ? `${r.total} coincidencias en ${r.results.length} archivo${r.results.length === 1 ? '' : 's'}` : 'Sin coincidencias en las transcripciones.';
  $('#sRes').innerHTML = r.results.map(x => {
    const a = x.kind === 'audio' ? S.audios.get(x.path) : null;
    const title = a ? fullTitle(a) : nfc(x.name);
    const k = x.kind === 'audio' ? 'a' : 'd';
    const hits = x.hits.map(h => `<div class="hit" data-go="${k}" data-path="${esc(x.path)}" data-t="${h.start ?? ''}" data-q="${esc(q)}">`
      + `<span class="ts">${h.start != null ? fmtClock(h.start) : '·'}</span><span>${hlTerms(h.text, terms)}</span></div>`).join('');
    return `<div class="card sres"><div class="h" data-go="${k}" data-path="${esc(x.path)}" data-q="${esc(q)}"><b>${x.kind === 'doc' ? I.doc : ''}${esc(title)}</b>`
      + `<span class="f">${esc(nfc(x.folder || APP_NAME).replaceAll('/', ' › '))} · ${x.count} ${x.count === 1 ? 'vez' : 'veces'}</span></div>${hits}`
      + `${x.count > x.hits.length ? `<div class="muted" style="font-size:12.5px;padding-top:6px">y ${x.count - x.hits.length} más…</div>` : ''}</div>`;
  }).join('');
}
let gqTimer;
$('#gq').addEventListener('input', () => {
  clearTimeout(gqTimer);
  gqTimer = setTimeout(() => {
    const q = $('#gq').value.trim();
    if (q.length >= 2) go('s', q, null, S.route.kind === 's');
    else if (!q && S.route.kind === 's') history.back();
  }, 220);
});
$('#gq').addEventListener('keydown', e => { if (e.key === 'Enter') { clearTimeout(gqTimer); const q = $('#gq').value.trim(); if (q.length >= 2) go('s', q, null, S.route.kind === 's'); } });

/* ---------------------------------------------------------------- reproductor */
function loadAudio(a, t, autoplay = true) {
  // grabacion en curso: si se pide un punto mas alla de lo que el reproductor cargo, se recarga
  if (S.playing === a.path && isLive(a) && t != null && !(t < (audio.duration || 0) - 1)) S.playing = null;
  if (S.playing !== a.path) {
    savePos();
    S.playing = a.path;
    audio.src = '/media?path=' + enc(a.path);
    audio.defaultPlaybackRate = audio.playbackRate = S.rate;
    const saved = parseFloat(localStorage.getItem('pos:' + a.path));
    const start = t != null ? t : (saved > 5 ? saved : 0);
    if (start) audio.currentTime = start;
    $('#pTitle').textContent = fullTitle(a);
    $('#pTitle').href = href('a', a.path);
    $('#pSeek').max = estDuration(a);
    $('#pDur').textContent = fmtClock(estDuration(a));
    $('#player').classList.remove('hidden');
    document.body.classList.add('playing');
    if (t == null && saved > 5) toast(`Continúas donde lo dejaste: ${fmtClock(saved)}`);
  } else if (t != null) audio.currentTime = t;
  if (autoplay) audio.play().catch(err => toast('No se pudo reproducir: ' + esc(err.message), 'err'));
  S.curIdx = -1;
  syncHighlight();
}
function togglePlay() {
  if (!S.playing) {
    const a = S.route.kind === 'a' && S.audios.get(S.route.path);
    if (a) loadAudio(a);
    return;
  }
  audio.paused ? audio.play() : audio.pause();
}
function seekBy(d) { if (S.playing) audio.currentTime = Math.max(0, Math.min((audio.duration || 1e9) - 0.5, audio.currentTime + d)); }
function setRate(r) {
  S.rate = r; localStorage.setItem('rate', r);
  audio.playbackRate = audio.defaultPlaybackRate = r;
  $$('#pSpeed button').forEach(b => b.classList.toggle('on', +b.dataset.r === r));
}
let lastSave = 0;
function savePos() {
  if (!S.playing || !audio.currentTime) return;
  const d = audio.duration || 0;
  if (d && audio.currentTime > d - 15) localStorage.removeItem('pos:' + S.playing);
  else localStorage.setItem('pos:' + S.playing, audio.currentTime.toFixed(1));
}
$('#pSpeed').innerHTML = SPEEDS.map(r => `<button data-r="${r}" class="${r === S.rate ? 'on' : ''}">${String(r).replace('.', ',')}×</button>`).join('');
$('#pSpeed').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setRate(+b.dataset.r); });
$('#pPlay').onclick = togglePlay;
$('#pBack').onclick = () => seekBy(-15);
$('#pFwd').onclick = () => seekBy(15);
let seeking = false;
const seek = $('#pSeek');
const paintSeek = () => seek.style.setProperty('--pct', (100 * seek.value / (seek.max || 1)) + '%');
seek.addEventListener('input', () => { seeking = true; $('#pCur').textContent = fmtClock(seek.value); paintSeek(); });
seek.addEventListener('change', () => { audio.currentTime = +seek.value; seeking = false; });
audio.addEventListener('play', () => { $('#player').classList.add('on'); playBtns(); });
audio.addEventListener('pause', () => { $('#player').classList.remove('on'); savePos(); playBtns(); });
audio.addEventListener('durationchange', () => { if (isFinite(audio.duration)) { seek.max = audio.duration; $('#pDur').textContent = fmtClock(audio.duration); } });
audio.addEventListener('timeupdate', () => {
  if (!seeking) { seek.value = audio.currentTime; $('#pCur').textContent = fmtClock(audio.currentTime); paintSeek(); }
  syncHighlight();
  if (Date.now() - lastSave > 5000) { lastSave = Date.now(); savePos(); }
});
audio.addEventListener('ended', () => { if (S.playing) localStorage.removeItem('pos:' + S.playing); });
audio.addEventListener('error', () => { if (audio.getAttribute('src')) toast('El navegador no puede reproducir este formato de audio.', 'err'); });
function playBtns() {
  $$('[data-act="play"]').forEach(b => {
    const on = b.dataset.path === S.playing && !audio.paused;
    b.innerHTML = `${I.play}${on ? 'Pausa' : 'Reproducir'}`;
  });
}
window.addEventListener('beforeunload', () => { savePos(); flushEdits(); });

/* ---------------------------------------------------------------- acciones (delegadas) */
main.addEventListener('click', async e => {
  const t = e.target;
  const act = t.closest('[data-act]');
  if (act && main.contains(act)) {
    e.stopPropagation();
    const a = act.dataset.act, p = act.dataset.path;
    if (a === 'play') {
      const au = S.audios.get(p);
      if (S.playing === p) togglePlay(); else if (au) loadAudio(au);
    } else if (a === 'play-item') { const au = S.audios.get(p); if (au) loadAudio(au); }
    else if (a === 'live') {
      const preset = S.livePreset || S.config.live_preset || 'medium';
      try {
        await api('/api/live', { path: p, preset });
        S.config.live_preset = preset;
        toast(`Transcripción en directo en marcha · ${esc(S.presetsById[preset]?.live_label || preset)}`);
        pollJobs(true);
      } catch (err) { toast(esc(err.message), 'err'); }
    }
    else if (a === 'live-model') {
      const preset = act.dataset.preset, pr = S.presetsById[preset];
      if (pr.engine === 'cloud' && !S.config.cloud.has_key) return openSettings();
      if (act.dataset.id) {
        try {
          await api('/api/live/model', { id: act.dataset.id, preset });
          S.config.live_preset = preset;
          toast(`Desde el siguiente trozo: <b>${esc(pr.live_label)}</b>`);
          pollJobs(true);
        } catch (err) { toast(esc(err.message), 'err'); }
      } else {
        S.livePreset = preset;
        const au = S.audios.get(S.route.path);
        if (au) renderLiveHero(au);
      }
    }
    else if (a === 'live-restart') {
      const j = S.jobsById.get(act.dataset.id), pr = j && S.presetsById[j.preset];
      if (!j) return;
      const behind = j.recorded / Math.max(0.5, pr.live_speed || 1);
      if (!await ask('¿Rehacer desde el principio?',
        `Se vuelve a transcribir todo lo grabado (${fmtLeft(j.recorded)}) con <b>${esc(pr.live_label)}</b> y se sustituye el texto actual. `
        + `Ponerse al día llevará unos ${fmtLeft(behind)}; mientras tanto sigue grabando sin problema.`, 'Rehacer')) return;
      try { await api('/api/live/model', { id: j.id, preset: j.preset, restart: true }); S.seenSegs = 0; pollJobs(true); }
      catch (err) { toast(esc(err.message), 'err'); }
    }
    else if (a === 'play-live') {
      const au = S.audios.get(p);
      if (au) { S.playing = null; loadAudio(au, null, true); }  // recarga: el audio ha crecido
    }
    else if (a === 'rec-stop') stopRec();
    else if (a === 'tx-edit') startEdit();
    else if (a === 'tx-done') finishEdit();
    else if (a === 'tx-replace-all') replaceAll();
    else if (a === 'tx-restore') restoreOriginal();
    else if (a === 'to-live') { main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' }); S.userScroll = 0; }
    else if (a === 'tx-toggle') { S.txOpen = !S.txOpen; const au = S.audios.get(S.route.path); if (au) { $('#aJob').innerHTML = ''; $('#aJob').dataset.id = ''; renderAudioHead(au); if (S.txOpen) $('#aBox').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } }
    else if (a === 'tx-go') {
      const id = act.dataset.box, sel = $(`select[data-lang="${id}"]`);
      S.selLang = sel ? sel.value : '';
      const box = S.boxes[id] || { paths: [], resume: false };
      submitTx(box.paths, S.selPreset || S.config.preset, S.selLang, box.resume);
    }
    else if (a === 'move') openMove(p);
    else if (a === 'reveal') api('/api/reveal', { path: p }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'open-txt') api('/api/open', { path: p, ext: '.txt' }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'open-doc') api('/api/open', { path: p }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'new-folder') openNewFolder(p);
    else if (a === 'rename-folder') openRenameFolder(p);
    else if (a === 'link-remove') unlinkFolder(p);
    else if (a === 'cancel-job') { await api('/api/cancel', { id: act.dataset.id }); pollJobs(true); }
    else if (a === 'copy-tx') copy(transcriptText(), 'Transcripción copiada');
    else if (a === 'copy-doc') copy(S.docText || '', 'Texto copiado');
    else if (a === 'mark-next') gotoMark(S.markIdx + 1);
    else if (a === 'mark-prev') gotoMark(S.markIdx - 1);
    return;
  }
  const pr = t.closest('.preset');
  if (pr) {
    const p = S.presetsById[pr.dataset.preset];
    if (p.engine === 'cloud' && !S.config.cloud.has_key) return openSettings();
    S.selPreset = p.id;
    $$(`.preset[data-box="${pr.dataset.box}"]`).forEach(b => b.classList.toggle('on', b === pr));
    const h = $(`[data-hint="${pr.dataset.box}"]`);
    if (h) h.textContent = p.hint;
    return;
  }
  const mode = t.closest('[data-mode]');
  if (mode) { S.mode = mode.dataset.mode; localStorage.setItem('txmode', S.mode); renderTxSection(); return; }
  const seg = t.closest('#txBody .s, #txBody .ts');
  if (seg && S.editing && seg.classList.contains('s')) return;  // editando: el clic pone el cursor (el tiempo sigue saltando)
  if (seg && S.tx && S.tx.kind === 'srt') {
    if (window.getSelection().toString()) return;  // seleccionando texto para copiar
    const au = S.audios.get(S.tx.path), s = S.tx.segs[+seg.dataset.i];
    if (au && s) { S.userScroll = Date.now(); loadAudio(au, s.start, true); }
    return;
  }
  const g = t.closest('[data-go]');
  if (g) {
    const params = {};
    if (g.dataset.t) params.t = g.dataset.t;
    if (g.dataset.q) params.q = g.dataset.q;
    go(g.dataset.go, g.dataset.path, Object.keys(params).length ? params : null);
  }
});
main.addEventListener('change', e => {
  if (e.target.id === 'follow') { S.follow = e.target.checked; localStorage.setItem('follow', S.follow ? '1' : '0'); if (S.follow) { S.userScroll = 0; syncHighlight(); } }
  if (e.target.dataset.lang) S.selLang = e.target.value;
});
let tqTimer;
main.addEventListener('input', e => {
  if (S.editing && e.target.closest && e.target.closest('#txBody .s')) { S.dirty = true; scheduleSave(); return; }
  if (e.target.id !== 'tq') return;
  clearTimeout(tqTimer);
  tqTimer = setTimeout(() => { S.tq = e.target.value; renderTxBody(); if (S.marks.length) gotoMark(0); }, 160);
});
main.addEventListener('keydown', e => {
  if (e.target.id === 'tq' && e.key === 'Enter') { e.preventDefault(); gotoMark(S.markIdx + (e.shiftKey ? -1 : 1)); }
  if (e.target.id === 'trep' && e.key === 'Enter') { e.preventDefault(); replaceAll(); }
  const seg = S.editing && e.target.closest && e.target.closest('#txBody .s');
  if (!seg) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveEdits(); }
  else if (e.key === 'Enter') {  // Intro: a la frase siguiente (no se parten frases sin querer)
    e.preventDefault();
    const next = $(`#txBody .s[data-i="${+seg.dataset.i + (e.shiftKey ? -1 : 1)}"]`);
    if (next) {
      next.focus();
      const r = document.createRange(); r.selectNodeContents(next); r.collapse(false);
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    }
  }
});

async function copy(text, msg) {
  try { await navigator.clipboard.writeText(text); toast(msg); } catch { toast('No se pudo copiar', 'err'); }
}

/* ---------------------------------------------------------------- teclado */
document.addEventListener('keydown', e => {
  const el = document.activeElement;
  const typing = el && (el.isContentEditable || (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.type !== 'range' && el.type !== 'checkbox'));
  const k = e.key.toLowerCase();
  if (e.ctrlKey && e.altKey && k === 'r') { e.preventDefault(); toggleRec(); return; }  // si Windows no dejo registrar el atajo global
  if ((e.ctrlKey || e.metaKey) && k === 'p' && !e.altKey) { e.preventDefault(); if ($('#modal').classList.contains('hidden')) openPalette(); return; }  // paleta, como Notion
  if ((e.ctrlKey || e.metaKey) && k === 'k' && !e.altKey) { e.preventDefault(); const g = $('#gq'); if (g && $('#modal').classList.contains('hidden')) { g.focus(); g.select(); } return; }  // la caja de búsqueda de arriba (su kbd dice Ctrl K)
  if ((e.ctrlKey || e.metaKey) && k === 'f' && $('#tq')) { e.preventDefault(); $('#tq').focus(); $('#tq').select(); return; }
  if (e.key === 'Escape') {
    if (!$('#modal').classList.contains('hidden')) return closeModal(false);
    if (!$('#jobsPanel').classList.contains('hidden')) return $('#jobsPanel').classList.add('hidden');
    if (typing) el.blur();
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || !$('#modal').classList.contains('hidden')) return;
  if (e.key === ' ') { e.preventDefault(); togglePlay(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); seekBy(e.shiftKey ? -15 : -5); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); seekBy(e.shiftKey ? 15 : 5); }
  else if (e.key === '[' || e.key === ']') {
    const i = SPEEDS.indexOf(S.rate);
    setRate(SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 1 : i) + (e.key === ']' ? 1 : -1)))]);
    toast(`Velocidad ${String(S.rate).replace('.', ',')}×`);
  }
});

/* ---------------------------------------------------------------- cola de trabajos */
let jobsTimer = null;
async function pollJobs(now) {
  clearTimeout(jobsTimer);
  if (S.off) return;
  try { onJobs((await api('/api/jobs')).jobs); } catch { /* servidor reiniciandose */ }
  const active = S.jobs.some(isActive);
  jobsTimer = setTimeout(pollJobs, active ? 1000 : 4000);
}
function onJobs(jobs) {
  const prev = S.jobsById;
  S.jobs = jobs;
  S.jobsById = new Map(jobs.map(j => [j.id, j]));
  let finished = false;
  for (const j of jobs) {
    const p = prev.get(j.id);
    if (p && p.status !== j.status && ['done', 'error'].includes(j.status)) { notifyJob(j); finished = true; }
  }
  const sig = jobs.map(j => j.id + j.status + (j.recording ? 'r' : '') + (j.live ? j.preset : '')).join();
  const statusChanged = sig !== S.jobsSig;
  S.jobsSig = sig;
  const active = jobs.filter(isActive), running = jobs.find(j => j.status === 'running');
  const badge = $('#jobsCount');
  badge.textContent = active.length;
  badge.classList.toggle('hidden', !active.length);
  badge.classList.toggle('pulse', !!running);
  document.title = running ? `(${Math.round(running.progress * 100)} %) ${APP_NAME}` : APP_NAME;
  if (!$('#jobsPanel').classList.contains('hidden')) renderJobsPanel();
  onSummaryJobs(prev);
  onVoicesJobs(prev);
  if (typeof onDirectoJobs === 'function') onDirectoJobs(prev);
  if (finished) { refreshTree(true); api('/api/config').then(setConfig).catch(() => { }); }
  if (statusChanged) { renderTree(); refreshView(); renderLiveChip(); }
  else applyProgress();
}
function applyProgress() {
  for (const j of S.jobs) {
    if (j.status !== 'running') continue;
    const w = (j.progress * 100).toFixed(1) + '%';
    const row = document.querySelector(`.trow.audio[data-audio="${CSS.escape(j.path)}"] .tprog i`);
    if (row) row.style.width = w;
    $$(`[data-bar="${CSS.escape(j.path)}"] i`).forEach(x => x.style.width = w);
  }
  if (S.route.kind === 'a') {
    const a = S.audios.get(S.route.path);
    if (a) {
      if (S.viewMode === 'live') updateLiveHero(a); else renderAudioJob(a);
      const j = jobFor(a.path);
      if (j && j.live && j.rev !== S.liveRev) { S.liveRev = j.rev; loadTranscript(a); }  // texto nuevo del directo
    }
  }
  $$('.live-banner').forEach(b => {
    const a = S.audios.get(b.dataset.path), m = $('.lb-t .muted', b);
    if (a && m) { syncClock(a); const j = liveJob(a); m.innerHTML = j ? liveStatus(a, j) : 'Grabando · sin transcripción en directo'; }
  });
  renderLiveChip();
}
function notifyJob(j) {
  if (j.kind === 'voces') {
    const a = S.audios.get(j.path), title = a ? fullTitle(a) : j.name;
    if (j.status === 'done') toast(`🗣 ${esc(j.stage)}: ${esc(title)}`, 'ok', () => go('a', j.path));
    else toast(`No se pudieron identificar las voces de ${esc(title)}: ${esc(j.error)}`, 'err', () => go('a', j.path));
    return;
  }
  if (j.kind === 'summary') { if (j.status === 'error') toast(`No se pudo resumir: ${esc(j.error)}`, 'err'); return; }  // el aviso de listo lo da notas.js
  const a = S.audios.get(j.path), title = a ? fullTitle(a) : j.name;
  if (j.status === 'done') toast(`<b>✓</b> ${j.live ? 'Grabación transcrita' : 'Transcrito'}: ${esc(title)}`, 'ok', () => go('a', j.path));
  else toast(`No se pudo transcribir ${esc(title)}: ${esc(j.error)}`, 'err', () => go('a', j.path));
  if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
    const n = new Notification(j.status === 'done' ? 'Transcripción lista' : 'Error al transcribir', { body: title });
    n.onclick = () => { window.focus(); go('a', j.path); };
  }
}
function renderJobsPanel() {
  const list = [...S.jobs].reverse();
  const hasDone = list.some(j => !isActive(j));
  $('#jobsPanel').innerHTML = `<div class="ph"><b>Transcripciones</b>${hasDone ? '<button class="btn ghost sm" data-jact="clear">Limpiar terminadas</button>' : ''}</div>`
    + (list.length ? list.map(j => {
      const a = S.audios.get(j.path), title = a ? fullTitle(a) : j.name;
      const pct = j.status === 'done' ? 100 : Math.round(j.progress * 100);
      const bar = isActive(j) ? `<div class="pbar${j.status === 'running' && j.progress < 0.004 ? ' indet' : ''}" style="grid-column:1/-1"><i style="width:${pct}%"></i></div>` : '';
      const sub = j.status === 'error' ? `<div class="s err">${esc(j.error)}</div>` : `<div class="s">${esc(jobStage(j))} · ${jobSub(j)}</div>`;
      const btn = isActive(j) ? `<button class="btn ghost sm danger" data-jact="cancel" data-id="${j.id}">${j.live ? 'Detener' : 'Cancelar'}</button>`
        : `<span class="pill ${j.status === 'done' ? 'ok' : j.status === 'error' ? 'err' : ''}">${{ done: 'Hecho', error: 'Error', cancelled: 'Cancelado' }[j.status]}</span>`;
      return `<div class="jitem"><span class="n" data-jgo="${esc(j.path)}">${esc(title)}</span>${btn}${bar}${sub}</div>`;
    }).join('') : '<div class="empty">No hay transcripciones en marcha.<br>Elige un audio y pulsa <b>Transcribir</b>.</div>');
}
$('#btnJobs').onclick = e => { e.stopPropagation(); const p = $('#jobsPanel'); p.classList.toggle('hidden'); if (!p.classList.contains('hidden')) renderJobsPanel(); };
$('#jobsPanel').addEventListener('click', async e => {
  e.stopPropagation();
  const b = e.target.closest('[data-jact]'), g = e.target.closest('[data-jgo]');
  if (b && b.dataset.jact === 'cancel') { await api('/api/cancel', { id: b.dataset.id }); pollJobs(true); }
  else if (b && b.dataset.jact === 'clear') { await api('/api/jobs/clear', {}); pollJobs(true); }
  else if (g) { go('a', g.dataset.jgo); $('#jobsPanel').classList.add('hidden'); }
});
document.addEventListener('click', e => { if (!e.target.closest('#jobsPanel, #btnJobs')) $('#jobsPanel').classList.add('hidden'); });

/* ---------------------------------------------------------------- modales */
let modalResolve = null;
function openModal(html, onOpen, cls = '') {
  const m = $('#modal');
  m.innerHTML = `<div class="dialog ${cls}">${html}</div>`;
  m.classList.remove('hidden');
  onOpen && onOpen($('.dialog', m));
  const f = $('.dialog [autofocus]', m) || $('.dialog input, .dialog button.primary', m);
  f && f.focus();
  return new Promise(r => { modalResolve = r; });
}
function closeModal(v) {
  if (S.modalCleanup) { const f = S.modalCleanup; S.modalCleanup = null; f(); }
  $('#modal').classList.add('hidden');
  $('#modal').innerHTML = '';
  const r = modalResolve; modalResolve = null;
  r && r(v);
}
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(false); });
function ask(title, html, ok = 'Aceptar') {
  return openModal(`<h3>${esc(title)}</h3><div>${html}</div><div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">${esc(ok)}</button></div>`, d => {
    d.addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) closeModal(b.dataset.m === 'ok'); });
  });
}

function flatFolders() {
  const out = [];
  (function w(n, d) { out.push({ path: n.path, name: n.path ? (n.linked ? '🔗 ' : '') + nfc(n.name) : `${APP_NAME} (raíz)`, depth: d }); n.folders.forEach(f => w(f, d + 1)); })(S.tree, 0);
  return out;
}
/* vincular una carpeta de fuera de Audios (p. ej. la del proyecto): selector de carpetas de Windows */
async function linkFolder() {
  toast('📂 Elige la carpeta en la ventana de Windows que se acaba de abrir…');
  let path;
  try { path = (await api('/api/link/pick', {})).path; }
  catch (err) { path = prompt(`${err.message}\n\nPega aquí la ruta de la carpeta:`); }
  if (!path) return null;
  try {
    const r = await api('/api/link/add', { path });
    setConfig(await api('/api/config'));
    await refreshTree(true);
    toast(`🔗 <b>${esc(r.name)}</b> añadida a ${esc(APP_NAME)}: aparece arriba en Carpetas`);
    return r.rel;
  } catch (err) { toast(esc(err.message), 'err'); return null; }
}
async function unlinkFolder(rel) {
  const f = S.folders.get(rel);
  if (!f || !await ask(`¿Quitar «${nfc(f.name)}» de ${APP_NAME}?`, `No se borra ni se mueve nada: la carpeta y sus grabaciones siguen en<br><code>${esc(f.linked)}</code><br>y la puedes volver a añadir cuando quieras.`, `Quitar de ${APP_NAME}`)) return false;
  try {
    await api('/api/link/remove', { path: rel });
    setConfig(await api('/api/config'));
    await refreshTree(true);
    if ((S.route.path || '').startsWith(rel)) go('home', '');
    toast(`«${esc(nfc(f.name))}» ya no aparece en ${esc(APP_NAME)} (sus archivos siguen donde estaban)`);
    return true;
  } catch (err) { toast(esc(err.message), 'err'); return false; }
}
function openMove(path) {
  const item = S.audios.get(path) || S.docs.get(path);
  if (!item) return;
  const isAudio = S.audios.has(path);
  const stem = nfc(isAudio ? item.stem : item.name.replace(/\.[^.]+$/, ''));
  let dest = item.parent ? item.parent.path : '';
  const list = () => flatFolders().map(f => `<div data-f="${esc(f.path)}" class="${f.path === dest ? 'on' : ''}" style="padding-left:${10 + f.depth * 16}px">${f.depth ? I.folder : I.home}${esc(f.name)}</div>`).join('');
  openModal(`<h3>Mover / renombrar</h3>
    <label>Nombre</label><input class="input" id="mvName" value="${esc(stem)}" autofocus>
    <div class="help">${isAudio ? 'Se renombran y mueven también su .txt y .srt. Truco: deja la fecha y añade detrás quién o qué, p. ej. “… - Reunión de seguimiento”.' : ''}</div>
    <label>Carpeta de destino</label><div class="flist" id="mvList">${list()}</div>
    <button class="btn ghost sm" id="mvNew" style="margin-top:8px">${I.plus}Nueva carpeta dentro de la seleccionada</button>
    <div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Guardar</button></div>`, d => {
    $('#mvList', d).addEventListener('click', e => { const f = e.target.closest('[data-f]'); if (!f) return; dest = f.dataset.f; $$('#mvList div', d).forEach(x => x.classList.toggle('on', x === f)); });
    $('#mvList', d).addEventListener('dblclick', e => { if (e.target.closest('[data-f]')) $('[data-m="ok"]', d).click(); });
    $('#mvNew', d).onclick = async () => {
      const name = prompt('Nombre de la nueva carpeta:');
      if (!name) return;
      try { const r = await api('/api/mkdir', { parent: dest, name }); await refreshTree(true); dest = r.path; $('#mvList', d).innerHTML = list(); $('#mvList .on', d)?.scrollIntoView({ block: 'nearest' }); }
      catch (err) { toast(esc(err.message), 'err'); }
    };
    d.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.id === 'mvName') $('[data-m="ok"]', d).click(); });
    d.addEventListener('click', e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m === 'no') return closeModal(false);
      const name = $('#mvName', d).value.trim();
      closeModal(true);
      moveItem(path, dest, name && name !== stem ? name : null);
    });
  });
}
function openNewFolder(parent) {
  openModal(`<h3>Nueva carpeta</h3><div class="muted">Dentro de <b>${esc(folderName(parent))}</b></div>
    <label>Nombre</label><input class="input" id="nfName" placeholder="p. ej. Proyecto Alfa" autofocus>
    <div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Crear</button></div>`, d => {
    const ok = async () => {
      const name = $('#nfName', d).value.trim();
      if (!name) return;
      try { const r = await api('/api/mkdir', { parent, name }); closeModal(true); await refreshTree(true); S.exp.add(parent); saveExp(); go('f', r.path); }
      catch (err) { toast(esc(err.message), 'err'); }
    };
    d.addEventListener('keydown', e => { if (e.key === 'Enter') ok(); });
    d.addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) b.dataset.m === 'ok' ? ok() : closeModal(false); });
  });
}
function openRenameFolder(path) {
  const f = S.folders.get(path);
  openModal(`<h3>Renombrar carpeta</h3><label>Nombre</label><input class="input" id="rfName" value="${esc(nfc(f.name))}" autofocus>
    <div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Renombrar</button></div>`, d => {
    const ok = async () => {
      const name = $('#rfName', d).value.trim();
      if (!name || name === nfc(f.name)) return closeModal(false);
      if (S.playing && S.playing.startsWith(path + '/')) { audio.pause(); audio.removeAttribute('src'); audio.load(); S.playing = null; }
      try {
        const r = await api('/api/rename-folder', { path, name });
        closeModal(true);
        [...S.exp].forEach(p => { if (p === path || p.startsWith(path + '/')) { S.exp.delete(p); S.exp.add(r.path + p.slice(path.length)); } });
        saveExp(); await refreshTree(true); go('f', r.path, null, true);
      } catch (err) { toast(esc(err.message), 'err'); }
    };
    d.addEventListener('keydown', e => { if (e.key === 'Enter') ok(); });
    d.addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) b.dataset.m === 'ok' ? ok() : closeModal(false); });
  });
}
$('#btnNewFolder').onclick = () => openNewFolder(S.route.kind === 'f' ? S.route.path : '');

function openSettings() {
  const c = S.config.cloud, P = S.config.providers;
  const ac = S.config.acerca || {};
  const provOpts = Object.entries(P).map(([k, v]) => `<option value="${k}"${k === c.provider ? ' selected' : ''}>${esc(v.label)}</option>`).join('');
  openModal(`<h3>${I.gear}Ajustes</h3>
    <fieldset><legend>${I.nube}Transcripción en la nube</legend>
      <div class="notice">Las grabaciones de reuniones pueden ser información confidencial. Antes de mandar audio a un servicio externo (Groq, OpenAI), confirma que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay. Si tu empresa tiene una pasarela propia (Azure OpenAI u otra compatible con OpenAI), configúrala aquí.</div>
      <label>Proveedor</label><select id="cProv">${provOpts}</select>
      <div id="cFields"></div>
      ${typeof directoSettingsHtml === 'function' ? directoSettingsHtml(c) : ''}
      <div class="grid2">
        <div><label>Minutos por trozo</label><input class="input" id="cChunk" type="number" min="2" max="20" value="${c.chunk_minutes}"></div>
        <div><label>Trozos en paralelo</label><input class="input" id="cPar" type="number" min="1" max="8" value="${c.parallel}"></div>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn sm" id="cTest">Probar conexión</button><span id="cTestRes" class="muted" style="font-size:12.5px"></span></div>
    </fieldset>
    <fieldset><legend><i class="recdot"></i>En directo (mientras graba OBS)</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="lAuto" ${S.config.live_auto ? 'checked' : ''}> Empezar a transcribir en cuanto OBS empiece a grabar</label>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="lFinal" ${S.config.live_final_pass !== false ? 'checked' : ''}> Al terminar un directo en la nube, repasar la grabación entera para mejorar el texto</label>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="lMeet" ${S.config.meeting_detect !== false ? 'checked' : ''}> Avisarme si estoy en una llamada (Teams, Zoom…) y no estoy grabando</label>
      <div class="help">El motor con el que empieza cada directo se elige en «Perfil de reunión» y se puede cambiar en mitad de la grabación desde la propia pantalla. En la nube el texto va unos 5–10 s por detrás; en local, unos 20–40 s. El repaso final tarda 1–2 min por hora de reunión (mientras tanto ves el texto del directo), gasta el doble de cupo de Groq y no se hace si has corregido el texto a mano.</div>
    </fieldset>
    ${window.reunionFieldsetHtml?.() || ''}
    <fieldset><legend>${I.monitor}Transcripción local</legend>
      <label>Hilos de CPU</label><input class="input" id="sThreads" type="number" min="1" max="${S.config.cpu_count}" value="${S.config.threads}">
      <div class="help">Tu procesador tiene ${S.config.cpu_count} hilos. Con más hilos va más rápido, pero el portátil irá más justo mientras transcribe.</div>
    </fieldset>
    <fieldset><legend>${I.spark}Resumen con IA</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="sAuto" ${S.config.summary?.auto !== false ? 'checked' : ''}> Resumir solo al terminar de transcribir (reuniones de los últimos 3 días)</label>
      <label>Modelo</label><input class="input" id="sModel" list="sModels" value="${esc(S.config.summary?.model || '')}">
      <datalist id="sModels">${(S.config.summary?.models || []).map(m => `<option value="${esc(m)}">`).join('')}</datalist>
      <div class="help">El idioma del resumen y las instrucciones extra están en «Perfil de reunión». Usa el mismo proveedor y API key que la transcripción en la nube, y solo se envía el texto (no el audio). Con el plan gratuito de Groq (~8K tokens por minuto) una reunión de 1 h se resume por partes en unos 3 minutos, y caben unas 7 reuniones largas al día.</div>
    </fieldset>
    ${typeof perfilFieldsetHtml === 'function' ? perfilFieldsetHtml() : `<fieldset><legend>${I.globe}Idioma de las reuniones</legend><label>Por defecto</label>${langSelect('sLang', S.config.language ?? 'es-en')}</fieldset>`}
    <fieldset><legend>${I.folder}Carpetas fuera de Audios</legend>
      <div id="sLinks"></div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="lnAdd">${I.plus}Añadir una carpeta…</button></div>
      <div class="help">Por ejemplo la carpeta del proyecto en OneDrive. ${esc(APP_NAME)} la trata como suya: puedes grabar ahí (● Grabar → Carpeta), escuchar y transcribir sus audios, y la transcripción y las notas se guardan junto al audio. Quitarla de ${esc(APP_NAME)} no borra nada.</div>
    </fieldset>
    <fieldset><legend>${I.book}Vocabulario</legend>
      <label>Por tema <span class="muted" style="font-weight:400">· se elige al grabar o en cada reunión</span></label>
      <div id="sVocab" class="help" style="margin-top:0">Cargando…</div>
      <label>General · para reuniones sin tema</label>
      <textarea id="sPrompt" placeholder="Nombres propios, empresas, siglas, productos…">${esc(S.config.prompt)}</textarea>
      <div class="help">Se le pasa a Whisper como contexto para que escriba bien nombres propios y siglas. Opcional: si notas frases raras o repetidas, déjalo vacío.</div>
    </fieldset>
    ${vocesFieldsetHtml()}
    <fieldset><legend>${I.package}Compartir ${esc(APP_NAME)}</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="xVocab"> Incluir mis listas de vocabulario por tema <span class="muted" style="font-weight:400">· suelen llevar nombres de personas y empresas: solo para gente del mismo proyecto</span></label>
      <div class="row" style="margin-top:8px"><button class="btn sm" id="xExport" type="button">Crear instalador para otra persona</button><span id="xRes" class="muted" style="font-size:12.5px"></span></div>
      <div class="help">Crea en el escritorio <b>${esc(APP_NAME)} - instalador.zip</b> con la app completa y un INSTALAR.bat. No lleva tus grabaciones, notas, ajustes ni tu API key: la otra persona conecta su propio proveedor. Compártelo por un enlace de OneDrive o Teams (el correo suele bloquear los .zip con .bat).<br>
        <b>Para actualizar a alguien que ya lo tiene</b>: mándale el zip nuevo y que ejecute su INSTALAR.bat. El instalador cierra la versión abierta y actualiza la que ya tenía, en su carpeta de siempre (con sus grabaciones).</div>
    </fieldset>
    <div class="help" style="margin-top:16px"><b>Atajos:</b> Ctrl+Alt+R empezar/parar grabación (desde cualquier programa) · Espacio reproducir/pausa · ← → 5 s · Shift + ← → 15 s · [ ] velocidad · Ctrl K buscar en todas las transcripciones · Ctrl P ir a una reunión · Ctrl F buscar en la transcripción</div>
    <fieldset style="margin-top:14px"><legend>${I.info}Acerca de</legend>
      <div class="help" style="margin-top:0"><b>${esc(APP_NAME)}</b> · versión ${esc(S.config.version || '')}${ac.licencia ? ` · licencia ${esc(ac.licencia)}` : ''}<br>
        Código, novedades y ayuda: <a href="${esc(ac.repo || '#')}" id="aRepo">${esc((ac.repo || '').replace(/^https?:\/\//, ''))}</a><br>
        Datos y registros (ajustes, API key cifrada, <code>server.log</code>): <code>${esc(ac.datos || '')}</code></div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="aDatos">${I.folder}Abrir</button><button class="btn sm" type="button" id="aDiag">${I.clipboard}Copiar diagnóstico</button><span class="muted" style="font-size:12.5px">versión, sistema, motor y las últimas líneas del registro, sin claves: para pedir ayuda</span></div>
    </fieldset>
    <div class="row" style="margin-top:10px"><button class="btn ghost sm danger" id="sOff" type="button" title="Cierra la app del todo, también el motor que sigue en segundo plano al cerrar la ventana">${I.power}Apagar ${esc(APP_NAME)}</button>
      <span class="muted" style="font-size:12.5px">Cerrar la ventana no apaga la app: sigue en segundo plano para Ctrl+Alt+R.</span></div>
    <div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Guardar</button></div>`, d => {
    $('#sOff', d).onclick = () => { closeModal(false); confirmShutdown(); };
    $('#aDatos', d).onclick = () => api('/api/acerca/abrir', { que: 'datos' }).catch(e => toast(e.message, 'err'));
    $('#aRepo', d).onclick = e => { e.preventDefault(); api('/api/acerca/abrir', { que: 'repo' }).catch(() => window.open(ac.repo, '_blank')); };
    $('#aDiag', d).onclick = async () => {
      try { const r = await api('/api/diagnostico'); await copy(r.texto, 'Diagnóstico copiado: pégalo donde pidas ayuda'); } catch (e) { toast(e.message, 'err'); }
    };
    const fields = () => {
      const p = $('#cProv', d).value, info = P[p];
      if (p === 'azure') {
        $('#cFields', d).innerHTML = `<label>Endpoint</label><input class="input" id="cAzEp" placeholder="https://<recurso>.openai.azure.com" value="${esc(c.azure_endpoint)}">
          <div class="grid2"><div><label>Deployment (whisper)</label><input class="input" id="cAzDep" value="${esc(c.azure_deployment)}"></div>
          <div><label>API version</label><input class="input" id="cAzVer" value="${esc(c.azure_api_version)}"></div></div>
          <label>API key</label><input class="input" id="cKey" type="password" placeholder="${c.has_key ? 'Guardada: ' + esc(c.api_key) + ' (escribe para cambiarla)' : 'Clave del recurso de Azure'}">`;
      } else if (p === 'custom') {
        $('#cFields', d).innerHTML = `<label>URL base de la API</label><input class="input" id="cBase" placeholder="https://api.groq.com/openai/v1" value="${esc(c.base_url || '')}">
          <div class="help">Cualquier servicio con el formato de OpenAI (pasarela o proxy de la empresa, LiteLLM…). Se usan <code>/audio/transcriptions</code> para transcribir y <code>/chat/completions</code> para el resumen.</div>
          <label>API key</label><input class="input" id="cKey" type="password" placeholder="${c.has_key ? 'Guardada: ' + esc(c.api_key) + ' (escribe para cambiarla)' : 'Pega aquí tu API key'}">
          <label>Modelo de transcripción</label><input class="input" id="cModel" list="cModels" value="${esc(c.model || 'whisper-large-v3')}">
          <datalist id="cModels">${['whisper-large-v3', 'whisper-large-v3-turbo', 'whisper-1'].map(m => `<option value="${m}">`).join('')}</datalist>`;
      } else {
        $('#cFields', d).innerHTML = `<label>API key</label><input class="input" id="cKey" type="password" placeholder="${c.has_key ? 'Guardada: ' + esc(c.api_key) + ' (escribe para cambiarla)' : 'Pega aquí tu API key'}">
          <div class="help">Se guarda solo en este PC (%LOCALAPPDATA%\\AudiosApp), fuera de OneDrive. Consíguela en <a href="${info.keys}" target="_blank" style="color:var(--accent)">${esc(info.keys.replace('https://', ''))}</a>.</div>
          <label>Modelo</label><select id="cModel">${info.models.map(m => `<option${m === c.model ? ' selected' : ''}>${m}</option>`).join('')}</select>`;
      }
    };
    fields();
    $('#cProv', d).onchange = fields;
    bindVocesSettings(d);
    if (typeof bindDirectoSettings === 'function') bindDirectoSettings(d);
    window.bindReunionSettings?.(d);
    if (typeof bindPerfilSettings === 'function') bindPerfilSettings(d);
    const collect = () => {
      const p = $('#cProv', d).value;
      const cloud = { provider: p, chunk_minutes: +$('#cChunk', d).value, parallel: +$('#cPar', d).value };
      const key = $('#cKey', d)?.value.trim();
      if (key) cloud.api_key = key;
      if (p === 'azure') Object.assign(cloud, { azure_endpoint: $('#cAzEp', d).value, azure_deployment: $('#cAzDep', d).value, azure_api_version: $('#cAzVer', d).value });
      else cloud.model = $('#cModel', d).value;
      if (p === 'custom') cloud.base_url = $('#cBase', d).value;
      if (typeof collectDirectoSettings === 'function') Object.assign(cloud, collectDirectoSettings(d));
      S.selLang = $('#sLang', d).value;
      return { cloud, threads: +$('#sThreads', d).value, prompt: $('#sPrompt', d).value.trim(), language: $('#sLang', d).value,
        live_auto: $('#lAuto', d).checked, live_preset: $('#lPreset', d)?.value, meeting_detect: $('#lMeet', d).checked,
        live_final_pass: $('#lFinal', d).checked, ...(window.reunionCollect?.(d) || {}),
        ...(typeof collectPerfilSettings === 'function' ? collectPerfilSettings(d) : {}),
        summary: { auto: $('#sAuto', d).checked, model: $('#sModel', d).value, lang: $('#sSumLang', d)?.value, extra: $('#sExtra', d)?.value } };
    };
    $('#cTest', d).onclick = async () => {
      const res = $('#cTestRes', d);
      res.textContent = 'Probando…';
      try {
        setConfig(await api('/api/settings', collect()));
        const r = await api('/api/cloud/test', {});
        res.innerHTML = `<span style="color:var(--ok)">✓ Funciona (respuesta en ${String(r.seconds).replace('.', ',')} s)</span>`;
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ ${esc(err.message)}</span>`; }
    };
    const drawLinks = () => {
      const box = $('#sLinks', d), ls = S.config.links || [];
      box.innerHTML = ls.length ? `<table class="vtable">${ls.map(l => `<tr><td>${I.link}${esc(nfc(l.name))}</td><td><code>${esc(l.path)}</code></td>
          <td><button class="btn ghost sm" type="button" data-unlink="${esc(l.rel)}">quitar</button></td></tr>`).join('')}</table>`
        : '<div class="help" style="margin-top:0">Ninguna todavía: las grabaciones se guardan dentro de la carpeta Audios.</div>';
    };
    drawLinks();
    $('#lnAdd', d).onclick = async () => { if (await linkFolder()) drawLinks(); };
    // la confirmacion usa el mismo hueco que Ajustes: al responder, se vuelve a Ajustes
    d.addEventListener('click', async e => { const u = e.target.closest('[data-unlink]'); if (u) { await unlinkFolder(u.dataset.unlink); openSettings(); } });
    loadVocab(S.config.language ?? 'es-en').then(() => {
      const box = $('#sVocab', d);
      if (!box) return;
      // agrupado por proyecto: cada uno con sus temas y un boton para anadir otro
      const groups = {};
      S.vocab.forEach(v => (groups[v.project] ||= []).push(v));
      box.innerHTML = `${Object.entries(groups).map(([p, vs]) => `<div class="vproj"><b>${I.folder}${esc(p)}</b>
          <button class="btn ghost sm" type="button" data-vnew="${esc(p)}">${I.plus}Tema</button>
          <button class="btn ghost sm" type="button" data-vdir="${esc(vs[0].id.split('/').slice(0, -1).join('/'))}" title="Abrir su carpeta Vocabulario">${I.ext}Carpeta</button></div>
        <table class="vtable">${vs.map(v => `<tr><td>${esc(v.name)}</td><td>${vocabFit(v)}</td>
          <td><button class="btn ghost sm" type="button" data-vedit="${esc(v.id)}">editar</button></td></tr>`).join('')}</table>`).join('')}
        ${typeof perfilVocabEmptyHtml === 'function' && !S.vocab.length ? perfilVocabEmptyHtml() : ''}
        <div class="row" style="margin-top:10px"><button class="btn sm" type="button" data-vnew="">${I.plus}Nuevo proyecto</button>${typeof perfilImportar === 'function' ? `<button class="btn sm" type="button" id="sVocabImport">${I.folder}Importar listas de otra carpeta…</button>` : ''}</div>
        <div class="help">Cada tema es una lista de nombres y siglas, separados por comas y <b>los más importantes primero</b>: Whisper lee como mucho ${S.vocab[0]?.limit || 223} tokens (unos 45 términos) y lo que no cabe se queda fuera por el final.</div>`;
    });
    d.addEventListener('click', e => {
      const ed = e.target.closest('[data-vedit]'), nw = e.target.closest('[data-vnew]'), r = e.target.closest('[data-vdir]');
      if (e.target.closest('#sVocabImport') && typeof perfilImportar === 'function') perfilImportar('', openSettings);
      if (ed) openVocabEditor(S.vocab.find(v => v.id === ed.dataset.vedit));
      if (nw) openVocabEditor(null, openSettings, nw.dataset.vnew);
      if (r) api('/api/reveal', { path: r.dataset.vdir }).catch(err => toast(esc(err.message), 'err'));
    });
    $('#xExport', d).onclick = async () => {
      const res = $('#xRes', d);
      res.textContent = 'Creando…';
      try {
        const r = await api('/api/export', { vocab: $('#xVocab', d).checked });
        res.innerHTML = `<span style="color:var(--ok)">✓ Creado en el escritorio (${(r.size / 1e6).toFixed(1).replace('.', ',')} MB${r.vocab ? ` · con ${r.vocab} listas de vocabulario` : ''})</span>`;
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ ${esc(err.message)}</span>`; }
    };
    d.addEventListener('click', async e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m === 'no') return closeModal(false);
      try { setConfig(await api('/api/settings', collect())); closeModal(true); toast('Ajustes guardados'); refreshView(); }
      catch (err) { toast(esc(err.message), 'err'); }
    });
  });
}
$('#btnSettings').onclick = openSettings;

/* ---------------------------------------------------------------- apagar */
async function confirmShutdown() {
  const busy = S.jobs.some(isActive);
  const ok = await ask(`Apagar ${APP_NAME}`, `<p>Se cierra la app del todo: la ventana y el motor que corre en segundo plano. El atajo Ctrl+Alt+R dejará de funcionar hasta que la vuelvas a abrir con el acceso directo.</p>${busy ? '<p><b>Hay transcripciones en marcha</b>: se cancelan (lo ya escrito se conserva) y podrás relanzarlas al volver.</p>' : ''}`, 'Apagar');
  if (!ok) return openSettings();  // la confirmacion ocupa el hueco de Ajustes: al cancelar, se vuelve
  try { await api('/api/apagar', {}); shutdownScreen(); }
  catch (err) { toast(esc(err.message), 'err'); }
}
/* La ventana propia (window.py) llama a esto al pulsar la X: la app sigue en segundo plano. Devuelve 'handled' si la
   pagina se encarga de pedir /api/ocultar (tras el aviso, la primera vez); si no, window.py oculta por su cuenta. */
function onWindowClosing() {
  if (S.off) return 'no';
  const hide = () => api('/api/ocultar', {}).catch(() => { });
  if (localStorage.getItem('bgNotice') === 'off') { hide(); return 'handled'; }
  openModal(`<h3>${esc(APP_NAME)} sigue en segundo plano</h3>
    <p>Al cerrar la ventana la app <b>no se apaga</b>: las grabaciones y el atajo <b>Ctrl+Alt+R</b> siguen funcionando, y el acceso directo del escritorio vuelve a abrir esta ventana.</p>
    <p>Para apagarla del todo: Ajustes ${I.gear} → <b>${I.power}Apagar ${esc(APP_NAME)}</b>.</p>
    <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="bgNoMore"> No volver a avisar</label>
    <div class="btns"><button class="btn" data-m="off" type="button">Apagar ahora</button><button class="btn primary" data-m="ok" type="button" autofocus>Entendido</button></div>`, d => {
    d.addEventListener('click', e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if ($('#bgNoMore', d).checked) localStorage.setItem('bgNotice', 'off');
      closeModal(b.dataset.m);
    });
  }).then(v => { if (v === 'off') confirmShutdown(); else hide(); });  // 'ok', o cerrado haciendo clic fuera: se oculta
  return 'handled';
}
function shutdownScreen() {
  S.off = true;  // los sondeos paran: el servidor ya no contesta
  clearTimeout(recTimer); clearTimeout(jobsTimer);
  document.title = `${APP_NAME} · apagada`;
  document.body.innerHTML = `<div class="page" style="max-width:520px;margin:18vh auto;text-align:center"><img src="/notas.png" alt="" style="width:72px;height:72px">
    <h2 style="margin:16px 0 8px">${esc(APP_NAME)} se ha apagado</h2><p class="muted">Cierra esta ventana. Para volver a usar ${esc(APP_NAME)}, ábrela con el acceso directo del escritorio.</p></div>`;
}

function toast(html, kind = '', onclick) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = html;
  el.onclick = () => { onclick && onclick(); el.remove(); };
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 9000 : 4000);
}

/* ---------------------------------------------------------------- arranque */
function setConfig(c) {
  S.config = c;
  if (c.app_name) APP_NAME = c.app_name;
  S.presetsById = Object.fromEntries(c.presets.map(p => [p.id, p]));
  if (!S.selPreset || !S.presetsById[S.selPreset]) S.selPreset = c.preset in S.presetsById ? c.preset : 'rapido';
  if (S.selLang == null) S.selLang = c.language ?? 'es-en';
}
async function refreshTree(force) {
  if (S.dragging) return;
  try {
    const r = await api('/api/tree' + (force ? '' : '?v=' + S.version));
    if (r.same) return;
    S.version = r.version;
    indexTree(r.tree);
    renderTree();
    renderLiveChip();
    if (S.route) refreshView();
  } catch { /* servidor reiniciandose */ }
}
async function init() {
  try {
    setConfig(await api('/api/config'));
    const r = await api('/api/tree');
    S.version = r.version;
    indexTree(r.tree);
    await loadVocab();  // temas (<proyecto>/Vocabulario/*.txt…) para los selectores
  } catch (err) {
    main.innerHTML = `<div class="page"><div class="card empty">No se puede conectar con el servidor de la app.<br>Ábrela con el acceso directo <b>${esc(APP_NAME)}</b> del escritorio.<br><br><span class="muted">${esc(err.message)}</span></div></div>`;
    return;
  }
  route();
  pollJobs();
  pollRec();
  setInterval(() => { if (!document.hidden && !S.off) refreshTree(); }, 6000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshTree(); });
}
addEventListener('DOMContentLoaded', init);  // tras cargar tambien notas.js
