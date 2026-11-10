/* Directo en la nube (Groq): cupo y latencia en la cabecera del directo, «modo seguir» (letra grande y el texto
   siempre abajo), traduccion al espanol o al ingles (msg 1152) por reunion en una columna lateral y, en Ajustes, el
   modelo del directo y las claves adicionales. Todo sobre j.directo (lo publica live.py en /api/jobs), /api/config y
   /api/live/traducir.
   Sin dependencias: su CSS se inyecta aqui mismo para no tocar app.css. Nunca ensena ni guarda claves (las mascaras
   vienen del servidor). */
I.eye = '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
I.translate = '<svg viewBox="0 0 24 24"><path d="M4 5h9M8.5 3v2M11 5c-.6 3.6-2.6 6.4-6 8.5M6 8.5c1 2.1 2.6 3.8 4.8 5"/><path d="M13 20l4.5-11L22 20M14.6 16.5h5.8"/></svg>';
S.directo = {
  seguir: localStorage.getItem('directoSeguir') === '1',
  seen: 0, lastFrom: null, txPath: null,  // frases ya vistas y donde empieza el ultimo trozo llegado
  atBottom: true,                        // el usuario estaba abajo del todo antes del ultimo repintado
  pausedFor: null,                       // id del directo del que ya se aviso la pausa por cupo
  tradN: -1,                             // bloques de traduccion ya pintados (para el autoscroll del aside)
};

(function directoCss() {
  const css = `
.directo-bar { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; margin-top: 10px; font-size: 12.5px; color: var(--muted); }
.directo-bar .sp { flex: 1; }
.directo-bar .db-item { white-space: nowrap; }
.directo-bar .db-trad { font-weight: 500; cursor: pointer; }
.directo-bar .db-trad input { margin: 0 2px 0 0; accent-color: var(--accent); }
.directo-bar .db-trad-grupo { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.directo-bar .db-trad-to { height: 28px; font: inherit; font-weight: 500; padding: 0 6px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); }
.directo-bar .btn.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.directo-bar .db-err { flex-basis: 100%; color: var(--err); }
.directo-bar .pill.err { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; border-radius: 11px; font-size: 12px; font-weight: 600; background: var(--err-soft); color: var(--err); }
.directo-bar .btn svg { width: 16px; height: 16px; margin-right: 5px; }
.db-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); margin-left: 4px; animation: dots 1.2s infinite; }
#txBody .s.directo-last { background: var(--accent-soft); box-shadow: 0 0 0 2px var(--accent-soft); }
/* modo seguir: para leer de reojo durante la reunion */
.directo-seguir .live-page #txBody { font-size: 23px; line-height: 1.6; max-width: 900px; }
.directo-seguir .live-page #txBody .ts { font-size: 13px; vertical-align: 4px; }
.directo-seguir .live-page #txBody .s.directo-last { background: color-mix(in srgb, var(--accent) 18%, transparent); box-shadow: none; padding: 0 2px; border-radius: 6px; }
.directo-seguir .live-page .typing { font-size: 16px; margin: 8px 0 20px; }
.directo-seguir .live-page .typing i { width: 9px; height: 9px; }
/* traduccion: dos columnas, la transcripcion a la izquierda y la traduccion fija a la derecha */
.live-page.directo-dos { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 0 28px; max-width: 1400px; }
.live-page.directo-dos > * { grid-column: 1 / -1; min-width: 0; }
.live-page.directo-dos > #aTx { grid-column: 1; }
.live-page.directo-dos > #directoTrad { grid-column: 2; }
.directo-trad { position: sticky; top: 0; align-self: start; max-height: calc(100vh - 120px); overflow: auto; margin-top: 22px; padding: 14px 16px 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); font: 15px/1.6 var(--font-read); }
.directo-trad .dt-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 10px; font-size: 12.5px; }
.directo-trad .dt-head b { font-size: 13px; color: var(--ink); display: inline-flex; align-items: center; gap: 6px; }
.directo-trad .dt-note { font-size: 12px; margin: 4px 0 8px; }
.directo-trad p { margin: 0 0 10px; }
.directo-trad p.dt-last { background: var(--accent-soft); border-radius: 6px; padding: 2px 4px; margin-left: -4px; margin-right: -4px; }
.directo-trad .ts { display: inline-block; font: 12px var(--mono); color: var(--accent); background: var(--accent-soft); padding: 0 6px; border-radius: 5px; margin-right: 8px; vertical-align: 2px; }
.directo-trad .db-err { color: var(--err); font-size: 12.5px; margin-top: 6px; }
.directo-seguir .directo-trad { font-size: 20px; }
@media (max-width: 1100px) {
  .live-page.directo-dos { grid-template-columns: minmax(0, 1fr); }
  .live-page.directo-dos > #directoTrad { grid-column: 1; position: static; max-height: 40vh; }
}
/* ajustes: claves adicionales */
.dk-row { margin-top: 6px; }
.dk-row .input { flex: 1; }`;
  const st = document.createElement('style');
  st.id = 'directoCss';
  st.textContent = css;
  document.head.appendChild(st);
})();

/* ---------------------------------------------------------------- helpers puros (se prueban con node) */
function directoFmtWait(seg) {
  seg = Math.max(0, Math.round(+seg || 0));
  if (seg >= 3600) return `${Math.floor(seg / 3600)} h ${String(Math.floor(seg % 3600 / 60)).padStart(2, '0')} min`;
  if (seg >= 120) return `${Math.floor(seg / 60)} min`;
  return `${seg} s`;
}

function directoQuotaText(q) {
  if (!q || !q.daily) return '';
  let t;
  if (q.blocked_for > 0) t = `Groq pide esperar ${directoFmtWait(q.blocked_for)}`;
  else if (q.remaining == null) t = 'Groq: cupo sin medir aún';
  else {
    t = `Groq: quedan ${q.remaining} peticiones hoy`;
    if (q.reset_in > 0) t += ` · se renuevan en ${directoFmtWait(q.reset_in)}`;
  }
  if (q.keys > 1) {
    t += ` · clave ${q.active || 1}/${q.keys}`;
    if (q.others_remaining > 0) t += ` · +${q.others_remaining} en otras claves`;
  }
  if (q.invalid > 0) t += ` · ${q.invalid} ${q.invalid === 1 ? 'clave no válida' : 'claves no válidas'}`;
  return t;
}

/* destinos de la traduccion del directo (live.TRANSLATE_TO) y como se nombran */
const DIRECTO_TRAD_TO = [['es', 'español'], ['en', 'inglés']];
function directoTradTo(d) { return d && d.translate_to === 'en' ? 'en' : 'es'; }
function directoTradNombre(to) { return (DIRECTO_TRAD_TO.find(([v]) => v === to) || DIRECTO_TRAD_TO[0])[1]; }

/* traduccion del directo: con que modelo y en que proveedor de verdad («gpt-oss-20b en Groq», «gpt-4o-mini en OpenAI») */
function directoTradLabel(d) {
  const m = String((d && d.translate_model) || '').replace(/^openai\/(?=gpt-oss)/, '');
  const p = (d && d.provider) || '';
  return m ? (p ? `${m} en ${p}` : m) : '';
}

function directoLatencyText(lat) {
  if (!lat || !lat.n) return 'midiendo…';
  const f = x => String(Math.round((+x || 0) * 10) / 10).replace('.', ',');
  let t = `texto a los ${f(lat.p50)} s (p95 ${f(lat.p95)} s)`;
  const ph = lat.phases || {};  // por fases: cuanto es red/Groq (y cupo) y cuanto queda de este lado
  if (ph.net) t += ` · red ${f(ph.net.p50)} s` + (ph.infer ? ` (inferencia ${f(ph.infer.p50)} s)` : '');
  if (ph.wait && ph.wait.p50 >= 0.05) t += ` · cupo ${f(ph.wait.p50)} s`;
  return t;
}

/* ---------------------------------------------------------------- barra del directo (dentro del hero) */
function renderDirectoBar(a, j) {
  const el = $('#directoBar');
  if (!el) return;
  const d = j && j.directo;
  if (!j || !d || !d.cloud) {  // en local no hay cupo ni latencia que ensenar
    if (el.innerHTML) el.innerHTML = '';
    el.dataset.sig = '';
    return;
  }
  const quota = directoQuotaText(d.quota);
  const lat = directoLatencyText(d.latency);
  const tradOk = d.translate_ok !== false, tradLabel = directoTradLabel(d);  // sin chat (Azure): la casilla, apagada
  const to = directoTradTo(d);
  const sig = JSON.stringify([j.id, quota, lat, d.pace, d.paused, d.translate, to, d.translate_error, d.translating, d.translate_ok, d.translate_model, S.directo.seguir]);
  // «Traducir al ▾ español / inglés»: la casilla enciende y apaga; el destino se recuerda para esta reunión
  const toSel = `<select class="db-trad-to" id="directoTradTo" data-id="${esc(j.id)}" aria-label="Idioma al que se traduce"${tradOk ? '' : ' disabled'}>${DIRECTO_TRAD_TO.map(([v, l]) => `<option value="${v}"${v === to ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
  if (el.dataset.sig === sig) return;  // sin cambios: no se repinta (no se pisa el clic en la casilla)
  // con el desplegable del destino abierto o con el foco, repintar lo cerraría a mitad de elegir: se espera
  if (document.activeElement && document.activeElement.id === 'directoTradTo' && el.contains(document.activeElement)) return;
  el.dataset.sig = sig;
  el.innerHTML = `<div class="directo-bar">
    ${d.paused ? '<span class="pill err">Directo en pausa por cupo</span>' : ''}
    ${quota ? `<span class="db-item" title="Cupo gratuito de Groq: peticiones por día y por cuenta">${esc(quota)}</span>` : ''}
    ${d.pace ? `<span class="db-item" title="Tamaño de los trozos de audio que se mandan: más largos cuando queda menos cupo">trozos de ${d.pace[0]}–${d.pace[1]} s</span>` : ''}
    <span class="db-item" title="Del final de cada trozo de audio al texto en pantalla (mediana y percentil 95 de esta sesión)">${esc(lat)}</span>
    <span class="sp"></span>
    <span class="db-trad-grupo">${tradOk ? `<label class="btn sm db-trad" title="${esc(`Traduce al idioma elegido (español o inglés) lo último que se ha dicho, cada 10–15 s${tradLabel ? `, con ${tradLabel}` : ''}. Solo para esta reunión; no se guarda.`)}"><input type="checkbox" id="directoTradChk" data-id="${esc(j.id)}"${d.translate ? ' checked' : ''}> Traducir al${d.translating ? '<i class="db-dot" title="traduciendo…"></i>' : ''}</label>`
      : `<label class="btn sm db-trad" title="${esc(d.translate_why || 'La traducción necesita un servicio de chat en la nube.')}" aria-disabled="true"><input type="checkbox" id="directoTradChk" data-id="${esc(j.id)}" disabled> Traducir al</label>`}${toSel}</span>
    <button class="btn sm${S.directo.seguir ? ' on' : ''}" id="directoSeguir" type="button" title="Letra grande y el texto siempre abajo del todo: para leer de reojo durante la reunión">${I.eye}Modo seguir</button>
    ${d.translate_error ? `<div class="db-err">Traducción: ${esc(d.translate_error)}</div>` : ''}
  </div>`;
}

function directoScrollEnd() {
  main.scrollTo({ top: main.scrollHeight, behavior: 'smooth' });
}

function directoSetSeguir(on) {
  S.directo.seguir = !!on;
  localStorage.setItem('directoSeguir', on ? '1' : '0');
  document.body.classList.toggle('directo-seguir', !!on);
  const b = $('#directoSeguir');
  if (b) b.classList.toggle('on', !!on);
  if (on) { S.userScroll = 0; directoScrollEnd(); }
}
document.body.classList.toggle('directo-seguir', S.directo.seguir);

document.addEventListener('click', e => {
  const b = e.target.closest('#directoSeguir');
  if (b) directoSetSeguir(!S.directo.seguir);
});

/* la casilla (encender o apagar) y el destino: los dos mandan {id, on, to}. El destino se recuerda para esta reunión
   aunque la traducción esté apagada; con ella encendida, lo último se vuelve a traducir al idioma nuevo. */
document.addEventListener('change', async e => {
  const chk = e.target.id === 'directoTradChk', sel = e.target.id === 'directoTradTo';
  if (!chk && !sel) return;
  const c = $('#directoTradChk'), s = $('#directoTradTo');
  const on = chk ? e.target.checked : !!(c && c.checked), id = e.target.dataset.id;
  const prevTo = s && s.dataset.prev ? s.dataset.prev : null;
  const to = s ? s.value : 'es';
  if (sel) e.target.blur();  // elegido: la barra vuelve a ponerse al día (renderDirectoBar no repinta con el foco ahí)
  try {
    const j = await api('/api/live/traducir', { id, on, to });
    if (j.directo) Object.assign(j.directo, { translate: on, translate_to: to });  // live.py lo publica en el siguiente sondeo
    if (on) toast(chk ? `Traducción al ${directoTradNombre(to)} activada: aparece a la derecha en unos 10–15 s`
      : `Ahora se traduce al ${directoTradNombre(to)}: lo último sale en unos segundos`);
    const a = curAudio();
    if (a) { renderDirectoBar(a, j); renderDirectoTrad(j); }
    pollJobs(true);
  } catch (err) {
    if (chk) e.target.checked = !on;
    else if (prevTo) e.target.value = prevTo;
    toast(esc(err.message), 'err');
  }
});
document.addEventListener('focusin', e => { if (e.target.id === 'directoTradTo') e.target.dataset.prev = e.target.value; });

/* cada sondeo de /api/jobs (lo llama onJobs de app.js, antes de recargar el texto del directo) */
function onDirectoJobs(prev) {
  const a = curAudio(), j = a && liveJob(a);
  S.directo.atBottom = main.scrollHeight - main.scrollTop - main.clientHeight < 160;
  if (!a || !j) {
    S.directo.pausedFor = null;
    renderDirectoTrad(null);
    return;
  }
  const d = j.directo;
  if ($('#directoBar')) renderDirectoBar(a, j);
  renderDirectoTrad(j);
  renderDirectoAvisos();  // «Lo último suena a …» llega con /api/jobs
  const paused = !!(d && d.paused);
  if (paused && S.directo.pausedFor !== j.id) {  // una vez por pausa
    S.directo.pausedFor = j.id;
    toast(esc(j.stage || 'Directo en pausa por falta de cupo en Groq'), 'err');
  } else if (!paused && S.directo.pausedFor === j.id) S.directo.pausedFor = null;
}

/* tras cada repintado del cuerpo en directo (lo llama renderTxBody de app.js): ultimo trozo resaltado + autoscroll */
function directoAfterRender(tx, lj) {
  const body = $('#txBody');
  if (!body || !tx) return;
  const n = tx.segs.length;
  const st = S.directo;
  if (st.txPath !== tx.path || n < st.seen) {  // otra reunion, o «rehacer desde el principio»: se empieza de cero
    st.txPath = tx.path; st.seen = n; st.lastFrom = null;
  } else if (n > st.seen) {  // llego un trozo nuevo: desde la primera frase nueva
    st.lastFrom = st.seen; st.seen = n;
  }
  if (st.lastFrom != null) {
    $$('.s', body).forEach(s => { if (+s.dataset.i >= st.lastFrom) s.classList.add('directo-last'); });
  }
  const recent = Date.now() - S.userScroll < 4000;  // acaba de subir a leer algo: no se le arrastra abajo
  if (lj && !recent && (st.seguir || st.atBottom)) requestAnimationFrame(directoScrollEnd);
}

/* ---------------------------------------------------------------- traduccion al espanol o al ingles (columna lateral) */
function renderDirectoTrad(j) {
  const page = $('.live-page'), tx = $('#aTx');
  let aside = $('#directoTrad');
  const d = j && j.directo;
  const blocks = (d && d.translation) || [];
  const want = !!(page && tx && d && (d.translate || blocks.length));
  if (!want) {
    if (aside) aside.remove();
    if (page) page.classList.remove('directo-dos');
    S.directo.tradN = -1;
    return;
  }
  if (!aside) {
    aside = document.createElement('aside');
    aside.id = 'directoTrad';
    aside.className = 'directo-trad';
    tx.insertAdjacentElement('afterend', aside);
    S.directo.tradN = -1;
  }
  page.classList.add('directo-dos');
  const n = d.translation_n || blocks.length, to = directoTradTo(d);
  // translation_epoch: al cambiar el destino o el idioma de la reunión se quitan bloques y llegan otros (pueden ser
  // los mismos en número)
  const sig = [j.id, n, d.translating ? 1 : 0, d.translate ? 1 : 0, d.translate_error || '', to, d.translation_epoch || 0].join('|');
  if (aside.dataset.sig === sig) return;
  const atEnd = S.directo.tradN < 0 || aside.scrollHeight - aside.scrollTop - aside.clientHeight < 120;
  aside.dataset.sig = sig;
  const tl = directoTradLabel(d);
  aside.innerHTML = `<div class="dt-head"><b>${I.translate}Traducción al ${directoTradNombre(to)}</b><span class="muted">${tl ? `${esc(tl)} · ` : ''}una ayuda para seguir la reunión, no se guarda</span></div>
    ${n > blocks.length ? '<div class="muted dt-note">(bloques anteriores no mostrados)</div>' : ''}
    ${blocks.length ? blocks.map((b, i) => `<p${i === blocks.length - 1 ? ' class="dt-last"' : ''}><span class="ts">${fmtClock(b.start)}</span>${esc(b.text)}</p>`).join('')
      : `<div class="muted dt-note">${d.translate ? 'Traduciendo lo que se vaya diciendo…' : ''}</div>`}
    ${d.translating ? '<div class="typing"><i></i><i></i><i></i><span>traduciendo…</span></div>' : ''}
    ${d.translate_error ? `<div class="db-err">${esc(d.translate_error)}</div>` : ''}`;
  if (n > S.directo.tradN && atEnd) aside.scrollTop = aside.scrollHeight;
  S.directo.tradN = n;
}

/* ---------------------------------------------------------------- Ajustes: modelo del directo y claves adicionales */
/* Las claves adicionales son de un proveedor: se guardan con el y solo se usan con el. Al cambiar de proveedor en
   Ajustes se ven las de ese proveedor; las de los demas se conservan y no se envian a ningun otro servicio. */
function directoProvName(p) {
  if (p === 'custom') return 'tu API compatible («Otra»)';
  return (S.config && S.config.providers && S.config.providers[p] && S.config.providers[p].label) || p;
}

function directoKeyRowHtml(mask, prov) {
  return `<div class="row dk-row"><input class="input" type="password" data-xkey autocomplete="off" spellcheck="false" placeholder="${mask ? `Guardada: ${esc(mask)} (escribe para cambiarla)` : `Pega aquí la API key de otra cuenta de ${esc(directoProvName(prov || 'groq'))}`}"${mask ? ` data-mask="${esc(mask)}"` : ''}><button class="btn ghost sm" type="button" data-xkey-del title="Quitar esta clave">quitar</button></div>`;
}

/* texto de a quien pertenecen las claves (puro: se prueba con node). rows: {proveedor: [{mask, value}]} */
function directoOwnerText(p, rows) {
  if (!p) return 'Elige el proveedor en Procesado para añadir claves de otras cuentas.';
  const n = rs => (rs || []).filter(r => r.mask || String(r.value || '').trim()).length;
  let t = `Estas claves son solo de ${directoProvName(p)}: se usan con ${directoProvName(p)} y no se envían a ningún otro proveedor.`;
  Object.keys(rows || {}).filter(k => k !== p && n(rows[k])).forEach(k => {
    const c = n(rows[k]);
    t += ` Tienes ${c} ${c === 1 ? 'clave adicional' : 'claves adicionales'} de ${directoProvName(k)}: se conservan y solo se usan con ${directoProvName(k)}.`;
  });
  return t;
}

function directoSettingsHtml(c) {
  const lm = c.live_model || 'whisper-large-v3-turbo';
  const models = [['whisper-large-v3-turbo', 'whisper-large-v3-turbo · más rápido por trozo'], ['whisper-large-v3', 'whisper-large-v3 · más preciso, algo más lento']];
  const quota = c.quota ? directoQuotaText(c.quota) : '';
  return `<div id="dkModel"><label>Modelo del directo</label>
      <select id="cLiveModel">${models.map(([v, l]) => `<option value="${v}"${v === lm ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <div class="help">El directo manda trozos de 3–6 s; el repaso final al terminar usa el modelo de arriba.</div></div>
    <div id="dkModelCustom" class="hidden"><label>Modelo del directo</label>
      <input class="input" id="cLiveModelCustom" list="cLiveModels" value="${esc(c.live_model_custom || '')}" placeholder="Vacío: el mismo modelo de transcripción de arriba">
      <datalist id="cLiveModels">${['whisper-large-v3-turbo', 'whisper-large-v3', 'whisper-1'].map(m => `<option value="${m}">`).join('')}</datalist>
      <div class="help">Si tu API tiene un modelo más rápido para trozos cortos, escríbelo aquí tal como lo llama ella. Vacío, el directo usa el de arriba.</div></div>
    <label>Claves adicionales (opcional)</label>
    <div id="dkRows"></div>
    <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="dkAdd">${I.plus}Añadir clave</button></div>
    <div class="notice">Cada clave es de una cuenta distinta: el audio y el texto se procesan bajo esa cuenta.</div>
    <div class="help" id="dkOwner"></div>
    <div class="help">Se pasa a la siguiente cuando a la clave activa le quedan menos de 20 peticiones del día (si tu proveedor las limita por día), cuando el servicio pide esperar más de un minuto o cuando una clave no es válida. También las usan el resumen y el repaso final.</div>
    ${quota ? `<div class="help" id="dkQuota">${esc(quota)}</div>` : ''}`;
}

function bindDirectoSettings(d) {
  const rows = $('#dkRows', d), add = $('#dkAdd', d), prov = $('#cProv', d), model = $('#dkModel', d);
  const modelC = $('#dkModelCustom', d), owner = $('#dkOwner', d);
  if (!rows || !add) return;
  const c = (S.config && S.config.cloud) || {};
  const saved = c.extra_keys_by || { [c.provider || 'groq']: c.extra_keys || [] };
  // filas por proveedor: lo escrito sin guardar no se pierde al cambiar de proveedor, y nunca pasa a otro
  const state = {};
  Object.keys(saved).forEach(p => { state[p] = (saved[p] || []).map(m => ({ mask: m, value: '' })); });
  let cur = prov ? prov.value : (c.provider || 'groq');
  const stash = () => { state[cur] = $$('[data-xkey]', rows).map(i => ({ mask: i.dataset.mask || '', value: i.value })); };
  const draw = () => {
    const rs = state[cur] || [];
    rows.innerHTML = rs.map(r => directoKeyRowHtml(r.mask, cur)).join('');
    $$('[data-xkey]', rows).forEach((i, n) => { i.value = rs[n] ? rs[n].value : ''; });
  };
  const sync = () => {
    add.disabled = !cur || $$('[data-xkey]', rows).length >= 10;  // sin proveedor elegido, no hay de quién
    if (model) model.classList.toggle('hidden', cur !== 'groq');  // la lista de modelos es la de Whisper en Groq
    if (modelC) modelC.classList.toggle('hidden', cur !== 'custom');  // una pasarela propia: el suyo (o el de arriba)
    if (owner) { stash(); owner.textContent = directoOwnerText(cur, state); }
  };
  add.onclick = () => {
    if (add.disabled) return;
    rows.insertAdjacentHTML('beforeend', directoKeyRowHtml('', cur));
    sync();
    $$('[data-xkey]', rows).pop().focus();
  };
  rows.addEventListener('click', e => {
    const b = e.target.closest('[data-xkey-del]');
    if (b) { b.closest('.dk-row').remove(); sync(); }
  });
  if (prov) prov.addEventListener('change', () => { stash(); cur = prov.value; draw(); sync(); });
  d.directoKeys = { state, stash: () => stash() };  // collectDirectoSettings manda las de cada proveedor
  draw();
  sync();
}

function collectDirectoSettings(d) {
  const out = {};
  const sel = $('#cLiveModel', d);
  if (sel) out.live_model = sel.value;
  const lc = $('#cLiveModelCustom', d);
  if (lc) out.live_model_custom = lc.value.trim();
  const k = d.directoKeys;
  if (k) {
    k.stash();
    // las de cada proveedor por separado: lo escrito (clave nueva) o, si la fila esta vacia y era una guardada, su
    // mascara (= conservarla). El servidor no deja que una clave pase de un proveedor a otro
    out.extra_keys_by = {};
    Object.keys(k.state).forEach(p => {
      out.extra_keys_by[p] = k.state[p].map(r => String(r.value || '').trim() || r.mask || '').filter(Boolean);
    });
  }
  return out;
}

/* ---------------------------------------------------------------- carpeta en pleno directo (msg 1198) */
/* «Se guarda en … · Cambiar» en la cabecera del directo. La grabación sigue donde empezó; lo elegido se ve como «Se
   moverá a … al terminar · Deshacer» y, cuando ya no queda nada en marcha (directo, repaso final, voces y resumen), el
   servidor mueve la reunión entera (POST /api/reunion/destino, App._aplicar_movimientos). Lo pendiente y lo hecho
   llegan con /api/jobs (movimientos); al moverse, la página pasa sola a la ruta nueva. */
S.movs = { pendientes: {}, hechos: [] };
S.movsVistos = new Set();
S.movsDesde = Date.now() / 1000;

(function destinoCss() {
  const st = document.createElement('style');
  st.id = 'destinoCss';
  st.textContent = `
.dest-line { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin: 10px 0 2px; font-size: 13px; color: var(--muted); }
.dest-line svg { width: 16px; height: 16px; flex: none; }
.dest-line code { font: 12.5px var(--mono); color: var(--ink); background: none; padding: 0; word-break: break-all; }
.dest-line.pend { background: var(--accent-soft); border-radius: 8px; padding: 6px 10px; color: var(--ink); }
.dest-line.pend code { color: var(--accent); }
.dest-line .dest-sub, .dest-line .dest-err { flex-basis: 100%; font-size: 12px; color: var(--muted); }
.dest-line .dest-err { color: var(--err); }
.dest-pop { position: fixed; z-index: 60; min-width: 300px; max-width: 440px; max-height: 70vh; overflow: auto; padding: 8px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); box-shadow: 0 10px 30px rgba(0,0,0,.18); }
.dest-pop .dp-t { font-weight: 600; font-size: 13px; padding: 4px 8px 6px; color: var(--ink); }
.dest-pop .dp-it { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 7px 8px; border: 0; border-radius: 8px; background: none; color: var(--ink); font: inherit; font-size: 13.5px; cursor: pointer; }
.dest-pop .dp-it:hover, .dest-pop .dp-it.on { background: var(--accent-soft); }
.dest-pop .dp-it svg { width: 16px; height: 16px; flex: none; }
.dest-pop .dp-it span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dest-pop .dp-it small { color: var(--muted); font-size: 12px; }
.dest-pop .dp-n { font-size: 12px; color: var(--muted); padding: 8px 8px 2px; border-top: 1px solid var(--line); margin-top: 6px; }`;
  document.head.appendChild(st);
})();

/* Chuleta\Proyecto Alfa\2026-11-07 Comité\: la raíz con el nombre que tiene en el disco y las vinculadas con el
   suyo, igual que la tarjeta de Grabar y los avisos (server.ruta_txt) */
function destinoRel(rel) {
  const parts = rel ? rel.split('/') : [];
  const raiz = parts.length && parts[0].startsWith(':') ? '' : rootName() + '\\';
  return raiz + parts.map((_, i) => nodeName(parts.slice(0, i + 1).join('/')) + '\\').join('');
}
/* la carpeta principal donde está ahora: la que contiene su carpeta (una carpeta por reunión) o la suya */
function destinoActual(a) {
  const f = S.folders.get(a.folder);  // la de la reunión (la creó la app, aunque tenga más cosas): la que la contiene
  return f && (f.mia || f.por_reunion) ? (f.parent ? f.parent.path : '') : a.folder;
}
/* lo que obliga a repintar la cabecera del directo (liveHeroSig) */
function destinoSig(a) {
  const p = S.movs.pendientes[a.path];
  return p ? `|${p.destino}|${p.error || ''}` : '';
}
function destinoPendienteHtml(a) {
  const p = S.movs.pendientes[a.path];
  if (!p) return '';
  return `<div class="dest-line pend" data-dest="${esc(a.path)}">${I.folder}<span>Se moverá a</span><code>${esc(p.display)}</code>`
    + `<span>al terminar</span><button class="btn ghost sm" type="button" data-dest-act="deshacer" data-path="${esc(a.path)}">Deshacer</button>`
    + (p.error ? `<div class="dest-err">No se ha podido mover todavía: ${esc(p.error)} Se sigue intentando.</div>`
      : '<div class="dest-sub">Audio, texto, notas, resumen y voces: la reunión entera, cuando acabe todo.</div>') + '</div>';
}
function destinoHtml(a) {
  return destinoPendienteHtml(a) || `<div class="dest-line" data-dest="${esc(a.path)}">${I.folder}<span>Se guarda en</span>`
    + `<code>${esc(destinoRel(a.folder))}</code><button class="btn ghost sm" type="button" data-dest-act="cambiar" data-path="${esc(a.path)}">Cambiar</button></div>`;
}
/* a dónde se puede mandar: la de ahora, la carpeta principal de cada proyecto y la de cada workstream con la suya */
function destinoOpciones(a) {
  const cur = destinoActual(a), pend = S.movs.pendientes[a.path];
  const out = [], seen = new Set();
  const add = (folder, label) => {
    if (folder == null || seen.has(folder) || !S.folders.has(folder) || S.folders.get(folder).por_reunion) return;
    seen.add(folder);
    out.push({ folder, label });
  };
  add(cur, cur ? pathLabel(cur) : `${rootName()} (raíz)`);
  const pf = S.config.project_folders || {};
  Object.keys(pf).sort((x, y) => x.localeCompare(y)).forEach(p => add(pf[p], p));
  Object.entries(S.config.workstream_folders || {}).forEach(([vid, f]) => {
    const v = (S.vocab || []).find(x => x.id === vid);
    if (v) add(f, `${v.project} › ${v.name}`);
  });
  return { out, cur, sel: pend ? pend.folder : cur };
}
function closeDestino() { $$('.dest-pop').forEach(p => p.remove()); }
function openDestino(btn, path) {
  closeDestino();
  const a = S.audios.get(path);
  if (!a) return;
  const { out, cur, sel } = destinoOpciones(a);
  const pop = document.createElement('div');
  pop.className = 'dest-pop';
  pop.dataset.path = path;
  pop.innerHTML = '<div class="dp-t">Guardar esta reunión en</div>'
    + out.map(o => `<button class="dp-it${o.folder === sel ? ' on' : ''}" type="button" data-dest-folder="${esc(o.folder)}" title="${esc(destinoRel(o.folder))}">`
      + `${o.folder === sel ? I.check : I.folder}<span>${esc(nfc(o.label))}</span>${o.folder === cur ? '<small>ahora</small>' : ''}</button>`).join('')
    + `<button class="dp-it" type="button" data-dest-otra="1">${I.ext}<span>Otra carpeta…</span><small>selector de Windows</small></button>`
    + '<div class="dp-n">La grabación sigue sin cortes. Al terminar la transcripción y el resumen, se mueve sola.</div>';
  document.body.append(pop);
  const r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  pop.style.top = (r.bottom + 6 + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
}
async function destinoElegir(path, body) {
  closeDestino();
  try {
    const r = await api('/api/reunion/destino', { path, ...body });
    if (r.pendiente) S.movs.pendientes[path] = r.pendiente; else delete S.movs.pendientes[path];
    if (r.movida || r.path !== path) await destinoSeguir(path, r.path);  // movida ya (el aviso llega con /api/jobs)
    else if (r.pendiente) toast(`Se moverá a <b>${esc(r.pendiente.display)}</b> cuando termine todo`);
    else if (body.folder === null) toast('Se queda donde está');
    refreshView();
    pollJobs(true);
  } catch (err) { toast(esc(err.message), 'err'); }
}
async function destinoOtra(path) {
  closeDestino();
  toast('📂 Elige la carpeta en la ventana de Windows que se acaba de abrir…');
  let abs;
  try { abs = (await api('/api/link/pick', { title: 'Carpeta donde guardar esta reunión' })).path; }
  catch (err) { abs = prompt(`${err.message}\n\nPega aquí la ruta de la carpeta:`); }
  if (!abs) return;
  await destinoElegir(path, { folder_abs: abs });
  try { setConfig(await api('/api/config')); await refreshTree(true); } catch { /* sigue: la elegida ya está apuntada */ }
}
/* la página (y lo que se esté escuchando) sigue a la reunión movida */
async function destinoSeguir(old, nuevo) {
  if (!nuevo || old === nuevo) return;
  const pos = localStorage.getItem('pos:' + old);
  if (pos) { localStorage.setItem('pos:' + nuevo, pos); localStorage.removeItem('pos:' + old); }
  await refreshTree(true);
  if (S.playing === old) S.playing = nuevo;
  if ((S.route.kind === 'a') && S.route.path === old) go('a', nuevo, S.route.params?.toString() ? Object.fromEntries(S.route.params) : null, true);
}
/* /api/jobs → movimientos: {pendientes: {ruta: {folder, destino, display, error}}, hechos: [{de, a, carpeta, display,
   error, at}]}. Cada hecho se avisa una vez («Movida a … · Abrir carpeta») y la página la sigue. */
function onMovimientos(m) {
  if (!m) return;
  const antes = JSON.stringify(S.movs.pendientes);
  S.movs = { pendientes: m.pendientes || {}, hechos: m.hechos || [] };
  let cambio = antes !== JSON.stringify(S.movs.pendientes);
  for (const h of S.movs.hechos) {
    const k = h.de + '|' + h.at;
    if (S.movsVistos.has(k)) continue;
    S.movsVistos.add(k);
    cambio = true;
    const nuevo = h.at >= S.movsDesde - 5;  // de antes de abrir esta ventana: se sigue, sin aviso
    if (h.error) { if (nuevo) toast(esc(h.error), 'err'); continue; }
    destinoSeguir(h.de, h.a);
    if (nuevo) toast(`${I.check} Movida a <b>${esc(h.display)}</b> · <u>Abrir carpeta</u>`, '',
      () => api('/api/reveal', { path: h.carpeta }).catch(err => toast(esc(err.message), 'err')));
  }
  if (cambio) refreshView();
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-dest-act], [data-dest-folder], [data-dest-otra]');
  if (!b) { if (!e.target.closest('.dest-pop')) closeDestino(); return; }
  const path = b.dataset.path || b.closest('.dest-pop')?.dataset.path;
  if (b.dataset.destAct === 'cambiar') {
    if ($('.dest-pop') && $('.dest-pop').dataset.path === path) return closeDestino();
    return openDestino(b, path);
  }
  if (b.dataset.destAct === 'deshacer') return destinoElegir(path, { folder: null });
  if (b.dataset.destFolder !== undefined) return destinoElegir(path, { folder: b.dataset.destFolder });
  if (b.dataset.destOtra) return destinoOtra(path);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDestino(); });

/* ---------------------------------------------------------------- avisos de la franja del directo (B6, msg 1378) */
/* Tres avisos, en la cabecera del directo y con lo que ya llega en los sondeos (sin peticiones nuevas):
   - fuente callada (/api/rec/status → callada {mic, sys} en segundos; el criterio, en directo_avisos.Callada);
   - «La llamada ha terminado: paro en 30 s · No terminar» (/api/rec/status → fin_llamada; FinDeLlamada);
   - «Lo último suena a portugués · Cambiar» (/api/jobs → directo.propuesta; Propuesta). «Cambiar» es el cambio de
     idioma de siempre (el selector de la cabecera); «No», POST /api/live/propuesta, y no se vuelve a proponer. */
const DIRECTO_IDIOMAS = { es: 'español', en: 'inglés', pt: 'portugués', fr: 'francés', it: 'italiano', de: 'alemán', ca: 'catalán' };
function directoIdiomaNombre(c) { return DIRECTO_IDIOMAS[c] || c; }
function directoMinTxt(s) { return `${Math.max(1, Math.floor((+s || 0) / 60))} min`; }
/* lo que se enseña (puro: se prueba con node). rec: /api/rec/status; d: j.directo; mine: esta app graba esta reunión */
function directoAvisos(rec, d, mine) {
  const out = [], fin = mine && rec && rec.fin_llamada, c = (mine && rec && rec.callada) || {};
  if (fin && fin.quedan_s != null) {
    out.push({ id: 'fin', texto: 'La llamada ha terminado: paro en ', quedan: Math.max(0, Math.round(fin.quedan_s)),
      botones: [{ act: 'rec-seguir', label: 'No terminar', title: 'Sigue grabando. Si después termina otra llamada, lo vuelve a proponer' }] });
  }
  if (c.mic != null) {
    out.push({ id: 'mic', texto: `El micro no recibe sonido desde hace ${directoMinTxt(c.mic)}`,
      nota: '¿Silenciado en el portátil o en Windows? Tu voz no se está grabando.' });
  }
  if (c.sys != null) {
    out.push({ id: 'sys', texto: `El ordenador no suena desde hace ${directoMinTxt(c.sys)}`,
      nota: 'Si los demás están hablando, no se les está grabando: revisa la salida de sonido.' });
  }
  const p = d && d.propuesta;
  if (p && p.idioma) {
    const mezcla = p.cambiar_a && p.cambiar_a !== p.idioma && p.cambiar_a.includes('-');
    const label = mezcla ? `Cambiar a ${p.cambiar_a.split('-').map(directoIdiomaNombre).join(' + ')}` : 'Cambiar';
    out.push({ id: 'idioma', texto: `Lo último suena a ${directoIdiomaNombre(p.idioma)}`,
      botones: [{ act: 'idioma-cambiar', label, valor: p.cambiar_a || p.idioma,
        title: 'Cambia el idioma de esta reunión al momento y rehace en él lo del último minuto' },
      { act: 'idioma-no', label: 'No', valor: p.idioma, title: 'No volver a proponerlo en esta grabación' }] });
  }
  return out;
}
/* «La llamada ha terminado…» al ver que la grabación se paró sola (onRecChange de app.js); '' si se paró a mano */
function directoParadaTexto(rec) {
  const p = rec && rec.parada;
  return p && p.motivo === 'llamada' ? '■ La llamada ha terminado: grabación parada y guardada · la transcripción termina sola' : '';
}

(function avisosCss() {
  const st = document.createElement('style');
  st.id = 'avisosCss';
  st.textContent = `
.lh-avisos:empty { display: none; }
.lh-avisos { display: flex; flex-direction: column; gap: 6px; margin: 10px 0 2px; }
.lh-aviso { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 10px; padding: 7px 12px; border-radius: 10px; background: var(--warn-soft); color: var(--ink); font-size: 13.5px; }
.lh-aviso b { font-weight: 600; }
.lh-aviso .la-nota { color: var(--muted); font-size: 12.5px; }
.lh-aviso .sp { flex: 1; }
.lh-aviso.la-fin { background: var(--accent-soft); }
.lh-aviso.la-idioma { background: var(--accent-soft); }`;
  document.head.appendChild(st);
})();

S.directo.finVisto = false;      // el «paro en 30 s» ya se avisó con un toast (una vez por aviso)
S.directo.propOculta = null;     // propuesta ya contestada aquí (hasta que el servidor la quite o cambie)
function renderDirectoAvisos() {
  const rec = S.rec || {};
  const fin = rec.recording && rec.fin_llamada && rec.fin_llamada.quedan_s != null;
  if (fin && !S.directo.finVisto) {  // en cualquier página: lo que va a pasar no puede pillar por sorpresa
    S.directo.finVisto = true;
    toast(`La llamada ha terminado: paro en ${Math.round(rec.fin_llamada.quedan_s)} s · <u>No terminar</u>`, '', directoSeguir);
  } else if (!fin) S.directo.finVisto = false;
  const el = $('[data-lh-avisos]');
  const a = curAudio();
  if (!el || !a) return;
  const j = liveJob(a), d = j && j.directo;
  const mine = !!(rec.recording && rec.path === a.path);
  let avisos = directoAvisos(rec, d, mine);
  const pk = d && d.propuesta ? `${j.id}|${d.propuesta.idioma}|${d.propuesta.cambiar_a}` : null;
  if (S.directo.propOculta && S.directo.propOculta !== pk) S.directo.propOculta = null;
  if (pk && S.directo.propOculta === pk) avisos = avisos.filter(x => x.id !== 'idioma');
  // la cuenta atrás se actualiza en su sitio: repintar cada segundo se comería el clic en «No terminar»
  const sig = JSON.stringify(avisos.map(x => [x.id, x.texto, (x.botones || []).map(b => b.valor || b.act)]));
  if (el.dataset.sig !== sig) {
    el.dataset.sig = sig;
    el.innerHTML = avisos.map(x => `<div class="lh-aviso la-${x.id}" data-aviso="${x.id}" role="status">`
      + `<span>${esc(x.texto)}${x.quedan != null ? `<b data-quedan>${x.quedan}</b> s` : ''}</span>`
      + (x.nota ? `<span class="la-nota">${esc(x.nota)}</span>` : '') + '<span class="sp"></span>'
      + (x.botones || []).map(b => `<button class="btn sm${b.act === 'idioma-no' ? ' ghost' : ''}" type="button" data-aviso-act="${b.act}"`
        + `${b.valor ? ` data-valor="${esc(b.valor)}"` : ''}${j ? ` data-id="${esc(j.id)}"` : ''} data-path="${esc(a.path)}" title="${esc(b.title || '')}">${esc(b.label)}</button>`).join('')
      + '</div>').join('');
  }
  const q = $('[data-quedan]', el), f = avisos.find(x => x.id === 'fin');
  if (q && f && q.textContent !== String(f.quedan)) q.textContent = String(f.quedan);
}
async function directoSeguir() {
  try {
    const r = await api('/api/rec/seguir', {});
    toast(r.grabando === false ? 'La grabación ya se había parado: está guardada y la transcripción termina sola'
      : r.seguia ? 'Sigue grabando. Si después termina otra llamada, lo volverá a proponer'
        : 'Sigue grabando: ya no había nada que parar');
    pollRec();
  } catch (err) { toast(esc(err.message), 'err'); }
}
/* «Cambiar»: el mismo camino que elegir el idioma en la cabecera (notas.js / app.js: guarda lo corregido, cambia y
   avisa de cuándo se verá); sin el selector a mano, el mismo guardado */
function directoCambiarIdioma(path, code) {
  const sel = $("#lHero select[data-prop='idioma']");
  if (sel && [...sel.options].some(o => o.value === code)) {
    sel.value = code;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    return;
  }
  const a = S.audios.get(path);
  if (a) saveProps(a, { idioma: code }).then(r => { if (r && typeof perfilSavedText === 'function') toast(perfilSavedText('idioma', true, false, r.directo?.idioma)); });
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-aviso-act]');
  if (!b) return;
  const act = b.dataset.avisoAct, pk = b.dataset.id ? `${b.dataset.id}|` : null;
  if (act === 'rec-seguir') return directoSeguir();
  const a = curAudio(), j = a && liveJob(a), p = j && j.directo && j.directo.propuesta;
  if (p && pk) S.directo.propOculta = `${j.id}|${p.idioma}|${p.cambiar_a}`;
  renderDirectoAvisos();
  if (act === 'idioma-cambiar') return directoCambiarIdioma(b.dataset.path, b.dataset.valor);
  if (act === 'idioma-no') {
    try {
      await api('/api/live/propuesta', { id: b.dataset.id, rechazar: b.dataset.valor });
      toast(`De acuerdo: no se vuelve a proponer el ${directoIdiomaNombre(b.dataset.valor)} en esta grabación`);
      pollJobs(true);
    } catch (err) { S.directo.propOculta = null; toast(esc(err.message), 'err'); }
  }
});
