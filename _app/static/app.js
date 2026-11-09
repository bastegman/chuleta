'use strict';
/* Tenca: interfaz (vanilla JS, sin dependencias). Habla con server.py en /api/* */

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
  upload: '<svg viewBox="0 0 24 24"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5"/><path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg>',
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
  cafe: '<svg viewBox="0 0 24 24"><path d="M4 10h12v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M16 11.5h1.5a2.5 2.5 0 0 1 0 5H16"/><path d="M8 4.5c0 1.2 1 1.6 1 3M12 4.5c0 1.2 1 1.6 1 3"/></svg>',
  tenca: '<svg viewBox="0 0 64 64" class="i-tenca"><path fill-rule="evenodd" d="M33 21a10 10 0 1 0 20 0a10 10 0 1 0-20 0ZM43.6 18.8a2 2 0 1 0 4 0a2 2 0 1 0-4 0Z"/><path d="M51.6 16.6 62.5 21 51.6 25.4Z"/><rect x="6" y="32" width="40" height="7" rx="3.5"/><rect x="6" y="43" width="29" height="7" rx="3.5"/><rect x="6" y="54" width="18" height="7" rx="3.5"/></svg>',
};
/* iconos de los presets de transcripcion por id; el emoji que manda engines.py queda solo de reserva */
const PRESET_ICONS = { rapido: 'bolt', turbo: 'scale', medium: 'gauge', calidad: 'target', nube: 'nube' };
const presetIcon = p => !p ? '' : PRESET_ICONS[p.id] ? I[PRESET_ICONS[p.id]] : (p.icon ? esc(p.icon) + ' ' : '');

const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const DIA = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
const LANGS = [['es-en', 'Español + inglés (spanglish)'], ['', 'Detectar el idioma (cualquiera)'], ['es', 'Solo español'], ['en', 'Solo inglés'],
  ['pt', 'Portugués'], ['fr', 'Francés'], ['it', 'Italiano'], ['de', 'Alemán'], ['ca', 'Catalán'],
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
const provName = () => S.config?.procesado === 'agente' ? `${S.config.agente?.label || 'Claude Code'} (tu cuenta)`
  : provElegido() ? (S.config?.providers?.[S.config.cloud?.provider]?.label || 'la nube') : 'el proveedor que elijas';
/* hay un proveedor de la nube elegido (server.proveedor_elegido): en una instalación nueva no, y ninguno se da por
   elegido ni se nombra (Bernardo, msg 1386: «Lo que no quiero es sugerir Groq a un nuevo instalador») */
const provElegido = () => S.config?.cloud?.elegido !== false;
/* los proveedores para elegir: por su nombre, y la API de tu empresa al final (sin uno delante de los demás) */
const provOrden = P => Object.entries(P || {}).sort(([a, x], [b, y]) => (a === 'custom') - (b === 'custom') || x.label.localeCompare(y.label, 'es'));
const estDuration = a => a.duration || (a.ext === '.wav' ? a.size / 192000 : a.size / 16000);
/* nombres de carpeta: las vinculadas (fuera de Audios) van por dentro como ":<id>/…" y se ven con su nombre */
const nodeName = p => nfc(S.folders.get(p)?.name ?? p.split('/').pop());
/* la carpeta de la app como se llama en el disco (server.root_name): «Chuleta» en una instalación encima de Chuleta,
   que no renombra la carpeta; las rutas y la raíz se enseñan con ese nombre, el de la app queda para la app */
const rootName = () => nfc(S.config?.root_name || APP_NAME);
const folderName = p => p ? nodeName(p) : `${rootName()} (raíz)`;
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
/* la carpeta de una reunión (una carpeta por reunión: server.scan la marca con por_reunion) se ve como la reunión */
const subFolders = f => f.folders.filter(x => !x.por_reunion);
function ownAudios(f) {
  return [...f.audios, ...f.folders.filter(x => x.por_reunion).flatMap(x => x.audios)]
    .sort((a, b) => (a.date || '').localeCompare(b.date || '') || a.name.localeCompare(b.name));
}
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
  if (!j) {
    if (!a.recording) return '';
    // el último directo de esta grabación falló: se dice por qué (antes solo quedaba un aviso que se iba)
    const l = lastJobFor(a.path);
    if (l && l.live && l.status === 'error' && l.error) return `<span class="motor-err"><b>No se ha podido transcribir en directo.</b> ${esc(l.aviso?.frase || l.error)}</span> La grabación sigue; cuando lo arregles, pulsa «Transcribir en directo».`;
    return 'La transcripción en directo está parada. Elige modelo y pulsa «Transcribir en directo».';
  }
  if (typeof prepEstado === 'function' && prepEstado(j)) return '';  // lo dice «Preparando la transcripción», con su %
  if (/Cargando|Descargando|Reinici|Rehaciendo|Empezando/.test(j.stage)) return esc(j.stage);
  const lag = Math.max(0, j.recorded - j.transcribed);
  const p = S.presetsById[j.preset] || {};
  if (lag <= 75) return `<b>Al día</b> · el texto va unos ${Math.max(5, Math.round(lag / 5) * 5)} s por detrás de lo que se dice`;
  const slow = (p.live_speed || 1) < 1.05 && j.recording;
  return `<b>Poniéndose al día</b> · ${fmtLeft(lag)} por detrás${slow ? ' · este modelo no da abasto en directo: terminará después de la grabación' : ''}`;
}
/* lo que obliga a repintar la cabecera del directo entera (el resto se refresca en su sitio) */
function liveHeroSig(a, j, sel, mine, muted) {
  const p = S.presetsById[sel] || {}, falta = !j && p.cached === false ? (S.modelos?.[p.model]?.downloading ? 'd' : 'f') : '';
  return (j ? j.id + j.preset : 'none') + sel + mine + muted + (S.config.procesado || '') + (S.lhModelos ? 'm' : '')
    + (typeof prepEstado === 'function' && prepEstado(j) ? 'p' : '') + falta + (typeof destinoSig === 'function' ? destinoSig(a) : '');
}
function renderLiveHero(a) {
  const el = $('#lHero');
  if (!el) return;
  const j = liveJob(a);
  const sel = j ? j.preset : (S.livePreset || S.config.live_preset || 'medium');
  const mine = !!(S.rec.recording && S.rec.path === a.path);  // la esta grabando esta app (no OBS)
  syncClock(a);
  const muted = !!(mine && S.rec.mic_muted);
  el.dataset.sig = liveHeroSig(a, j, sel, mine, muted);  // el modo y «Cambiar»: la línea del modo dice dónde
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
    ${typeof destinoHtml === 'function' ? destinoHtml(a) : crumbs(a.folder)}
    <h1>${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(pageTitle(a))}${!titleOf(a) && a.label ? `<span class="sub"> · ${esc(nfc(a.label))}</span>` : ''}</h1>
    <div class="lh-status" data-lh-status>${liveStatus(a, j)}</div>
    <div id="directoBar"></div>
    ${S.vocab.length ? `<div class="lh-ws">${I.topic}Proyecto ${wsSelect(a)}</div>` : ''}
    ${typeof perfilLiveHtml === 'function' ? perfilLiveHtml(a) : ''}
    ${j ? `<div class="lh-bar" title="Transcrito / grabado"><i data-lh-bar style="width:${(100 * Math.min(1, j.transcribed / Math.max(1, j.recorded))).toFixed(1)}%"></i></div>` : ''}
    ${typeof modoLineaHtml === 'function' ? modoLineaHtml(sel, { cambiar: 'lh-modelos', abierto: S.lhModelos, falta: !j }) + prepHtml(j) : ''}
    <div class="lh-modelos${S.lhModelos || typeof modoLineaHtml !== 'function' ? '' : ' hidden'}">
    <div class="lh-label">Modelo del directo${typeof motorHtml === 'function' ? motorHtml(sel) : ''} ${j ? '<span class="muted">(el cambio se aplica a lo que se diga a partir de ahora)</span>' : ''}</div>
    ${liveModels(sel, j)}
    ${S.presetsById[sel]?.live_note ? `<div class="lh-note">${esc(S.presetsById[sel].live_note)}</div>` : ''}
    ${j && typeof modoLineaHtml === 'function' ? `<div class="lh-actions"><button class="btn sm" data-act="live-restart" data-id="${j.id}">${I.refresh}Rehacer desde el principio con este modelo</button></div>` : ''}
    </div>
    <div class="lh-actions">
      ${j && typeof modoLineaHtml !== 'function' ? `<button class="btn sm" data-act="live-restart" data-id="${j.id}">${I.refresh}Rehacer desde el principio con este modelo</button>` : ''}
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
  if (el.dataset.sig !== liveHeroSig(a, j, sel, mine, !!(mine && S.rec.mic_muted))) return renderLiveHero(a);
  syncClock(a);
  const st = $('[data-lh-status]', el);
  if (st) st.innerHTML = liveStatus(a, j);
  const pr = $('[data-prep]', el);
  if (pr && typeof prepHtml === 'function') pr.outerHTML = prepHtml(j);
  const hueco = $('[data-prep-hueco]');
  if (hueco && typeof prepHueco === 'function') hueco.textContent = prepHueco(j) || hueco.dataset.base || '';
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
  const lj = liveJob(a);
  chip.title = lives.length > 1 ? `${lives.length} grabaciones en curso` : 'Grabación en curso: ' + fullTitle(a)
    + (typeof modoLinea === 'function' ? `. ${modoLinea(lj ? lj.preset : (S.livePreset || S.config.live_preset || 'medium'))}.` : '');
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
    if (S.rec.build !== S.build && $('#modal').classList.contains('hidden') && audio.paused && await txSavedForReload()) return location.reload();
  }
  if (S.config && S.config.meeting_detect === false) S.rec.meeting = null;  // «Avisarme si estoy en una llamada» apagado: sin chip
  if (was !== !!S.rec.recording) onRecChange();
  renderRecBtn(); updateMeters(); renderMeetChip();
  recTimer = setTimeout(pollRec, S.rec.recording || recDialog ? 300 : 1500);
}
/* empezo o paro la grabacion (tambien con Ctrl+Alt+R desde otro programa) */
function onRecChange() {
  refreshTree(true); pollJobs(true);
  if (S.recLocal) { S.recLocal = false; return; }  // lo ha pedido esta misma pagina: ya avisa ella
  if (S.rec.recording) toast(`● Grabando${S.rec.resumen ? ` · ${esc(S.rec.resumen)}` : ''} · <b>Ctrl+Alt+R</b> para parar · clic para verla y cambiar el idioma o el contexto`, '', () => go('a', S.rec.path));
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
  + `<option value="__pick">Otra carpeta (fuera de ${esc(APP_NAME)})…</option>`;
/* «● Grabar»: la tarjeta «Así se va a grabar» (grabar.js, rediseño B1 del 07/11/2026). Lo que enseña y lo que se graba
   sale de /api/grabar/plan, el mismo cálculo que Ctrl+Alt+R y el aviso de Teams */
function openRec() {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  if (typeof abrirGrabar === 'function') return abrirGrabar();
  toast('No se ha podido cargar la tarjeta de grabar: recarga la página (Ctrl+R) o usa Ctrl+Alt+R.', 'err');
}
$('#btnRec').onclick = openRec;
/* «Subir audio» (09/11/2026, msg 1296): junto a Grabar, donde se busca cómo meter una reunión en la app. Abre el selector
   de archivos y, con lo elegido, la tarjeta de subir (grabar.js); desde una carpeta, sube ahí */
$('#btnSubir').onclick = () => {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  if (typeof elegirAudios === 'function') return elegirAudios({ carpeta: S.route.kind === 'f' ? S.route.path : null });
  toast('No se ha podido cargar «Subir audio»: recarga la página (Ctrl+R).', 'err');
};

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
  if (!S.config) return;  // arrancando (init espera a /api/config): se pinta la ruta cuando llegue, no un error
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
  const loose = ownAudios(root).filter(a => matchA(a, q) && !isLive(a));
  if (loose.length || !q) {
    html.push(`<div class="tsec" data-drop="">Sin clasificar ${loose.length ? `· ${loose.length}` : ''}</div>`);
    html.push(loose.length ? loose.map(a => audioRow(a, 0)).join('') : '<div class="trow muted" data-drop="" style="padding-left:16px;cursor:default">Nada suelto</div>');
  }
  const folders = subFolders(root).map(f => folderNode(f, 0, q)).join('');
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
    ...subFolders(f).map(c => folderNode(c, depth + 1, cq)),
    ...ownAudios(f).filter(a => matchA(a, cq)).map(a => audioRow(a, depth + 1)),
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
  const home = item?.parent?.por_reunion ? item.parent.parent?.path : item?.parent?.path;  // su carpeta: ya es ella
  if (!name && item && item.parent && (item.parent.path === folder || home === folder)) return;
  const wasPlaying = S.playing === path, t = audio.currentTime, playingNow = !audio.paused;
  if (wasPlaying) { audio.pause(); audio.removeAttribute('src'); audio.load(); }  // suelta el archivo para poder moverlo
  try {
    const r = await api('/api/move', { path, folder, name: name || undefined });
    if (r.pendiente) {  // se está grabando o procesando: se mueve sola al terminar (directo.js)
      if (wasPlaying) { const a = S.audios.get(path); S.playing = null; if (a) loadAudio(a, t, playingNow); }
      toast(`Se moverá a <b>${esc(r.pendiente.display)}</b> cuando termine todo`);
      return pollJobs(true);
    }
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
  if (S.folders.get(path)?.por_reunion) parts.pop();  // la carpeta de la reunión: ya es la reunión
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
const TX_EACH = '__cada';  // idioma «el de cada reunión» (en lote): el servidor recibe null y usa el de cada .md
/* resume: las que se quedaron a medias se continuan desde donde acaba el texto (se estima solo lo que falta) */
const remaining = (a, resume) => resume && a.partial ? Math.max(1, estDuration(a) - a.tx_end) : estDuration(a);
function txBox(list, title, opts = {}) {
  const id = 'b' + (++boxSeq), resume = !!opts.resume, key = list.map(a => a.path).join('\n');
  S.boxes[id] = { paths: list.map(a => a.path), resume, key };
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
  // idioma: lo que hayas elegido en esta caja; si no, en lote «el de cada reunión» y, con una, el suyo (o el del perfil)
  const each = list.length > 1, langs = each ? [[TX_EACH, 'El de cada reunión (o el del perfil)']].concat(LANGS) : LANGS;
  const lang = S.txLang?.[key] ?? (each ? TX_EACH : list.length === 1 && typeof perfilLangOf === 'function' ? perfilLangOf(list[0]) : (S.config.language ?? ''));
  const hint = S.presetsById[sel]?.hint || '';
  return `<div class="card txbox" data-box="${id}">
    <div class="head"><b>${esc(title)}</b><span class="muted">${list.length > 1 ? `${list.length} audios · ` : ''}${fmtDur(total)} de audio${resume && list.some(a => a.partial) ? ' por transcribir' : ''}</span></div>
    <div class="presets">${cards}</div>
    <div class="preset-hint" data-hint="${id}">${esc(hint)}</div>
    <div class="foot">
      <select data-lang="${id}" title="${each ? 'Idioma de cada reunión' : 'Idioma de la reunión'}">${langs.map(([v, l]) => `<option value="${v}"${v === lang ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <button class="btn primary" data-act="tx-go" data-box="${id}">${I.spark}${opts.button || (list.length > 1 ? `Transcribir ${list.length} audios` : 'Transcribir')}</button>
      <span class="muted" style="font-size:12.5px">Se guarda como .txt y .srt junto al audio. Puedes seguir usando la app mientras tanto.</span>
    </div></div>`;
}

async function submitTx(paths, preset, language, resume = false) {
  const p = S.presetsById[preset];
  if (p.engine === 'cloud' && !S.config.cloud.has_key) return openSettings({ sec: 'procesado' });
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
  // un solo nombre por modelo (configurar.js: Rápido, Normal, Preciso, Máxima); la nube, con el suyo
  const lab = p.engine === 'local' && p.model && typeof modeloNombre === 'function' ? modeloNombre(p.model) : (j.live ? (p.live_label || p.label) : p.label);
  const parts = [`${presetIcon(p)}${esc(lab)}`];
  if (j.live) {
    parts.push(`transcrito hasta ${fmtClock(j.transcribed)} de ${fmtClock(j.recorded)}${j.recording ? ' grabados' : ''}`);
    const lag = j.recorded - j.transcribed;
    const prep = typeof prepCola === 'function' ? prepCola(j) : '';  // descargando o cargando el modelo: aún no va «al día»
    if (j.status === 'running' && j.recording) parts.push(prep || (lag > 90 ? `poniéndose al día (${fmtLeft(lag)} por detrás)` : 'al día'));
    else if (prep) parts.push(prep);
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
  if (j.live && j.status === 'running') return `● En directo · ${typeof prepEstado === 'function' && prepEstado(j) ? 'preparando la transcripción' : j.stage}`;
  if (j.status === 'error') return 'No se pudo transcribir';
  if (typeof prepCola === 'function' && prepCola(j)) return prepCola(j);  // con el nombre del modelo, como en todas partes
  return j.stage;
}

/* el hueco del texto del directo: escuchando o, mientras se descarga o carga el modelo, lo que falta (configurar.js) */
function txEscuchando(lj) {
  const base = `Escuchando… el texto aparecerá aquí en cuanto se hable (${lj.engine === 'cloud' || S.presetsById[lj.preset]?.engine === 'cloud' ? 'unos 5–10 s' : 'unos 20–40 s'} de retraso).`;
  const prep = typeof prepHueco === 'function' ? prepHueco(lj) : '';
  return `<div data-prep-hueco data-base="${esc(base)}">${esc(prep || base)}</div>`;
}

/* ---------------------------------------------------------------- vistas */
function viewMissing() {
  main.innerHTML = `<div class="page"><div class="card empty">Esto ya no está aquí (¿se ha movido o renombrado?).<br><br><a class="btn" href="#/">Volver al inicio</a></div></div>`;
}

/* Tras actualizar: el resumen automático ya no manda a la nube lo transcrito en local (server.py,
   App._aviso_resumen_local). Se avisa una vez, arriba en Inicio, hasta que se cierra o se va al ajuste. */
function avisoResumenLocalHtml() {
  if (!S.config.summary?.aviso_local || S.config.procesado !== 'api') return '';  // habla de la API: con el agente no aplica
  return `<div class="notice info" id="avisoSumLocal" style="margin:0 0 14px">${I.info}El resumen automático ya no envía a <b>${esc(provName())}</b> las reuniones transcritas en local. <a href="#" data-act="aviso-sum-ajuste">Actívalo en Ajustes</a> si quieres. <button class="btn sm ghost" type="button" data-act="aviso-sum-ok">Entendido</button></div>`;
}
function avisoResumenLocalVisto(irAlAjuste) {
  S.config.summary = { ...(S.config.summary || {}), aviso_local: false };
  $('#avisoSumLocal')?.remove();
  api('/api/settings', { summary: { aviso_visto: true } }).catch(err => toast(esc(err.message), 'err'));
  if (!irAlAjuste) return;
  openSettings({ sec: 'procesado' });
  const c = $('#sAutoLocal');
  if (c) { c.scrollIntoView({ block: 'center' }); c.focus(); }
}

function viewHome() {
  const t = S.tree, all = allAudios();
  const pending = all.filter(a => (!(a.txt || a.srt) || a.partial) && !jobFor(a.path) && !a.recording);
  const done = all.filter(a => a.txt || a.srt).length;
  const secs = sum(all, estDuration);
  const recent = [...all].sort((a, b) => (b.date || '').localeCompare(a.date || '') || b.mtime - a.mtime).slice(0, 8);
  const pct = all.length ? Math.round(100 * done / all.length) : 0;
  const lives = liveAudios(), loose = ownAudios(t).filter(a => !isLive(a));
  main.innerHTML = `<div class="page">
    ${lives.map(liveBanner).join('')}
    ${typeof avisoProbarHtml === 'function' ? avisoProbarHtml() : ''}
    ${avisoResumenLocalHtml()}
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
    <h2>Carpetas <span class="count">${subFolders(t).length}</span>
      <button class="btn sm" data-act="new-folder" data-path="" style="margin-left:auto">${I.plus}Nueva carpeta</button></h2>
    <div class="fgrid">${subFolders(t).map(fcard).join('')}</div>
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
      <button class="btn" data-act="subir" data-path="${esc(f.path)}" title="Un audio que ya tengas (del móvil, de WhatsApp…): se guarda en esta carpeta y se transcribe">${I.upload}Subir audio aquí</button>
      <button class="btn" data-act="new-folder" data-path="${esc(f.path)}">${I.plus}Subcarpeta</button>
      <button class="btn" data-act="rename-folder" data-path="${esc(f.path)}">${I.edit}Renombrar</button>
      <button class="btn" data-act="reveal" data-path="${esc(f.path)}">${I.ext}Abrir en el Explorador</button>
      ${f.linked ? `<button class="btn" data-act="link-remove" data-path="${esc(f.path)}" title="Deja de verse en ${esc(APP_NAME)}; no se borra nada">Quitar de ${esc(APP_NAME)}</button>` : ''}
    </div>
    ${f.linked ? `<div class="help">${I.link}Carpeta vinculada: <code>${esc(f.linked)}</code>${f.missing ? ' · <span class="warn-txt">ahora no se encuentra (¿disco o OneDrive desconectado?)</span>' : ''}. «Renombrar» solo cambia cómo se llama en ${esc(APP_NAME)}.</div>` : ''}
    ${pending.length ? `<h2>Pendientes <span class="count">${pending.length}</span></h2>${txBox(pending, pending.length === 1 ? 'Transcribir el pendiente' : `Transcribir los ${pending.length} pendientes`, { resume: true })}` : ''}
    ${subFolders(f).length ? `<h2>Carpetas <span class="count">${subFolders(f).length}</span></h2><div class="fgrid">${subFolders(f).map(fcard).join('')}</div>` : ''}
    ${ownAudios(f).length || !subFolders(f).length ? `<h2>Grabaciones <span class="count">${ownAudios(f).length}</span></h2>${alist(ownAudios(f))}` : ''}
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
  el.innerHTML = `${crumbs(a.folder)}${typeof destinoPendienteHtml === 'function' ? destinoPendienteHtml(a) : ''}
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
    el.innerHTML = `<div class="card jobline error"><div><div class="st">No se pudo transcribir</div><div class="sub">${esc(j.aviso?.frase || j.error)}</div></div>${typeof cfgAccionHtml === 'function' ? cfgAccionHtml(j) : ''}<button class="btn sm" data-act="tx-toggle">Reintentar</button></div>`;
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
  // el directo ha rehecho texto ya escrito (cambio de idioma, «rehacer desde el principio»): no se añade, se cambia
  if (t.text_gen != null && S.tx.gen != null && t.text_gen !== S.tx.gen) return liveRehecho(a);
  const segs = S.tx.segs, last = segs.length ? (segs[segs.length - 1].start ?? -1) : -1;
  const add = t.segments.filter(s => (s.start ?? -1) > last + 0.005);
  add.forEach(s => { s.orig = s.text; });
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

/* Corrigiendo en directo y el directo rehace lo ya escrito (cambio de idioma: el último minuto, en el idioma nuevo).
   Lo que hay en el editor es el texto de antes: se guarda lo corregido (el servidor no toca lo rehecho y dice desde
   dónde: rehecho_desde, ver saveEdits) y el editor pasa al texto nuevo sin salir de «Editar». Antes, lo rehecho no
   llegaba al editor y al guardar se daba por borrado (revisión de idioma-directo, 09/11/2026). */
async function liveRehecho(a) {
  if (S.liveRehaciendo) return;
  S.liveRehaciendo = true;
  try {
    // si lo corregido no se ha podido guardar, el editor se queda como está (y se reintenta en el siguiente sondeo):
    // cambiarlo ahora perdería esa corrección
    if (!(await saveEdits(true))) return;
    if (!S.editing || !S.tx || S.tx.path !== a.path) return;
    let t;
    try { t = await api('/api/transcript?path=' + enc(a.path)); } catch { return; }
    if (!S.editing || !S.tx || S.tx.path !== a.path) return;
    const body = $('#txBody');
    if (body) body.innerHTML = '';  // que renderTxSection (syncEdits) no copie el texto de antes en las frases nuevas
    t.segments.forEach(s => { s.orig = s.text; });
    Object.assign(S.tx, { kind: t.kind, segs: t.segments, starts: t.segments.map(s => s.start ?? 0), gen: t.text_gen ?? null });
    S.dirty = false;
    renderTxSection();
  } finally { S.liveRehaciendo = false; }
}

async function loadTranscript(a) {
  if (S.editing && S.tx && S.tx.path === a.path) return liveJob(a) ? appendLive(a) : undefined;  // no pisar lo que se esta corrigiendo
  const want = a.path;
  try { await txInflight.get(want); } catch { /* lo avisa saveTxNow */ }  // primero, la correccion que aun se guarda
  let t;
  try { t = await api('/api/transcript?path=' + enc(a.path)); } catch (err) { t = { kind: null, segments: [], error: err.message }; }
  if (S.route.kind !== 'a' || S.route.path !== want) return;
  const keepScroll = S.tx && S.tx.path === want;
  const st = main.scrollTop;
  const atBottom = main.scrollHeight - main.scrollTop - main.clientHeight < 160;
  const typing = document.activeElement && document.activeElement.id === 'tq';
  t.segments.forEach(s => { s.orig = s.text; });  // para saber, si el directo rehace algo, qué habías corregido
  S.tx = { path: want, kind: t.kind, file: t.file, segs: t.segments, mtime: a.tx_mtime, starts: t.segments.map(s => s.start ?? 0), has_backup: !!t.has_backup, gen: t.text_gen ?? null };
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
      ? `<div class="card empty tx-empty">${lj ? `<div class="typing"><i></i><i></i><i></i></div>${txEscuchando(lj)}`
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
  const p = { path: S.tx.path, segments: S.tx.segs.filter(s => s.text.trim()).map(s => ({ start: s.start, end: s.end, text: s.text })) };
  if (S.tx.gen != null) p.gen = S.tx.gen;  // en directo: sobre qué versión del texto se ha corregido
  return p;
}
/* Correcciones que aun no han llegado al servidor: la ultima version de cada reunion, hasta que el servidor la
   confirma. Cada version lleva su numero (sin salir en el JSON) y la reunion de la que sale: una version vieja no pisa
   a una nueva, y los guardados de una misma reunion van en orden. Con alguna pendiente la pagina no se recarga sola y,
   al cerrarla, se pide confirmacion si no cabe en el ultimo envio. */
const txPending = new Map(), txInflight = new Map(), txLatest = new Map();
let txSeq = 0;
const TX_RETRY_MS = 1000, TX_KEEPALIVE_MAX = 60 * 1024;  // Chromium (WebView2) rechaza keepalive de mas de 64 KiB
function txVersion() {
  const p = editPayload();
  Object.defineProperties(p, { seq: { value: ++txSeq }, tx: { value: S.tx } });
  txLatest.set(p.path, p.seq);
  return p;
}
const txStale = p => txLatest.get(p.path) !== p.seq;
function sendTx(payload) {
  const path = payload.path, prev = txInflight.get(path);
  txPending.set(path, payload);
  const p = (prev ? prev.catch(() => { }) : Promise.resolve())
    .then(() => api('/api/transcript/save', payload))
    .then(r => {
      if (txPending.get(path) === payload) txPending.delete(path);
      const a = S.audios.get(path);
      if (a) a.tx_mtime = r.tx_mtime;
      if (S.tx && S.tx === payload.tx) S.tx.mtime = r.tx_mtime;  // si la pagina muestra otra copia, que la recargue
      return r;
    })
    .finally(() => { if (txInflight.get(path) === p) txInflight.delete(path); });
  txInflight.set(path, p);
  return p;
}
/* guardado al cambiar de reunion: un fetch normal (sin tope de tamano), dos reintentos y, si sigue fallando, aviso */
async function saveTxNow(payload, tries = 3, quiet = false) {
  for (let i = 1; ; i++) {
    if (txStale(payload)) return true;  // ya hay una version mas nueva, que se guarda por su cuenta
    try { await sendTx(payload); return true; } catch (err) {
      if (txStale(payload)) return true;
      if (i >= tries) {
        const a = S.audios.get(payload.path);
        if (!quiet) toast(`No se han podido guardar tus correcciones${a ? ` de «${esc(fullTitle(a))}»` : ''}: ${esc(err.message)} · <b>clic para reintentar</b>`, 'err', () => saveTxNow(payload));
        return false;
      }
      await new Promise(res => setTimeout(res, TX_RETRY_MS * i));
    }
  }
}
/* quiet: la recarga automatica, que lo reintenta en cada sondeo, no repite el aviso de un fallo que ya ha salido */
async function saveEdits(quiet = false) {
  clearTimeout(S.saveTimer);
  if (!S.tx) return true;
  syncEdits();
  if (!S.dirty) {  // nada nuevo: si aun va en camino el guardado anterior de este texto, se espera a que se confirme
    const tx = S.tx;
    try { await txInflight.get(tx.path); } catch { /* lo dice quien lo lanzo */ }
    const pend = txPending.get(tx.path);
    return !(pend && pend.tx === tx);
  }
  S.dirty = false;  // lo que se escriba mientras se guarda lo vuelve a marcar (syncEdits)
  setSaveState('Guardando…');
  const payload = txVersion();
  try {
    const r = await sendTx(payload);
    if (r && r.rehecho_desde != null) {  // el directo rehízo texto mientras corregías: lo de ahí no se ha tocado
      const n = payload.tx.segs.filter(s => (s.start ?? -1) >= r.rehecho_desde - 0.005 && s.orig != null && s.text !== s.orig).length;
      if (n) toast(`${n === 1 ? 'Una corrección era' : `${n} correcciones eran`} sobre lo que el directo ha rehecho en el idioma nuevo (desde ${fmtClock(r.rehecho_desde)}): no se ${n === 1 ? 'ha' : 'han'} guardado. Hazla${n === 1 ? '' : 's'} sobre el texto nuevo.`, 'err');
    }
    if (S.tx && S.tx === payload.tx) {
      S.tx.saveErr = null;
      if (!r.live) {  // en directo la copia del original se guarda al terminar
        S.tx.has_backup = true;
        $('[data-act="tx-restore"]')?.classList.remove('hidden');
      }
    }
    setSaveState(S.dirty ? 'Sin guardar…' : 'Guardado ✓');
    return !S.dirty;
  } catch (err) {
    if (S.editing && S.tx && S.tx === payload.tx) {  // sigue en la pagina: el siguiente guardado lo reintenta
      S.dirty = true; setSaveState('No se ha podido guardar');
      if (!quiet || S.tx.saveErr !== err.message) toast(esc(err.message), 'err');
      S.tx.saveErr = err.message;
    } else saveTxNow(payload);  // ya ha cambiado de reunion: reintentos y aviso
    return false;
  }
}
/* al cambiar de reunion: se guarda en el momento, sin esperar al guardado diferido */
function flushEdits() {
  if (!S.editing || !S.tx) return;
  syncEdits();
  clearTimeout(S.saveTimer);
  if (S.dirty) {
    S.dirty = false;
    saveTxNow(txVersion());
  }
  S.editing = false;
}
/* al cerrar o recargar la pagina: ultimo intento con keepalive solo si cabe; devuelve cuantas no se han podido mandar.
   No sale del modo edicion: si tras el aviso se sigue en la pagina, lo que se escriba despues se sigue guardando */
function flushTxOnUnload() {
  if (S.editing && S.tx) saveEdits();  // sin esperar: sale ya con un fetch normal y queda pendiente para el keepalive
  let left = 0;
  for (const p of txPending.values()) {
    const body = JSON.stringify(p);
    if (new TextEncoder().encode(body).length > TX_KEEPALIVE_MAX) { left++; continue; }
    try { fetch('/api/transcript/save', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-Audios': '1' }, body }).catch(() => { }); } catch { left++; }
  }
  return left;
}
/* antes de la recarga automatica (app actualizada): primero se guarda lo corregido (y se reintenta, sin avisos, lo
   que hubiera fallado); si algo no se puede guardar, no se recarga. Va en cada sondeo: el mismo fallo se avisa una vez */
async function txSavedForReload() {
  if (S.editing && !(await saveEdits(true))) return false;
  for (const p of [...txPending.values()]) await saveTxNow(p, 1, true);
  return !txPending.size;
}
function startEdit() {
  if (S.tx && S.tx.kind === 'txt') toast('Esta transcripción no tiene tiempos (.txt): se edita igual.');
  S.editing = true; S.dirty = false;
  renderTxSection();
  $('#txBody .s')?.focus();
}
async function finishEdit() {
  const tx = S.tx;
  if (!(await saveEdits()) || S.tx !== tx) return;  // no se ha guardado: se sigue editando (el aviso ya ha salido)
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
  if (!await ask('¿Restaurar el original?', 'Se descartan todas tus correcciones y vuelve el texto tal como lo transcribió Whisper.', 'Restaurar') || !S.tx) return;
  const tx = S.tx, path = tx.path, unsaved = S.dirty || txPending.has(path);
  // lo corregido que aun no ha llegado al servidor se descarta: no se reintenta ni sale al cerrar o recargar la pagina
  // (pisaria el original restaurado), y lo que ya va en camino se espera, para que llegue antes que la restauracion
  clearTimeout(S.saveTimer);
  S.editing = false; S.dirty = false;
  txPending.delete(path); txLatest.delete(path);
  for (let p; (p = txInflight.get(path));) { try { await p; } catch { /* descartado */ } }
  try {
    await api('/api/transcript/restore', { path });
    const a = S.audios.get(path);
    S.tx = null;
    if (a) loadTranscript(a);
    toast('Restaurado el texto original de Whisper');
  } catch (err) {
    if (S.tx === tx) { S.editing = true; S.dirty = unsaved; if (unsaved) scheduleSave(); }  // sin restaurar: se sigue corrigiendo
    toast(esc(err.message), 'err');
  }
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
  const who = whoFor(tx);
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

// quien habla en cada frase (el sidecar de voces casado con el .srt): el mismo para la pantalla y para «Copiar texto»
function whoFor(tx) {
  return tx && S.spk && S.spk.path === tx.path && S.spk.exists && tx.kind === 'srt' && S.spk.who && S.spk.who.length === tx.segs.length ? S.spk.who : null;
}

// «Copiar texto»: como en pantalla, un parrafo por turno y el nombre de quien habla al cambiar (msg 1160)
function transcriptText() {
  const tx = S.tx;
  if (!tx) return '';
  const who = whoFor(tx);
  return paragraphs(tx.segs, who).map(p => {
    const w = who && who[p[0]] && (p[0] === 0 || who[p[0]] !== who[p[0] - 1]) ? spkLabel(who[p[0]]) : '';
    return (w ? w + ': ' : '') + p.map(i => tx.segs[i].text).filter(Boolean).join(' ');
  }).join('\n\n');
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
      + `<span class="f">${esc(x.folder ? nfc(x.folder).replaceAll('/', ' › ') : rootName())} · ${x.count} ${x.count === 1 ? 'vez' : 'veces'}</span></div>${hits}`
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
// algunos audios se transcriben pero la ventana no los reproduce (ALAC del iPhone «sin pérdida», AMR, CAF, AIFF, WMA)
audio.addEventListener('error', () => { if (audio.getAttribute('src')) toast(`Este audio no se puede escuchar dentro de ${esc(APP_NAME)}, pero se transcribe igual. Para oírlo, «Mostrar en carpeta» y ábrelo con tu reproductor.`, 'err'); });
function playBtns() {
  $$('[data-act="play"]').forEach(b => {
    const on = b.dataset.path === S.playing && !audio.paused;
    b.innerHTML = `${I.play}${on ? 'Pausa' : 'Reproducir'}`;
  });
}
window.addEventListener('beforeunload', e => {
  savePos();
  if (flushTxOnUnload()) { e.preventDefault(); e.returnValue = ''; }  // correcciones sin guardar: se pide confirmar
});

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
    else if (a === 'lh-modelos') { e.preventDefault(); S.lhModelos = !S.lhModelos; const au = curAudio(); if (au) renderLiveHero(au); }
    else if (a === 'modelo-bajar') { e.preventDefault(); act.disabled = true; if (typeof modeloBajar === 'function') modeloBajar(act.dataset.model); }
    else if (a === 'live-model') {
      const preset = act.dataset.preset, pr = S.presetsById[preset];
      if (pr.engine === 'cloud' && !S.config.cloud.has_key) return openSettings({ sec: 'procesado' });
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
      const id = act.dataset.box, sel = $(`select[data-lang="${id}"]`), lang = sel ? sel.value : '';
      const box = S.boxes[id] || { paths: [], resume: false };
      submitTx(box.paths, S.selPreset || S.config.preset, lang === TX_EACH ? null : lang, box.resume);
    }
    else if (a === 'move') openMove(p);
    else if (a === 'reveal') api('/api/reveal', { path: p }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'open-txt') api('/api/open', { path: p, ext: '.txt' }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'open-doc') api('/api/open', { path: p }).catch(err => toast(esc(err.message), 'err'));
    else if (a === 'new-folder') openNewFolder(p);
    else if (a === 'rename-folder') openRenameFolder(p);
    else if (a === 'link-remove') unlinkFolder(p);
    else if (a === 'aviso-sum-ok' || a === 'aviso-sum-ajuste') { e.preventDefault(); avisoResumenLocalVisto(a === 'aviso-sum-ajuste'); }
    else if (a === 'cfg-abrir' && typeof abrirConfigurar === 'function') abrirConfigurar();
    else if (a === 'cfg-probar' || a === 'cfg-aviso-no') {  // el aviso único de Inicio (configurar.js)
      localStorage.setItem('avisoProbar', '1');
      $('#avisoProbar')?.remove();
      if (a === 'cfg-probar' && typeof abrirConfigurar === 'function') abrirConfigurar({ paso: 3 });
    }
    else if (a === 'cancel-job') { await api('/api/cancel', { id: act.dataset.id }); pollJobs(true); }
    else if (a === 'copy-tx') copy(transcriptText(), whoFor(S.tx) ? 'Transcripción copiada (con hablantes)' : 'Transcripción copiada');
    else if (a === 'copy-doc') copy(S.docText || '', 'Texto copiado');
    else if (a === 'mark-next') gotoMark(S.markIdx + 1);
    else if (a === 'mark-prev') gotoMark(S.markIdx - 1);
    return;
  }
  const pr = t.closest('.preset');
  if (pr) {
    const p = S.presetsById[pr.dataset.preset];
    if (p.engine === 'cloud' && !S.config.cloud.has_key) return openSettings({ sec: 'procesado' });
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
  if (e.target.dataset.lang) {  // lo elegido en esa caja se recuerda para esas mismas reuniones (no para las demás)
    const box = S.boxes[e.target.dataset.lang];
    if (box) (S.txLang ||= {})[box.key] = e.target.value;
  }
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

/* Con formato (text/html) y, de reserva, el mismo texto limpio (text/plain), en una sola copia: Outlook, Teams y Word
   pegan el HTML; el Bloc de notas y lo que no admite formato, el texto. Si el navegador no deja escribir HTML, solo el
   texto (que tampoco lleva Markdown). */
async function copyRich(html, text, msg) {
  try {
    if (!window.ClipboardItem || !navigator.clipboard?.write) throw new Error('sin ClipboardItem');
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([text], { type: 'text/plain' }) })]);
    toast(msg);
  } catch {
    try { await navigator.clipboard.writeText(text); toast(msg + ' (sin formato)'); } catch { toast('No se pudo copiar', 'err'); }
  }
}
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
    if (cerrarSobre(false)) return;  // la confirmación de encima, sola (Ajustes sigue debajo)
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
  try {
    const r = await api('/api/jobs');
    onJobs(r.jobs);
    if (typeof onMovimientos === 'function') onMovimientos(r.movimientos);  // cambiar la carpeta en pleno directo
  } catch { /* servidor reiniciandose */ }
  const active = S.jobs.some(isActive);
  jobsTimer = setTimeout(pollJobs, active ? 1000 : 4000);
}
function onJobs(jobs) {
  const prev = S.jobsById;
  S.jobs = jobs;
  if (typeof prepVigilar === 'function') prepVigilar(jobs);
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
    else toast(`No se pudieron identificar las voces de ${esc(title)}: ${esc(j.aviso?.frase || j.error)}`, 'err', () => go('a', j.path));
    return;
  }
  if (j.kind === 'summary') { if (j.status === 'error') toast(`No se pudo resumir: ${esc(j.aviso?.frase || j.error)}`, 'err'); return; }  // el aviso de listo lo da notas.js
  const a = S.audios.get(j.path), title = a ? fullTitle(a) : j.name;
  if (j.status === 'done') toast(`<b>✓</b> ${j.live ? 'Grabación transcrita' : 'Transcrito'}: ${esc(title)}`, 'ok', () => go('a', j.path));
  else toast(`No se pudo transcribir ${esc(title)}: ${esc(j.aviso?.frase || j.error)}`, 'err', () => go('a', j.path));
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
      const sub = j.status === 'error' ? `<div class="s err" title="${esc(j.error)}">${esc(j.aviso?.frase || j.error)}${typeof cfgAccionHtml === 'function' ? cfgAccionHtml(j) : ''}</div>` : `<div class="s">${esc(jobStage(j))} · ${jobSub(j)}</div>`;
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
  cerrarSobre(false);  // una confirmación de encima no se queda sobre otro diálogo (p. ej. Ctrl+Alt+R mientras tanto)
  m.innerHTML = `<div class="dialog ${cls}">${html}</div>`;
  m.classList.remove('hidden');
  document.body.classList.toggle('aj-abierto', cls === 'aj');  // los avisos, arriba solo con Ajustes (su «Guardar» abajo)
  onOpen && onOpen($('.dialog', m));
  const f = $('.dialog [autofocus]', m) || $('.dialog input, .dialog button.primary', m);
  f && f.focus();
  return new Promise(r => { modalResolve = r; });
}
function closeModal(v) {
  cerrarSobre(false);
  if (S.modalCleanup) { const f = S.modalCleanup; S.modalCleanup = null; f(); }
  $('#modal').classList.add('hidden');
  $('#modal').innerHTML = '';
  const r = modalResolve; modalResolve = null;
  r && r(v);
}
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(false); });
/* Una confirmación encima de Ajustes, sin cerrarlo: lo escrito sigue en el formulario. Antes la confirmación ocupaba
   el hueco de Ajustes y lo escrito se reponía al volver, un mecanismo que en cada revisión traía un fallo nuevo
   (revisiones 5 a 10 de B2). Escape y un clic fuera la cierran a ella sola. */
let sobreResolve = null, sobreFoco = null;
function abrirSobre(html, onOpen) {
  let m = $('#modalSobre');
  if (!m) {
    m = document.createElement('div');
    m.id = 'modalSobre';
    m.className = 'modal sobre hidden';
    // un clic fuera la cierra sin llevar el foco al cuerpo de la página (vuelve a donde estaba, revisión 11)
    m.addEventListener('mousedown', e => { if (e.target === m) { e.preventDefault(); cerrarSobre(false); } });
    // es modal de verdad: Tab y Mayús+Tab dan la vuelta dentro, y el foco no se va a lo de debajo (revisión 11: con
    // Tab se llegaba a «Grabar» o a ⚙, detrás, y Enter sacaba de Ajustes sin preguntar)
    const enfocables = () => $$('button, [href], input, select, textarea', m).filter(x => !x.disabled && !x.hidden);
    m.addEventListener('keydown', e => {
      if (e.key !== 'Tab') return;
      const fs = enfocables();
      if (!fs.length) return;
      e.preventDefault();
      const i = fs.indexOf(document.activeElement);
      fs[(i < 0 ? 0 : i + (e.shiftKey ? fs.length - 1 : 1)) % fs.length].focus();
    });
    document.addEventListener('focusin', e => {
      if (!m.classList.contains('hidden') && !m.contains(e.target)) enfocables()[0]?.focus();
    });
    document.body.append(m);
  }
  cerrarSobre(false);  // (una a la vez)
  sobreFoco = document.activeElement;
  m.innerHTML = `<div class="dialog" role="alertdialog" aria-modal="true">${html}</div>`;
  const dlg = $('.dialog', m), h = $('h3', dlg);
  if (h) { h.id = 'sobreTitulo'; dlg.setAttribute('aria-labelledby', 'sobreTitulo'); }
  m.classList.remove('hidden');
  onOpen && onOpen(dlg);
  ($('button.primary', dlg) || $('button', dlg))?.focus();
  return new Promise(r => { sobreResolve = r; });
}
function cerrarSobre(v) {
  const m = $('#modalSobre');
  if (!m || m.classList.contains('hidden')) return false;
  m.classList.add('hidden');
  m.innerHTML = '';
  const r = sobreResolve, f = sobreFoco;
  sobreResolve = sobreFoco = null;
  if (f && document.body.contains(f)) f.focus();
  r && r(v);
  return true;
}
function ask(title, html, ok = 'Aceptar') {
  const encima = !!$('#modal .dialog.aj');  // con Ajustes abierto, encima de Ajustes
  const cuerpo = `<h3>${esc(title)}</h3><div>${html}</div><div class="btns"><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">${esc(ok)}</button></div>`;
  const enlazar = d => d.addEventListener('click', e => {
    const b = e.target.closest('[data-m]');
    if (b) (encima ? cerrarSobre : closeModal)(b.dataset.m === 'ok');
  });
  return encima ? abrirSobre(cuerpo, enlazar) : openModal(cuerpo, enlazar);
}

function flatFolders() {
  const out = [];
  if (!S.tree) return out;  // aún sin árbol (p. ej. «Reuniones» pulsado al arrancar): nada, no un TypeError
  (function w(n, d) { out.push({ path: n.path, name: n.path ? (n.linked ? '🔗 ' : '') + nfc(n.name) : `${rootName()} (raíz)`, depth: d }); subFolders(n).forEach(f => w(f, d + 1)); })(S.tree, 0);
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
  let dest = item.parent ? (item.parent.por_reunion ? item.parent.parent?.path ?? '' : item.parent.path) : '';
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

/* Dónde se procesan las reuniones: solo en este PC, con la cuenta de Claude del usuario (Claude Code) o con una API
   externa. Las dos últimas avisan de que el contenido de la reunión sale del PC (decisión de producto del 02/11/2026). */
function procesadoFieldsetHtml() {
  const mode = S.config.procesado || 'local', ag = S.config.agente || {}, agLabel = esc(ag.label || 'Claude Code');
  const opt = (v, titulo, texto, aviso) => `<label class="proc${mode === v ? ' on' : ''}"><input type="radio" name="proc" value="${v}"${mode === v ? ' checked' : ''}>
      <div><b>${titulo}</b><div class="help" style="margin-top:2px">${texto}</div>${aviso ? `<div class="notice proc-aviso">${I.info}${aviso}</div>` : ''}</div></label>`;
  const cuando = ag.comprobado ? new Date(ag.comprobado * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }) : '';
  const estado = !ag.encontrado ? `Todavía no encuentro ${agLabel} en este PC`
    : cuando ? `${agLabel} ${esc(ag.version || '')} · comprobado el ${cuando}` : `${agLabel} encontrado · sin comprobar todavía`;
  return `<fieldset><legend>${I.monitor}${typeof cfgEstadoHtml === 'function' ? 'Cambiar de opción aquí' : 'Dónde se procesan tus reuniones'}</legend>
    ${typeof cfgEstadoHtml === 'function' ? '<div class="help" style="margin-top:0">Se aplica con «Aplicar ahora». Con «Cambiar…», arriba, el asistente te guía y lo prueba al final.</div>' : ''}
    <div class="procs">
      ${opt('local', 'Solo en este PC', 'Whisper transcribe en tu ordenador. Nada sale de él. Sin resumen con IA.')}
      ${opt('agente', `Con tu cuenta de Claude (${agLabel})`, `Transcribe en tu PC y resume con Claude usando tu suscripción, personal o de empresa, a través de ${agLabel}: ${esc(APP_NAME)} no ve ni guarda tus credenciales.`,
    'El texto de la reunión (título, fecha, transcripción con quién habla si se sabe, notas, contexto, participantes confirmados e instrucciones; el audio no) se envía a Anthropic con esa cuenta: con la de empresa, bajo el acuerdo de tu empresa; con la personal, bajo sus términos de uso. En algunas reuniones puede ser un bloqueante: compruébalo antes. Al terminar de transcribir, las reuniones de los últimos 3 días se resumen solas; se desactiva aquí debajo.')}
      ${opt('api', 'API externa', 'Transcripción rápida en la nube y resumen con el proveedor que elijas (o la API de tu empresa), con su API key.',
    'El audio (para transcribir) y el texto (para resumir) salen del PC hacia el proveedor. Comprueba la política de tu empresa y el acuerdo con tu cliente, si lo hay.')}
    </div>
    <div class="help" id="procGuardado" aria-live="polite"></div>
    ${typeof modelosHtml === 'function' ? modelosHtml() : ''}
    <div id="procAgente"${mode === 'agente' ? '' : ' hidden'}>
      <ol class="pasos">
        <li><b>Instala ${agLabel}</b> (una vez): pulsa «Abrir PowerShell», pega <code>irm https://claude.ai/install.ps1 | iex</code> y pulsa Intro (o <code>winget install Anthropic.ClaudeCode</code>). Tarda menos de un minuto y no hace falta ser administrador.</li>
        <li><b>Entra con tu cuenta</b>: en esa misma ventana escribe <code>claude</code> y pulsa Intro. Se abre el navegador con el inicio de sesión de Claude: entra con tu cuenta (Pro, Max, Ultra, Team, Enterprise o Console); si es la de tu empresa, con su inicio de sesión de siempre. Cuando ponga «Login successful», pulsa Intro y cierra la ventana.</li>
        <li><b>Comprueba</b>: ${esc(APP_NAME)} busca ${agLabel} en este PC, lee su versión y le hace una petición mínima con tu cuenta.</li>
      </ol>
      <div class="help">¿Usas Claude Code en VS Code? ${esc(APP_NAME)} también encuentra esa copia y su sesión. La app de escritorio de Claude no basta: su sesión es solo suya.</div>
      <div class="grid2">
        <div><label>Modelo</label><select id="agModelo">${(ag.modelos || []).map(([v, l]) => `<option value="${esc(v)}"${v === ag.modelo ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
        <div><label>Estado</label><div class="help" style="margin-top:8px" id="agEstado">${estado}</div></div>
      </div>
      <div class="row" style="margin-top:10px"><button class="btn sm" type="button" id="agPs">Abrir PowerShell</button><button class="btn sm" type="button" id="agCheck">Comprobar</button><span id="agRes" class="muted" style="font-size:12.5px"></span></div>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:12px"><input type="checkbox" id="sAutoAg" ${S.config.summary?.auto !== false ? 'checked' : ''}> Resumir solo al terminar de transcribir (reuniones de los últimos 3 días)</label>
      <div class="help">Desmarcado, solo se resume a mano con «Resumir esta reunión». Cada resumen gasta cupo de tu suscripción.</div>
    </div>
  </fieldset>`;
}

/* la ayuda del directo en Ajustes › Avanzado (OBS): con la API, la nube y su cupo; sin ella, solo el PC */
const lAyuda = api => 'El motor con el que empieza cada directo se elige en Procesado › Motores y se puede cambiar en mitad de la grabación desde la propia pantalla. '
  + (api ? 'En la nube el texto va unos 5–10 s por detrás; en local, unos 20–40 s. El repaso final tarda 1–2 min por hora de reunión (mientras tanto ves el texto del directo), gasta el doble de cupo del proveedor y no se hace si has corregido el texto a mano.'
    : 'En este PC el texto va unos 20–40 s por detrás de lo que se dice.');
/* Ajustes por secciones (07/11/2026, configurar.js): antes, un diálogo de 6 a 7,5 pantallas con «Guardar» al final.
   Todas las secciones están en el DOM (collect() lee de todas); solo se ve la elegida. opts.sec: la que se abre (si
   no, la última vista). Se llama también como manejador de clic: un Event no trae .sec. */
// (rediseño B2, 07/11/2026) por lo que se hace: «Al grabar» primero; Vocabulario y Carpetas pasan a Proyectos; el
// idioma y las instrucciones del resumen, a Resumen; Outlook y Teams, a Calendario y Teams; OBS, a Avanzado
const AJ_SECS = [['algrabar', 'Al grabar'], ['proyectos', 'Proyectos'], ['procesado', 'Procesado'], ['resumen', 'Resumen'],
  ['calendario', 'Calendario y Teams'], ['voces', 'Voces'], ['avanzado', 'Avanzado'], ['ayuda', 'Ayuda']];
function openSettings(opts) {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  // lo escrito y sin guardar no sale de Ajustes: las confirmaciones van encima (ask) y, antes de abrir un subdiálogo
  // (una lista, el asistente), «Tienes cambios sin guardar» (d.ajAntesDeSalir). Así, cada vez que se abre, Ajustes
  // pinta lo guardado (revisión 10 de B2: el borrador que se reponía al volver traía un fallo nuevo en cada revisión)
  const sec = (opts && typeof opts === 'object' && AJ_SECS.some(x => x[0] === opts.sec) && opts.sec)
    || (AJ_SECS.some(x => x[0] === S.ajSec) && S.ajSec) || 'algrabar';
  S.ajSec = sec;
  const enApi = S.config.procesado === 'api';  // (no «api»: taparía la función api() en todo Ajustes)
  const c = S.config.cloud, P = S.config.providers;
  const ac = S.config.acerca || {};
  const elegido = provElegido();  // en una instalación nueva, «Elige el proveedor…»: ninguno preseleccionado
  const provOpts = (elegido ? '' : '<option value="" selected>Elige el proveedor…</option>')
    + provOrden(P).map(([k, v]) => `<option value="${k}"${elegido && k === c.provider ? ' selected' : ''}>${esc(v.label)}</option>`).join('');
  openModal(`<h3>${I.gear}Ajustes</h3>
    <div class="aj-grid">
    <div class="aj-side"><nav class="aj-nav" aria-label="Secciones de Ajustes">${AJ_SECS.map(([id, l]) => `<button type="button" data-sec="${id}" class="${id === sec ? 'on' : ''}" aria-current="${id === sec ? 'true' : 'false'}"${id === sec ? ' autofocus' : ''}>${l}</button>`).join('')}</nav>
      ${ac.donar ? `<a class="aj-cafe" id="ajCafe" href="${esc(ac.donar)}" title="${esc(APP_NAME)} es gratis. Si te sirve, puedes invitar al autor a un café (PayPal)">${I.cafe}Invitar a un café</a>` : ''}</div>
    <div class="aj-main">
    ${S.config.settings_broken ? `<div class="notice">${settingsBrokenHtml(S.config.settings_broken)}</div>` : ''}
    <section data-secc="algrabar"${sec === 'algrabar' ? '' : ' hidden'}>
    ${typeof ajAlGrabarHtml === 'function' ? ajAlGrabarHtml() : `<fieldset><legend>${I.globe}Idioma de las reuniones</legend><label>Por defecto</label>${langSelect('sLang', S.config.language ?? 'es-en')}</fieldset>`}
    </section>
    <section data-secc="proyectos"${sec === 'proyectos' ? '' : ' hidden'}>
    ${typeof ajProyectosHtml === 'function' ? ajProyectosHtml() : ''}
    <fieldset><legend>${I.book}Listas de vocabulario</legend>
      <div id="sVocab" class="help" style="margin-top:0">Cargando…</div>
      <label for="sPrompt">Vocabulario de las reuniones sin proyecto</label>
      <textarea id="sPrompt" placeholder="Nombres propios, empresas, siglas, productos…">${esc(S.config.prompt)}</textarea>
      <div class="help">Se le pasa a Whisper como contexto para que escriba bien nombres propios y siglas. Opcional: si notas frases raras o repetidas, déjalo vacío.</div>
    </fieldset>
    <fieldset><legend>${I.folder}Carpetas fuera de ${esc(APP_NAME)}</legend>
      <div id="sLinks"></div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="lnAdd">${I.plus}Añadir una carpeta…</button></div>
      <div class="help">Por ejemplo la carpeta del proyecto en OneDrive. ${esc(APP_NAME)} la trata como suya: puedes grabar ahí, escuchar y transcribir sus audios, y la transcripción y las notas se guardan junto al audio. Quitarla de ${esc(APP_NAME)} no borra nada.</div>
    </fieldset>
    ${typeof perfilSalidasHtml === 'function' ? perfilSalidasHtml() : ''}
    </section>
    <section data-secc="procesado"${sec === 'procesado' ? '' : ' hidden'}>
    ${typeof cfgEstadoHtml === 'function' ? cfgEstadoHtml() : ''}
    ${procesadoFieldsetHtml()}
    <div id="procApi"${S.config.procesado === 'api' ? '' : ' hidden'}>
    <fieldset><legend>${I.nube}Transcripción en la nube</legend>
      <div class="notice">Las grabaciones de reuniones pueden ser información confidencial. Antes de mandar audio a un servicio externo, confirma que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay. Si tu empresa tiene una pasarela propia (Azure OpenAI u otra compatible con OpenAI), configúrala aquí.</div>
      <label>Proveedor</label><select id="cProv">${provOpts}</select>
      <div id="cFields"></div>
      <div class="row" style="margin-top:14px"><button class="btn sm" id="cTest">Probar conexión</button><span id="cTestRes" class="muted" style="font-size:12.5px"></span></div>
      <div class="help">El modelo del directo, otras claves, los trozos y el paralelismo están en <a href="#" data-sec-ir="avanzado">Avanzado</a>.</div>
    </fieldset>
    <fieldset><legend>${I.spark}Resumen con IA</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="sAuto" ${S.config.summary?.auto !== false ? 'checked' : ''}> Resumir solo al terminar de transcribir en la nube (reuniones de los últimos 3 días)</label>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="sAutoLocal" ${S.config.summary?.local ? 'checked' : ''}> Resumir también las reuniones transcritas en local (las envía a ${esc(provName())})</label>
      <div class="help" id="sAutoLocalQue">De cada reunión se envía a <b>${esc(provName())}</b>: el título (o el nombre del archivo), la fecha y la duración, la transcripción (con quién habla, si se sabe), tus notas, el contexto, los participantes confirmados y tus instrucciones para el resumen. El audio no.</div>
      <div class="notice" id="sAutoLocalAviso"${S.config.summary?.local ? '' : ' hidden'}>Si son reuniones de trabajo, asegúrate de que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay.</div>
      <label>Modelo</label><input class="input" id="sModel" list="sModels" value="${esc(S.config.summary?.model || '')}">
      <datalist id="sModels">${(S.config.summary?.models || []).map(m => `<option value="${esc(m)}">`).join('')}</datalist>
      <div class="help">El idioma del resumen y las instrucciones extra están en <a href="#" data-sec-ir="resumen">Resumen</a>. Usa el mismo proveedor y API key que la transcripción en la nube, y solo se envía el texto (no el audio). Lo que transcribes en local no sale del PC por el resumen automático salvo que marques la segunda casilla; a mano («Resumir esta reunión») se puede resumir siempre.${elegido && c.provider === 'groq' ? ' Con el plan gratuito de Groq (~8K tokens por minuto) una reunión de 1 h se resume por partes en unos 3 minutos, y caben unas 7 reuniones largas al día.' : ''}</div>
    </fieldset>
    </div>
    ${typeof perfilMotoresHtml === 'function' ? perfilMotoresHtml() : ''}
    </section>
    <section data-secc="resumen"${sec === 'resumen' ? '' : ' hidden'}>
    ${typeof perfilResumenHtml === 'function' ? perfilResumenHtml() : ''}
    </section>
    <section data-secc="calendario"${sec === 'calendario' ? '' : ' hidden'}>
    ${typeof ajCalendarioHtml === 'function' ? ajCalendarioHtml() : window.reunionFieldsetHtml?.() || ''}
    </section>
    <section data-secc="voces"${sec === 'voces' ? '' : ' hidden'}>
    ${vocesFieldsetHtml()}
    </section>
    <section data-secc="avanzado"${sec === 'avanzado' ? '' : ' hidden'}>
    <fieldset><legend><i class="recdot"></i>Grabaciones de OBS</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="lAuto" ${S.config.live_auto ? 'checked' : ''}> Empezar a transcribir en cuanto OBS empiece a grabar</label>
      <label id="lNube" style="display:flex;gap:8px;align-items:center;font-weight:500"${enApi ? '' : ' hidden'}><input type="checkbox" id="lFinal" ${S.config.live_final_pass !== false ? 'checked' : ''}> Al terminar un directo en la nube, repasar la grabación entera para mejorar el texto</label>
      <div class="help" id="lAyuda">${esc(lAyuda(enApi))}</div>
    </fieldset>
    <fieldset><legend>${I.monitor}Transcripción local</legend>
      <label for="sThreads">Hilos de CPU</label><input class="input" id="sThreads" type="number" min="1" max="${S.config.cpu_count}" value="${S.config.threads}">
      <div class="help">Tu procesador tiene ${S.config.cpu_count} hilos. Con más hilos va más rápido, pero el portátil irá más justo mientras transcribe.</div>
    </fieldset>
    <div id="procApiAv"${enApi ? '' : ' hidden'}>
    <fieldset><legend>${I.nube}La nube, en detalle</legend>
      ${typeof directoSettingsHtml === 'function' ? directoSettingsHtml(c) : ''}
      <div class="grid2">
        <div><label>Minutos por trozo</label><input class="input" id="cChunk" type="number" min="2" max="20" value="${c.chunk_minutes}"></div>
        <div><label>Trozos en paralelo</label><input class="input" id="cPar" type="number" min="1" max="8" value="${c.parallel}"></div>
      </div>
    </fieldset>
    </div>
    </section>
    <section data-secc="ayuda"${sec === 'ayuda' ? '' : ' hidden'}>
    <fieldset><legend>${I.package}Compartir ${esc(APP_NAME)}</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="xVocab"> Incluir mis listas de vocabulario <span class="muted" style="font-weight:400">· suelen llevar nombres de personas y empresas: solo para gente del mismo proyecto</span></label>
      <div class="row" style="margin-top:8px"><button class="btn sm" id="xExport" type="button">Crear instalador para otra persona</button><span id="xRes" class="muted" style="font-size:12.5px"></span></div>
      <div class="help">Crea en el escritorio <b>${esc(APP_NAME)} - instalador.zip</b> con la app completa y un INSTALAR.bat. No lleva tus grabaciones, notas, ajustes ni tu API key: la otra persona conecta su propio proveedor. Compártelo por un enlace de OneDrive o Teams (el correo suele bloquear los .zip con .bat).<br>
        <b>Para actualizar a alguien que ya lo tiene</b>: mándale el zip nuevo y que ejecute su INSTALAR.bat. El instalador cierra la versión abierta y actualiza la que ya tenía, en su carpeta de siempre (con sus grabaciones).</div>
    </fieldset>
    <div class="help" style="margin-top:16px"><b>Atajos:</b> Ctrl+Alt+R empezar/parar grabación (desde cualquier programa) · Espacio reproducir/pausa · ← → 5 s · Shift + ← → 15 s · [ ] velocidad · Ctrl K buscar en todas las transcripciones · Ctrl P ir a una reunión · Ctrl F buscar en la transcripción</div>
    <fieldset class="acerca" style="margin-top:14px"><legend>${I.info}Acerca de</legend>
      <div class="ac-marca"><img src="/logo.svg" alt=""><b>${esc(APP_NAME)}</b><span class="muted">versión ${esc(S.config.version || '')}${ac.licencia ? ` · licencia ${esc(ac.licencia)}` : ''}</span></div>
      <div class="help" style="margin-top:0">
        Código, novedades y ayuda: <a href="${esc(ac.repo || '#')}" id="aRepo">${esc((ac.repo || '').replace(/^https?:\/\//, ''))}</a><br>
        Datos y registros (ajustes, API key cifrada, <code>server.log</code>): <code>${esc(ac.datos || '')}</code><br>
        ${esc(APP_NAME)} es gratis. Si te sirve, puedes <a href="${esc(ac.donar || '#')}" id="aDonar">invitar al autor a un café</a> (PayPal).</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="aDatos">${I.folder}Abrir</button><button class="btn sm" type="button" id="aDiag">${I.clipboard}Copiar diagnóstico</button><span class="muted" style="font-size:12.5px">versión, sistema, motor y las últimas líneas del registro: para pedir ayuda</span></div>
      <div class="help">Solo lleva datos técnicos: la versión, el sistema, el motor y el modelo, cuántas carpetas vinculadas y copias en otras carpetas hay y, de las últimas líneas del registro, el texto fijo de cada mensaje con sus cifras y códigos de error. Los nombres de reuniones, carpetas, proyectos, temas y personas, las rutas y cualquier otro texto van como &lt;texto&gt;, y las claves no van. El registro completo (<code>server.log</code>) sí lleva nombres y rutas: no lo compartas tal cual. Aun así, revisa el diagnóstico antes de pegarlo y no lo publiques en un sitio público.</div>
      ${ac.tenca && /^tenca$/i.test(APP_NAME) ? `<p class="ac-tenca">${I.tenca}<span>El nombre viene de la tenca (<a href="${esc(ac.tenca)}" id="aTenca" title="Museo Nacional de Historia Natural de Chile"><i>Mimus thenca</i></a>), un pájaro de Chile.</span></p>` : ''}
    </fieldset>
    <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:10px"><input type="checkbox" id="sInicioWin" data-se-guarda-solo disabled> Arrancar ${esc(APP_NAME)} al iniciar Windows <span class="muted" id="sInicioWinNota" style="font-weight:400">· lo mismo que «Arrancar con Windows» en el menú del icono de la bandeja</span></label>
    <div class="row" style="margin-top:10px"><button class="btn ghost sm danger" id="sOff" type="button" title="Cierra la app del todo, también el motor que sigue en segundo plano al cerrar la ventana">${I.power}Apagar ${esc(APP_NAME)}</button>
      <span class="muted" style="font-size:12.5px">Cerrar la ventana no apaga la app: sigue en segundo plano para Ctrl+Alt+R.</span></div>
    </section>
    </div>
    </div>
    <div class="btns aj-pie"><span class="aj-pend" aria-live="polite"></span><button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Guardar</button></div>`, d => {
    // secciones: se cambia sin perder lo escrito (todas siguen en el DOM)
    const irSec = id => {
      S.ajSec = id;
      $$('.aj-nav [data-sec]', d).forEach(x => { const on = x.dataset.sec === id; x.classList.toggle('on', on); x.setAttribute('aria-current', on ? 'true' : 'false'); });
      $$('[data-secc]', d).forEach(x => { x.hidden = x.dataset.secc !== id; });
      $('.aj-main', d).scrollTop = 0;
    };
    d.irSec = irSec;
    // «Hay cambios sin guardar» de verdad: se compara con cómo se abrió. Lo que se guarda solo (Voces, personas) o no
    // es de «Guardar» (el vocabulario del instalador) no cuenta.
    // agProy: solo elige qué plan enseñar; data-se-guarda-solo: lo que guarda al momento (Outlook con su estado)
    const NO_CUENTA = new Set(['vzOn', 'xVocab', 'agProy']);
    const campos = () => $$('.aj-main input[id], .aj-main select[id], .aj-main textarea[id]', d)
      .filter(x => x.name !== 'proc' && !NO_CUENTA.has(x.id) && !('seGuardaSolo' in x.dataset) && x.type !== 'file'
        && x.type !== 'button');
    // y lo que no es un campo con id: los destinos de «Copias en otras carpetas» (perfil.js) y las claves adicionales
    // del directo (directo.js) (revisión 11: quitar un destino no decía «Hay cambios sin guardar» ni preguntaba)
    const otros = () => {
      d.directoKeys?.stash();
      // las claves, como las manda «Guardar» (collectDirectoSettings): una fila vacía no es un cambio
      const claves = d.directoKeys && Object.fromEntries(Object.entries(d.directoKeys.state)
        .map(([p, rs]) => [p, (rs || []).map(r => String(r.value || '').trim() || r.mask || '').filter(Boolean)]));
      return { __destinos: typeof perfilOuts === 'undefined' ? '' : JSON.stringify(perfilOuts),
        __claves: JSON.stringify(claves ?? null) };
    };
    const foto = () => ({ ...Object.fromEntries(campos().map(x => [x.id, x.type === 'checkbox' || x.type === 'radio' ? x.checked : x.value])), ...otros() });
    const inicial = foto();  // como viene de los ajustes guardados
    // «Dónde se procesan» marcado y sin aplicar también lo es: «Guardar» lo aplica (revisión 2 de B2, P1-2)
    const procSinAplicar = () => {
      const v = $('input[name="proc"]:checked', d)?.value;
      return !!v && v !== (S.config.procesado || 'local');
    };
    // solo lo que está en las dos fotos: un campo que deja de contar (pasa a guardarse solo, como Outlook al activarlo)
    // no es un cambio, y uno que aparece después ya tiene su valor de partida («tarde»). (En la fusión con el
    // calendario, Outlook activado daba «Hay cambios sin guardar» sin haber cambiado nada.)
    const hayCambios = () => { const ahora = foto(); return Object.keys(inicial).some(k => k in ahora && ahora[k] !== inicial[k]) || procSinAplicar(); };
    const marcar = () => { $('.aj-pend', d).textContent = hayCambios() ? 'Hay cambios sin guardar' : ''; };
    d.ajMarcar = marcar;
    // antes de salir de Ajustes a un subdiálogo (una lista, el asistente) con cambios sin guardar: encima, «Guardar y
    // seguir», «Descartar» o «Volver» (Escape y un clic fuera también vuelven). true: se puede seguir
    d.ajAntesDeSalir = async () => {
      if (!hayCambios()) return true;
      const r = await abrirSobre(`<h3>Tienes cambios sin guardar</h3><div><p>Si sigues sin guardarlos, se pierden.</p></div>
        <div class="btns"><button class="btn" data-m="volver">Volver</button><button class="btn" data-m="descartar">Descartar</button><button class="btn primary" data-m="guardar">Guardar y seguir</button></div>`,
      x => x.addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) cerrarSobre(b.dataset.m); }));
      if (r === 'descartar') return true;
      return r === 'guardar' ? guardar(false) : false;
    };
    document.body.classList.add('aj-abierto');
    setTimeout(() => $('.aj-nav [data-sec].on', d)?.focus(), 0);  // el foco, dentro del diálogo (no en ⚙)
    d.addEventListener('click', e => {
      const x = e.target.closest('.aj-nav [data-sec], [data-sec-ir]');
      if (!x) return;
      e.preventDefault();
      irSec(x.dataset.sec || x.dataset.secIr);
      // desde un enlace de dentro (el origen de una línea del plan, «Proyectos»…), el foco a su sección en el menú: si
      // no, con el teclado caía en el cuerpo de la página, fuera del diálogo (revisión 2 de B2)
      if (x.dataset.secIr) $(`.aj-nav [data-sec="${x.dataset.secIr}"]`, d)?.focus();
    });
    const pendiente = e => { if (e.target.closest('.aj-main') && !NO_CUENTA.has(e.target.id)) marcar(); };
    d.addEventListener('input', pendiente);
    d.addEventListener('change', pendiente);
    d.addEventListener('click', e => {  // la tarjeta de estado (configurar.js): «Probar» y «Cambiar…» abren el asistente
      const x = e.target.closest('[data-cfg]');
      if (!x || typeof abrirConfigurar !== 'function') return;
      e.preventDefault();
      const volver = () => openSettings({ sec: 'procesado' });
      d.ajAntesDeSalir().then(ok => {
        if (ok) abrirConfigurar(x.dataset.cfg === 'probar' ? { paso: 3, volver, empezar: true } : { volver });  // «Probar»: ya
      });
    });
    // la tarjeta, al día tras «Aplicar ahora» o «Probar conexión»
    function ajTarjeta() { const t = $('.cfg-estado', d); if (t && typeof cfgEstadoHtml === 'function') t.outerHTML = cfgEstadoHtml(); }
    // la confirmación va encima de Ajustes: con «Cancelar», Ajustes sigue ahí, con lo escrito
    $('#sOff', d).onclick = () => confirmShutdown();
    // «Arrancar con Windows» (la bandeja deja /api/inicio-windows): se aplica al marcarla, como en el menú del icono;
    // null = no se puede (fuera de Windows o una copia sin instalar)
    const ini = $('#sInicioWin', d);
    if (ini) {
      api('/api/inicio-windows').then(r => {
        if (r.activado == null) { $('#sInicioWinNota', d).textContent = '· solo con la app instalada en Windows'; return; }
        ini.checked = !!r.activado;
        ini.disabled = false;
      }).catch(() => { });
      ini.onchange = async () => {
        ini.disabled = true;
        try {
          const r = await api('/api/inicio-windows', { activado: ini.checked });
          ini.checked = !!r.activado;
          toast(r.activado ? `${esc(APP_NAME)} arrancará al iniciar Windows` : `${esc(APP_NAME)} ya no arrancará al iniciar Windows`);
        } catch (err) { ini.checked = !ini.checked; toast(esc(err.message), 'err'); }
        finally { ini.disabled = false; }
      };
    }
    // «Dónde se procesan» (07/11/2026): elegirla y cerrar sin «Guardar», abajo del todo, dejaba el modo anterior sin
    // decirlo (al grabar seguía usando Groq). Ahora el cambio pendiente se dice junto a las opciones, con «Aplicar ahora»
    // (y su aviso si hay trabajo en la nube, que pasaría a este PC), y al cerrar sin aplicarlo se avisa. Un clic de
    // prueba no cambia nada por sí solo.
    const procNombre = { local: '«Solo en este PC»', agente: `«Con tu cuenta de Claude»`, api: '«API externa»' };
    const procElegido = () => $('input[name="proc"]:checked', d)?.value;
    const procPendiente = () => { const v = procElegido(); return v && v !== S.config.procesado ? v : null; };
    const procPintar = () => {
      const v = procPendiente(), res = $('#procGuardado', d);
      if (!v) { if (!res.querySelector('.ok-aplicado')) res.innerHTML = ''; return; }
      const enNube = S.config.procesado === 'api' && v !== 'api'
        && S.jobs.some(j => isActive(j) && j.kind !== 'summary' && (j.engine === 'cloud' || j.preset === 'nube'));
      res.innerHTML = `<span class="motor-err">Has elegido ${procNombre[v]}, pero aún no está aplicado: sigue ${procNombre[S.config.procesado] || ''}.</span>`
        + (enNube ? ' <b>Hay transcripciones en la nube en marcha:</b> al aplicar, el directo sigue en este PC con Whisper y lo que espera en la nube se cancela.' : '')
        + ` <button class="btn sm primary" type="button" id="procAplicar">Aplicar ahora</button>`;
    };
    $$('input[name="proc"]', d).forEach(r => r.addEventListener('change', () => {
      const v = procElegido();
      $$('.proc', d).forEach(l => l.classList.toggle('on', l.querySelector('input').value === v));
      $('#procApi', d).hidden = v !== 'api';
      $('#procAgente', d).hidden = v !== 'agente';
      if ($('#procModelos', d)) $('#procModelos', d).hidden = v === 'api';
      if ($('#procApiAv', d)) $('#procApiAv', d).hidden = v !== 'api';
      if ($('#lNube', d)) $('#lNube', d).hidden = v !== 'api';
      if ($('#lAyuda', d)) $('#lAyuda', d).textContent = lAyuda(v === 'api');
      procPintar();
    }));
    $('#procGuardado', d).addEventListener('click', async e => {
      if (!e.target.closest('#procAplicar')) return;
      const v = procPendiente(), res = $('#procGuardado', d);
      if (!v) return procPintar();
      res.textContent = 'Aplicando…';
      try {
        const c = await api('/api/settings', { procesado: v });
        setConfig(c);
        localStorage.setItem('procElegido', '1');
        res.innerHTML = `<span class="ok-aplicado" style="color:var(--ok)">✓ Aplicado: ${esc({ local: 'transcribe Whisper en este PC; sin resumen con IA', agente: `transcribe Whisper en este PC y resume ${S.config.agente?.label || 'Claude Code'} con tu cuenta`, api: `transcribe y resume ${provName()} con tu API key` }[v] || v)}.</span>`;
        if (c.descarga) toast(`Descargando el modelo ${esc(c.descarga)} de Whisper para transcribir en este PC: el progreso, en Ajustes`);
        d.modelosPoll?.();
        if (typeof motoresRefrescar === 'function') motoresRefrescar(d);
        ajTarjeta();
        ponerAlDia(['lPreset', 'pfPreset']);  // el servidor ha puesto los motores del modo
        marcar();  // el modo ya está aplicado: deja de ser «sin guardar»
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ No se ha podido aplicar: ${esc(err.message)}</span>`; }
    });
    S.modalCleanup = () => {  // al cerrar Ajustes (Cancelar, Esc, fuera) con el cambio sin aplicar: que se sepa
      document.body.classList.remove('aj-abierto');
      if (!document.body.contains(d)) return;  // otro diálogo ocupó el hueco
      const v = procPendiente();
      if (v) toast(`No se ha aplicado ${procNombre[v]}: sigue ${procNombre[S.config.procesado] || 'la opción de antes'}. Para cambiarlo, ⚙ Ajustes → «Aplicar ahora».`, 'err');
    };
    $('#agCheck', d).onclick = async () => {  // no guarda el formulario: solo comprueba con el modelo elegido
      // con los segundos a la vista (07/11/2026: en su PC tardó minutos con un «Comprobando…» quieto)
      const res = $('#agRes', d), btn = $('#agCheck', d), t0 = Date.now();
      const seg = () => Math.round((Date.now() - t0) / 1000);
      const tic = () => { res.textContent = `Comprobando… ${seg()} s` + (seg() >= 20 ? ' · la primera vez, Claude Code puede tardar uno o dos minutos en arrancar' : ''); };
      btn.disabled = true;
      tic();
      const timer = setInterval(tic, 1000);
      try {
        const r = await api('/api/agente/comprobar', { tipo: S.config.agente?.tipo || 'claude', modelo: $('#agModelo', d)?.value });
        // origen: «Claude Code de VS Code» si es la copia de la extensión; modelo_usado y aviso: si la cuenta no deja
        // el modelo elegido, Claude Code usa su predeterminado y lo dice (rama claude-empresa, 08/11/2026)
        const quien = r.origen || S.config.agente?.label || 'Claude Code';
        res.innerHTML = `<span style="color:var(--ok)">✓ ${esc(quien)} ${esc(r.version)} responde (${String(r.seconds).replace('.', ',')} s)${r.modelo_usado && !r.aviso ? ` · modelo ${esc(r.modelo_usado)}` : ''}</span>`
          + (r.aviso ? `<span class="warn-txt" style="display:block;margin-top:4px">${esc(r.aviso)}</span>` : '');
        $('#agEstado', d).textContent = `${quien} ${r.version} · comprobado ahora`;
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ ${esc(err.message)}</span> <span class="muted">(tras ${seg()} s)</span>`; }
      finally { clearInterval(timer); btn.disabled = false; }
    };
    $('#agPs', d).onclick = () => api('/api/abrir-powershell', {}).catch(err => toast(esc(err.message), 'err'));
    $('#aDatos', d).onclick = () => api('/api/acerca/abrir', { que: 'datos' }).catch(e => toast(e.message, 'err'));
    $('#aRepo', d).onclick = e => { e.preventDefault(); api('/api/acerca/abrir', { que: 'repo' }).catch(() => window.open(ac.repo, '_blank')); };
    $('#aDonar', d).onclick = e => { e.preventDefault(); if (ac.donar) api('/api/acerca/abrir', { que: 'donar' }).catch(() => window.open(ac.donar, '_blank')); };
    const cafe = $('#ajCafe', d);
    if (cafe) cafe.onclick = e => { e.preventDefault(); api('/api/acerca/abrir', { que: 'donar' }).catch(() => window.open(ac.donar, '_blank')); };
    const tenca = $('#aTenca', d);
    if (tenca) tenca.onclick = e => { e.preventDefault(); api('/api/acerca/abrir', { que: 'tenca' }).catch(() => window.open(ac.tenca, '_blank')); };
    $('#aDiag', d).onclick = async () => {
      try { const r = await api('/api/diagnostico'); await copy(r.texto, 'Diagnóstico copiado: revísalo antes de pegarlo donde pidas ayuda'); } catch (e) { toast(e.message, 'err'); }
    };
    const fields = () => {
      // con la nube de ahora, no la de cuando se abrió Ajustes: tras «Probar conexión», ir a otro proveedor y volver
      // repintaba lo de antes, y «Guardar» lo guardaba (revisión 14)
      const c = S.config.cloud || {}, P = S.config.providers || {};
      const p = $('#cProv', d).value, info = P[p];
      if (!info) { $('#cFields', d).innerHTML = '<div class="help">Elige el proveedor para ver qué pide (la clave y, según cuál, la dirección de la API).</div>'; return; }
      if (p === 'azure') {
        $('#cFields', d).innerHTML = `<label>Endpoint</label><input class="input" id="cAzEp" placeholder="https://<recurso>.openai.azure.com" value="${esc(c.azure_endpoint)}">
          <div class="grid2"><div><label>Deployment (whisper)</label><input class="input" id="cAzDep" value="${esc(c.azure_deployment)}"></div>
          <div><label>API version</label><input class="input" id="cAzVer" value="${esc(c.azure_api_version)}"></div></div>
          <label>API key</label><input class="input" id="cKey" type="password" placeholder="${c.has_key ? 'Guardada: ' + esc(c.api_key) + ' (escribe para cambiarla)' : 'Clave del recurso de Azure'}">`;
      } else if (p === 'custom') {
        $('#cFields', d).innerHTML = `<label>URL base de la API</label><input class="input" id="cBase" placeholder="https://api.tu-empresa.com/v1" value="${esc(c.base_url || '')}">
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
    $('#sAutoLocal', d).onchange = e => { $('#sAutoLocalAviso', d).hidden = !e.target.checked; if (e.target.checked) localStorage.setItem('sumOk', '1'); };
    bindVocesSettings(d);
    if (typeof bindModelos === 'function') bindModelos(d);
    if (typeof bindDirectoSettings === 'function') bindDirectoSettings(d);
    window.bindReunionSettings?.(d);
    if (typeof bindPerfilSettings === 'function') bindPerfilSettings(d);
    if (typeof bindAjAlGrabar === 'function') bindAjAlGrabar(d);  // ajustes.js: el plan arriba, las reglas debajo
    if (typeof bindAjProyectos === 'function') bindAjProyectos(d);
    if (typeof bindAjCalendario === 'function') bindAjCalendario(d);
    // los campos que se pintan después de la foto (los de la nube, según el proveedor): su valor de ahora es el de
    // partida. Antes contaban siempre: deshacer un cambio seguía diciendo «Hay cambios sin guardar» (revisión 2 de B2)
    const tarde = campos().filter(x => !(x.id in inicial));
    tarde.forEach(x => { inicial[x.id] = x.type === 'checkbox' || x.type === 'radio' ? x.checked : x.value; });
    Object.assign(inicial, otros());  // los destinos y las claves, ya pintados por sus bind*
    // tras guardar una parte desde aquí («Aplicar ahora», «Probar conexión»), lo guardado es el nuevo punto de partida
    // de esos campos (revisión 12: seguía «Hay cambios sin guardar» con todo guardado, y editar una lista preguntaba).
    // Los motores, con lo que pinta motoresRefrescar como guardado (dataset.ini): si se habían cambiado aquí, siguen
    // contando. Lo demás, con lo que se mandó (de, una foto de antes de guardar): lo escrito mientras se guardaba no se
    // da por guardado (revisión 13)
    const ponerAlDia = (ids, de = null) => {
      ids.forEach(id => {
        const el = $('#' + id, d);
        if (!el) return;
        inicial[id] = 'ini' in el.dataset ? el.dataset.ini : de && id in de ? de[id]
          : el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value;
      });
      if (ids.includes('__claves')) inicial.__claves = de ? de.__claves : otros().__claves;
    };
    // quitar o añadir un destino o una clave es un clic, no un «change»: el aviso se mira después de cada clic (de
    // cualquiera: el de quitar una fila la saca del DOM antes de llegar aquí, revisión 12)
    d.addEventListener('click', () => setTimeout(marcar, 0));
    // el modo que pide quien abre, marcado y sin aplicar (con su aviso y sus campos, que pinta su «change»): «Revisar
    // en Ajustes» del asistente con «Con tu cuenta de Claude» elegido (revisión 6)
    const pedido = opts && typeof opts === 'object' && opts.proc;
    const proc = pedido && $(`input[name="proc"][value="${pedido}"]`, d);
    if (proc && !proc.checked) { proc.checked = true; proc.dispatchEvent(new Event('change', { bubbles: true })); marcar(); }
    const collect = () => {
      const p = $('#cProv', d).value;  // '' = sin elegir: no se manda (el servidor tampoco guarda uno vacío)
      const cloud = { ...(p ? { provider: p } : {}), chunk_minutes: +$('#cChunk', d).value, parallel: +$('#cPar', d).value };
      const key = $('#cKey', d)?.value.trim();
      if (key) cloud.api_key = key;
      if (p === 'azure') Object.assign(cloud, { azure_endpoint: $('#cAzEp', d).value, azure_deployment: $('#cAzDep', d).value, azure_api_version: $('#cAzVer', d).value });
      else if ($('#cModel', d)) cloud.model = $('#cModel', d).value;
      if (p === 'custom') cloud.base_url = $('#cBase', d).value;
      if (typeof collectDirectoSettings === 'function') Object.assign(cloud, collectDirectoSettings(d));
      S.selLang = $('#sLang', d).value;
      const out = { procesado: $('input[name="proc"]:checked', d)?.value, agente: { tipo: S.config.agente?.tipo || 'claude', modelo: $('#agModelo', d)?.value },
        cloud, threads: +$('#sThreads', d).value, prompt: $('#sPrompt', d).value.trim(), language: $('#sLang', d).value,
        live_auto: $('#lAuto', d).checked, live_preset: $('#lPreset', d)?.value, meeting_detect: $('#lMeet', d).checked,
        live_final_pass: $('#lFinal', d).checked, ...(window.reunionCollect?.(d) || {}),
        ...(typeof ajAlGrabarCollect === 'function' ? ajAlGrabarCollect(d) : {}),
        ...(typeof collectPerfilSettings === 'function' ? collectPerfilSettings(d) : {}),
        summary: { auto: ($('input[name="proc"]:checked', d)?.value === 'agente' ? $('#sAutoAg', d) : $('#sAuto', d)).checked, local: $('#sAutoLocal', d).checked, model: $('#sModel', d).value, lang: $('#sSumLang', d)?.value, extra: $('#sExtra', d)?.value } };
      // los motores, solo si se han cambiado aquí: un selector sin tocar, pintado con el modo de antes, devolvía el directo
      // a Whisper local tras elegir «API externa» y «Probar conexión» (07/11/2026)
      for (const [id, k] of [['#lPreset', 'live_preset'], ['#pfPreset', 'preset']]) {
        const el = $(id, d);
        if (el && el.value === el.dataset.ini) delete out[k];
      }
      // lo que se guarda solo (Outlook con su estado: «Activar» guarda al momento y Outlook puede tardar en contestar)
      // no lo pisa «Guardar» con lo que haya en la casilla (revisión 3 de B2)
      if ('seGuardaSolo' in ($('#rnOutlook', d)?.dataset || {})) delete out.outlook_on;
      return out;
    };
    // como se pintaron con lo guardado (la foto inicial): «Guardar» manda el motor solo si se ha cambiado aquí
    for (const id of ['#lPreset', '#pfPreset']) { const el = $(id, d); if (el) el.dataset.ini = String(inicial[el.id] ?? el.value); }
    $('#cTest', d).onclick = async () => {
      const res = $('#cTestRes', d);
      if (!$('#cProv', d).value) { res.innerHTML = '<span style="color:var(--err)">Elige antes el proveedor.</span>'; $('#cProv', d).focus(); return; }
      res.textContent = 'Probando…';
      try {
        // guarda el modo elegido y la nube (para probar con la clave escrita), no el resto del formulario: «Cancelar»
        // no deshace un guardado, y el resto sigue siendo «cambios sin guardar» (revisión del 07/11/2026)
        const x = collect(), enviado = foto();  // lo que se manda (si se guarda, el nuevo punto de partida)
        setConfig(await api('/api/settings', { procesado: x.procesado, cloud: x.cloud }));
        if (typeof motoresRefrescar === 'function') motoresRefrescar(d);  // la primera clave pone la nube
        procPintar();  // «Probar conexión» guarda: el modo elegido ya está aplicado
        ajTarjeta();
        // la nube que se acaba de guardar ya no es «sin guardar»; la clave, guardada, se enseña como tal
        const k = $('#cKey', d);
        if (k && S.config.cloud?.has_key) k.placeholder = `Guardada: ${S.config.cloud.api_key || ''} (escribe para cambiarla)`;
        if (k && k.value && k.value === enviado.cKey) k.value = '';  // (si se ha vuelto a escribir mientras, se queda)
        ponerAlDia(['cProv', 'cModel', 'cBase', 'cAzEp', 'cAzDep', 'cAzVer', 'cLiveModel', 'cLiveModelCustom', 'cChunk',
          'cPar', 'lPreset', 'pfPreset', '__claves'], enviado);
        marcar();
        const r = await api('/api/cloud/test', {});
        res.innerHTML = `<span style="color:var(--ok)">✓ Funciona (respuesta en ${String(r.seconds).replace('.', ',')} s)</span>`;
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ ${esc(err.message)}</span>`; }
    };
    const drawLinks = () => {
      const box = $('#sLinks', d), ls = S.config.links || [];
      box.innerHTML = ls.length ? `<table class="vtable">${ls.map(l => `<tr><td>${I.link}${esc(nfc(l.name))}</td><td><code>${esc(l.path)}</code></td>
          <td><button class="btn ghost sm" type="button" data-unlink="${esc(l.rel)}">quitar</button></td></tr>`).join('')}</table>`
        : `<div class="help" style="margin-top:0">Ninguna todavía: las grabaciones se guardan dentro de la carpeta de ${esc(APP_NAME)}${S.config.root ? ` (<code>${esc(S.config.root)}</code>)` : ''}.</div>`;
    };
    drawLinks();
    d.ajDibujarEnlaces = drawLinks;  // también tras «Otra carpeta (fuera de Tenca)…» (perfil.js)
    $('#lnAdd', d).onclick = async () => { if (await linkFolder()) drawLinks(); };
    // «quitar»: la confirmación va encima y, si se quita, la lista y «Reuniones sin proyecto», al día aquí mismo (si
    // era la elegida sin guardar, vuelve a lo guardado: ya no se puede elegir)
    d.addEventListener('click', async e => {
      const u = e.target.closest('[data-unlink]');
      if (!u || !await unlinkFolder(u.dataset.unlink)) return;
      drawLinks();
      $('#lnAdd', d)?.focus();  // su «quitar» ya no existe: el foco, en «Añadir» (no en el cuerpo de la página)
      const f = $('#pfFolder', d);
      if (f && typeof perfilFolderOpts === 'function') {
        const v = f.value, sigue = v === PERFIL_AUTO || flatFolders().some(x => x.path === v);
        f.innerHTML = perfilFolderOpts(sigue ? v : (S.config.perfil?.folder ?? PERFIL_AUTO));
        f.dataset.prev = f.value;
        marcar();
        d.ajPlanPedir?.();
      }
    });
    // las listas, aquí mismo: al abrir y tras «Importar listas…» o «Probar otra vez» (ajustes.js), sin cerrar Ajustes
    const drawVocab = () => {
      const box = $('#sVocab', d);
      if (!box || !document.body.contains(box)) return;
      // agrupado por proyecto: cada uno con sus temas y un boton para anadir otro
      const groups = {};
      S.vocab.forEach(v => (groups[v.project] ||= []).push(v));
      const conectar = typeof vocabConectar === 'function';
      box.innerHTML = `${Object.entries(groups).map(([p, vs]) => `<div class="vproj"><b>${I.folder}${esc(p)}${vs.every(v => v.conectada) ? '<span class="muted" style="font-weight:400" title="Sus listas se leen de una carpeta conectada (abajo)"> · carpeta conectada</span>' : ''}</b>
          <button class="btn ghost sm" type="button" data-vnew="${esc(p)}">${I.plus}Workstream</button>
          <button class="btn ghost sm" type="button" data-vdir="${esc(vs[0].id.split('/').slice(0, -1).join('/'))}" title="Abrir su carpeta de listas">${I.ext}Carpeta</button></div>
        <table class="vtable">${vs.map(v => `<tr><td>${esc(v.name)}</td><td>${vocabFit(v)}</td>
          <td><button class="btn ghost sm" type="button" data-vedit="${esc(v.id)}">editar</button></td></tr>`).join('')}</table>`).join('')}
        ${typeof perfilVocabEmptyHtml === 'function' && !S.vocab.length ? perfilVocabEmptyHtml() : ''}
        <div class="row" style="margin-top:10px"><button class="btn sm" type="button" data-vnew="">${I.plus}Nuevo proyecto</button>${conectar ? `<button class="btn sm" type="button" id="sVocabConectar" title="Elige la carpeta donde tienes tus listas: se leen al grabar, sin copiarlas">${I.folder}Usar las listas de una carpeta…</button>` : ''}${typeof perfilImportar === 'function' ? `<button class="btn ghost sm" type="button" id="sVocabImport" title="${esc(typeof VOCAB_COPIAR_TITLE === 'string' ? VOCAB_COPIAR_TITLE : '')}">Copiar listas de otra carpeta…</button>` : ''}</div>
        <div class="help">Cada workstream es una lista de nombres y siglas, separados por comas y <b>los más importantes primero</b>: Whisper lee como mucho ${S.vocab[0]?.limit || 223} tokens (unos 45 términos) y lo que no cabe se queda fuera por el final.</div>
        ${conectar ? `<label>Carpetas con tus listas <span class="muted" style="font-weight:400">· se leen en vivo al grabar: nada se copia</span></label><div id="sVocabCarpetas">${vocabCarpetasHtml(S.vocabCarpetas)}</div>` : ''}`;
    };
    d.ajPintarListas = drawVocab;
    loadVocab(S.config.language ?? 'es-en').then(drawVocab);
    const redrawVocab = () => loadVocab(S.config.language ?? 'es-en').then(drawVocab);
    d.addEventListener('click', e => {
      const ed = e.target.closest('[data-vedit]'), nw = e.target.closest('[data-vnew]'), r = e.target.closest('[data-vdir]');
      const vo = e.target.closest('[data-vopen]'), vu = e.target.closest('[data-vunlink]');
      if (e.target.closest('#sVocabImport') && typeof perfilImportar === 'function') perfilImportar('', drawVocab);
      if (e.target.closest('#sVocabConectar') && typeof vocabConectar === 'function') vocabConectar(redrawVocab);
      if (vu && typeof vocabDesconectar === 'function') {
        vocabDesconectar(vu.dataset.vunlink, () => {
          redrawVocab();
          if (typeof perfilVocabRedraw !== 'function') return;
          perfilVocabRedraw(d);
          // lo guardado ha cambiado en el servidor (el tema, a automático): el punto de partida también, o «Hay
          // cambios sin guardar» (y la pregunta antes de un subdiálogo) lo darían por cambiado sin serlo
          const g = S.config?.perfil?.vocab;
          inicial.pfVocab = g == null ? PERFIL_AUTO : g;
          marcar();
        });
      }
      // editar o crear una lista sale de Ajustes: antes, si hay algo sin guardar, se pregunta
      if (ed) d.ajAntesDeSalir().then(ok => ok && openVocabEditor(S.vocab.find(v => v.id === ed.dataset.vedit)));
      if (nw) d.ajAntesDeSalir().then(ok => ok && openVocabEditor(null, openSettings, nw.dataset.vnew));
      const rev = r ? r.dataset.vdir : vo ? vo.dataset.vopen : null;
      if (rev != null) api('/api/reveal', { path: rev }).catch(err => toast(esc(err.message), 'err'));
    });
    $('#xExport', d).onclick = async () => {
      const res = $('#xRes', d);
      res.textContent = 'Creando…';
      try {
        const r = await api('/api/export', { vocab: $('#xVocab', d).checked });
        res.innerHTML = `<span style="color:var(--ok)">✓ Creado en el escritorio (${(r.size / 1e6).toFixed(1).replace('.', ',')} MB${r.vocab ? ` · con ${r.vocab} listas de vocabulario` : ''})</span>`;
      } catch (err) { res.innerHTML = `<span style="color:var(--err)">✖ ${esc(err.message)}</span>`; }
    };
    // «Guardar» (cierra Ajustes) y «Guardar y seguir» (no lo cierra: lo que sigue ocupa su hueco). true si se guardó
    const guardar = async (cerrar = true) => {
      try {
        const c = await api('/api/settings', collect());
        setConfig(c); localStorage.setItem('procElegido', '1');
        if (cerrar) closeModal(true);
        toast('Ajustes guardados'); refreshView();
        if (c.descarga) toast(`Descargando el modelo ${esc(c.descarga)} de Whisper para transcribir en este PC: el progreso, en Ajustes`);
        return true;
      } catch (err) { toast(esc(err.message), 'err'); return false; }
    };
    d.addEventListener('click', e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m === 'no') return closeModal(false);
      guardar();
    });
  }, 'aj');
}
// ⚙ abre siempre en «Al grabar» (rediseño B2): lo que se usará al grabar, arriba. Volver de un subdiálogo (editar una
// lista, el asistente…) sí vuelve a la sección en la que estabas (openSettings sin sección)
$('#btnSettings').onclick = () => openSettings({ sec: 'algrabar' });

/* ---------------------------------------------------------------- apagar */
async function confirmShutdown() {
  const busy = S.jobs.some(isActive);
  const ok = await ask(`Apagar ${APP_NAME}`, `<p>Se cierra la app del todo: la ventana y el motor que corre en segundo plano. El atajo Ctrl+Alt+R dejará de funcionar hasta que la vuelvas a abrir con el acceso directo.</p>${busy ? '<p><b>Hay transcripciones en marcha</b>: se cancelan (lo ya escrito se conserva) y podrás relanzarlas al volver.</p>' : ''}`, 'Apagar');
  // desde Ajustes, la confirmación va encima: al cancelar, Ajustes sigue ahí, con lo escrito
  if (!ok) return;
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
  document.body.innerHTML = `<div class="page" style="max-width:520px;margin:18vh auto;text-align:center"><img src="/tenca.png" alt="" style="width:72px;height:72px">
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
/* settings.json no se pudo leer al arrancar (server.py, Store): se copió como settings.json.roto-<fecha> y se rescató
   lo que se pudo. Se avisa al abrir la app y arriba en Ajustes. */
function settingsBrokenHtml(b) {
  const rec = (b.recuperado || []).length ? `Se han recuperado ${esc(b.recuperado.join(', '))}; lo demás vuelve a los valores por defecto.` : 'Tus ajustes vuelven a los valores por defecto.';
  return `${I.info}No se han podido leer tus ajustes: settings.json estaba dañado. ${b.copia
    ? `Hay una copia tal como estaba, <b>${esc(b.copia)}</b>, en la carpeta de datos (Ajustes → Acerca de → Abrir).`
    : 'No se ha podido hacer una copia, así que en esta sesión los cambios de Ajustes no se guardan en disco.'} ${rec} Revisa Ajustes.`;
}
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
/* La pagina la sirve un proceso y /api/config la contesta otro: una version anterior de la app que seguia abierta al
   actualizar en el sitio (o un acceso directo viejo). Sin app_name ni perfil la API es anterior al 01/11/2026, y el
   titulo sin sustituir ({{APP_NAME}}) delata que quien sirve el HTML no es el servidor de esta version. Antes
   (02/11/2026) los botones no hacian nada y nadie decia por que. */
function otraVersion(c) { return !c || !c.app_name || !c.perfil || /\{\{\s*APP_NAME\s*\}\}/.test(document.title); }
function otraVersionHtml(c) {
  const nombre = (c && c.app_name) || (/\{\{/.test(APP_NAME) ? 'la app' : APP_NAME);
  return `<div class="page"><div class="card empty">${I.info}Hay abierta una <b>versión anterior</b> de ${esc(nombre)}${c && c.version ? ` (${esc(String(c.version))})` : ''} y esta ventana se ha conectado a ella, así que no funciona bien.<br>Ciérrala (su ventana de consola o, en el Administrador de tareas, <code>pythonw.exe</code>) y vuelve a abrir ${esc(nombre)} desde el acceso directo.</div></div>`;
}
/* Un error de JavaScript no se traga en silencio: un clic que no hace nada pasa a ser un aviso con el motivo
   (02/11/2026: ocho minutos de botones muertos sin un solo mensaje). Cada mensaje distinto, una vez. */
const errVistos = new Set();
function avisoError(msg) {
  const m = String(msg || 'error').slice(0, 300);
  if (errVistos.has(m)) return;
  errVistos.add(m);
  try { toast(`Algo ha fallado en la interfaz: ${esc(m)}. Si se repite, cierra la ventana y vuelve a abrir ${esc(APP_NAME)}.`, 'err'); } catch { /* sin toast aun */ }
}
window.addEventListener('error', e => avisoError(e.message || (e.error && e.error.message)));
window.addEventListener('unhandledrejection', e => avisoError((e.reason && e.reason.message) || e.reason));

/* Mientras /api/config no contesta, se dice (con los segundos) en vez de dejar la pagina en blanco. */
function esperandoHtml(seg) {
  return `<div class="page"><div class="card empty">Arrancando… el servidor de ${esc(APP_NAME)} tarda en contestar (${seg} s).<br><span class="muted">Si pasa de un minuto, mira <code>%LOCALAPPDATA%\\AudiosApp\\server.log</code> o cierra la ventana y vuelve a abrir ${esc(APP_NAME)}.</span></div></div>`;
}
async function init() {
  const t0 = Date.now();
  const lento = setInterval(() => { if (!S.config) main.innerHTML = esperandoHtml(Math.round((Date.now() - t0) / 1000)); }, 3000);
  try {
    setConfig(await api('/api/config'));
    clearInterval(lento);
    if (otraVersion(S.config)) { main.innerHTML = otraVersionHtml(S.config); return; }
    if (S.config.settings_broken) toast(settingsBrokenHtml(S.config.settings_broken), 'err', openSettings);
    const r = await api('/api/tree');
    S.version = r.version;
    indexTree(r.tree);
    await loadVocab();  // temas (<proyecto>/Vocabulario/*.txt…) para los selectores
  } catch (err) {
    clearInterval(lento);
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
