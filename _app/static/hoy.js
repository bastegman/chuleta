'use strict';
/* Hoy (rediseño B7, msgs 1380 y 1390): la pantalla de inicio, qué grabar ahora y qué tienes pendiente.
   - Arriba, la reunión de ahora (la ventana de reunión de Teams o la cita de Outlook en curso) con su proyecto y su
     idioma, cambiables, y «Grabar», que empieza ya con el plan del servidor: /api/hoy lo trae calculado con
     plan.py, la misma regla que la tarjeta de Grabar, Ctrl+Alt+R y el aviso de Teams, y al cambiar un chip se pide
     otra vez a /api/grabar/plan. Lo que se ve es el cuerpo exacto de /api/rec/start. Si la reunión se repite, lo que
     quedó abierto la última vez.
   - «Para revisar»: las reuniones de los últimos 30 días que piden algo (error, sin transcribir, voces sin nombre,
     sin proyecto), cada una con su botón. «Esta semana», por día.
   - La agenda de Outlook del próximo mes (msg 1390: «un mes al menos»), que el servidor lee en segundo plano y guarda
     en memoria: Hoy no espera a Outlook. Sin Outlook, la pantalla es la misma sin agenda, con una línea que dice por qué.
   - Sin reuniones: «Graba tu primera reunión», con las tres formas de grabar y la de subir un audio.
   Se carga después de app.js, notas.js y grabar.js y usa sus utilidades ($, $$, api, esc, I, S, main, go, toast,
   allAudios, liveAudios, isLive, jobFor, statusOf, estDuration, fmtDur, fmtClock, aDate, titleOf, nfc, pad, iconOf, MES,
   LANGS, vocabOf, vocabOptions, saveProps, wsPatch, submitTx, openRec, openModal, closeModal, openSettings,
   refreshTree, pollRec, pollJobs, liveBanner, welcomeCard, avisoResumenLocalHtml). app.js lo llama con
   `typeof viewHoy === 'function'`: sin este fichero, Inicio es la lista de antes. */

const HOY_REVISAR_DIAS = 30;  // «Para revisar»: lo de los últimos 30 días (lo de antes, en Reuniones)
const HOY_REVISAR_MAX = 8;
const HOY_AGENDA_DIAS_VISTA = 7;  // la agenda enseña una semana; el resto del mes, con «Ver el resto del mes»
const HOY_CADA_MS = 30000;  // /api/hoy mientras Hoy está a la vista: la reunión de ahora cambia sola
const HOY_DIAS_C = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const HOY_DIAS_L = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
const HOY_MESES_L = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
  'noviembre', 'diciembre'];
// lo que se ha pedido al servidor y lo que has cambiado en la tarjeta de ahora (pedido: {vocab?, cambios: {idioma?}})
const HOY = { datos: null, at: 0, cargando: null, pedido: null, plan: null, seq: 0, pendiente: null, todo: false,
  leyendo: 0, error: null };

/* ---------------------------------------------------------------- fechas */
const hoyIniDia = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const hoyFechaDe = a => aDate(a) || new Date((a.mtime || 0) * 1000);
/* el título en Hoy: el suyo, el del archivo o, sin ninguno, «21:15 · sin nombre» (no la fecha larga: ya va al lado) */
const hoyTitulo = a => titleOf(a) || (a.label ? nfc(a.label) : `${pad(hoyFechaDe(a).getHours())}:${pad(hoyFechaDe(a).getMinutes())} · sin nombre`);
const hoyHora = iso => (iso || '').slice(11, 16);
function hoyDiaTxt(d, now, conMes) {
  const dd = Math.round((hoyIniDia(d) - hoyIniDia(now)) / 864e5);  // redondeado: los días de 23 y 25 h del cambio de hora
  if (dd === 0) return 'Hoy';
  if (dd === -1) return 'Ayer';
  if (dd === 1) return 'Mañana';
  const s = `${HOY_DIAS_C[d.getDay()]} ${d.getDate()}${conMes ? ' ' + MES[d.getMonth()] : ''}`;
  return s[0].toUpperCase() + s.slice(1);
}
function hoyFechaLarga(now) { return `${HOY_DIAS_L[now.getDay()]} ${now.getDate()} de ${HOY_MESES_L[now.getMonth()]}`; }
function hoyFaltan(min) {
  if (min < 1) return 'ya';
  if (min < 60) return `en ${Math.round(min)} min`;
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return `en ${h} h${m ? ` ${m} min` : ''}`;
}

/* ---------------------------------------------------------------- lo que pide algo de ti (Para revisar) */
const hoySumError = path => { for (let i = S.jobs.length - 1; i >= 0; i--) { const j = S.jobs[i]; if (j.path === path && j.kind === 'summary') return j.status === 'error'; } return false; };
/* Lo que pide una reunión, en orden: lo que no deja leerla primero (error, a medias, sin transcribir), luego las voces,
   el resumen fallido y el proyecto. El botón es el del primero. null si no pide nada o si está en marcha (la cola y el
   directo ya lo dicen). */
function hoyRevision(a) {
  if (isLive(a) || a.recording || jobFor(a.path)) return null;
  const st = statusOf(a), motivos = [];
  let accion = null;
  const anota = (texto, aviso, k, label) => { motivos.push({ texto, aviso }); if (!accion && k) accion = { k, label }; };
  if (st === 'error') anota('No se pudo transcribir', true, 'transcribir', 'Reintentar');
  else if (st === 'partial') anota(`La transcripción se cortó en ${fmtClock(a.tx_end)}`, true, 'continuar', 'Continuar');
  else if (st === 'pending') anota(`Grabada · sin transcribir (${fmtDur(estDuration(a))})`, false, 'transcribir', 'Transcribir');
  const sinNombre = (a.speakers && a.speakers.sin_nombre) || 0;
  if (sinNombre) anota(`${sinNombre} ${sinNombre === 1 ? 'voz' : 'voces'} sin nombre`, true, 'voces', 'Ponles nombre');
  if (!a.has_summary && hoySumError(a.path)) anota('No se pudo resumir', true, 'resumir', 'Reintentar el resumen');
  if ((S.vocab || []).length && !(a.props || {}).workstream) anota('¿De qué proyecto es?', false, 'proyecto', 'Elegir proyecto');
  if (!motivos.length) return null;
  if (a.has_summary) motivos.unshift({ texto: 'Resumen listo', aviso: false });
  return { a, motivos, accion };
}
function hoyRevisar(audios, now) {
  const desde = hoyIniDia(now).getTime() - (HOY_REVISAR_DIAS - 1) * 864e5;
  return audios.filter(a => hoyFechaDe(a).getTime() >= desde).map(hoyRevision).filter(Boolean)
    .sort((x, y) => hoyFechaDe(y.a) - hoyFechaDe(x.a));
}
/* Esta semana (de lunes a hoy), por día, de la más reciente; sin las que ya salen en Para revisar */
function hoySemana(audios, now, fuera) {
  const lunes = hoyIniDia(now).getTime() - ((now.getDay() + 6) % 7) * 864e5, dias = new Map();
  audios.filter(a => !fuera.has(a.path) && hoyFechaDe(a).getTime() >= lunes)
    .sort((x, y) => hoyFechaDe(y) - hoyFechaDe(x))
    .forEach(a => { const k = hoyIniDia(hoyFechaDe(a)).getTime(); if (!dias.has(k)) dias.set(k, []); dias.get(k).push(a); });
  return [...dias.entries()].map(([k, l]) => ({ dia: hoyDiaTxt(new Date(k), now), audios: l }));
}
/* La agenda por día desde ahora (las citas que ya han terminado no salen); `ahora`: la de la tarjeta de arriba */
function hoyAgendaDias(citas, now, ahora) {
  const dias = new Map();
  (citas || []).forEach(c => {
    const s = new Date(c.start), e = new Date(c.end);
    if (isNaN(s) || isNaN(e) || e <= now) return;
    const k = hoyIniDia(s).getTime();
    if (!dias.has(k)) dias.set(k, []);
    dias.get(k).push(Object.assign({}, c, { ahora: !!(ahora && ahora.inicio === c.start && ahora.titulo === c.subject) }));
  });
  return [...dias.entries()].sort((x, y) => x[0] - y[0]).map(([k, l]) => ({ k, dia: hoyDiaTxt(new Date(k), now, true), citas: l }));
}

/* ---------------------------------------------------------------- cargar */
const hoyViejo = () => Date.now() - Math.max(HOY.at, HOY.intento || 0) > HOY_CADA_MS;  // (un fallo no se reintenta en bucle)
function hoyCargar(refrescar, otraVez) {
  // con otra en camino, que puede traer lo de antes de un cambio: otraVez pide una más al terminar
  if (HOY.cargando) { if (otraVez) HOY.otraVez = refrescar ? 'r' : (HOY.otraVez || true); return HOY.cargando; }
  HOY.intento = Date.now();
  HOY.cargando = api('/api/hoy' + (refrescar ? '?refrescar=1' : '')).then(d => {
    const antes = HOY.datos?.plan?.nombre?.valor;
    HOY.datos = d; HOY.at = Date.now(); HOY.error = null;
    // otra reunión: lo que cambiaste era para la anterior
    if (antes !== d.plan?.nombre?.valor) { HOY.pedido = null; HOY.plan = null; }
    if (!HOY.pedido) HOY.plan = null;
    // la agenda se está leyendo: se vuelve a preguntar enseguida (como mucho un minuto)
    clearTimeout(HOY.leerTimer);
    if (d.agenda?.estado === 'leyendo' && HOY.leyendo < 20) { HOY.leyendo++; HOY.leerTimer = setTimeout(() => hoyCargar(), 3000); }
    else HOY.leyendo = 0;
  }).catch(err => { HOY.error = err.message; }).finally(() => {
    HOY.cargando = null;
    const otra = HOY.otraVez;
    HOY.otraVez = false;
    if (otra) hoyCargar(otra === 'r');
    hoyPintarVivo();
    if (S.route?.kind === 'c' && typeof renderView === 'function') renderView();  // el calendario, con las citas
  });
  return HOY.cargando;
}
/* la tarjeta de ahora y la agenda, en su sitio (sin repintar la página ni pisar un chip que estés eligiendo) */
function hoyPintarVivo(forzar) {
  if (S.route?.kind !== 'home') return;
  const ah = $('#hoyAhora'), ag = $('#hoyAgenda');
  if (ah && (forzar || !ah.contains(document.activeElement))) ah.innerHTML = hoyAhoraHtml();
  if (ag && (forzar || !ag.contains(document.activeElement))) ag.innerHTML = hoyAgendaHtml();
}
setInterval(() => {
  if (S.route?.kind === 'home' && !document.hidden && !S.off && $('#modal')?.classList.contains('hidden')) hoyCargar();
}, HOY_CADA_MS);

/* ---------------------------------------------------------------- la reunión de ahora */
const hoyPlan = () => HOY.plan || HOY.datos?.plan || null;
function hoyLinea(p, clave) { return (p?.lineas || []).find(l => l.clave === clave); }
function hoyKicker(info, now) {
  const horas = info.inicio && info.fin ? ` · ${hoyHora(info.inicio)}–${hoyHora(info.fin)}` : '';
  if (info.teams) return `Ahora en Teams${horas}`;
  if (info.en_curso) return `Ahora${horas}`;
  const min = info.inicio ? (new Date(info.inicio) - now) / 60000 : 0;
  return `A las ${hoyHora(info.inicio)} · ${hoyFaltan(min)}`;
}
function hoyUltimaHtml(u) {
  if (!u) return '';
  const t = u.tareas == null ? (u.resumen ? '' : 'sin resumen') : u.tareas === 0 ? 'sin tareas abiertas'
    : `<b>${u.tareas} ${u.tareas === 1 ? 'tarea abierta' : 'tareas abiertas'}</b>`;
  return `<div class="ha-ultima">${I.refresh}<span>La última vez (${esc(u.fecha_txt || '')})${t ? `: ${t}` : ''} · <a href="#" data-act="hoy-ultima" data-path="${esc(u.path)}">${u.resumen ? 'ver resumen' : 'ver la reunión'}</a></span></div>`;
}
function hoyAhoraHtml() {
  if (S.rec?.recording) return '';  // grabando: arriba está el directo (liveBanner)
  const d = HOY.datos, p = hoyPlan(), now = new Date();
  if (!d) return `<section class="card hoy-ahora vacio" aria-busy="true"><div class="ha-txt"><div class="ha-kicker">Ahora</div><div class="muted">${HOY.error ? esc(HOY.error) : 'Mirando si estás en una reunión…'}</div></div></section>`;
  const info = d.ahora;
  if ((!info || !p) && !allAudios().length) return '';  // sin reunión ni reuniones: lo dice «Graba tu primera reunión»
  if (!info || !p) {  // sin reunión: «Grabar» abre la tarjeta para ponerle nombre
    const prox = hoyAgendaDias(d.agenda?.citas, now).flatMap(x => x.citas)[0];
    return `<section class="card hoy-ahora vacio"><div class="ha-ico">${I.mic}</div><div class="ha-txt">
      <div class="ha-kicker">Ahora</div><div class="ha-titulo sin">No estás en ninguna reunión</div>
      ${prox ? `<div class="ha-prox">Próxima: <b>${esc(prox.subject || '(sin asunto)')}</b> · ${esc(hoyDiaTxt(new Date(prox.start), now))} a las ${hoyHora(prox.start)}</div>` : ''}</div>
      <button class="btn ha-grabar" data-act="hoy-tarjeta" title="Abre la tarjeta de grabar para ponerle nombre y proyecto"><i class="recdot"></i>Grabar…</button></section>`;
  }
  const idi = hoyLinea(p, 'idioma'), proy = p.proyecto || {};
  const vocab = (S.vocab || []).length ? `<label class="ha-chip" title="Proyecto › workstream: su carpeta, su idioma y su vocabulario">${I.folder}<select data-hoy="vocab" aria-label="Proyecto">${hoyProyOpciones(proy.vocab || '')}</select></label>` : '';
  const lang = idi ? `<label class="ha-chip" title="Idioma de la reunión">${I.globe}<select data-hoy="idioma" aria-label="Idioma">${LANGS.map(([v, l]) => `<option value="${v}"${v === (idi.code || '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>` : '';
  const motivo = [proy.vocab ? proy.motivo : '', idi && idi.origen !== 'ajustes' ? `idioma ${idi.origen_txt}` : ''].filter(Boolean).join(' · ');
  const nombre = p.nombre?.valor || info.titulo;
  return `<section class="card hoy-ahora"><div class="ha-ico">${info.teams ? I.phone : I.calendar}</div><div class="ha-txt">
    <div class="ha-kicker">${esc(hoyKicker(info, now))}</div>
    <h2 class="ha-titulo">${esc(nombre)}</h2>
    <div class="ha-chips">${vocab}${lang}${motivo ? `<span class="ha-motivo">${esc(motivo)}</span>` : ''}</div>
    ${hoyUltimaHtml(d.ultima)}
    </div><button class="btn primary ha-grabar" data-act="hoy-grabar" title="${esc(p.resumen || '')}"><i class="recdot"></i>Grabar</button></section>`;
}
/* el chip del proyecto: «Proyecto › Workstream» en cada opción (cerrado, un <select> solo enseña la opción, sin su grupo) */
function hoyProyOpciones(sel) {
  return `<option value=""${sel ? '' : ' selected'}>Sin proyecto</option>` + (S.vocab || []).map(v =>
    `<option value="${esc(v.id)}"${v.id === sel ? ' selected' : ''}>${esc(v.project)} › ${esc(v.name)}</option>`).join('');
}
/* un chip cambiado: el plan otra vez, con lo cambiado para esta reunión (como «Solo esta reunión» en la tarjeta) */
function hoyCambio(el) {
  const p = hoyPlan();
  if (!p) return;
  const pedido = HOY.pedido || { cambios: {} };
  if (el.dataset.hoy === 'vocab') pedido.vocab = el.value;
  else if (el.dataset.hoy === 'idioma') pedido.cambios.idioma = el.value;
  HOY.pedido = pedido;
  const seq = ++HOY.seq;
  HOY.pendiente = api('/api/grabar/plan', pedido).then(plan => {
    if (seq !== HOY.seq) return;
    HOY.plan = plan;
    const ah = $('#hoyAhora');
    if (ah) { ah.innerHTML = hoyAhoraHtml(); $(`[data-hoy="${el.dataset.hoy}"]`, ah)?.focus(); }
  }).catch(err => toast(esc(err.message), 'err'));
}
async function hoyGrabar(b) {
  if (S.rec?.recording) return toast('Ya hay una grabación en marcha.', 'err');
  b.disabled = true;
  try {
    await HOY.pendiente;  // lo último que has cambiado: se graba con lo que se está viendo
    const p = hoyPlan();
    if (!p) throw new Error('Aún no se sabe cómo grabar: espera un segundo y vuelve a pulsar.');
    S.recLocal = true;
    const r = await api('/api/rec/start', { ...p.rec, resumen: p.resumen });
    toast(`● Grabando · ${esc(p.resumen)}${r.aviso ? ` · ${esc(r.aviso)}` : ''}`);
    HOY.pedido = null; HOY.plan = null; HOY.at = 0;
    await refreshTree(true);
    pollRec(); pollJobs(true);
    go('a', r.path);
  } catch (err) { S.recLocal = false; toast(esc(err.message), 'err'); b.disabled = false; }
}

/* ---------------------------------------------------------------- Para revisar y Esta semana */
function hoyProyTxt(a) {
  const p = a.props || {};
  return p.workstream ? [p.proyecto, p.workstream].filter(Boolean).join(' › ') : '';
}
function hoyRevisarHtml(lista, now) {
  if (!lista.length) return '';
  const filas = lista.slice(0, HOY_REVISAR_MAX).map(r => {
    const a = r.a, proy = hoyProyTxt(a);
    return `<div class="hr-fila" data-go="a" data-path="${esc(a.path)}"><span class="hr-dia">${esc(hoyDiaTxt(hoyFechaDe(a), now))}</span>
      <div class="hr-t"><div class="hr-n">${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(hoyTitulo(a))}${proy ? `<span class="hr-proy">${esc(proy)}</span>` : (S.vocab || []).length ? '<span class="hr-proy sin">Sin proyecto</span>' : ''}</div>
      <div class="hr-sub">${r.motivos.map(m => m.aviso ? `<b class="aviso">${esc(m.texto)}</b>` : esc(m.texto)).join(' · ')}</div></div>
      <button class="btn sm" data-act="hoy-accion" data-k="${r.accion.k}" data-path="${esc(a.path)}">${esc(r.accion.label)}</button></div>`;
  }).join('');
  const mas = lista.length - HOY_REVISAR_MAX;
  return `<section class="hoy-revisar"><h2>Para revisar <span class="count">${lista.length}</span></h2><div class="card hr-lista">${filas}</div>
    ${mas > 0 ? `<a class="hr-mas" href="#/r/">y ${mas} más · ver en Reuniones</a>` : ''}</section>`;
}
function hoyChip(a) {
  const j = jobFor(a.path);
  if (isLive(a) || a.recording) return '<span class="pill rec"><i class="recdot"></i>Grabando</span>';
  if (j) return `<span class="pill run">${j.kind === 'voces' ? 'Voces' : 'Transcribiendo'}${j.status === 'running' ? ` ${Math.round(j.progress * 100)} %` : ''}</span>`;
  if (a.has_summary) return `<a class="pill ok" href="#" data-act="hoy-resumen" data-path="${esc(a.path)}">${I.check}Resumen</a>`;
  if (a.txt || a.srt) return '<span class="pill">Transcrita</span>';
  return '<span class="pill pending">Sin transcribir</span>';
}
function hoySemanaHtml(dias) {
  if (!dias.length) return '';
  const filas = dias.map(d => d.audios.map((a, i) => {
    const p = a.props || {}, sub = [hoyProyTxt(a), fmtDur(estDuration(a)), p.participantes].filter(Boolean).join(' · ');
    return `<div class="hs-fila" data-go="a" data-path="${esc(a.path)}"><span class="hr-dia">${i ? '' : esc(d.dia)}</span>
      <div class="hr-t"><div class="hr-n">${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(hoyTitulo(a))}</div><div class="hr-sub">${esc(sub)}</div></div>${hoyChip(a)}</div>`;
  }).join('')).join('');
  return `<section class="hoy-semana"><h2>Esta semana</h2><div class="card hr-lista">${filas}</div></section>`;
}
function hoyVacioHtml() {
  const hk = S.config?.hotkey?.ok, aviso = S.config?.meeting_detect !== false;
  return `<section class="card hoy-vacio"><h2>Graba tu primera reunión</h2>
    <ol class="hv-formas">
      <li><button class="btn primary" data-act="hoy-tarjeta"><i class="recdot"></i>Grabar</button><span>con el botón, aquí o arriba</span></li>
      <li><span class="hv-kbd"><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>R</kbd></span><span>desde cualquier programa${hk ? '' : ` (con ${esc(APP_NAME)} en primer plano: ${esc(S.config?.hotkey?.error || 'el atajo global no está disponible')})`}</span></li>
      <li><span class="hv-ico">${I.phone}</span><span>al entrar en una llamada de Teams, ${esc(APP_NAME)} te avisa con un botón para grabar${aviso ? '' : ' (el aviso está apagado en Ajustes › Al grabar)'}</span></li>
    </ol>
    <div class="hv-subir">${I.upload}<span>o arrastra aquí un audio que ya tengas, o <a href="#" data-act="hoy-subir">súbelo</a></span></div></section>`;
}

/* ---------------------------------------------------------------- la agenda de Outlook */
function hoyAgendaHtml() {
  const d = HOY.datos, g = d?.agenda;
  if (!g) return '';
  const now = new Date(), dias = hoyAgendaDias(g.citas, now, d.ahora);
  const limite = hoyIniDia(now).getTime() + HOY_AGENDA_DIAS_VISTA * 864e5;
  const vista = HOY.todo ? dias : dias.filter(x => x.k < limite);
  const resto = dias.filter(x => x.k >= limite).reduce((n, x) => n + x.citas.length, 0);
  const activo = g.estado !== 'apagado' && !['no_windows', 'sin_outlook', 'outlook_nuevo'].includes(g.estado);
  const accion = g.accion ? `<button class="btn sm" data-act="hoy-agenda-${esc(g.accion.id)}">${esc(g.accion.texto)}</button>` : '';
  const linea = g.estado !== 'ok' ? `<div class="ag-estado${g.estado === 'leyendo' ? ' leyendo' : ''}">${g.estado === 'leyendo' ? '' : I.info}<span>${esc(g.frase)}</span>${accion}</div>` : '';
  const filas = vista.map(x => `<div class="ag-dia"><div class="ag-dia-h">${esc(x.dia)}</div>${x.citas.map(c => {
    const tags = [c.ahora ? '<span class="ag-tag ahora">Ahora</span>' : '', c.is_teams ? '<span class="ag-tag">Teams</span>' : '',
      c.recurring ? `<span class="ag-tag" title="Periódica">${I.refresh}</span>` : ''].join('');
    const lugar = c.location && !c.is_teams ? `<div class="ag-lugar">${esc(c.location)}</div>` : '';
    const prep = c.preparada ? `<div class="ag-prep-txt">${I.check}${esc(hoyPreparadaTxt(c.preparada))}</div>` : '';
    // «Preparar» en las que aún no han empezado (la de ahora se cambia en la tarjeta de arriba)
    const boton = c.ahora || new Date(c.start) <= now || !(c.subject || '').trim() ? ''
      : `<button class="btn ghost sm ag-prep${c.preparada ? ' on' : ''}" data-act="hoy-preparar" data-asunto="${esc(c.subject)}" data-inicio="${esc(c.start)}" data-fin="${esc(c.end)}" title="Elige ya su proyecto, su idioma y su contexto: se usarán al grabarla">${c.preparada ? 'Cambiar' : 'Preparar'}</button>`;
    return `<div class="ag-cita${c.ahora ? ' ahora' : ''}"><span class="ag-h">${hoyHora(c.start)}<small>${hoyHora(c.end)}</small></span>
      <div class="ag-t"><div class="ag-n">${esc(c.subject || '(sin asunto)')}${tags}</div>${lugar}${prep}</div>${boton}</div>`;
  }).join('')}</div>`).join('');
  const nada = g.estado === 'ok' && !dias.length ? `<div class="ag-nada">No tienes más reuniones en los próximos ${g.dias} días.</div>` : '';
  const pie = g.leida ? `<div class="ag-pie">Leída de Outlook a las ${hoyHora(g.leida)}${g.cortada ? ' · solo las primeras citas: tu agenda tiene más' : ''}
    <button class="btn ghost sm" data-act="hoy-agenda-leer" title="Volver a leer la agenda de Outlook">${I.refresh}Actualizar</button></div>` : '';
  return `<h2>Agenda${activo ? ` <span class="count">próximos ${g.dias} días</span>` : ''}</h2>${linea}
    ${filas || nada ? `<div class="card ag-lista">${filas}${nada}</div>` : ''}
    ${resto && !HOY.todo ? `<button class="btn ghost sm ag-mas" data-act="hoy-agenda-todo">Ver el resto del mes (${resto} ${resto === 1 ? 'reunión' : 'reuniones'})</button>` : ''}${pie}`;
}

/* Calendario (notas.js, viewCalendar): las citas de Outlook que aún no han pasado, en su día, junto a las grabadas.
   Es donde Bernardo buscó «las siguientes reuniones» (msg 1380). Se ven distintas y no se abren (no son reuniones
   grabadas). */
function hoyCalDia(key) {
  if (!HOY.cargando && (!HOY.datos || hoyViejo())) hoyCargar();
  const now = new Date(), citas = (HOY.datos?.agenda?.citas || []).filter(c => c.start.slice(0, 10) === key && new Date(c.end) > now);
  return citas.slice(0, 3).map(c => `<div class="cev outlook" title="${esc(c.subject || '(sin asunto)')} · ${hoyHora(c.start)}–${hoyHora(c.end)} · de tu Outlook">`
    + `<span class="ct">${hoyHora(c.start)}</span>${esc(c.subject || '(sin asunto)')}</div>`).join('')
    + (citas.length > 3 ? `<div class="cmore outlook">+${citas.length - 3} en Outlook</div>` : '');
}

/* ---------------------------------------------------------------- la página */
function viewHoy() {
  const all = allAudios(), now = new Date();
  if (!HOY.datos || hoyViejo()) hoyCargar();
  const revisar = all.length ? hoyRevisar(all, now) : [];
  const fuera = new Set(revisar.slice(0, HOY_REVISAR_MAX).map(r => r.a.path));
  main.innerHTML = `<div class="page hoy">
    ${liveAudios().map(liveBanner).join('')}
    ${typeof avisoProbarHtml === 'function' ? avisoProbarHtml() : ''}
    ${typeof avisoResumenLocalHtml === 'function' ? avisoResumenLocalHtml() : ''}
    <h1>Hoy <span class="sub">· ${esc(hoyFechaLarga(now))}</span></h1>
    ${typeof welcomeCard === 'function' ? welcomeCard(all) : ''}
    <div class="hoy-grid">
      <div class="hoy-ahora-box" id="hoyAhora">${hoyAhoraHtml()}</div>
      <section class="hoy-agenda" id="hoyAgenda">${hoyAgendaHtml()}</section>
      <div class="hoy-rev">${all.length ? hoyRevisarHtml(revisar, now) : hoyVacioHtml()}</div>
      <div class="hoy-sem">${all.length ? hoySemanaHtml(hoySemana(all, now, fuera)) : ''}</div>
    </div></div>`;
}

/* ---------------------------------------------------------------- preparar una reunión de la agenda */
function hoyPreparadaTxt(p) {
  const v = 'vocab' in p ? (p.vocab ? (S.vocab || []).find(x => x.id === p.vocab) : null) : undefined;
  const lang = 'idioma' in p ? (LANGS.find(([c]) => c === p.idioma) || [p.idioma, p.idioma])[1].replace(/ \(.*\)$/, '') : '';
  return ['Preparada', v === undefined ? '' : v ? `${v.project} › ${v.name}` : 'sin proyecto', lang,
    p.contexto ? 'con contexto' : ''].filter(Boolean).join(' · ');
}
/* «Preparar»: proyecto, idioma y contexto de una cita que aún no ha empezado. El servidor lo guarda y plan.py lo usa
   al grabarla (de 10 min antes a 5 después), con «Grabar», la tarjeta, Ctrl+Alt+R o el aviso de Teams */
function hoyPreparar(c) {
  const p = c.preparada || {}, ini = new Date(c.start);
  const vocab = `<option value="__auto"${'vocab' in p ? '' : ' selected'}>Lo de siempre (la última vez o el título)</option>`
    + `<option value=""${p.vocab === '' ? ' selected' : ''}>Sin proyecto</option>`
    + (S.vocab || []).map(v => `<option value="${esc(v.id)}"${p.vocab === v.id ? ' selected' : ''}>${esc(v.project)} › ${esc(v.name)}</option>`).join('');
  const langs = `<option value="__auto"${'idioma' in p ? '' : ' selected'}>El del proyecto o el de Ajustes</option>`
    + LANGS.map(([v, l]) => `<option value="${v}"${'idioma' in p && p.idioma === v ? ' selected' : ''}>${esc(l)}</option>`).join('');
  openModal(`<h3>Preparar «${esc(c.subject)}»</h3>
    <div class="help" style="margin:0 0 12px">${esc(hoyDiaTxt(ini, new Date(), true))} a las ${hoyHora(c.start)}. Al grabarla (con «Grabar», Ctrl+Alt+R o el aviso de Teams) se usará esto.</div>
    <label class="hp-l">Proyecto<select id="hpVocab" class="input">${vocab}</select></label>
    <label class="hp-l">Idioma<select id="hpIdioma" class="input">${langs}</select></label>
    <label class="hp-l">Contexto <span class="muted">(opcional: de qué va y quién participa; ayuda a la transcripción y al resumen)</span>
      <textarea id="hpCtx" class="input" rows="3" maxlength="500">${esc(p.contexto || '')}</textarea></label>
    <div class="btns">${c.preparada ? '<button class="btn ghost danger" data-m="quitar" type="button">Quitar</button><span class="hp-hueco"></span>' : ''}
      <button class="btn" data-m="no" type="button">Cancelar</button><button class="btn primary" data-m="ok" type="button">Guardar</button></div>`, d => {
    d.addEventListener('click', async e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m === 'no') return closeModal(false);
      const auto = x => (x === '__auto' ? null : x);
      const cuerpo = { asunto: c.subject, inicio: c.start, fin: c.end, quitar: b.dataset.m === 'quitar',
        vocab: auto($('#hpVocab', d).value), idioma: auto($('#hpIdioma', d).value), contexto: $('#hpCtx', d).value };
      b.disabled = true;
      try {
        const r = await api('/api/hoy/preparar', cuerpo);
        closeModal(true);
        toast(r.preparada ? `Preparada: al grabar «${esc(c.subject)}» se usará esto` : `«${esc(c.subject)}» sin preparar: se grabará como siempre`);
        HOY.at = 0;
        hoyCargar(false, true);
      } catch (err) { toast(esc(err.message), 'err'); b.disabled = false; }
    });
  });
}

/* ---------------------------------------------------------------- acciones */
function hoyElegirProyecto(a) {
  openModal(`<h3>¿De qué proyecto es?</h3><div class="help" style="margin:0 0 10px">${esc(hoyTitulo(a))}: su carpeta y su vocabulario serán los del proyecto.</div>
    <select id="hoyProy" class="input">${vocabOptions('', 'Sin proyecto')}</select>
    <div class="btns"><button class="btn" data-m="no" type="button">Cancelar</button><button class="btn primary" data-m="ok" type="button">Guardar</button></div>`, d => {
    d.addEventListener('click', async e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m !== 'ok') return closeModal(false);
      const v = (S.vocab || []).find(x => x.id === $('#hoyProy', d).value);
      if (!v) return closeModal(false);
      b.disabled = true;
      const r = await saveProps(a, wsPatch(v));
      closeModal(!!r);
      if (r) { toast(`🗂 ${esc(v.project)} › ${esc(v.name)}`); refreshTree(true); }
    });
  });
}
async function hoyAccion(k, a) {
  if (k === 'transcribir' || k === 'continuar') return submitTx([a.path], S.config.preset in S.presetsById ? S.config.preset : 'rapido', null, k === 'continuar');
  if (k === 'voces') { S.tab = 'tx'; return go('a', a.path); }
  if (k === 'proyecto') return hoyElegirProyecto(a);
  if (k === 'resumir') {
    try { await api('/api/summarize', { path: a.path }); toast('Resumiendo…'); pollJobs(true); } catch (err) { toast(esc(err.message), 'err'); }
  }
}
main.addEventListener('click', e => {
  const b = e.target.closest('[data-act^="hoy-"]');
  if (!b || !main.contains(b)) return;
  e.preventDefault();
  e.stopPropagation();  // dentro de una fila con data-go: el botón no abre la reunión
  const act = b.dataset.act, a = b.dataset.path ? S.audios.get(b.dataset.path) : null;
  if (act === 'hoy-grabar') hoyGrabar(b);
  else if (act === 'hoy-tarjeta') openRec();
  else if (act === 'hoy-subir') $('#btnSubir')?.click();
  else if (act === 'hoy-ultima' || act === 'hoy-resumen') { S.tab = 'sum'; go('a', b.dataset.path); }
  else if (act === 'hoy-accion' && a) hoyAccion(b.dataset.k, a);
  else if (act === 'hoy-agenda-leer') { HOY.leyendo = 0; hoyCargar(true, true); }
  else if (act === 'hoy-agenda-ajustes') openSettings({ sec: 'calendario' });
  else if (act === 'hoy-agenda-todo') { HOY.todo = true; hoyPintarVivo(true); }
  else if (act === 'hoy-preparar') {
    const c = (HOY.datos?.agenda?.citas || []).find(x => x.start === b.dataset.inicio && x.subject === b.dataset.asunto);
    if (c) hoyPreparar(c);
  }
}, true);
main.addEventListener('change', e => { if (e.target.dataset?.hoy) hoyCambio(e.target); });
