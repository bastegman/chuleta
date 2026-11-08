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
  return `<div class="ag-cab"><h4>Al grabar</h4><div class="help" style="margin-top:2px">Se usa con el botón Grabar, con Ctrl+Alt+R y con el aviso de Teams.</div></div>
    <div class="ag-plan">
      <div class="ag-plan-h"><span>Así se graba ahora una reunión de</span><select id="agProy" title="El proyecto que quieres comprobar"></select></div>
      <div id="agFilas"><div class="gr-cargando">Calculando…</div></div>
    </div>
    <div class="ag-reglas">
      ${typeof perfilReglasHtml === 'function' ? perfilReglasHtml() : `<div class="ag-regla"><label for="sLang">Idioma por defecto</label><div>${langSelect('sLang', c.language ?? 'es-en')}</div></div>`}
      ${'carpeta_por_reunion' in c ? `<div class="ag-regla"><label for="agPorReunion">Una carpeta por reunión</label><div><label class="ag-sw"><input type="checkbox" id="agPorReunion"${c.carpeta_por_reunion !== false ? ' checked' : ''}>fecha y nombre, dentro de la carpeta del proyecto</label></div></div>` : ''}
      <div class="ag-regla"><label for="agMic">Micrófono · audio del PC</label><div class="ag-dos"><select id="agMic">${ajDispositivos(null)[0]}</select><select id="agSys">${ajDispositivos(null)[1]}</select><span id="agDevsAviso" class="hidden"></span></div></div>
      <div class="ag-regla"><label for="agDirecto">Texto en directo</label><div><label class="ag-sw"><input type="checkbox" id="agDirecto"${c.rec_directo !== false ? ' checked' : ''}>si el portátil va lento, quítalo: solo graba y transcribe al terminar</label></div></div>
      <div class="ag-regla"><label for="lMeet">Llamada detectada</label><div><label class="ag-sw"><input type="checkbox" id="lMeet"${c.meeting_detect !== false ? ' checked' : ''}>aviso con «Grabar» al detectar una llamada (Teams, Zoom…)</label></div></div>
    </div>
    <details class="ag-como"><summary>¿Cómo funciona?</summary><div class="help">Lo de arriba sale de tres capas, de más a menos fuerte: lo que cambies para una reunión en la tarjeta de grabar, el proyecto o workstream (<a href="#" data-sec-ir="proyectos">Proyectos</a>) y estas reglas. <b>Español + inglés</b>, <b>Español + portugués</b> e <b>Inglés + portugués</b> detectan el idioma cada pocos segundos, pero solo entre esos dos, y dejan cada frase en su idioma; si una reunión es solo en uno, fijarlo es algo más preciso. El contexto lo recibe Whisper detrás del vocabulario (si no cabe todo, se recorta antes que el vocabulario) y lo tiene en cuenta el resumen.</div></details>
    <div class="ag-pie help">Carpeta, idioma y vocabulario de cada proyecto: <a href="#" data-sec-ir="proyectos">Proyectos ›</a></div>`;
}
/* opciones de micrófono y audio del PC con lo guardado elegido (sin la lista de dispositivos: las genéricas) */
function ajDispositivos(devs) {
  const c = S.config || {}, def = list => nfc(((list || []).find(x => x.default) || {}).name || '');
  const o = (v, l, s) => `<option value="${esc(v)}"${v === s ? ' selected' : ''}>${esc(l)}</option>`;
  const mic = c.rec_mic || 'default', sys = c.rec_sys || 'all';
  let m = o('default', `Micrófono predeterminado de Windows${def(devs?.mics) ? ` (ahora: ${def(devs.mics)})` : ''}`, mic)
    + (devs?.mics?.length ? `<optgroup label="Fijar un micrófono">${devs.mics.map(x => o(x.id, nfc(x.name), mic)).join('')}</optgroup>` : '')
    + o('none', 'Sin micrófono', mic);
  let s = o('all', 'Todo lo que suena en el PC', sys) + o('default', `Salida predeterminada de Windows${def(devs?.outputs) ? ` (ahora: ${def(devs.outputs)})` : ''}`, sys)
    + (devs?.outputs?.length ? `<optgroup label="Fijar una salida">${devs.outputs.map(x => o(x.id, nfc(x.name), sys)).join('')}</optgroup>` : '')
    + o('none', 'Sin el audio del PC', sys);
  // un dispositivo guardado que no está en la lista se enseña tal cual, para no cambiarlo sin querer
  if (!['default', 'none'].includes(mic) && !(devs?.mics || []).some(x => x.id === mic)) m += o(mic, 'El que elegiste (no está conectado)', mic);
  if (!['all', 'default', 'none'].includes(sys) && !(devs?.outputs || []).some(x => x.id === sys)) s += o(sys, 'La que elegiste (no está conectada)', sys);
  return [m, s];
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
    box.innerHTML = plan.lineas.filter(l => l.clave !== 'contexto' || l.valor).map(l => {
      const ir = { proyecto: 'proyectos', workstream: 'proyectos', procesado: 'procesado' }[l.origen];
      const origen = ir ? `<a href="#" class="gr-origen ag-ir" data-sec-ir="${ir}">${esc(l.origen_txt)}</a>` : `<span class="gr-origen">${esc(l.origen_txt)}</span>`;
      const v = l.clave === 'carpeta' ? `<code class="gr-ruta">${esc(l.valor)}</code>` : `<b>${esc(l.valor)}</b>`;
      const icon = (GR_FILAS.find(([k]) => k === l.clave) || [])[1];
      return `<div class="gr-fila" data-fila="${l.clave}"><span class="gr-ic">${icon ? I[icon] : ''}</span><span class="gr-et">${esc(l.etiqueta)}</span><span class="gr-v">${v}</span>${origen}<span></span></div>`;
    }).join('');
  };
  const pedir = async () => {
    const n = ++seq;
    try {
      const plan = await api('/api/grabar/plan', { nombre: '', participantes: '', vocab: proy.value, ajustes: ajAlGrabarValores(d) });
      if (n === seq && document.body.contains(box)) pintar(plan);
    } catch (err) { if (n === seq) box.innerHTML = `<div class="notice">${esc(err.message)}</div>`; }
  };
  const luego = () => { clearTimeout(t); t = setTimeout(pedir, 250); };
  const listas = () => {
    const sel = proy.value;
    proy.innerHTML = `<option value="">Sin proyecto</option>${(S.vocab || []).map(v => `<option value="${esc(v.id)}">${esc(v.project)} › ${esc(v.name)}</option>`).join('')}`;
    // sin haberlo elegido tú, el primer proyecto (si lo hay): lo normal es querer ver el de un proyecto
    proy.value = elegido && [...proy.options].some(o => o.value === sel) ? sel : (S.vocab?.[0]?.id || '');
  };
  listas();
  proy.onchange = () => { elegido = true; pedir(); };
  // las reglas de abajo cambian el plan de arriba al momento, aunque no se hayan guardado
  ['sLang', 'agMic', 'agSys', 'agDirecto', 'pfFolder', 'pfVocab', 'pfContext'].forEach(id => {
    const el = $('#' + id, d);
    if (el) { el.addEventListener('change', luego); el.addEventListener('input', luego); }
  });
  // micrófono y audio del PC: los de Ajustes (lo de una reunión se cambia en su tarjeta). Con la lista de
  // dispositivos, sin cambiar lo elegido (ni lo que ya hayas tocado)
  const c = S.config || {}, mic = $('#agMic', d), sys = $('#agSys', d);
  api('/api/rec/devices').then(r => {
    if (!document.body.contains(mic)) return;
    const [m, s] = ajDispositivos(r), vm = mic.value, vs = sys.value;
    mic.innerHTML = m; sys.innerHTML = s;
    if ([...mic.options].some(x => x.value === vm)) mic.value = vm;
    if ([...sys.options].some(x => x.value === vs)) sys.value = vs;
  }).catch(err => {
    const av = $('#agDevsAviso', d);
    if (!av || !document.body.contains(av)) return;
    av.outerHTML = grDevsAviso(err.message, ': aquí solo están los predeterminados de Windows').replace('<span ', '<span id="agDevsAviso" ');
  });
  // las listas pueden llegar después de abrir Ajustes
  loadVocab(c.language ?? 'es-en').then(() => { if (document.body.contains(proy)) { listas(); pedir(); } });
  pedir();
}

/* ---------------------------------------------------------------- Proyectos: carpeta e idioma de cada uno */
function ajProyectosHtml() {
  return `<div class="ag-cab"><h4>Proyectos y workstreams</h4><div class="help" style="margin-top:2px">La carpeta y el idioma de cada uno. Lo que está en gris no lo has elegido aquí: el workstream usa lo del proyecto, y el proyecto, su carpeta y el idioma de <a href="#" data-sec-ir="algrabar">Al grabar</a>. Los cambios de esta tabla se guardan al momento.</div></div>
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
   tarjeta de grabar (null mientras llega o si falla: entonces solo lo asignado) */
function ajProyectosTabla(car) {
  const c = S.config || {}, pl = c.project_langs || {}, wl = c.workstream_langs || {};
  // con la rama de carpetas, project_folders llega resuelta (con la del árbol) y las asignadas van aparte
  const asig = c.project_folders_asignadas || c.project_folders || {};
  const grupos = typeof perfilGroups === 'function' ? perfilGroups(S.vocab || []) : [];
  if (!grupos.length) {
    const conectar = typeof vocabConectar === 'function' ? ' o conecta la carpeta donde ya tienes tus listas' : '';
    return `<div class="help" style="margin-top:0">Todavía no hay proyectos. Crea uno con «Nuevo proyecto», abajo en Listas de vocabulario${conectar}: cada proyecto tiene sus workstreams, y cada workstream, su lista de nombres y siglas.</div>`;
  }
  const lang = code => esc(perfilLangLabel(code));
  const ruta = x => `<code title="${esc(x.ruta)}">${esc(x.ruta)}</code>`;
  const carpeta = p => {
    const x = car?.proyectos?.[p];
    if (!x) return p in asig ? `<code>${esc(asig[p])}</code>` : '<span class="pj-her">sin asignar</span>';
    if (p in asig && x.rel === asig[p]) return ruta(x);
    const perdida = p in asig ? '<span class="warn-txt">La asignada ya no se encuentra</span> · ' : '';
    return `${perdida}<span class="pj-her">${esc(ajCarpetaHeredada(x))} · ${ruta(x)}</span>`;
  };
  const carpetaW = id => {
    const x = car?.workstreams?.[id];
    return x && x.origen === 'workstream' ? ruta(x) : '<span class="pj-her">la del proyecto</span>';
  };
  const filas = grupos.map(([p, vs]) => {
    const cab = `<tr class="pj-p" data-pj="${esc(p)}"><td><b>${esc(p)}</b></td>
      <td>${carpeta(p)}</td>
      <td>${p in pl ? lang(pl[p]) : `<span class="pj-her">el de Al grabar · ${lang(c.language)}</span>`}</td>
      <td><button class="btn ghost sm" type="button" data-pj-editar="${esc(p)}">Editar</button></td></tr>`;
    return cab + vs.map(v => `<tr class="pj-w" data-pjw="${esc(v.id)}"><td>› ${esc(v.name)} <span class="muted">· ${v.terms} ${v.terms === 1 ? 'término' : 'términos'}</span></td>
      <td>${carpetaW(v.id)}</td>
      <td>${v.id in wl ? lang(wl[v.id]) : `<span class="pj-her">el del proyecto${p in pl ? ` · ${lang(pl[p])}` : ''}</span>`}</td>
      <td><button class="btn ghost sm" type="button" data-pjw-editar="${esc(v.id)}">Editar</button></td></tr>`).join('');
  }).join('');
  return `<table class="vtable pj-tabla"><thead><tr><th>Proyecto › workstream</th><th>Carpeta principal</th><th>Idioma</th><th></th></tr></thead><tbody>${filas}</tbody></table>`;
}
function bindAjProyectos(d) {
  const box = $('#pjTabla', d);
  if (!box) return;
  let car = null, seq = 0;
  const dibujar = () => { if (document.body.contains(box)) box.innerHTML = ajProyectosTabla(car); };
  // las carpetas, del servidor (el mismo cálculo que la tarjeta, con lo escrito en Al grabar aunque no se haya guardado)
  const pintar = async () => {
    const n = ++seq;
    try {
      const p = await api('/api/grabar/plan', { nombre: '', participantes: '', vocab: '', proyectos: true, ajustes: ajAlGrabarValores(d) });
      if (n === seq) car = p.carpetas || null;
    } catch { if (n === seq) car = null; }
    if (n === seq && !box.querySelector('.pj-ed')) dibujar();  // una fila a medio editar no se pisa
  };
  loadVocab(S.config.language ?? 'es-en').then(pintar);
  // al volver a Proyectos (quizá con otra carpeta en Al grabar), otra vez
  d.addEventListener('click', e => { if (e.target.closest('.aj-nav [data-sec="proyectos"], [data-sec-ir="proyectos"]')) pintar(); });
  const guardar = async cuerpos => {
    try {
      for (const b of cuerpos) await api('/api/grabar/recordar', b);
      setConfig(await api('/api/config'));
      toast('Guardado');
    } catch (err) { toast(esc(err.message), 'err'); }
    dibujar();
    pintar();
  };
  box.addEventListener('click', async e => {
    const pe = e.target.closest('[data-pj-editar]'), we = e.target.closest('[data-pjw-editar]'), ok = e.target.closest('[data-pj-ok]');
    if (e.target.closest('[data-pj-no]')) return dibujar();
    if (ok) {
      const tr = ok.closest('tr'), vid = tr.dataset.vocab, cuerpos = [];
      const f = $('[data-pj-f]', tr), l = $('[data-pj-l]', tr);
      if (f && f.value !== f.dataset.ini) cuerpos.push({ clave: 'carpeta', alcance: 'proyecto', vocab: vid, valor: f.value === PERFIL_AUTO ? null : f.value });
      if (l && l.value !== l.dataset.ini) cuerpos.push({ clave: 'idioma', alcance: tr.dataset.alcance, vocab: vid, valor: l.value === PERFIL_AUTO ? null : l.value });
      return cuerpos.length ? guardar(cuerpos) : dibujar();
    }
    if (e.target.closest('[data-pj-lista]')) {
      const v = (S.vocab || []).find(x => x.id === e.target.closest('[data-pj-lista]').dataset.pjLista);
      if (v) openVocabEditor(v, openSettings);
      return;
    }
    if (!pe && !we) return;
    dibujar();
    const c = S.config, langs = (sel, her) => `<option value="${PERFIL_AUTO}"${sel == null ? ' selected' : ''}>${esc(her)}</option>`
      + LANGS.map(([v, t]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${esc(t)}</option>`).join('');
    if (pe) {
      const p = pe.dataset.pjEditar, v = (S.vocab || []).find(x => x.project === p), tr = $(`tr[data-pj="${CSS.escape(p)}"]`, box);
      const asig = c.project_folders_asignadas || c.project_folders || {}, fsel = asig[p] ?? PERFIL_AUTO, lsel = c.project_langs?.[p];
      const sa = car?.proyectos?.[p]?.sin_asignar, sinTxt = `Sin asignar · ${ajCarpetaHeredada(sa)}${sa ? ` (${sa.ruta})` : ''}`;
      tr.outerHTML = `<tr class="pj-ed" data-vocab="${esc(v?.id || '')}" data-alcance="proyecto"><td><b>${esc(p)}</b></td>
        <td><select data-pj-f data-ini="${esc(fsel)}" title="Dónde se guardan las reuniones de ${esc(p)}"><option value="${PERFIL_AUTO}"${fsel === PERFIL_AUTO ? ' selected' : ''}>${esc(sinTxt)}</option>${folderOpts(fsel).replace(/<option value="__pick">[^<]*<\/option>/, '')}</select></td>
        <td><select data-pj-l data-ini="${esc(lsel ?? PERFIL_AUTO)}">${langs(lsel, `El de Al grabar · ${perfilLangLabel(c.language)}`)}</select></td>
        <td class="pj-acc"><button class="btn sm primary" type="button" data-pj-ok>Guardar</button><button class="btn ghost sm" type="button" data-pj-no>Cancelar</button></td></tr>`;
    } else {
      const id = we.dataset.pjwEditar, v = (S.vocab || []).find(x => x.id === id), tr = $(`tr[data-pjw="${CSS.escape(id)}"]`, box);
      const lsel = c.workstream_langs?.[id], pl = c.project_langs?.[v?.project];
      tr.outerHTML = `<tr class="pj-ed" data-vocab="${esc(id)}" data-alcance="workstream"><td>› ${esc(v?.name || '')}</td>
        <td><button class="btn sm" type="button" data-pj-lista="${esc(id)}">${I.edit}Editar la lista</button></td>
        <td><select data-pj-l data-ini="${esc(lsel ?? PERFIL_AUTO)}">${langs(lsel, `El del proyecto · ${perfilLangLabel(pl ?? c.language)}`)}</select></td>
        <td class="pj-acc"><button class="btn sm primary" type="button" data-pj-ok>Guardar</button><button class="btn ghost sm" type="button" data-pj-no>Cancelar</button></td></tr>`;
    }
  });
}

/* ---------------------------------------------------------------- Calendario y Teams: el estado en una frase (B3) */
/* Teams, aquí. Outlook lo pinta reunion.js (rama fix-calendario: #rnEstado con GET /api/calendario/estado). Mientras
   esa parte no esté, una fila con lo que se sabe sin preguntar a Outlook ni llamar a una ruta que aún no existe. */
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
    api('/api/reunion/actual').then(r => {
      if (r?.teams?.title && document.body.contains(tf)) tf.innerHTML = `Ahora: <b>${esc(r.teams.title)}</b>. No hace falta nada.`;
    }).catch(() => { });
  }
  if ($('#rnEstado', d)) return;  // el estado de Outlook ya lo dice reunion.js
  const fila = $('#ctOutFila', d), oe = $('#ctOutEstado', d), of = $('#ctOutFrase', d), oa = $('#ctOutAcc', d);
  const pintar = () => {
    const on = !!$('#rnOutlook', d)?.checked;
    chip(oe, '', on ? 'Activado' : 'No conectado');
    of.textContent = on ? 'Pulsa «Probar» para ver si responde ahora.'
      : `${APP_NAME} solo lee el Outlook clásico de escritorio, abierto. Sin él, el nombre sale de Teams o de tus reuniones recientes.`;
    oa.innerHTML = on ? '' : '<button class="btn sm" type="button" data-ct-activar>Activar</button>';
  };
  fila.classList.remove('hidden');
  pintar();
  $('#rnOutlook', d)?.addEventListener('change', pintar);
  oa.addEventListener('click', e => {
    if (!e.target.closest('[data-ct-activar]')) return;
    const o = $('#rnOutlook', d);
    if (o) { o.checked = true; o.dispatchEvent(new Event('change', { bubbles: true })); }
  });
}
