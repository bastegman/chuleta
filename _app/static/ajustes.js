'use strict';
/* Ajustes por lo que hace el usuario (rediseño B2 y B3, 07/11/2026): «Al grabar» abre con «Así se graba ahora una
   reunión de [proyecto]», el mismo plan del servidor que la tarjeta de grabar y Ctrl+Alt+R (/api/grabar/plan), con lo
   que esté escrito y sin guardar; debajo, solo las reglas, en una pantalla. «Proyectos» dice la carpeta y el idioma de
   cada proyecto y workstream y si son suyos o heredados. «Calendario y Teams» dice el estado en una frase.
   Se carga después de app.js, notas.js, perfil.js, reunion.js y grabar.js. app.js lo invoca con
   `typeof x === 'function'`. */

/* ---------------------------------------------------------------- Al grabar */
function ajAlGrabarHtml() {
  const c = S.config || {};
  return `<div class="ag-cab"><h4>Al grabar</h4><div class="help" style="margin-top:2px">Se usa con el botón Grabar, con Ctrl+Alt+R y con el aviso de Teams. <button type="button" class="ag-como-btn" aria-expanded="false" aria-controls="agComo">¿Cómo funciona?</button></div><div class="help" id="agComo" hidden>Lo de arriba sale de tres capas, de más a menos fuerte: lo que cambies para una reunión en la tarjeta de grabar, el proyecto o workstream (<a href="#" data-sec-ir="proyectos">Proyectos</a>) y estas reglas. <b>Español + inglés</b>, <b>Español + portugués</b> e <b>Inglés + portugués</b> detectan el idioma cada pocos segundos, pero solo entre esos dos, y dejan cada frase en su idioma; si una reunión es solo en uno, fijarlo es algo más preciso. El contexto lo recibe Whisper detrás del vocabulario (si no cabe todo, se recorta antes que el vocabulario) y lo tiene en cuenta el resumen.</div></div>
    <div class="ag-plan">
      <div class="ag-plan-h"><span id="agPlanT">Así se graba ahora una reunión de</span><select id="agProy" aria-labelledby="agPlanT" title="El proyecto que quieres comprobar"></select></div>
      <div id="agFilas" aria-live="polite"><div class="gr-cargando">Calculando…</div></div>
    </div>
    <div class="ag-reglas">
      ${typeof perfilReglasHtml === 'function' ? perfilReglasHtml() : `<div class="ag-regla"><label for="sLang">Idioma por defecto</label><div>${langSelect('sLang', c.language ?? 'es-en')}</div></div>`}
      ${'carpeta_por_reunion' in c ? `<div class="ag-regla"><label for="agPorReunion">Una carpeta por reunión</label><div><label class="ag-sw"><input type="checkbox" id="agPorReunion"${c.carpeta_por_reunion !== false ? ' checked' : ''}>fecha y nombre, dentro de la carpeta del proyecto</label></div></div>` : ''}
      <div class="ag-regla"><label for="agMic">Micrófono · audio del PC</label><div class="ag-dos"><select id="agMic" aria-label="Micrófono">${ajDispositivos(null)[0]}</select><select id="agSys" aria-label="Audio del PC">${ajDispositivos(null)[1]}</select><span id="agDevsAviso" class="hidden"></span></div></div>
      <div class="ag-regla"><label for="agDirecto">Texto en directo</label><div><label class="ag-sw"><input type="checkbox" id="agDirecto"${c.rec_directo !== false ? ' checked' : ''}>si el portátil va lento, quítalo: transcribe al terminar</label></div></div>
      <div class="ag-regla"><label for="lMeet">Llamada detectada</label><div><label class="ag-sw"><input type="checkbox" id="lMeet"${c.meeting_detect !== false ? ' checked' : ''}>avisarme si estoy en una llamada y no estoy grabando</label></div></div>
    </div>`;
}
/* opciones de micrófono y audio del PC con lo guardado elegido (sin la lista de dispositivos: las genéricas) */
function ajDispositivos(devs) {
  const c = S.config || {}, def = list => nfc(((list || []).find(x => x.default) || {}).name || '');
  const o = (v, l, s, t = '') => `<option value="${esc(v)}"${v === s ? ' selected' : ''}${t ? ` title="${esc(t)}"` : ''}>${esc(l)}</option>`;
  const mic = c.rec_mic || 'default', sys = c.rec_sys || 'all';
  // cortos: van dos en una fila («Micrófono · audio del PC»); cuál es ahora el de Windows, al pasar el ratón (title)
  const ahora = list => def(list) ? `ahora: ${def(list)}` : '';
  let m = o('default', 'El de Windows', mic, ahora(devs?.mics))
    + (devs?.mics?.length ? `<optgroup label="Fijar un micrófono">${devs.mics.map(x => o(x.id, nfc(x.name), mic)).join('')}</optgroup>` : '')
    + o('none', 'Sin micrófono', mic);
  let s = o('all', 'Todo lo que suena', sys) + o('default', 'La de Windows', sys, ahora(devs?.outputs))
    + (devs?.outputs?.length ? `<optgroup label="Fijar una salida">${devs.outputs.map(x => o(x.id, nfc(x.name), sys)).join('')}</optgroup>` : '')
    + o('none', 'Sin el audio del PC', sys);
  // un dispositivo guardado que no está en la lista se enseña tal cual, para no cambiarlo sin querer
  if (!['default', 'none'].includes(mic) && !(devs?.mics || []).some(x => x.id === mic)) m += o(mic, 'El elegido, desconectado', mic);
  if (!['all', 'default', 'none'].includes(sys) && !(devs?.outputs || []).some(x => x.id === sys)) s += o(sys, 'La elegida, desconectada', sys);
  return [m, s];
}
/* Las listas de vocabulario, una sola petición por cada vez que se abre Ajustes (Al grabar y Proyectos las piden a la
   vez). loadVocab (notas.js) no falla: deja S.vocabError si no se han podido leer. */
/* las listas de vocabulario se han leído alguna vez (bien, aunque no haya ninguna, o con error) */
function listasLeidas() { return Array.isArray(S.vocabCarpetas) || !!S.vocabError; }
function ajListas(d) {
  return d.ajListas || (d.ajListas = loadVocab(S.config?.language ?? 'es-en'));
}
/* sin las listas no se puede decir qué proyectos hay: una frase con «Probar otra vez» (reabre Ajustes en la misma
   sección, con lo que no se haya guardado) en vez de «Todavía no hay proyectos» */
function ajListasFalloHtml(extra = '') {
  return `<div class="notice ag-sin-listas">No se han podido leer las listas de vocabulario${extra}. <button class="btn sm" type="button" data-aj-listas-otra>Probar otra vez</button></div>`;
}
/* lo que Al grabar tiene escrito ahora (guardado o no), para enseñar el plan con ello y para «Guardar» */
function ajAlGrabarValores(d) {
  const v = id => $('#' + id, d);
  const out = { language: v('sLang')?.value ?? '', rec_directo: !!v('agDirecto')?.checked };
  if (v('agMic')?.value) out.rec_mic = v('agMic').value;
  if (v('agSys')?.value) out.rec_sys = v('agSys').value;
  const f = v('pfFolder')?.value, voc = v('pfVocab')?.value;
  out.perfil = { context: (v('pfContext')?.value || '').trim() };
  if (f != null && f !== '__pick') out.perfil.folder = f === PERFIL_AUTO ? null : f;
  if (voc != null) out.perfil.vocab = voc === PERFIL_AUTO ? null : voc;
  // lo que se cambia en otras secciones y también cambia el plan: motores (Procesado › Motores), resumir al terminar
  // (el de la opción marcada, como al guardar), vocabulario de las reuniones sin proyecto y una carpeta por reunión
  if (v('lPreset')?.value) out.live_preset = v('lPreset').value;
  if (v('pfPreset')?.value) out.preset = v('pfPreset').value;
  // la opción marcada en «Dónde se procesan», aplicada o no: «Guardar» también la aplica (collect, en app.js)
  const proc = $('input[name="proc"]:checked', d)?.value;
  if (proc) out.procesado = proc;
  const auto = proc === 'agente' ? v('sAutoAg') : v('sAuto');
  if (auto) out.summary = { auto: auto.checked, local: !!v('sAutoLocal')?.checked };
  if (v('sPrompt')) out.prompt = v('sPrompt').value;
  if (v('agPorReunion')) out.carpeta_por_reunion = v('agPorReunion').checked;
  return out;
}
function ajAlGrabarCollect(d) {
  const x = ajAlGrabarValores(d), out = { rec_directo: x.rec_directo };
  if (x.rec_mic) out.rec_mic = x.rec_mic;
  if (x.rec_sys) out.rec_sys = x.rec_sys;
  if ($('#agPorReunion', d)) out.carpeta_por_reunion = $('#agPorReunion', d).checked;
  return out;
}
function bindAjAlGrabar(d) {
  const proy = $('#agProy', d), box = $('#agFilas', d);
  if (!proy || !box) return;
  let seq = 0, t = null, elegido = false;
  const pintar = plan => {
    const tmp = document.createElement('div');
    tmp.innerHTML = plan.lineas.filter(l => l.clave !== 'contexto' || l.valor).map(l => {
      const ir = { proyecto: 'proyectos', workstream: 'proyectos', procesado: 'procesado' }[l.origen];
      const origen = ir ? `<a href="#" class="gr-origen ag-ir" data-sec-ir="${ir}">${esc(l.origen_txt)}</a>` : `<span class="gr-origen">${esc(l.origen_txt)}</span>`;
      // la ruta en una línea (la entera, al pasar el ratón): con la carpeta por reunión puede ser larga
      // y el contexto, también en una línea: está entero justo debajo, en su casilla
      const v = (l.clave === 'carpeta' ? `<code class="gr-ruta" title="${esc(l.valor)}">${esc(l.valor)}</code>`
        : l.clave === 'contexto' ? `<b class="gr-ctx" title="${esc(l.valor)}">${esc(l.valor)}</b>` : `<b>${esc(l.valor)}</b>`)
        + (l.aviso ? `<span class="gr-aviso">${esc(l.aviso)}</span>` : '');
      const icon = (GR_FILAS.find(([k]) => k === l.clave) || [])[1];
      return `<div class="gr-fila" data-fila="${l.clave}"><span class="gr-ic">${icon ? I[icon] : ''}</span><span class="gr-et">${esc(l.etiqueta)}</span><span class="gr-v">${v}</span>${origen}<span></span></div>`;
    }).join('');
    // solo se cambian las filas que cambian: con aria-live, un lector de pantalla dice lo que ha cambiado, no el plan
    // entero tras cada pausa al escribir
    const ant = [...box.children], nue = [...tmp.children];
    if (ant.length !== nue.length || ant.some((x, i) => !x.dataset.fila || x.dataset.fila !== nue[i].dataset.fila)) {
      box.replaceChildren(...nue);
      return;
    }
    nue.forEach((x, i) => { if (ant[i].outerHTML !== x.outerHTML) ant[i].replaceWith(x); });
  };
  const pedir = async (extra = {}) => {
    const n = ++seq;
    try {
      const plan = await api('/api/grabar/plan', { nombre: '', participantes: '', vocab: proy.value, ajustes: ajAlGrabarValores(d), ...extra });
      if (n === seq && document.body.contains(box)) pintar(plan);
      return plan;
    } catch (err) {
      if (n !== seq || !document.body.contains(box)) return null;
      box.innerHTML = `<div class="notice" title="${esc(err.message)}">No se ha podido calcular cómo se grabaría ahora. <button class="btn sm" type="button" data-ag-reintentar>Probar otra vez</button></div>`;
      return null;
    }
  };
  box.addEventListener('click', e => { if (e.target.closest('[data-ag-reintentar]')) pedir(); });
  // «Probar otra vez» de las listas (aquí o en Proyectos): Ajustes otra vez, en la misma sección y con lo no guardado
  d.addEventListener('click', e => {
    if (!e.target.closest('[data-aj-listas-otra]')) return;
    e.preventDefault();
    d.ajMarcar?.();
    openSettings({ sec: S.ajSec, volver: true });
  });
  const como = $('.ag-como-btn', d), comoTxt = $('#agComo', d);
  if (como && comoTxt) como.onclick = () => { comoTxt.hidden = !comoTxt.hidden; como.setAttribute('aria-expanded', String(!comoTxt.hidden)); };
  const luego = () => { clearTimeout(t); t = setTimeout(pedir, 250); };
  const listas = () => {
    const sel = proy.value;
    proy.innerHTML = `<option value="">Sin proyecto</option>${(S.vocab || []).map(v => `<option value="${esc(v.id)}">${esc(v.project)} › ${esc(v.name)}</option>`).join('')}`;
    // sin haberlo elegido tú, el primer proyecto (si lo hay): lo normal es querer ver el de un proyecto
    proy.value = elegido && [...proy.options].some(o => o.value === sel) ? sel : (S.vocab?.[0]?.id || '');
  };
  listas();
  proy.onchange = () => { elegido = true; pedir(); };
  // «Proyecto al grabar» fijo: el plan de arriba pasa a ese proyecto, para que se vea lo que cambia (el selector de
  // arriba elige qué plan enseñar y, si no, lo taparía)
  $('#pfVocab', d)?.addEventListener('change', e => {
    e.target.dataset.tocado = '1';  // elegido por ti: el borrador de antes ya no lo pisa
    const v = e.target.value;
    if (v === PERFIL_AUTO || ![...proy.options].some(o => o.value === v)) return;
    proy.value = v;
    elegido = true;
  });
  // las reglas de abajo cambian el plan de arriba al momento, aunque no se hayan guardado
  ['sLang', 'agMic', 'agSys', 'agDirecto', 'pfFolder', 'pfVocab', 'pfContext', 'agPorReunion', 'lPreset', 'pfPreset',
    'sAuto', 'sAutoAg', 'sAutoLocal', 'sPrompt'].forEach(id => {
    const el = $('#' + id, d);
    if (el) { el.addEventListener('change', luego); el.addEventListener('input', luego); }
  });
  $$('input[name="proc"]', d).forEach(el => el.addEventListener('change', luego));
  // micrófono y audio del PC: los de Ajustes (lo de una reunión se cambia en su tarjeta). Con la lista de
  // dispositivos, sin cambiar lo elegido (ni lo que ya hayas tocado)
  const c = S.config || {}, mic = $('#agMic', d), sys = $('#agSys', d), tocado = {};
  const titulo = s => {  // el texto entero y, con el de Windows, cuál es ahora
    const x = s.options[s.selectedIndex];
    s.title = [x?.textContent, x?.title].filter(Boolean).join(' · ');
  };
  mic.addEventListener('change', () => { tocado.mic = true; mic.dataset.tocado = '1'; titulo(mic); });
  sys.addEventListener('change', () => { tocado.sys = true; sys.dataset.tocado = '1'; titulo(sys); });
  titulo(mic); titulo(sys);
  const borrador = d.borradorAlAbrir || {};  // lo que había sin guardar al volver de un subdiálogo
  const listos = () => { (d.ajOpcionesListas ||= {}).agMic = true; d.ajOpcionesListas.agSys = true; };
  api('/api/rec/devices').then(r => {
    listos();  // micrófono y salida ya no esperan sus opciones (app.js, marcar)
    if (!document.body.contains(mic)) return;
    // lo que ya estaba elegido sin guardar: lo de ahora o, si no lo has tocado desde que se abrió, el borrador (al
    // abrir, su opción aún no existía y el valor no se pudo poner)
    const [m, s] = ajDispositivos(r);
    const vm = !tocado.mic && borrador.agMic != null ? borrador.agMic : mic.value;
    const vs = !tocado.sys && borrador.agSys != null ? borrador.agSys : sys.value;
    const antes = [mic.value, sys.value];
    mic.innerHTML = m; sys.innerHTML = s;
    if ([...mic.options].some(x => x.value === vm)) mic.value = vm;
    if ([...sys.options].some(x => x.value === vs)) sys.value = vs;
    titulo(mic); titulo(sys);
    // si ha cambiado lo que se ve, que lo sepan el plan de arriba y el aviso de «sin guardar»
    if (mic.value !== antes[0]) mic.dispatchEvent(new Event('change', { bubbles: true }));
    if (sys.value !== antes[1]) sys.dispatchEvent(new Event('change', { bubbles: true }));
  }).catch(err => {
    listos();
    const av = $('#agDevsAviso', d);
    if (!av || !document.body.contains(av)) return;
    av.outerHTML = grDevsAviso(err.message).replace('<span ', '<span id="agDevsAviso" ');  // en una línea
  });
  // al volver a Al grabar (quizá tras guardar algo en Proyectos), otra vez; y cuando cambian las listas
  d.addEventListener('click', e => { if (e.target.closest('.aj-nav [data-sec="algrabar"], [data-sec-ir="algrabar"]')) pedir(); });
  // las listas pueden llegar (o cambiar) después de abrir Ajustes: el plan se pide otra vez solo si cambia el selector.
  // «Proyecto al grabar» también se rehace, con lo elegido (guardado o no) y, si no está, dicho
  const otraVez = () => {
    if (!document.body.contains(proy)) return;
    const antes = [...proy.options].map(o => o.value).join('|') + '#' + proy.value;
    listas();
    const pv = $('#pfVocab', d), esperaba = !d.ajOpcionesListas?.pfVocab;
    (d.ajOpcionesListas ||= {}).pfVocab = listasLeidas();  // ya no espera (app.js, marcar)
    if (pv && typeof perfilVocabOpts === 'function') {
      // con las listas ya aquí: lo que había (lo guardado o lo que has elegido) y, si no lo has tocado, lo escrito
      // antes de un subdiálogo, solo si su lista existe; si no (borrada o renombrada allí), queda lo guardado y no un
      // «Sin proyecto» que nadie eligió (revisión 7)
      const antesV = pv.value, b = d.borradorAlAbrir?.pfVocab;
      pv.innerHTML = perfilVocabOpts(antesV);
      const valeB = b != null && (b === PERFIL_AUTO || b === '' || (S.vocab || []).some(v => v.id === b));
      if (valeB && !pv.dataset.tocado && pv.value !== b) pv.value = b;
      // y el borrador al día: lo que esperaba a las listas y no está en ellas ya no espera (revisión 8: si marcar()
      // había corrido antes de llegar, se quedaba pendiente)
      if (pv.value !== antesV || (esperaba && d.ajOpcionesListas.pfVocab)) d.ajMarcar?.();
    }
    const av = $('.ag-sin-listas', d);
    // aquí, en una línea (Al grabar tiene que caber en una pantalla); en Proyectos y en las listas, con su recuadro
    if (S.vocabError && !S.vocab?.length && !av) $('.ag-plan', d)?.insertAdjacentHTML('afterend', '<div class="ag-sin-listas help" style="margin-top:4px"><span class="warn-txt">No se han podido leer las listas de vocabulario: solo se puede comprobar «Sin proyecto».</span> <a href="#" data-aj-listas-otra>Probar otra vez</a></div>');
    else if (av && !S.vocabError) av.remove();
    if ([...proy.options].map(o => o.value).join('|') + '#' + proy.value !== antes) pedir();
  };
  d.ajListasCambiadas = otraVez;
  d.ajPlanPedir = () => pedir();
  // si las listas ya se habían leído (al volver de su editor, por ejemplo), «Proyecto al grabar» se ha pintado ya con
  // ellas: no espera a la siguiente lectura
  (d.ajOpcionesListas ||= {}).pfVocab = listasLeidas();
  ajListas(d).then(otraVez);
  // el primero, con la carpeta de cada proyecto: Proyectos la toma de aquí al abrir (una petición menos)
  d.ajPrimerPlan = pedir({ proyectos: true });
}

/* ---------------------------------------------------------------- Proyectos: carpeta e idioma de cada uno */
function ajProyectosHtml() {
  return `<div class="ag-cab"><h4>Proyectos y workstreams</h4><div class="help" style="margin-top:2px">La carpeta y el idioma con los que se graba cada uno. En negro, lo asignado a ese proyecto o workstream; en gris, lo heredado (el workstream usa lo del proyecto, y el proyecto, su carpeta y el idioma de <a href="#" data-sec-ir="algrabar">Al grabar</a>). Lo que cambies en una fila se guarda al momento.</div></div>
    <div id="pjTabla"><div class="gr-cargando">Cargando…</div></div>`;
}
/* la carpeta de un proyecto que no tiene una asignada, en palabras (car: /api/grabar/plan con proyectos) */
function ajCarpetaHeredada(x) {
  if (!x) return 'sin asignar';
  if (x.origen === 'proyecto') return 'su carpeta';
  if (x.origen === 'ultima') return 'la última usada';
  return x.rel ? 'la de Al grabar' : `la carpeta de ${APP_NAME}`;
}
/* car: {proyectos: {p: {rel, ruta, origen, texto, sin_asignar}}, workstreams: {id: {...}}}, del mismo cálculo que la
   tarjeta de grabar; null mientras llega, false si no se ha podido calcular. idioma0: el de Al grabar, guardado o no */
function ajProyectosTabla(car, idioma0) {
  const c = S.config || {}, pl = c.project_langs || {}, wl = c.workstream_langs || {}, wf = c.workstream_folders || {};
  // con la rama de carpetas, project_folders llega resuelta (con la del árbol) y las asignadas van aparte
  const asig = c.project_folders_asignadas || c.project_folders || {};
  const grupos = typeof perfilGroups === 'function' ? perfilGroups(S.vocab || []) : [];
  if (!grupos.length && S.vocabError) return ajListasFalloHtml(', así que aquí no salen tus proyectos');
  if (!grupos.length) {
    const conectar = typeof vocabConectar === 'function' ? ' o conecta la carpeta donde ya tienes tus listas' : '';
    return `<div class="help" style="margin-top:0">Todavía no hay proyectos. Crea uno con «Nuevo proyecto», abajo en Listas de vocabulario${conectar}: cada proyecto tiene sus workstreams, y cada workstream, su lista de nombres y siglas.</div>`;
  }
  const lang = code => esc(perfilLangLabel(code));
  const ruta = x => `<code title="${esc(x.ruta)}">${esc(x.ruta)}</code>`;
  const perdida = '<span class="warn-txt">La asignada ya no se encuentra</span> · ';
  const sinCalculo = '<span class="warn-txt">No se ha podido calcular</span>';
  const carpeta = p => {
    const x = car?.proyectos?.[p];
    if (car === false) return sinCalculo;
    if (!x) return '<span class="pj-her">…</span>';
    if (p in asig && x.rel === asig[p]) return ruta(x);
    return `${p in asig ? perdida : ''}<span class="pj-her">${esc(ajCarpetaHeredada(x))} · ${ruta(x)}</span>`;
  };
  const carpetaW = id => {
    const x = car?.workstreams?.[id];
    if (car === false) return sinCalculo;
    if (x && x.origen === 'workstream') return ruta(x);
    return `${id in wf ? perdida : ''}<span class="pj-her">la del proyecto</span>`;
  };
  const filas = grupos.map(([p, vs]) => {
    const cab = `<tr class="pj-p" data-pj="${esc(p)}"><td><b>${esc(p)}</b></td>
      <td>${carpeta(p)}</td>
      <td>${p in pl ? lang(pl[p]) : `<span class="pj-her">el de Al grabar · ${lang(idioma0)}</span>`}</td>
      <td><button class="btn ghost sm" type="button" data-pj-editar="${esc(p)}" aria-label="Editar ${esc(p)}">Editar</button></td></tr>`;
    return cab + vs.map(v => `<tr class="pj-w" data-pjw="${esc(v.id)}"><td>› ${esc(v.name)} <span class="muted">· ${v.terms} ${v.terms === 1 ? 'término' : 'términos'}</span></td>
      <td>${carpetaW(v.id)}</td>
      <td>${v.id in wl ? lang(wl[v.id]) : `<span class="pj-her">el del proyecto · ${lang(pl[p] ?? idioma0)}</span>`}</td>
      <td><button class="btn ghost sm" type="button" data-pjw-editar="${esc(v.id)}" aria-label="Editar ${esc(p)} › ${esc(v.name)}">Editar</button></td></tr>`).join('');
  }).join('');
  const aviso = car === false ? `<div class="notice" style="margin-top:10px">No se ha podido calcular la carpeta de cada proyecto. <button class="btn sm" type="button" data-pj-reintentar>Probar otra vez</button></div>` : '';
  // anchos fijos: al editar una fila la tabla no salta y los desplegables no se cortan más de la cuenta
  return `${aviso}<table class="vtable pj-tabla"><colgroup><col style="width:28%"><col style="width:32%"><col style="width:26%"><col style="width:14%"></colgroup><thead><tr><th>Proyecto › workstream</th><th>Carpeta principal</th><th>Idioma</th><th></th></tr></thead><tbody>${filas}</tbody></table>`;
}
/* opciones de carpeta de una fila: la heredada primero y la asignada aunque ya no exista (para no perderla en silencio) */
function ajCarpetaOpts(sel, heredadaTxt) {
  const falta = sel !== PERFIL_AUTO && S.folders.size > 0 && !S.folders.has(sel);  // sin árbol aún, no se sabe
  // con la ruta entera («Alfa › Reuniones»): cerrado, el desplegable solo enseña la opción elegida, y «Reuniones» a
  // secas no decía de qué proyecto era
  const ruta = f => f.path ? (typeof perfilCarpetaTxt === 'function' ? perfilCarpetaTxt(f.path) : f.path).split('\\').join(' › ') : f.name;
  return `<option value="${PERFIL_AUTO}"${sel === PERFIL_AUTO ? ' selected' : ''}>${esc(heredadaTxt)}</option>`
    + (falta ? `<option value="${esc(sel)}" selected>${esc(typeof perfilCarpetaTxt === 'function' ? perfilCarpetaTxt(sel) : sel)} (no se encuentra)</option>` : '')
    + flatFolders().filter(f => fold(f.name) !== 'vocabulario')
      .map(f => `<option value="${esc(f.path)}"${!falta && f.path === sel ? ' selected' : ''}>${esc(ruta(f))}</option>`).join('');
}
function bindAjProyectos(d) {
  const box = $('#pjTabla', d);
  if (!box) return;
  let car = null, seq = 0;
  const idioma0 = () => $('#sLang', d)?.value ?? S.config.language;
  const dibujar = () => { if (document.body.contains(box)) box.innerHTML = ajProyectosTabla(car, idioma0()); };
  // las carpetas, del servidor (el mismo cálculo que la tarjeta, con lo escrito en Al grabar aunque no se haya guardado)
  const pintar = async () => {
    const n = ++seq;
    try {
      const p = await api('/api/grabar/plan', { nombre: '', participantes: '', vocab: '', proyectos: true, ajustes: ajAlGrabarValores(d) });
      if (n === seq) car = p.carpetas || false;
    } catch { if (n === seq) car = false; }
    if (n === seq && !box.querySelector('.pj-ed')) dibujar();  // una fila a medio editar no se pisa
  };
  // al abrir, la carpeta de cada proyecto llega con el primer plan de Al grabar; si no, se pide
  Promise.all([ajListas(d), d.ajPrimerPlan]).then(([, p]) => {
    if (seq === 0 && p?.carpetas) { car = p.carpetas; dibujar(); } else if (seq === 0) pintar();
  });
  // al volver a Proyectos (quizá con otra carpeta en Al grabar), otra vez
  d.addEventListener('click', e => { if (e.target.closest('.aj-nav [data-sec="proyectos"], [data-sec-ir="proyectos"]')) pintar(); });
  // cuando cambian las listas (una carpeta de listas conectada o quitada, rama vocab-carpeta), la tabla y el plan
  const sv = $('#sVocab', d);
  // sin poder leer las listas, el bloque diría «Todavía no hay listas»: lo dice tal cual, con «Probar otra vez»
  const sinListas = () => {
    if (!S.vocabError || S.vocab?.length || $('.ag-sin-listas', sv) || sv.textContent.trim() === 'Cargando…') return false;
    sv.insertAdjacentHTML('afterbegin', ajListasFalloHtml());
    [...sv.children].find(x => x.textContent.startsWith('Todavía no hay listas'))?.classList.add('hidden');
    return true;
  };
  if (sv && typeof MutationObserver === 'function') {
    let t = null, primera = true;  // el primer pintado del bloque (al abrir) no es un cambio: ya se pide arriba
    const obs = new MutationObserver(() => {
      if (sinListas()) obs.takeRecords();  // lo que se acaba de poner no es un cambio de las listas
      if (primera) { primera = false; return; }
      clearTimeout(t);
      t = setTimeout(() => { pintar(); d.ajListasCambiadas?.(); }, 150);
    });
    obs.observe(sv, { childList: true });
  }
  // cada cambio de una fila se guarda al momento (no espera al «Guardar» de abajo, que no lo vería)
  const guardarCampo = async (tr, sel) => {
    const est = $('.pj-estado', tr), valor = sel.value === PERFIL_AUTO ? null : sel.value, carpeta = sel.matches('[data-pj-f]');
    const cuerpo = { clave: carpeta ? 'carpeta' : 'idioma', alcance: tr.dataset.alcance, vocab: tr.dataset.vocab, valor };
    const hechos = tr.hechos || (tr.hechos = new Set());  // lo guardado en esta fila, por campo
    est.textContent = 'Guardando…';
    try {
      await api('/api/grabar/recordar', cuerpo);
      setConfig(await api('/api/config'));
      sel.dataset.ini = sel.value;
      hechos.add(carpeta ? 'Carpeta guardada' : 'Idioma guardado');
      est.textContent = [...hechos].join(' · ');
      pintar();
      d.ajPlanPedir?.();
    } catch (err) {
      sel.value = sel.dataset.ini;
      est.innerHTML = `<span class="warn-txt">${esc(err.message)}</span>`;
      // la fila ya se ha cerrado (o se ha abierto otra): que no se pierda en silencio
      if (!document.body.contains(est)) toast(`No se ha guardado ${carpeta ? 'la carpeta' : 'el idioma'} de ${esc(tr.dataset.nom || '')}: ${esc(err.message)}`, 'err');
    }
  };
  // un temporizador por desplegable: con el teclado, cada flecha es un «change» y se guarda el último; dos campos (o
  // dos filas) seguidos no se pisan. Lo pendiente se guarda ya al cerrar la fila o al abrir otra
  const pendientes = new Map();
  const guardarYa = () => {
    const van = [...pendientes].map(([sel, t]) => { clearTimeout(t); return guardarCampo(sel.closest('tr'), sel); });
    pendientes.clear();
    return Promise.all(van);
  };
  box.addEventListener('change', e => {
    const sel = e.target.closest('[data-pj-f], [data-pj-l]');
    if (!sel) return;
    clearTimeout(pendientes.get(sel));
    pendientes.delete(sel);
    if (sel.value === sel.dataset.ini) return;
    pendientes.set(sel, setTimeout(() => { pendientes.delete(sel); guardarCampo(sel.closest('tr'), sel); }, 300));
  });
  const cerrar = async tr => {
    const id = tr.dataset.alcance === 'proyecto' ? `[data-pj-editar="${CSS.escape(tr.dataset.pj)}"]` : `[data-pjw-editar="${CSS.escape(tr.dataset.vocab)}"]`;
    await guardarYa();  // la fila cerrada ya enseña lo guardado
    dibujar();
    $(id, box)?.focus();
  };
  box.addEventListener('click', async e => {
    const pe = e.target.closest('[data-pj-editar]'), we = e.target.closest('[data-pjw-editar]');
    if (e.target.closest('[data-pj-reintentar]')) return pintar();
    const listo = e.target.closest('[data-pj-no]');
    if (listo) return cerrar(listo.closest('tr'));
    if (e.target.closest('[data-pj-lista]')) {
      const v = (S.vocab || []).find(x => x.id === e.target.closest('[data-pj-lista]').dataset.pjLista);
      if (v) openVocabEditor(v, openSettings);
      return;
    }
    if (!pe && !we) return;
    await guardarYa();  // lo que quedara de otra fila
    dibujar();
    const c = S.config, langs = (sel, her) => `<option value="${PERFIL_AUTO}"${sel == null ? ' selected' : ''}>${esc(her)}</option>`
      + LANGS.map(([v, t]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${esc(t)}</option>`).join('');
    const acc = extra => `<td class="pj-acc">${extra}<button class="btn sm" type="button" data-pj-no>Listo</button><span class="pj-estado" aria-live="polite"></span></td>`;
    let tr;
    if (pe) {
      const p = pe.dataset.pjEditar, v = (S.vocab || []).find(x => x.project === p);
      const asig = c.project_folders_asignadas || c.project_folders || {}, fsel = asig[p] ?? PERFIL_AUTO, lsel = c.project_langs?.[p];
      // sin asignar: cuál quedaría (sin el cálculo, solo «Sin asignar»)
      const sa = car?.proyectos?.[p]?.sin_asignar, sinTxt = sa ? `Sin asignar (${ajCarpetaHeredada(sa)}: ${sa.ruta})` : 'Sin asignar';
      tr = $(`tr[data-pj="${CSS.escape(p)}"]`, box);
      tr.outerHTML = `<tr class="pj-ed" data-pj="${esc(p)}" data-vocab="${esc(v?.id || '')}" data-alcance="proyecto" data-nom="${esc(p)}"><td><b>${esc(p)}</b></td>
        <td><select data-pj-f data-ini="${esc(fsel)}" title="Dónde se guardan las reuniones de ${esc(p)}" aria-label="Carpeta principal de ${esc(p)}">${ajCarpetaOpts(fsel, sinTxt)}</select></td>
        <td><select data-pj-l data-ini="${esc(lsel ?? PERFIL_AUTO)}" aria-label="Idioma de ${esc(p)}">${langs(lsel, `El de Al grabar · ${perfilLangLabel(idioma0())}`)}</select></td>
        ${acc('')}</tr>`;
      tr = $(`tr.pj-ed[data-pj="${CSS.escape(p)}"]`, box);
    } else {
      const id = we.dataset.pjwEditar, v = (S.vocab || []).find(x => x.id === id);
      const lsel = c.workstream_langs?.[id], pl = c.project_langs?.[v?.project], fsel = c.workstream_folders?.[id] ?? PERFIL_AUTO;
      const delP = car?.proyectos?.[v?.project], nom = `${v?.project || ''} › ${v?.name || ''}`;
      tr = $(`tr[data-pjw="${CSS.escape(id)}"]`, box);
      tr.outerHTML = `<tr class="pj-ed" data-vocab="${esc(id)}" data-alcance="workstream" data-nom="${esc(nom)}"><td>› ${esc(v?.name || '')}<div style="margin-top:6px"><button class="btn ghost sm" type="button" data-pj-lista="${esc(id)}">${I.edit}Editar la lista</button></div></td>
        <td><select data-pj-f data-ini="${esc(fsel)}" title="Dónde se guardan las reuniones de ${esc(nom)}" aria-label="Carpeta principal de ${esc(nom)}">${ajCarpetaOpts(fsel, `La del proyecto${delP ? ` (${delP.ruta})` : ''}`)}</select></td>
        <td><select data-pj-l data-ini="${esc(lsel ?? PERFIL_AUTO)}" aria-label="Idioma de ${esc(nom)}">${langs(lsel, `El del proyecto · ${perfilLangLabel(pl ?? idioma0())}`)}</select></td>
        ${acc('')}</tr>`;
      tr = $(`tr.pj-ed[data-vocab="${CSS.escape(id)}"]`, box);
    }
    $('select', tr)?.focus();  // con el teclado, se sigue en la fila
  });
}

/* ---------------------------------------------------------------- Calendario y Teams: el estado en una frase (B3) */
/* Teams y Outlook, una fila cada uno, y Outlook una sola vez (revisión 2 de B2, P1-5). La casilla de reunion.js se
   queda en el DOM (la guarda reunionCollect) pero no se enseña: «Activar» y «Desactivar» van en la fila. Con la rama
   fix-calendario, la frase es su #rnEstado (GET /api/calendario/estado), que guarda al momento; sin ella, lo que se sabe
   sin preguntar a Outlook, y se guarda con «Guardar». */
function ajCalendarioHtml() {
  return `<div class="ag-cab"><h4>Calendario y Teams</h4><div class="help" style="margin-top:2px">De dónde saca ${esc(APP_NAME)} el nombre de la reunión. Sin calendario también funciona: el nombre sale de Teams o de tus reuniones recientes.</div></div>
    <div class="ct-filas">
      <div class="ct-fila"><span class="ct-n">${I.phone}Teams</span><span class="ct-estado" id="ctTeamsEstado">…</span><span class="ct-frase" id="ctTeamsFrase"></span><span class="ct-acc"></span></div>
      <div class="ct-fila hidden" id="ctOutFila"><span class="ct-n">${I.calendar}Outlook</span><span class="ct-estado" id="ctOutEstado"></span><span class="ct-frase" id="ctOutFrase"></span><span class="ct-acc" id="ctOutAcc"></span></div>
    </div>
    ${window.reunionFieldsetHtml?.() || ''}`;
}
function bindAjCalendario(d) {
  const te = $('#ctTeamsEstado', d), tf = $('#ctTeamsFrase', d);
  if (!te) return;
  const chip = (el, cls, txt) => { el.textContent = txt; el.className = 'ct-estado ' + cls; };
  const fueraWin = S.config?.hotkey?.error === 'el atajo global solo existe en Windows' || !/win/i.test(navigator.platform || '');
  if (fueraWin) {
    chip(te, 'aviso', 'Solo en Windows');
    tf.textContent = 'Lee el título de la ventana de la reunión de Teams en Windows.';
  } else {
    chip(te, 'ok', 'Funciona');
    tf.textContent = 'Lee el título de la reunión en curso. No hace falta nada.';
    // solo Teams: sin despertar a Outlook (por COM, hasta 8 s) cada vez que se abre Ajustes
    api('/api/reunion/actual?outlook=0').then(r => {
      if (r?.teams?.title && document.body.contains(tf)) tf.innerHTML = `Ahora: <b>${esc(r.teams.title)}</b>. No hace falta nada.`;
    }).catch(() => { });
  }
  // el aviso flotante depende de «Llamada detectada» (Al grabar), que es el interruptor general: sin él, no sale
  const meet = $('#lMeet', d), aviso = $('#rnAviso', d);
  if (meet && aviso) {
    const atar = () => { aviso.disabled = !meet.checked; aviso.closest('label')?.classList.toggle('ct-off', !meet.checked); };
    meet.addEventListener('change', atar);
    atar();
  }
  // la ayuda larga («¿Cómo funciona?») explica Teams y Outlook: bajo sus filas, no bajo el aviso de llamada
  const como = $('[data-secc="calendario"] fieldset details.ag-como', d);
  if (como) $('.ct-filas', d)?.after(como);
  const fila = $('#ctOutFila', d), oe = $('#ctOutEstado', d), of = $('#ctOutFrase', d), oa = $('#ctOutAcc', d);
  const casilla = $('#rnOutlook', d), probar = $('#rnProbar', d)?.closest('.row'), est = $('#rnEstado', d);
  if (!fila || !casilla) return;
  casilla.closest('label')?.classList.add('hidden');
  of.innerHTML = '<span></span>';
  const frase = of.firstChild;
  if (est) { est.style.marginTop = '0'; of.append(est); }
  if (probar) { probar.style.marginTop = '6px'; of.append(probar); }  // «Probar» es de Outlook: en su fila
  fila.classList.remove('hidden');
  // con su nombre entero para un lector de pantalla: «Activar» a secas no dice qué activa
  const boton = (on, txt, off = false) => `<button class="btn sm" type="button" data-ct-outlook="${on ? '1' : '0'}" aria-label="${txt} el calendario de Outlook"${off ? ' disabled' : ''}>${txt}</button>`;
  if (est) {
    // fix-calendario: «Activar» y «Desactivar» guardan al momento, así que la casilla no cuenta como «sin guardar» ni
    // la manda «Guardar» (app.js, collect)
    casilla.dataset.seGuardaSolo = '';
    const pintar = () => {
      const on = casilla.checked, activar = $('[data-rn-accion="activar"]', est);
      activar?.classList.add('hidden');  // el suyo, en la columna de la derecha, como «Desactivar»
      const soloWin = fueraWin && !on && !activar;  // fuera de Windows el servidor no ofrece nada
      // activado, el color es el de su estado: verde si conecta, aviso si la frase dice que no (falta pywin32…)
      const tono = $('span[style*="--ok"]', est) ? 'ok' : $('span[style*="--warn"], span[style*="--err"]', est) ? 'aviso' : '';
      chip(oe, on ? tono : soloWin ? 'aviso' : '', on ? 'Activado' : soloWin ? 'Solo en Windows' : 'Apagado');
      // «Probar» ya está en la frase cuando es lo que toca (Outlook cerrado): no dos veces (su resultado, sí). Fuera
      // de Windows no hay nada que probar
      $('#rnProbar', d)?.classList.toggle('hidden', soloWin || !!$('[data-rn-accion="probar"]', est));
      // mientras reunion.js activa, su botón está desactivado: el de aquí, igual
      const nuevo = on ? boton(false, 'Desactivar') : activar ? boton(true, 'Activar', activar.disabled) : '';
      if (oa.innerHTML === nuevo) return;
      // con el teclado, el foco no se pierde: si el botón nuevo está desactivado (activando), se espera al siguiente
      const foco = oa.contains(document.activeElement) || oa.dataset.foco;
      oa.innerHTML = nuevo;
      if (!foco) return;
      const b = $('[data-ct-outlook]', oa), destino = b ? (b.disabled ? null : b) : ($('#rnProbar:not(.hidden)', d) || $('.aj-nav [data-sec="calendario"]', d));
      if (destino) { destino.focus(); delete oa.dataset.foco; } else oa.dataset.foco = '1';
    };
    new MutationObserver(pintar).observe(est, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
    pintar();
    oa.addEventListener('click', async e => {
      const b = e.target.closest('[data-ct-outlook]');
      if (!b) return;
      if (document.activeElement === b) oa.dataset.foco = '1';
      if (b.dataset.ctOutlook === '1') return $('[data-rn-accion="activar"]', est)?.click();  // lo hace reunion.js
      b.disabled = true;
      try {
        const r = await api('/api/calendario/activar', { activar: false });
        casilla.checked = false;
        if (S.config) S.config.outlook_on = false;
        if (typeof reunionEstadoHtml === 'function') est.innerHTML = reunionEstadoHtml(r);  // y pintar(), al verlo
        else pintar();
      } catch (err) { b.disabled = false; toast(esc(err.message), 'err'); }
    });
    return;
  }
  // sin fix-calendario: lo que se sabe sin preguntar a Outlook, y «Activar» se guarda con «Guardar» (se dice)
  const pintar = () => {
    const on = casilla.checked;
    probar?.classList.toggle('hidden', !on || fueraWin);
    chip(oe, on && !fueraWin ? 'ok' : fueraWin ? 'aviso' : '', fueraWin ? 'Solo en Windows' : on ? 'Activado' : 'No conectado');
    const guardado = !!S.config?.outlook_on, pendiente = on !== guardado ? (on ? 'Se activa al pulsar Guardar. ' : 'Se desactiva al pulsar Guardar. ') : '';
    frase.textContent = pendiente + (fueraWin ? 'El calendario de Outlook solo se lee en Windows.'
      : on ? 'Al grabar, mira qué cita tienes a esa hora. «Probar» dice si responde ahora.'
        : `${APP_NAME} solo lee el Outlook clásico de escritorio, abierto. Sin él, el nombre sale de Teams o de tus reuniones recientes.`);
    // fuera de Windows no se ofrece «Activar» (no serviría); si estaba activado, sí «Desactivar»
    oa.innerHTML = on ? boton(false, 'Desactivar') : fueraWin ? '' : boton(true, 'Activar');
  };
  pintar();
  casilla.addEventListener('change', pintar);
  oa.addEventListener('click', e => {
    const b = e.target.closest('[data-ct-outlook]');
    if (!b) return;
    casilla.checked = b.dataset.ctOutlook === '1';
    casilla.dispatchEvent(new Event('change', { bubbles: true }));
    $('[data-ct-outlook]', oa)?.focus();  // el botón se ha repintado: el foco, al nuevo (teclado)
  });
}
