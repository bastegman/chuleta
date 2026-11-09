'use strict';
/* Grabar: la tarjeta «Así se va a grabar» (rediseño B1, 07/11/2026). Sustituye al formulario de 15 controles.
   Lo que enseña sale de POST /api/grabar/plan, el mismo cálculo que usan Ctrl+Alt+R y el aviso de Teams, y
   «Empezar a grabar» manda a /api/rec/start el cuerpo exacto que se está viendo (plan.rec): lo que se ve es lo que se
   usa. Cada línea dice de dónde sale (esta reunión, el workstream, el proyecto o Ajustes) y «Cambiar» pregunta para
   qué reuniones: solo esta (por defecto, D1), todas las del proyecto o todas (Ajustes).
   Se carga después de app.js y notas.js y usa sus utilidades ($, $$, api, esc, I, S, LANGS, folderOpts, liveFit,
   linkFolder, loadVocab, openModal, closeModal, copy, toast, go, pollRec, pollJobs, refreshTree, openVocabEditor,
   openSettings, CONSENT_TEXT). app.js lo invoca con `typeof abrirGrabar === 'function'`. */

const GR_FILAS = [['carpeta', 'folder'], ['idioma', 'globe'], ['vocab', 'book'], ['contexto', 'text'],
  ['transcribe', 'monitor'], ['resume', 'spark'], ['audio', 'mic']];
// qué se puede guardar para más reuniones desde cada línea (D1: por defecto, solo esta reunión)
const GR_ALCANCES = { carpeta: ['reunion', 'proyecto', 'ajustes'], idioma: ['reunion', 'proyecto', 'ajustes'],
  contexto: ['reunion', 'ajustes'], transcribe: ['reunion', 'ajustes'], audio: ['reunion', 'ajustes'] };
// las claves del pedido que cambia cada línea
const GR_CLAVES = { carpeta: ['carpeta'], idioma: ['idioma'], contexto: ['contexto'], transcribe: ['live', 'preset'],
  audio: ['mic', 'sys'] };
let G = null;  // la tarjeta abierta: {d, pedido, plan, tocado, recientes, seq, ...; subir: {items, rechazados}}
// «Usar las listas de una carpeta…» en Proyecto (rama vocab-carpeta, msg 1148: perfil.js define vocabConectar): no es un
// proyecto, abre el selector de carpetas y vuelve a la tarjeta con lo que tenías
const GR_CONECTAR = '__conectar';

function grFila(plan, clave) { return (plan?.lineas || []).find(l => l.clave === clave); }
// las líneas de la tarjeta: al subir un audio no hay nada que grabar (sin la de audio)
function grFilas() { return GR_FILAS.filter(([k]) => !(G?.subir && k === 'audio')); }
function grProyecto(plan) { return plan?.proyecto?.vocab ? plan.proyecto.proyecto : ''; }

/* ---------------------------------------------------------------- abrir */
async function abrirGrabar(opts = {}) {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  if (S.rec.recording) return stopRec();
  const pedido = opts.seguir && G ? G.pedido : { cambios: {} };
  if (!opts.seguir && S.route.kind === 'f') pedido.cambios.carpeta = S.route.path;  // desde una carpeta: ahí
  if (opts.vocab != null) pedido.vocab = opts.vocab;
  G = { pedido, plan: null, tocado: opts.seguir && G ? G.tocado : {}, recientes: [], seq: 0, abierto: null,
    pendiente: null, timer: null, devs: null, sugIdx: -1 };
  recDialog = true;
  const hk = S.config.hotkey?.ok ? 'Ctrl+Alt+R y el aviso de Teams graban con este mismo plan, sin los cambios que hagas solo para esta reunión.'
    : `El atajo Ctrl+Alt+R solo funciona con ${esc(APP_NAME)} en primer plano: ${esc(S.config.hotkey?.error || 'no se pudo registrar en Windows')}.`;
  openModal(`<div class="gr-cab"><span class="gr-kicker"><i class="recdot"></i>Nueva grabación</span>
      <span class="gr-llamada${S.rec.meeting ? '' : ' hidden'}" id="grLlamada">${I.phone}${esc(S.rec.meeting || 'Teams')}: en llamada</span></div>
    <div class="gr-cuerpo">
      <div class="gr-top">
        <label for="grNombre">Reunión</label>
        <div class="gr-nombre"><input class="input" id="grNombre" maxlength="120" autocomplete="off" spellcheck="false" placeholder="Buscando la reunión de Teams…" aria-autocomplete="list" aria-controls="grSug">
          <div class="gr-sug hidden" id="grSug" role="listbox"></div></div>
        <span class="gr-tag" id="grNombreTag"></span>
        <label for="grProy">Proyecto</label>
        <div class="gr-proyecto"><select id="grProy" class="gr-proy" title="Proyecto › workstream: su carpeta, su idioma y su vocabulario"></select>
          <span class="gr-motivo" id="grMotivo"></span></div>
        <label class="hidden" id="grAsistL">Asistentes</label><div class="gr-asist hidden" id="grAsist"></div>
      </div>
      <div class="gr-propuesta hidden" id="grPropuesta"></div>
      <div class="gr-plan" aria-live="polite"><div class="gr-plan-h">Así se va a grabar</div>
        <div id="grFilas"><div class="gr-cargando">Buscando la reunión de Teams…</div></div></div>
      <button type="button" class="gr-anadir hidden" id="grAnadirCtx" data-gr-cambiar="contexto">${I.plus}Contexto: de qué va y quién participa</button>
      <div class="gr-atajo">${I.keyboard}${hk}</div>
    </div>
    <div class="btns gr-pie"><button class="btn ghost sm" data-m="consent" type="button">${I.clipboard}Copiar aviso para el chat <span class="muted">es · en · pt</span></button>
      <span class="gr-hueco"></span>
      <button class="btn" data-m="no" type="button">Cancelar</button>
      <button class="btn primary rec-go" id="rGo" type="button" disabled><i class="recdot"></i>Empezar a grabar <kbd>Intro</kbd></button></div>`, d => grMontar(d), 'gr');
  S.modalCleanup = () => { if (recDialog) { recDialog = false; api('/api/rec/monitor', { stop: true }).catch(() => { }); } G = null; };
}

async function grMontar(d) {
  G.d = d;
  const nom = $('#grNombre', d);  // al subir varios audios no hay nombre: cada uno lleva el suyo
  if (nom && G.tocado.nombre && 'nombre' in G.pedido) nom.value = G.pedido.nombre;  // al volver de editar una lista
  d.addEventListener('click', e => { if (e.target.closest('[data-m="no"]')) closeModal(false); });
  const aviso = d.querySelector('[data-m="consent"]');
  if (aviso) aviso.onclick = () => copy(CONSENT_TEXT, 'Aviso copiado: pégalo en el chat de la reunión');
  $('#rGo', d).onclick = grEmpezar;
  if (nom) grNombreEventos(d);
  $('#grProy', d).onchange = e => grProyectoElegido(e.target.value);
  d.addEventListener('click', grClick);
  d.addEventListener('change', grCambio);
  d.addEventListener('keydown', grTecla);
  if (G.subir) return sbMontar(d);
  const g = G;
  // a la vez: dispositivos (medidores), listas de vocabulario y la reunión en curso (Teams y, si está activado,
  // Outlook). La tarjeta espera a la reunión como mucho 2 s y se rellena una sola vez (D4)
  const devs = api('/api/rec/devices').then(r => { g.devs = r; }).catch(err => { g.devsErr = err.message; });
  const voc = loadVocab(S.config.language ?? 'es-en');
  const reunion = api('/api/reunion/actual').then(r => r?.sugerencia || {}).catch(() => ({}));
  await Promise.race([reunion, new Promise(r => setTimeout(r, 2000))]);
  await voc;
  if (G !== g) return;
  await grPedir({ recientes: true });
  if (G !== g) return;
  grMonitor();
  pollRec();
  $('#rGo', d).disabled = false;
  devs.then(() => { if (G === g) grPintarFilas(); });
  // la reunión llegó tarde (Outlook tarda): si no has tocado nada, se rellena; si sí, se propone sin pisar lo tuyo
  reunion.then(async sug => {
    if (G !== g || !sug.name || sug.name === g.plan?.sugerencia?.nombre) return;
    if (!g.tocado.nombre && !g.tocado.vocab && !g.tocado.algo && !g.abierto) return grPedir();
    grProponer(sug);
  });
  if (!S.vocab.length) $('#grNombre', d).focus();
}

/* volver a la tarjeta abierta (la de grabar o la de subir) con lo que tenías, tras editar o conectar una lista */
function grVolver(extra = {}) {
  return G?.subir ? abrirSubir(null, { ...extra, seguir: true }) : abrirGrabar({ ...extra, seguir: true });
}

/* ---------------------------------------------------------------- pedir el plan al servidor */
function grPedir(extra = {}) {
  const g = G;
  if (!g) return Promise.resolve();
  const seq = ++g.seq;
  const p = api('/api/grabar/plan', { ...g.pedido, ...extra }).then(plan => {
    if (G !== g || seq !== g.seq) return;  // llegó otra más nueva
    if (plan.recientes) g.recientes = plan.recientes;
    g.plan = plan;
    grPintar();
  }).catch(err => {
    if (G !== g || seq !== g.seq) return;
    $('#grFilas', g.d).innerHTML = `<div class="notice">${esc(err.message)}</div>`;
  });
  g.pendiente = p;
  return p;
}
function grPedirLuego() {
  clearTimeout(G.timer);
  G.timer = setTimeout(() => { G.timer = null; grPedir(); }, 250);
}

/* ---------------------------------------------------------------- pintar */
function grPintar() {
  const g = G, p = g.plan, d = g.d;
  const nombre = $('#grNombre', d);
  if (nombre && !g.tocado.nombre && nombre.value !== (p.nombre.valor || '')) nombre.value = p.nombre.valor || '';  // no lo has tocado
  if (nombre) nombre.placeholder = 'Nombre de la reunión (opcional)';
  const tag = $('#grNombreTag', d) || document.createElement('span');
  // con signos que un nombre de archivo no admite, se graba sin ellos: se dice cómo quedará
  tag.textContent = p.nombre.archivo != null ? `se guarda como «${p.nombre.archivo || 'sin nombre'}»` : p.nombre.texto || '';
  tag.title = p.nombre.archivo != null ? 'Un nombre de archivo no puede llevar \\ / : * ? " < > |' : '';
  tag.className = 'gr-tag' + (p.nombre.archivo != null ? ' archivo'
    : p.nombre.origen === 'teams' || p.nombre.origen === 'outlook' ? ' de-reunion' : '');
  const sel = $('#grProy', d);
  sel.innerHTML = `<option value="">Sin proyecto</option>${(S.vocab || []).map(v => `<option value="${esc(v.id)}">${esc(v.project)} › ${esc(v.name)}</option>`).join('')}`
    + `<option value="__nuevo">Nuevo proyecto o workstream…</option>`
    + (typeof vocabConectar === 'function' ? `<option value="${GR_CONECTAR}">Usar las listas de una carpeta…</option>` : '');
  sel.value = p.proyecto.vocab || '';
  sel.classList.toggle('vacio', !p.proyecto.vocab);
  $('#grMotivo', d).textContent = p.proyecto.motivo || '';
  $('#grLlamada', d)?.classList.toggle('hidden', !S.rec.meeting && p.fuente !== 'teams');
  // asistentes de la cita de Outlook: a las notas como sugeridos, sin salir del PC hasta que los confirmes
  const part = grPartes(p.rec.participantes);
  $('#grAsistL', d)?.classList.toggle('hidden', !part.length);
  $('#grAsist', d)?.classList.toggle('hidden', !part.length);
  if (part.length) {
    const sep = grSep(part), nombres = part.slice(0, 4).join(sep) + (part.length > 4 ? ` y ${part.length - 4} más` : '');
    $('#grAsist', d).innerHTML = `<span title="${esc(part.join(sep))}"><b>${esc(nombres)}</b> · de ${p.rec.sugerido === 'outlook' ? 'Outlook' : 'Teams'}</span>`
      + ` <span class="muted">Irán a las notas de la reunión como sugeridos: no se envían al resumen ni a las copias en otras carpetas hasta que los confirmes.</span>`
      + ` <a href="#" data-gr-sin-asist>Quitar</a>`;
  }
  grPintarFilas();
}

/* los asistentes, como los une el servidor (plan.unir_participantes / calendario.join_participants): con «; » si algún
   nombre lleva coma («Apellido, Nombre», el directorio de Outlook de una empresa) y si no con «, » */
function grPartes(texto) {
  const t = String(texto || ''), sep = t.includes(';') ? ';' : ',';
  return t.split(sep).map(x => x.trim()).filter(Boolean);
}
function grSep(nombres) { return nombres.some(n => n.includes(',')) ? '; ' : ', '; }
/* la lista de micrófonos y salidas no ha llegado (sin grabadora, o Windows no contesta): una frase, y el detalle del
   servidor (la cola del error de la grabadora) solo al pasar el ratón. También lo usa Ajustes › Al grabar */
function grDevsAviso(msg, resto = '') {
  return `<span class="gr-aviso" title="${esc(msg || '')}">No se ha podido leer la lista de micrófonos y salidas de audio${resto}.</span>`;
}

function grValorHtml(l) {
  // el aviso de una línea (la carpeta que no se encuentra, un proyecto sin carpeta propia, la API key que falta…), en
  // la tarjeta como en Ajustes: es al grabar cuando importa (revisión 4 de B2)
  const aviso = l.aviso ? `<span class="gr-aviso">${esc(l.aviso)}</span>` : '';
  if (l.clave === 'carpeta') return `<code class="gr-ruta" title="${esc(l.valor)}">${esc(l.valor)}</code>${aviso}`;
  if (l.clave === 'audio') {
    const nom = (list, id) => (list || []).find(x => x.id === id)?.name;
    const mic = l.mic === 'none' ? 'Sin micrófono' : 'Tu micro', pc = l.sys === 'none' ? 'sin el audio del PC' : 'el PC';
    const t = [l.mic === 'default' ? 'Micrófono predeterminado de Windows' : nom(G.devs?.mics, l.mic) || '',
      l.sys === 'all' ? 'Todo lo que suena en el ordenador' : nom(G.devs?.outputs, l.sys) || ''].filter(Boolean).join(' · ');
    return `<span class="gr-audio" title="${esc(t)}"><b>${mic}</b>${l.mic === 'none' ? '' : grMedidor('mic')}<b>· ${pc}</b>${l.sys === 'none' ? '' : grMedidor('sys')}</span>`
      + (G.devsErr ? grDevsAviso(G.devsErr) : '') + aviso;
  }
  if (l.clave === 'contexto') return esc(l.valor) + aviso;
  return `<b>${esc(l.valor)}</b>${aviso}`;
}
/* medidor pequeño con la estructura que espera updateMeters() de app.js */
function grMedidor(kind) {
  return `<span class="meter gr-meter" data-meter="${kind}"><span class="ml"></span><div class="mbar"><i></i></div><small class="mdb"></small><small class="mdev"></small></span>`;
}

function grPintarFilas() {
  const g = G, p = g?.plan, box = g && $('#grFilas', g.d);
  if (!p || !box) return;
  if (!box.querySelector('[data-fila]')) {
    box.innerHTML = grFilas().map(([k, icon]) => `<div class="gr-fila" data-fila="${k}"><span class="gr-ic">${I[icon]}</span>`
      + `<span class="gr-et"></span><span class="gr-v"></span><span class="gr-origen"></span>`
      + `<button class="gr-cambiar" type="button" data-gr-cambiar="${k}" aria-expanded="false">Cambiar</button></div>`
      + `<div class="gr-panel hidden" data-panel="${k}"></div>`).join('');
  }
  for (const [k] of grFilas()) {
    const l = grFila(p, k), row = box.querySelector(`[data-fila="${k}"]`);
    if (!l || !row) continue;
    // el contexto solo se ve si hay uno (Ajustes o esta reunión) o si lo estás cambiando
    row.classList.toggle('hidden', k === 'contexto' && !l.valor && g.abierto !== 'contexto');
    $('.gr-et', row).textContent = l.etiqueta;
    $('.gr-v', row).innerHTML = grValorHtml(l);
    const o = $('.gr-origen', row);
    o.textContent = l.origen_txt;
    o.className = 'gr-origen' + (l.origen === 'reunion' ? ' cambiado' : '');
    row.classList.toggle('cambiado', l.origen === 'reunion');
  }
  // el modelo del directo aún no está en el PC: se dice antes de empezar (configurar.js)
  const t = grFila(p, 'transcribe');
  const falta = (t?.live || g.subir) && typeof faltaModeloHtml === 'function' ? faltaModeloHtml(t?.preset, g.subir ? 'subir' : '') : '';
  let fm = $('.gr-falta', box);
  if (!fm) { fm = document.createElement('div'); fm.className = 'gr-falta'; box.querySelector('[data-panel="transcribe"]').after(fm); }
  if (fm.dataset.sig !== falta) { fm.innerHTML = falta; fm.dataset.sig = falta; }
  g.d.pintaMotor = grPintarFilas;  // al llegar el % de una descarga (configurar.js)
  $('#grAnadirCtx', g.d)?.classList.toggle('hidden', !!grFila(p, 'contexto')?.valor || g.abierto === 'contexto');
  // con un panel abierto, «Usarlo en» sigue al proyecto que diga el plan ahora (p. ej. tras escribir otro nombre)
  const panel = g.abierto && box.querySelector(`[data-panel="${g.abierto}"]`);
  if (panel && g.panelProy !== grProyecto(p)) {
    g.panelProy = grProyecto(p);
    const nuevo = grAlcanceHtml(g.abierto), viejo = panel.querySelector('.gr-alcance');
    const marcado = viejo?.querySelector('input:checked')?.value;  // lo que habías marcado se queda, si sigue
    if (viejo) viejo.outerHTML = nuevo;
    else if (nuevo) panel.querySelector('.gr-panel-pie')?.insertAdjacentHTML('beforebegin', nuevo);
    const r = marcado && panel.querySelector(`.gr-alcance input[value="${marcado}"]`);
    if (r) r.checked = true;
  }
}

/* ---------------------------------------------------------------- «Cambiar» */
function grAlcanceHtml(k) {
  const opts = GR_ALCANCES[k];
  if (!opts || (G.subir && k === 'transcribe')) return '';
  const proy = grProyecto(G.plan);
  const txt = { reunion: G.subir?.items.length > 1 ? 'Solo estos audios' : 'Solo esta reunión', proyecto: proy ? `Todas las de ${esc(proy)}` : '',
    ajustes: k === 'carpeta' ? (proy ? '' : 'Todas las reuniones sin proyecto (Ajustes)') : 'Todas (Ajustes)' };
  const vis = opts.filter(a => txt[a]);
  if (vis.length < 2) return '';
  return `<div class="gr-alcance" role="radiogroup" aria-label="Usarlo en"><span class="gr-alcance-t">Usarlo en</span>${vis.map((a, i) =>
    `<label><input type="radio" name="grAlc_${k}" value="${a}"${i === 0 ? ' checked' : ''}>${txt[a]}</label>`).join('')}</div>`;
}
function grPanelHtml(k) {
  const p = G.plan, l = grFila(p, k) || {};
  const quitar = l.origen === 'reunion' ? `<a href="#" class="gr-quitar" data-gr-quitar="${k}">Quitar el cambio</a>` : '';
  const pie = (alc = true) => `${alc ? grAlcanceHtml(k) : ''}<div class="gr-panel-pie">${quitar}<button class="btn sm primary" type="button" data-gr-listo="${k}">Listo</button></div>`;
  if (k === 'carpeta') {
    return `<label class="gr-l" for="grSelCarpeta">Guardar esta reunión en</label><select data-gr="carpeta" id="grSelCarpeta">${folderOpts(l.rel)}</select>${pie()}`;
  }
  if (k === 'idioma') {
    return `<label class="gr-l" for="grSelIdioma">Idioma de la reunión</label><select data-gr="idioma" id="grSelIdioma">${LANGS.map(([v, t]) => `<option value="${v}"${v === l.code ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <div class="help">Las mezclas detectan el idioma cada pocos segundos, pero solo entre esos dos. Con uno solo, lo que se diga en otro idioma se pasa a ese.</div>${pie()}`;
  }
  if (k === 'contexto') {
    return `<label class="gr-l" for="grCtx">De qué va y quién participa</label><input class="input" data-gr="contexto" id="grCtx" maxlength="500" value="${esc(l.valor || '')}" placeholder="Una o dos frases: las recibe Whisper detrás del vocabulario, y el resumen" autocomplete="off">${pie()}`;
  }
  if (k === 'transcribe' && G.subir) return sbPanelTranscribe(l, pie);
  if (k === 'transcribe') {
    const presets = (S.config.presets || []).map(x => `<option value="${esc(x.id)}"${x.id === l.preset ? ' selected' : ''}>${esc(x.live_label || x.label)} · ${liveFit(x)[1]}</option>`).join('');
    return `<div class="seg2 gr-modo"><label class="${l.live ? 'on' : ''}"><input type="radio" name="grLive" data-gr="live" value="1"${l.live ? ' checked' : ''}><b>En directo</b><span>El texto va saliendo mientras se habla</span></label>
      <label class="${l.live ? '' : 'on'}"><input type="radio" name="grLive" data-gr="live" value="0"${l.live ? '' : ' checked'}><b>Solo grabar</b><span>Si el portátil va lento: se transcribe al terminar</span></label></div>
      <label class="gr-l" for="grSelPreset">Modelo del directo</label><select data-gr="preset" id="grSelPreset"${l.live ? '' : ' disabled'}>${presets}</select>${pie()}`;
  }
  if (k === 'audio') {
    const o = (v, t, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(t)}</option>`;
    const dv = G.devs || { mics: [], outputs: [] }, def = list => nfc((list.find(x => x.default) || {}).name || '—');
    const mics = o('default', `Predeterminado de Windows (ahora: ${def(dv.mics)})`, l.mic)
      + `<optgroup label="Fijar un micrófono">${dv.mics.map(x => o(x.id, nfc(x.name), l.mic)).join('')}</optgroup>` + o('none', 'Sin micrófono', l.mic);
    const outs = o('all', 'Todo lo que suena en el ordenador · recomendado', l.sys)
      + o('default', `Salida predeterminada de Windows (ahora: ${def(dv.outputs)})`, l.sys)
      + `<optgroup label="Fijar una salida">${dv.outputs.map(x => o(x.id, nfc(x.name), l.sys)).join('')}</optgroup>` + o('none', 'No grabar el audio del ordenador', l.sys);
    return `<label class="gr-l" for="grSelMic">Micrófono</label><select data-gr="mic" id="grSelMic">${mics}</select>
      <label class="gr-l" for="grSelSys">Audio del ordenador</label><select data-gr="sys" id="grSelSys">${outs}</select>
      <div class="help">Con las opciones de arriba da igual que conectes o quites los cascos: la grabación sigue con el dispositivo nuevo. Pon algo a sonar para ver que la barra se mueve.</div>${pie()}`;
  }
  if (k === 'vocab') {
    const v = (S.vocab || []).find(x => x.id === p.proyecto.vocab);
    return `<div class="help" style="margin-top:0">El vocabulario es la lista del proyecto › workstream de arriba: nombres, siglas y productos que Whisper debe escribir bien.${v ? ` ${vocabFit(v)}` : ''}</div>
      <div class="gr-panel-pie"><button class="btn sm" type="button" data-gr-ir="proyecto">Elegir otro proyecto</button>
        ${v ? `<button class="btn sm" type="button" data-gr-ir="editar">${I.edit}Editar la lista</button>` : ''}
        <button class="btn sm" type="button" data-gr-ir="nuevo">${I.plus}Nuevo proyecto o workstream…</button>
        ${typeof vocabConectar === 'function' ? `<button class="btn sm" type="button" data-gr-ir="conectar" title="Elige la carpeta donde tienes tus listas: se leen al grabar, sin copiarlas">${I.folder}Usar las listas de una carpeta…</button>` : ''}
        <button class="btn sm primary" type="button" data-gr-listo="vocab">Listo</button></div>`;
  }
  // resume: lo decide Ajustes › Procesado
  return `<div class="help" style="margin-top:0">Quién resume, y si lo hace solo al terminar, se elige en Ajustes › Procesado. Vale para todas las reuniones.</div>
    <div class="gr-panel-pie"><button class="btn sm" type="button" data-gr-ir="procesado">Abrir Ajustes › Procesado</button><button class="btn sm primary" type="button" data-gr-listo="resume">Listo</button></div>`;
}
function grAbrirPanel(k) {
  const g = G, box = $('#grFilas', g.d);
  if (g.abierto && g.abierto !== k) grCerrarPanel();
  if (g.abierto === k) return grListo(k);
  g.abierto = k;
  g.tocado.algo = true;
  g.panelProy = grProyecto(g.plan);
  const panel = box.querySelector(`[data-panel="${k}"]`);
  panel.innerHTML = grPanelHtml(k);
  panel.classList.remove('hidden');
  box.querySelector(`[data-gr-cambiar="${k}"]`).setAttribute('aria-expanded', 'true');
  box.querySelector(`[data-fila="${k}"]`).classList.add('abierta');
  grPintarFilas();
  (panel.querySelector('select, input:not([type=radio])') || panel.querySelector('button'))?.focus();
}
function grCerrarPanel() {
  const g = G, k = g?.abierto, box = g && $('#grFilas', g.d);
  if (!k || !box) return;
  g.abierto = null;
  const panel = box.querySelector(`[data-panel="${k}"]`);
  panel.classList.add('hidden');
  panel.innerHTML = '';
  box.querySelector(`[data-gr-cambiar="${k}"]`)?.setAttribute('aria-expanded', 'false');
  box.querySelector(`[data-fila="${k}"]`)?.classList.remove('abierta');
  grPintarFilas();
}
/* «Listo»: con «Solo esta reunión» el cambio ya está en el pedido; con el proyecto o Ajustes se guarda allí y la línea
   pasa a decir de dónde sale (y el pedido lo suelta: lo hereda) */
async function grListo(k) {
  const g = G, panel = $(`[data-panel="${k}"]`, g.d);
  const alc = panel?.querySelector(`input[name="grAlc_${k}"]:checked`)?.value || 'reunion';
  // lo cambiado en el panel o, si no has cambiado nada, lo que se ve: «Usarlo en» guarda ese valor
  const l = grFila(g.plan, k) || {};
  const actual = { carpeta: l.rel, idioma: l.code, contexto: l.valor, live: l.live, preset: l.preset, mic: l.mic, sys: l.sys };
  const claves = (GR_CLAVES[k] || []).filter(c => c in g.pedido.cambios);
  const guardar = claves.length ? claves : (GR_CLAVES[k] || []).filter(c => actual[c] !== undefined);
  if (alc !== 'reunion' && guardar.length) {
    try {
      for (const c of guardar) {
        const clave = k === 'transcribe' || k === 'audio' ? c : k;
        const valor = c in g.pedido.cambios ? g.pedido.cambios[c] : actual[c];
        await api('/api/grabar/recordar', { clave, valor, alcance: alc, vocab: g.plan.proyecto.vocab || '' });
        delete g.pedido.cambios[c];
      }
      const cfg = await api('/api/config');
      if (typeof setConfig === 'function') setConfig(cfg);
      toast(alc === 'proyecto' ? `Guardado para todas las reuniones de <b>${esc(grProyecto(g.plan))}</b>` : 'Guardado en Ajustes › Al grabar: vale para todas las reuniones');
    } catch (err) { toast(esc(err.message), 'err'); }
  }
  grCerrarPanel();
  await grPedir();
  $(`[data-gr-cambiar="${k}"]`, g.d)?.focus();
}

/* ---------------------------------------------------------------- eventos */
function grClick(e) {
  const c = e.target.closest('[data-gr-cambiar]');
  if (c) return grAbrirPanel(c.dataset.grCambiar);
  const l = e.target.closest('[data-gr-listo]');
  if (l) return grListo(l.dataset.grListo);
  const q = e.target.closest('[data-gr-quitar]');
  if (q) {
    e.preventDefault();
    (GR_CLAVES[q.dataset.grQuitar] || []).forEach(k => delete G.pedido.cambios[k]);
    grCerrarPanel();
    return grPedir();
  }
  const ir = e.target.closest('[data-gr-ir]');
  if (ir) {
    const v = (S.vocab || []).find(x => x.id === G.plan?.proyecto?.vocab);
    const volver = () => grVolver({ vocab: S.vocabJustSaved || undefined });
    if (ir.dataset.grIr === 'proyecto') { grCerrarPanel(); $('#grProy', G.d).focus(); }
    else if (ir.dataset.grIr === 'editar' && v) openVocabEditor(v, volver);
    else if (ir.dataset.grIr === 'nuevo') openVocabEditor(null, volver, v?.project || '');
    else if (ir.dataset.grIr === 'conectar' && typeof vocabConectar === 'function') vocabConectar(volver);
    else if (ir.dataset.grIr === 'procesado') { closeModal(false); openSettings({ sec: 'procesado' }); }
    return;
  }
  const s = e.target.closest('[data-sug-titulo]');
  if (s) return grElegirReciente(+s.dataset.sugTitulo);
  if (e.target.closest('[data-gr-sin-asist]')) {
    e.preventDefault();
    G.pedido.participantes = '';
    return grPedir();
  }
  const u = e.target.closest('[data-gr-usar]');
  if (u) {
    e.preventDefault();
    delete G.pedido.nombre;
    G.tocado.nombre = false;
    $('#grNombre', G.d).value = '';
    $('#grPropuesta', G.d).classList.add('hidden');
    return grPedir();
  }
}
async function grCambio(e) {
  const x = e.target.closest('[data-gr]');
  if (!x || !G) return;
  G.tocado.algo = true;
  const k = x.dataset.gr, c = G.pedido.cambios;
  if (k === 'carpeta' && x.value === '__pick') {  // otra carpeta: se vincula y se elige
    x.value = grFila(G.plan, 'carpeta')?.rel || '';
    const rel = await linkFolder();
    if (!rel || !G) return;
    x.innerHTML = folderOpts(rel);
    c.carpeta = rel;
  } else if (k === 'live') {
    c.live = x.value === '1';
    $$('.gr-modo label', G.d).forEach(lb => lb.classList.toggle('on', lb.querySelector('input').checked));
    const ps = $('[data-gr="preset"]', G.d);
    if (ps) ps.disabled = !c.live;
  } else {
    c[k] = x.value;
  }
  if (k === 'mic' || k === 'sys') grMonitor();
  grPedir();
}
function grProyectoElegido(val) {
  if (val === '__nuevo') {
    $('#grProy', G.d).value = G.plan?.proyecto?.vocab || '';
    return openVocabEditor(null, () => grVolver({ vocab: S.vocabJustSaved || undefined }));
  }
  if (val === GR_CONECTAR) {
    $('#grProy', G.d).value = G.plan?.proyecto?.vocab || '';
    if (typeof vocabConectar === 'function') vocabConectar(() => grVolver());
    return;
  }
  G.pedido.vocab = val;
  G.tocado.vocab = true;
  grPedir();
}
function grTecla(e) {
  if (e.key !== 'Enter' || e.isComposing || e.defaultPrevented) return;
  const t = e.target;
  if (t.id === 'grNombre' && !$('#grSug', G.d).classList.contains('hidden') && G.sugIdx >= 0) return;  // lo lleva el autocompletado
  if (t.closest('.gr-panel')) { if (t.tagName !== 'BUTTON' && t.tagName !== 'A') { e.preventDefault(); grListo(G.abierto); } return; }
  if (t.tagName === 'BUTTON' || t.tagName === 'A' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA') return;
  e.preventDefault();
  grEmpezar();
}
function grProponer(sug) {
  const box = $('#grPropuesta', G.d);
  box.innerHTML = `${I.phone}<span>${sug.source === 'outlook' ? 'Outlook' : 'Teams'} dice «${esc(sug.name)}»</span><a href="#" data-gr-usar>Usar</a>`;
  box.classList.remove('hidden');
}

/* ---------------------------------------------------------------- nombre con autocompletado (B3: sin calendario) */
function grNombreEventos(d) {
  const inp = $('#grNombre', d), box = $('#grSug', d);
  inp.addEventListener('input', () => {
    G.pedido.nombre = inp.value;
    G.tocado.nombre = true;
    grSugerir();
    grPedirLuego();
  });
  inp.addEventListener('focus', grSugerir);
  inp.addEventListener('blur', () => setTimeout(() => box.classList.add('hidden'), 150));
  box.addEventListener('mousedown', e => e.preventDefault());  // elegir con el ratón no le quita el foco a la caja
  inp.addEventListener('keydown', e => {
    const items = $$('[data-sug-titulo]', box), n = items.length;
    if (box.classList.contains('hidden') || !n) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (e.key === 'ArrowDown') G.sugIdx = G.sugIdx + 1 >= n ? -1 : G.sugIdx + 1;
      else G.sugIdx = G.sugIdx - 1 < -1 ? n - 1 : G.sugIdx - 1;
      items.forEach((x, i) => x.classList.toggle('on', i === G.sugIdx));
      inp.setAttribute('aria-activedescendant', G.sugIdx >= 0 ? `grSug${G.sugIdx}` : '');
    } else if (e.key === 'Enter' && G.sugIdx >= 0) {
      e.preventDefault();
      e.stopPropagation();  // elige la reunión y enseña su plan: Intro otra vez, y entonces graba
      grElegirReciente(+items[G.sugIdx].dataset.sugTitulo);
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      box.classList.add('hidden');
    }
  });
}
function grCoinciden(q) {
  const f = fold(q.trim());
  const rs = G.recientes || [];
  if (!f) return rs.slice(0, 6);
  return rs.filter(r => fold(r.titulo).includes(f) && fold(r.titulo) !== f).slice(0, 6);
}
function grSugerir() {
  const g = G, box = $('#grSug', g.d), inp = $('#grNombre', g.d);
  const rs = grCoinciden(inp.value);
  g.sugIdx = -1;
  if (!rs.length || document.activeElement !== inp) { box.classList.add('hidden'); return; }
  box.innerHTML = rs.map((r, i) => `<button type="button" role="option" id="grSug${i}" class="gr-sug-i" data-sug-titulo="${g.recientes.indexOf(r)}" tabindex="-1">`
    + `<span>${hlTerms(r.titulo, termsOf(inp.value))}</span><small>${esc(r.proyecto_txt || 'Sin proyecto')} · ${r.n}</small></button>`).join('')
    + `<div class="gr-sug-pie">Tus reuniones recientes · elegir una trae su proyecto</div>`;
  box.classList.remove('hidden');
}
function grElegirReciente(i) {
  const r = G.recientes[i], inp = $('#grNombre', G.d);
  if (!r) return;
  inp.value = r.titulo;
  G.pedido.nombre = r.titulo;
  G.tocado.nombre = true;
  $('#grSug', G.d).classList.add('hidden');
  clearTimeout(G.timer);
  grPedir();
  inp.focus();
}

/* ---------------------------------------------------------------- medidores y empezar */
function grMonitor() {
  const a = grFila(G?.plan, 'audio');
  if (!a) return;
  api('/api/rec/monitor', { mic: a.mic, sys: a.sys }).catch(() => { });
}
async function grEmpezar() {
  if (G?.subir) return sbSubir();
  const g = G, b = g && $('#rGo', g.d);
  if (!g || !b || b.disabled) return;
  b.disabled = true;
  if (g.timer) { clearTimeout(g.timer); g.timer = null; grPedir(); }
  await g.pendiente;  // lo último pedido: se graba con lo que se está viendo
  const p = g.plan;
  if (!p || G !== g) { b.disabled = false; return; }
  try {
    S.recLocal = true;
    const r = await api('/api/rec/start', { ...p.rec, resumen: p.resumen });
    recDialog = false;
    closeModal(true);
    toast(`● Grabando · ${esc(p.resumen)}${r.aviso ? ` · ${esc(r.aviso)}` : ''}`);
    await refreshTree(true);
    pollRec(); pollJobs(true);
    go('a', r.path);
  } catch (err) { S.recLocal = false; toast(esc(err.message), 'err'); b.disabled = false; }
}

/* ---------------------------------------------------------------- «Subir audio» (09/11/2026, msg 1296) */
/* Un audio que ya tienes (el móvil, un audio de WhatsApp, otra grabadora) entra como una reunión grabada con esta
   tarjeta: el mismo plan (/api/grabar/plan con `subir`), sin la línea de audio y con la transcripción de la cola. «Subir
   y transcribir» manda cada archivo a /api/subir con lo que se está viendo; el servidor comprueba con la cadena de la
   cola (PyAV) que se puede leer antes de guardarlo y lo pone a transcribir. Se llega con «Subir audio» (arriba, junto
   a Grabar), con «Subir audio aquí» en una carpeta y soltando audios en la ventana. */
const SB_EXT = ['.wav', '.mp3', '.m4a', '.mp4', '.flac', '.ogg', '.aac', '.wma', '.opus', '.mkv', '.mov', '.webm'];
function sbExts() { return S.config?.audio_ext?.length ? S.config.audio_ext : SB_EXT; }
function sbExt(f) { const m = /\.[^.\\/]+$/.exec(f?.name || ''); return m ? m[0].toLowerCase() : ''; }
function esAudio(f) { return sbExts().includes(sbExt(f)); }
// lo mismo que dice el servidor (FORMATOS_SUBIR) de un archivo que no es un audio
const SB_FORMATOS = 'm4a, mp3, opus, ogg, wav, aac, webm, flac, amr o un vídeo mp4 o mov';
const sbNoAudio = f => `«${esc(f.name)}» no es un audio que ${esc(APP_NAME)} sepa leer. Sirven ${SB_FORMATOS}.`;

/* el selector de archivos de Windows con los formatos que se leen; lo elegido abre la tarjeta */
function elegirAudios(opts = {}) {
  let inp = $('#sbInput');
  if (!inp) {
    inp = document.createElement('input');
    inp.type = 'file'; inp.id = 'sbInput'; inp.multiple = true; inp.hidden = true; inp.tabIndex = -1;
    document.body.append(inp);
  }
  inp.accept = sbExts().join(',');
  inp.value = '';
  inp.onchange = () => { const fs = [...inp.files]; inp.value = ''; if (fs.length) abrirSubir(fs, opts); };
  inp.click();
}

/* el nombre y la fecha que propone el archivo: «WhatsApp Ptt 2026-11-08 at 10.15.32», «PTT-20261108-WA0003»,
   «2026-11-08 10-15-32 - Comité» (de Tenca), «Nueva grabación 12»… Sin fecha en el nombre, la de cuándo se guardó */
function sbDesdeArchivo(f) {
  const stem = String(f.name || '').replace(/\.[^.]+$/, '');
  const ok = (y, mo, d, h = 0, mi = 0, se = 0) => y >= 2000 && y < 2100 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31 && h < 24 && mi < 60 && se < 60;
  let fecha = null, origen = 'archivo', resto = stem;
  const m = /(20\d{2})-?(\d{2})-?(\d{2})(?:[ _T-]|\s+at\s+)(\d{2})[.:_-]?(\d{2})(?:[.:_-]?(\d{2}))?(?!\d)/i.exec(stem);
  if (m && ok(+m[1], +m[2], +m[3], +m[4], +m[5], +(m[6] || 0))) {
    fecha = new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    origen = 'nombre';
    resto = stem.slice(0, m.index) + ' ' + stem.slice(m.index + m[0].length);
  } else {
    const d = /(?:^|\D)(20\d{2})-?(\d{2})-?(\d{2})(?!\d)/.exec(stem);
    if (d && ok(+d[1], +d[2], +d[3])) {  // solo el día (los de WhatsApp en Android): la hora, la del archivo
      const t = new Date(f.lastModified || Date.now());
      fecha = new Date(+d[1], d[2] - 1, +d[3], t.getHours(), t.getMinutes(), t.getSeconds());
      origen = 'nombre';
    }
  }
  if (!fecha) { fecha = new Date(f.lastModified || Date.now()); origen = f.lastModified ? 'guardado' : 'ahora'; }
  let nombre = /^(PTT|AUD)-\d{8}-WA\d+$/i.test(stem) || /^WhatsApp (Ptt|Audio)\b/i.test(stem) ? 'Mensaje de voz de WhatsApp'
    : resto.replace(/\s+/g, ' ').replace(/^[\s\-_·]+|[\s\-_·]+$/g, '');
  // lo que pone la grabadora sin decir nada («AUDIO_», «REC 12», «Recording», «Grabación 3», «Voice 001»): sin nombre
  if (/^(AUDIO|REC|VID|Recording|Grabaci[oó]n|Voice|Voz)?[\s_-]*\d*$/i.test(nombre)) nombre = '';
  return { file: f, nombre: nombre.slice(0, 120), fecha: fecha.getTime(), origen };
}
const SB_FECHA_ORIGEN = { nombre: 'del nombre del archivo', guardado: 'de cuándo se guardó el archivo', ahora: 'de ahora', tuya: 'cambiada por ti' };
function sbFechaInput(ms) {  // «AAAA-MM-DDTHH:MM» en hora local, para <input type="datetime-local">
  const d = new Date(ms), z = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;
}
// los audios de WhatsApp son de pocos KB: «1 MB» (fmtSize) engañaría
const sbTam = b => b >= 1e6 ? fmtSize(b) : `${Math.max(1, Math.round(b / 1e3))} KB`;
function sbFechaTxt(ms) {
  const d = new Date(ms), z = n => String(n).padStart(2, '0');
  return `${d.getDate()} ${MES[d.getMonth()]} ${d.getFullYear()}, ${z(d.getHours())}:${z(d.getMinutes())}`;
}

/* abrir la tarjeta con los archivos elegidos (files) o, con seguir, la que había (al volver de editar una lista) */
async function abrirSubir(files, opts = {}) {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  // la tarjeta de subir abierta (y sin subir nada) sigue con lo elegido: al volver de editar una lista o tras un fallo
  // (seguir), al soltar más audios (anadir: se suman) y con «Elegir otro» (reemplazar: los nuevos en su lugar)
  const actual = G?.subir && !G.enviando ? G : null;
  const base = actual && (opts.seguir || opts.anadir || opts.reemplazar) ? actual : null;
  let items = base && !opts.reemplazar ? base.subir.items : [];
  let rechazados = base && !opts.reemplazar ? base.subir.rechazados : [];
  if (files) {
    items = items.concat(files.filter(esAudio).map(sbDesdeArchivo));
    rechazados = rechazados.concat(files.filter(f => !esAudio(f)));
  }
  if (!items.length) {
    if (rechazados.length) toast(sbNoAudio(rechazados[0]), 'err');
    return;
  }
  const varios = items.length > 1;
  const pedido = base ? base.pedido : { cambios: {} };
  pedido.subir = true;
  pedido.varios = varios;
  if (!base && opts.carpeta) pedido.cambios.carpeta = opts.carpeta;  // desde una carpeta: ahí
  if (opts.vocab != null) pedido.vocab = opts.vocab;
  const tocado = base ? base.tocado : {};
  // un audio nuevo (o el único que queda) trae su nombre y su fecha; lo escrito para otro no se le pone
  const nuevoUno = !varios && (!base || !('nombre' in pedido) || opts.reemplazar);
  if (varios) { delete pedido.nombre; delete pedido.archivo; delete pedido.fecha; }
  else if (nuevoUno) {
    const it = items[0];
    Object.assign(pedido, { nombre: it.nombre, archivo: it.nombre, fecha: it.fecha });
    tocado.nombre = false;
  }
  G = { pedido, plan: null, tocado, recientes: [], seq: 0, abierto: null, pendiente: null, timer: null, devs: null,
    sugIdx: -1, subir: { items, rechazados, fechaOrigen: nuevoUno ? items[0].origen : base?.subir?.fechaOrigen || items[0].origen } };
  const uno = items[0], total = items.reduce((n, it) => n + (it.file.size || 0), 0);
  const lista = varios
    ? `<div class="sb-lista" role="list">${items.map(it => `<div class="sb-item" role="listitem">${I.wave}<span class="sb-nom" title="${esc(it.file.name)}">${esc(nfc(it.file.name))}</span>`
      + `<span class="muted">${sbTam(it.file.size || 0)} · ${esc(sbFechaTxt(it.fecha))}</span></div>`).join('')}</div>`
    : '';
  const archivo = `<div class="sb-archivo">${I.wave}<span class="sb-nom" title="${esc(uno.file.name)}">${varios ? `${items.length} audios` : esc(nfc(uno.file.name))}</span>`
    + `<span class="muted">${sbTam(total)}</span><button type="button" class="gr-cambiar" data-sb-otro>Elegir ${varios ? 'otros' : 'otro'}</button></div>`;
  const top = varios ? '' : `<label for="grNombre">Reunión</label>
        <div class="gr-nombre"><input class="input" id="grNombre" maxlength="120" autocomplete="off" spellcheck="false" placeholder="Nombre de la reunión (opcional)" aria-autocomplete="list" aria-controls="grSug">
          <div class="gr-sug hidden" id="grSug" role="listbox"></div></div>
        <span class="gr-tag" id="grNombreTag"></span>
        <label for="sbFecha">Fecha</label>
        <div class="sb-fecha"><input class="input" type="datetime-local" id="sbFecha" required>
          <span class="gr-motivo" id="sbFechaOrigen"></span></div>`;
  openModal(`<div class="gr-cab"><span class="gr-kicker">${I.upload}Subir audio</span></div>
    <div class="gr-cuerpo">
      ${archivo}${lista}
      <div class="sb-error${rechazados.length ? '' : ' hidden'}" id="sbError" role="alert">${rechazados.map(sbNoAudio).join('<br>')}</div>
      <div class="gr-top">${top}
        <label for="grProy">Proyecto</label>
        <div class="gr-proyecto"><select id="grProy" class="gr-proy" title="Proyecto › workstream: su carpeta, su idioma y su vocabulario"></select>
          <span class="gr-motivo" id="grMotivo"></span></div>
      </div>
      <div class="gr-plan" aria-live="polite"><div class="gr-plan-h">Así se va a subir</div>
        <div id="grFilas"><div class="gr-cargando">Preparando…</div></div></div>
      <button type="button" class="gr-anadir hidden" id="grAnadirCtx" data-gr-cambiar="contexto">${I.plus}Contexto: de qué va y quién participa</button>
      <div class="gr-atajo">${I.info}Se guarda como una reunión más, con su fecha, y se transcribe solo. Puedes seguir usando ${esc(APP_NAME)} mientras tanto.</div>
    </div>
    <div class="btns gr-pie"><span class="gr-hueco"></span>
      <button class="btn" data-m="no" type="button">Cancelar</button>
      <button class="btn primary" id="rGo" type="button" disabled>${I.upload}<span>${varios ? `Subir y transcribir los ${items.length}` : 'Subir y transcribir'}</span> <kbd>Intro</kbd></button></div>`, d => grMontar(d), 'gr sb');
  // cerrarla (Cancelar, Esc o un clic fuera) a mitad de subida la cancela: lo que estaba llegando no se guarda. Si ya
  // había llegado entero, el servidor lo está comprobando y se guarda igual: se dice
  S.modalCleanup = () => {
    const g = G;
    G = null;
    if (!g?.enviando) return;
    g.cancelado = true;
    if (g.xhr && !g.llegado) { g.xhr.abort(); toast('Subida cancelada: lo que estaba a medias no se ha guardado.'); }
    else toast('Ese audio ya había llegado: se guarda y se transcribe igual.');
  };
}

/* soltar audios en la ventana: con otra ventana abierta encima (Grabar, Ajustes…), o mientras se sube, no */
function sbNoSePuedeSoltar() {
  if (G?.subir && G.enviando) return 'Espera a que termine la subida que está en marcha.';
  const m = $('#modal');
  if (m && !m.classList.contains('hidden') && !$('.dialog.sb', m)) return 'Para subir audios, cierra antes esta ventana.';
  return '';
}

async function sbMontar(d) {
  const g = G;
  const f = $('#sbFecha', d);
  if (f) {
    f.value = sbFechaInput(g.pedido.fecha);
    f.max = sbFechaInput(Date.now() + 86400000);
    $('#sbFechaOrigen', d).textContent = SB_FECHA_ORIGEN[g.subir.fechaOrigen] || '';
    f.addEventListener('change', () => {
      const ms = new Date(f.value).getTime();
      if (!f.value || isNaN(ms)) return;
      g.pedido.fecha = ms;
      g.subir.fechaOrigen = 'tuya';
      $('#sbFechaOrigen', d).textContent = SB_FECHA_ORIGEN.tuya;
      grPedirLuego();
    });
  }
  // otro archivo: el selector otra vez, con lo elegido aquí; si lo cancelas, la tarjeta sigue como estaba
  d.querySelector('[data-sb-otro]').onclick = () => elegirAudios({ reemplazar: true });
  await voc0();
  if (G !== g) return;
  await grPedir({ recientes: true });
  if (G !== g) return;
  $('#rGo', d).disabled = false;
  const n = $('#grNombre', d);
  if (n) { n.focus(); n.select(); } else $('#rGo', d).focus();  // varios: Intro sube
}
const voc0 = () => (typeof loadVocab === 'function' ? loadVocab(S.config.language ?? 'es-en') : Promise.resolve()).catch(() => { });

/* el modelo con el que se transcribe al subir: el de la cola (Ajustes › Procesado), para este audio */
function sbPanelTranscribe(l, pie) {
  const nube = S.config.procesado === 'api';
  const ps = (S.config.presets || []).filter(x => x.engine !== 'cloud' || nube);
  const nom = x => x.engine === 'cloud' ? `${S.config.providers?.[S.config.cloud?.provider || 'groq']?.label || 'La nube'} · en la nube`
    : `${typeof modeloNombre === 'function' ? modeloNombre(x.model) : x.label} · en este PC`;
  return `<label class="gr-l" for="grSelPreset">Modelo</label><select data-gr="preset" id="grSelPreset">${ps.map(x => `<option value="${esc(x.id)}"${x.id === l.preset ? ' selected' : ''}>${esc(nom(x))}</option>`).join('')}</select>
    <div class="help">${esc(S.presetsById?.[l.preset]?.hint || '')} El de siempre se cambia en Ajustes › Procesado.</div>${pie()}`;
}

function sbProgreso(b, i, n, frac) {
  const pct = Math.min(100, Math.round(frac * 100));
  $('span', b).textContent = frac >= 1 ? 'Comprobando el audio…' : `Subiendo… ${pct} %` + (n > 1 ? ` (${i + 1} de ${n})` : '');
}
/* un archivo al servidor, con su progreso (fetch no da el de la subida) */
function sbEnviar(url, file, onProg, g) {
  return new Promise((ok, ko) => {
    const x = new XMLHttpRequest();
    g.xhr = x;
    g.llegado = false;
    x.open('POST', url);
    x.setRequestHeader('X-Audios', '1');
    x.upload.onprogress = e => { if (e.lengthComputable) onProg(e.loaded / e.total); };
    x.upload.onload = () => { g.llegado = true; onProg(1); };
    x.onabort = () => ko(Object.assign(new Error('cancelada'), { cancelada: true }));
    x.onloadend = () => { if (g.xhr === x) g.xhr = null; };
    x.onload = () => {
      let j;
      try { j = JSON.parse(x.responseText); } catch { j = { error: x.statusText || 'respuesta no válida' }; }
      if (x.status >= 400 || j.error) ko(new Error(j.error || x.statusText)); else ok(j);
    };
    x.onerror = () => ko(new Error(`No se ha podido subir: ¿se ha cerrado ${APP_NAME}?`));
    x.send(file);
  });
}

async function sbSubir() {
  const g = G, b = g && $('#rGo', g.d);
  if (!g || !b || b.disabled) return;
  b.disabled = true;
  const etiqueta = $('span', b).textContent;
  if (g.timer) { clearTimeout(g.timer); g.timer = null; grPedir(); }
  await g.pendiente;  // lo último pedido: se sube con lo que se está viendo
  const p = g.plan;
  if (!p || G !== g) { b.disabled = false; return; }
  g.enviando = true;
  g.cancelado = false;
  $('#sbError', g.d).classList.add('hidden');
  const items = g.subir.items, hechos = [], fallidos = [];
  for (const [i, it] of items.entries()) {
    if (g.cancelado) break;
    const uno = items.length === 1;
    const q = new URLSearchParams({
      folder: p.rec.folder || '', name: uno ? (p.rec.name || '') : it.nombre, fecha: String(uno ? g.pedido.fecha : it.fecha),
      vocab: p.rec.vocab || '', language: p.rec.language || '', contexto: p.rec.contexto || '', preset: p.rec.preset || '',
      archivo: it.file.name });
    try {
      hechos.push(await sbEnviar(`/api/subir?${q}`, it.file, frac => { if (G === g) sbProgreso(b, i, items.length, frac); }, g));
    } catch (err) {
      if (err.cancelada) break;
      fallidos.push({ it, msg: err.message });
    }
  }
  g.enviando = false;
  const abierta = G === g;
  if (g.cancelado || (fallidos.length && abierta)) {
    // cancelada, o algún archivo no se ha podido subir: lo subido se dice y se queda; sin ir a ningún sitio
    if (hechos.length) {
      toast(`✓ ${hechos.length === 1 ? 'Subido' : `${hechos.length} subidos`}: ${hechos.map(r => esc(folderName(r.carpeta))).join(', ')}`);
      hechos.filter(r => r.aviso).forEach(r => toast(esc(r.aviso), 'err'));
      await refreshTree(true);
      pollJobs(true);
    }
    if (g.cancelado || G !== g) return fallidos.forEach(f => toast(esc(f.msg), 'err'));
    // la tarjeta sigue abierta con los que han fallado y su motivo, para elegir otro o cambiar algo
    $('span', b).textContent = etiqueta;
    g.subir.items = fallidos.map(f => f.it);
    g.subir.rechazados = [];
    abrirSubir(null, { seguir: true });
    const e = $('#sbError');
    if (e) { e.innerHTML = fallidos.map(f => esc(f.msg)).join('<br>'); e.classList.remove('hidden'); }
    return;
  }
  if (abierta) closeModal(true);
  if (hechos.length) {
    const ult = hechos[hechos.length - 1], cola = hechos.filter(r => r.job).length;
    // varios: la carpeta principal (cada uno va en la suya, dentro); uno: la de su reunión
    toast(`✓ ${hechos.length === 1 ? 'Subido' : `${hechos.length} audios subidos`} a ${esc(folderName(hechos.length === 1 ? ult.carpeta : p.rec.folder || ''))}`
      + (cola ? ` · ${cola === hechos.length ? 'en la cola para transcribir' : `${cola} en la cola`}` : ''));
    hechos.filter(r => r.aviso).forEach(r => toast(esc(r.aviso), 'err'));
    await refreshTree(true);
    pollJobs(true);
    go('a', ult.path);
  }
  fallidos.forEach(f => toast(esc(f.msg), 'err'));
}
