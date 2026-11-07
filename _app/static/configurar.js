/* Configurar Chuleta (07/11/2026, propuesta de Steve sobre la queja de Bernardo, msg 1169: «Lo de config es un
   desastre»). Aquí vive lo que dice dónde se procesan las reuniones, con las mismas palabras en todas partes:
   - la línea del modo («Se transcribe en este PC (Normal) y al terminar la resume Claude con tu cuenta»), que usan el
     diálogo Grabar, la cabecera del directo, la Cola y el chip «EN VIVO»;
   - «Preparando la transcripción» con % mientras se descarga o se carga el modelo (antes: «Escuchando…» y «al día»);
   - el asistente de tres pasos y «Probar» (más abajo).
   Lo de arriba del marcador es puro (se prueba con node: tests/test_ux_configurar.py); lo de abajo toca el DOM. */

const MODELO_NOMBRE = { small: 'Rápido', medium: 'Normal', 'large-v3-turbo': 'Preciso', 'large-v3': 'Máxima' };
const MODO_NOMBRE = { local: 'Solo en este PC', agente: 'Con tu cuenta de Claude', api: 'Con una API externa' };

function modeloNombre(model) { return MODELO_NOMBRE[model] || model || ''; }
function modoNombre(m) { return MODO_NOMBRE[m] || m || ''; }
function cfgProv(c) { return c?.providers?.[c?.cloud?.provider || 'groq']?.label || 'la nube'; }
function cfgFmtGb(mb) {  // 1531 -> «1,5 GB»; 486 -> «486 MB»
  mb = +mb || 0;
  return mb >= 1000 ? `${(mb / 1000).toFixed(1).replace('.', ',')} GB` : `${Math.round(mb)} MB`;
}
function cfgFmtQueda(s) {
  s = Math.max(0, Math.round(+s || 0));
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')} min`;
  if (s >= 90) return `${Math.round(s / 60)} min`;
  return `${Math.max(5, Math.round(s / 5) * 5)} s`;
}

/* La línea del modo para un motor (preset): dónde se transcribe y quién resume, como en Ajustes */
function modoLinea(presetId, cfg) {
  const c = cfg || S.config || {}, p = S.presetsById?.[presetId] || {};
  const modo = c.procesado || 'local', prov = cfgProv(c);
  const nube = p.engine === 'cloud';
  const trans = nube ? `Se transcribe con ${prov}` : `Se transcribe en este PC (${modeloNombre(p.model || 'medium')})`;
  if (modo === 'local') return `${trans}. Sin resumen con IA`;
  const quien = modo === 'agente' ? 'Claude con tu cuenta' : prov;
  // el resumen automático: apagado en Ajustes, o (con la API) lo transcrito en el PC solo si se ha consentido
  const auto = c.summary?.auto !== false && (modo !== 'api' || nube || c.summary?.local);
  return auto ? `${trans} y al terminar la resume ${quien}` : `${trans}. El resumen, a mano con ${modo === 'agente' ? 'Claude (tu cuenta)' : prov}`;
}

/* Un trabajo que aún no transcribe porque está descargando o cargando su modelo: {tipo, nombre, pct, …} o null */
function prepEstado(j) {
  if (!j || j.status !== 'running') return null;
  const st = j.stage || '', p = S.presetsById?.[j.preset] || {}, model = p.model;
  if (/^Descargando/.test(st)) {
    const m = S.modelos?.[model] || {};
    let pct = m.frac != null ? Math.floor(m.frac * 100) : null;
    if (pct == null) { const x = st.match(/\((\d+) %\)/); pct = x ? +x[1] : null; }
    return { tipo: 'descarga', model, nombre: modeloNombre(model), pct, done_mb: m.done_mb ?? null,
      total_mb: m.total_mb || m.size_mb || p.size_mb || null, eta_s: m.eta_s ?? null };
  }
  if (/^Cargando/.test(st)) return { tipo: 'carga', model, nombre: modeloNombre(model) };
  return null;
}
/* lo que dice la Cola en vez de «al día» */
function prepCola(j) {
  const e = prepEstado(j);
  if (!e) return '';
  return e.tipo === 'descarga' ? `Preparando: descargando el modelo ${e.nombre}${e.pct != null ? ` · ${e.pct} %` : ''}`
    : `Preparando: cargando el modelo ${e.nombre}`;
}
/* la frase larga de la cabecera del directo */
function prepTexto(e, segs) {
  if (!e) return '';
  if (e.tipo === 'carga') return `Cargando el modelo ${e.nombre} en memoria${segs != null ? `… ${segs} s` : '…'}`;
  const tam = e.total_mb ? cfgFmtGb(e.total_mb) : '';
  let t = `Descargando el modelo ${e.nombre} (${tam ? tam + ', ' : ''}solo la primera vez)`;
  if (e.done_mb != null && e.total_mb) t += ` · ${cfgFmtGb(e.done_mb)} de ${tam}`;
  if (e.eta_s) t += ` · quedan unos ${cfgFmtQueda(e.eta_s)}`;
  return t + '. La grabación sigue y no se pierde nada: cuando acabe la descarga, el texto empieza desde el principio.';
}

/* ---8<--- DOM (lo de abajo usa el DOM, I, api, toast… de app.js) */

S.modelos = S.modelos || {};
S.prepDesde = S.prepDesde || {};
let cfgModelosTimer = null, cfgModelosHasta = 0;
/* /api/modelos cada segundo mientras haga falta (una descarga en marcha o alguien que la enseña) */
async function modelosRefrescar(seguir = 0) {
  if (seguir) cfgModelosHasta = Math.max(cfgModelosHasta, Date.now() + seguir);
  clearTimeout(cfgModelosTimer);
  try {
    const r = await api('/api/modelos');
    S.modelos = Object.fromEntries((r.modelos || []).map(m => [m.model, m]));
    document.dispatchEvent(new CustomEvent('modelos'));
    if ((r.modelos || []).some(m => m.downloading) || Date.now() < cfgModelosHasta) cfgModelosTimer = setTimeout(modelosRefrescar, 1000);
  } catch { cfgModelosTimer = setTimeout(modelosRefrescar, 3000); }
}
/* al llegar la Cola: si algún trabajo prepara su modelo, se mira el % (y se repinta lo que lo enseña) */
function prepVigilar(jobs) {
  const prep = (jobs || []).filter(j => prepEstado(j));
  prep.forEach(j => { if (!S.prepDesde[j.id + j.stage.slice(0, 9)]) S.prepDesde[j.id + j.stage.slice(0, 9)] = Date.now(); });
  if (prep.some(j => prepEstado(j).tipo === 'descarga') && !cfgModelosTimer) modelosRefrescar();
}
function prepSegs(j) {
  const t = S.prepDesde[j.id + (j.stage || '').slice(0, 9)];
  return t ? Math.round((Date.now() - t) / 1000) : null;
}

/* La línea del modo con su icono; cambiar: enlace que enseña los modelos (directo) */
function modoLineaHtml(presetId, opts = {}) {
  const p = S.presetsById?.[presetId] || {};
  const icon = p.engine === 'cloud' ? I.nube : I.monitor;
  const tec = typeof motorDe === 'function' ? motorDe(S.presetsById?.[presetId], S.config)?.texto : '';  // «en este PC · Whisper medium»
  return `<div class="modo-linea" data-modo-linea${tec ? ` title="${esc(tec)}"` : ''}>${icon}<span>${esc(modoLinea(presetId))}</span>`
    + `${opts.cambiar ? `<a href="#" data-act="${opts.cambiar}">${opts.abierto ? 'Ocultar' : 'Cambiar'}</a>` : ''}</div>`
    + (opts.falta ? faltaModeloHtml(presetId) : '');
}
/* el modelo de ese motor aún no está en el PC: aviso antes de empezar, con «Descargar ahora» y su % */
function faltaModeloHtml(presetId) {
  const p = S.presetsById?.[presetId] || {};
  if (p.engine === 'cloud' || p.cached !== false) return '';
  const m = S.modelos?.[p.model];
  if (m?.ready) return '';
  const nombre = modeloNombre(p.model), tam = cfgFmtGb(m?.total_mb || m?.size_mb || p.size_mb);
  if (m?.downloading) {
    const pct = Math.floor((m.frac || 0) * 100);
    return `<div class="notice info falta-modelo" data-falta="${esc(p.model)}"><span>Descargando el modelo ${esc(nombre)}: ${pct} %. Puedes empezar ya: el texto saldrá cuando termine.</span></div>`;
  }
  if (m?.error) {
    return `<div class="notice falta-modelo" data-falta="${esc(p.model)}"><span>${esc(m.error)}</span><button class="btn sm" type="button" data-act="modelo-bajar" data-model="${esc(p.model)}">Reintentar</button></div>`;
  }
  return `<div class="notice falta-modelo" data-falta="${esc(p.model)}"><span>El modelo ${esc(nombre)} (${esc(tam)}) aún no está en este PC. Puedes empezar ya: se descarga mientras grabas y el texto sale cuando termine.</span><button class="btn sm" type="button" data-act="modelo-bajar" data-model="${esc(p.model)}">Descargar ahora</button></div>`;
}
async function modeloBajar(model) {
  try {
    const r = await api('/api/modelos/descargar', { model });
    S.modelos = Object.fromEntries((r.modelos || []).map(m => [m.model, m]));
    document.dispatchEvent(new CustomEvent('modelos'));
    modelosRefrescar(4000);
  } catch (err) { toast(esc(err.message), 'err'); }
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-act="modelo-bajar"]');
  if (!b) return;
  e.preventDefault();
  b.disabled = true;
  modeloBajar(b.dataset.model);
});

/* «Preparando la transcripción» (cabecera del directo) */
function prepHtml(j) {
  const e = prepEstado(j);
  if (!e) return '<div data-prep></div>';
  const pct = e.tipo === 'descarga' ? e.pct : null;
  return `<div data-prep><div class="prep" role="status" aria-live="polite"><div class="prep-h"><span>Preparando la transcripción</span>${pct != null ? `<span>${pct} %</span>` : ''}</div>`
    + (e.tipo === 'descarga' ? `<div class="prep-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"${pct != null ? ` aria-valuenow="${pct}"` : ''}><i style="width:${pct || 0}%"></i></div>` : '')
    + `<div class="prep-s">${esc(prepTexto(e, prepSegs(j)))}</div></div></div>`;
}
/* el hueco del texto mientras se prepara */
function prepHueco(j) {
  const e = prepEstado(j);
  if (!e) return '';
  return e.tipo === 'descarga' ? 'El texto aparecerá aquí cuando termine la descarga del modelo.'
    : 'El texto aparecerá en unos segundos, en cuanto se cargue el modelo.';
}

/* llega el % de una descarga: se repinta lo que lo enseña (cabecera del directo, diálogo Grabar, Cola) */
document.addEventListener('modelos', () => {
  const au = typeof curAudio === 'function' ? curAudio() : null;
  if (au && $('#lHero') && typeof updateLiveHero === 'function') updateLiveHero(au);
  const m = $('#modal .dialog');
  if (m && typeof m.pintaMotor === 'function') m.pintaMotor();
  if (!$('#jobsPanel').classList.contains('hidden') && typeof renderJobsPanel === 'function') renderJobsPanel();
});
