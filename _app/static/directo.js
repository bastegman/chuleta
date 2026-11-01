/* Directo en la nube (Groq): cupo y latencia en la cabecera del directo, «modo seguir» (letra grande y el texto
   siempre abajo), traduccion al espanol por reunion en una columna lateral y, en Ajustes, el modelo del directo y
   las claves adicionales. Todo sobre j.directo (lo publica live.py en /api/jobs), /api/config y /api/live/traducir.
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
  const sig = JSON.stringify([j.id, quota, lat, d.pace, d.paused, d.translate, d.translate_error, d.translating, d.translate_ok, d.translate_model, S.directo.seguir]);
  if (el.dataset.sig === sig) return;  // sin cambios: no se repinta (no se pisa el clic en la casilla)
  el.dataset.sig = sig;
  el.innerHTML = `<div class="directo-bar">
    ${d.paused ? '<span class="pill err">Directo en pausa por cupo</span>' : ''}
    ${quota ? `<span class="db-item" title="Cupo gratuito de Groq: peticiones por día y por cuenta">${esc(quota)}</span>` : ''}
    ${d.pace ? `<span class="db-item" title="Tamaño de los trozos de audio que se mandan: más largos cuando queda menos cupo">trozos de ${d.pace[0]}–${d.pace[1]} s</span>` : ''}
    <span class="db-item" title="Del final de cada trozo de audio al texto en pantalla (mediana y percentil 95 de esta sesión)">${esc(lat)}</span>
    <span class="sp"></span>
    ${tradOk ? `<label class="btn sm db-trad" title="${esc(`Traduce al español lo último que se ha dicho, cada 10–15 s${tradLabel ? `, con ${tradLabel}` : ''}. Solo para esta reunión; no se guarda.`)}"><input type="checkbox" id="directoTradChk" data-id="${esc(j.id)}"${d.translate ? ' checked' : ''}> Traducir al español${d.translating ? '<i class="db-dot" title="traduciendo…"></i>' : ''}</label>`
      : `<label class="btn sm db-trad" title="${esc(d.translate_why || 'La traducción necesita un servicio de chat en la nube.')}" aria-disabled="true"><input type="checkbox" id="directoTradChk" data-id="${esc(j.id)}" disabled> Traducir al español</label>`}
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

document.addEventListener('change', async e => {
  if (e.target.id !== 'directoTradChk') return;
  const on = e.target.checked, id = e.target.dataset.id;
  try {
    const j = await api('/api/live/traducir', { id, on });
    if (j.directo) j.directo.translate = on;  // el estado publicado por live.py se actualiza en el siguiente sondeo
    if (on) toast('Traducción al español activada: aparece a la derecha en unos 10–15 s');
    const a = curAudio();
    if (a) { renderDirectoBar(a, j); renderDirectoTrad(j); }
    pollJobs(true);
  } catch (err) {
    e.target.checked = !on;
    toast(esc(err.message), 'err');
  }
});

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

/* ---------------------------------------------------------------- traduccion al espanol (columna lateral) */
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
  const n = d.translation_n || blocks.length;
  const sig = [j.id, n, d.translating ? 1 : 0, d.translate ? 1 : 0, d.translate_error || ''].join('|');
  if (aside.dataset.sig === sig) return;
  const atEnd = S.directo.tradN < 0 || aside.scrollHeight - aside.scrollTop - aside.clientHeight < 120;
  aside.dataset.sig = sig;
  const tl = directoTradLabel(d);
  aside.innerHTML = `<div class="dt-head"><b>${I.translate}Traducción al español</b><span class="muted">${tl ? `${esc(tl)} · ` : ''}una ayuda para seguir la reunión, no se guarda</span></div>
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
    <div class="help">Se pasa a la siguiente cuando a la clave activa le quedan menos de 20 peticiones del día (Groq), cuando el servicio pide esperar más de un minuto o cuando una clave no es válida. También las usan el resumen y el repaso final.</div>
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
    add.disabled = $$('[data-xkey]', rows).length >= 10;
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
