/* Reuniones: lo que la app ve de la reunión en curso (título de la ventana de reunión de Teams y, si se activa en
   Ajustes, la cita en curso del Outlook clásico), el botón «Probar» y los ajustes del aviso flotante «Grabar». La
   sugerencia al grabar (nombre, proyecto y participantes) la aplica el servidor en el plan (grabar.js).
   Se carga después de app.js y usa sus utilidades ($, api, esc, I, S). app.js lo invoca con `window.x?.()`:
   sin este fichero la app funciona igual, sin sugerencias. */

function reunionHora(iso) { return (iso || '').slice(11, 16); }

/* Una línea para el botón «Probar»: lo que la app ve ahora mismo (o por qué no ve nada). */
function reunionLinea(r) {
  const s = r.sugerencia || {}, o = r.outlook, t = r.teams;
  if (o) {
    const partes = [`Ahora: <b>${esc(o.subject || '(sin asunto)')}</b>`, `${reunionHora(o.start)}–${reunionHora(o.end)}`];
    if (o.organizer) partes.push(`organiza ${esc(o.organizer)}`);
    partes.push(`${(o.attendees || []).length} ${(o.attendees || []).length === 1 ? 'asistente' : 'asistentes'}`);
    partes.push(s.vocab_label ? `proyecto sugerido: <b>${esc(s.vocab_label)}</b>` : 'ningún proyecto encaja');
    if (o.blocked?.length) partes.push(`Outlook no dejó leer: ${esc(o.blocked.join(', '))}`);
    return partes.join(' · ');
  }
  const teams = t ? `Teams: <b>${esc(t.title)}</b>${s.vocab_label ? ` · proyecto sugerido: <b>${esc(s.vocab_label)}</b>` : ''}`
    : 'sin ventana de reunión de Teams abierta';
  return r.reason ? `<span style="color:var(--warn)">${esc(r.reason)}</span> · ${teams}` : teams.replace(/^s/, 'S');
}

/* Estado del calendario (GET /api/calendario/estado, contrato en calendario.estado): la frase con su color, el botón
   de su acción («Activar» o «Probar») y, tras «Probar», los pasos. Verde si está conectado; lo que no es un error
   (apagado, cerrado, Outlook nuevo, sin Outlook: el nombre sigue saliendo de Teams), sin color; lo demás, en aviso. */
const RN_AVISO = ['sin_pywin32', 'sin_permiso', 'esperando_permiso', 'ocupado', 'error'];
function reunionEstadoHtml(r) {
  if (!r || !r.frase) return '';
  const color = r.estado === 'conectado' ? 'var(--ok)' : RN_AVISO.includes(r.estado) ? 'var(--warn)' : '';
  let h = `<span${color ? ` style="color:${color}"` : ''}>${esc(r.frase)}</span>`;
  if (r.accion) h += ` <button class="btn sm" type="button" data-rn-accion="${esc(r.accion.id)}">${esc(r.accion.texto)}</button>`;
  if (r.pasos?.length) {
    h += `<ul style="margin:6px 0 0;padding-left:18px">${r.pasos.map(p => `<li>${p.ok === true ? '✓' : p.ok === false ? '✖' : '·'} ${esc(p.texto)}</li>`).join('')}</ul>`;
  }
  return h;
}

function reunionFieldsetHtml() {
  const c = S.config || {};
  return `<fieldset><legend>${I.calendar}Aviso al detectar una llamada</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="rnOutlook" ${c.outlook_on ? 'checked' : ''}> Leer mi calendario de Outlook para sugerir el nombre, el proyecto y los participantes al grabar</label>
      <div id="rnEstado" class="help" style="margin-top:6px" aria-live="polite">Comprobando el calendario…</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="rnProbar">Probar con mi calendario</button><span id="rnProbarRes" class="muted" style="font-size:12.5px"></span></div>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:12px"><input type="checkbox" id="rnAviso" ${c.rec_aviso !== false ? 'checked' : ''}> <span>Aviso flotante con «Grabar» al detectar una llamada <span class="muted" style="font-weight:400">(si «Llamada detectada» está activado en Al grabar)</span></span></label>
      <details class="ag-como"><summary>¿Cómo funciona?</summary><div class="help">El título de la ventana de reunión de Teams se lee siempre y no activa nada. Con Outlook, la app mira qué cita hay a esta hora en tu calendario: necesita el Outlook clásico de escritorio abierto y la librería pywin32 (la instala INSTALAR.bat). Si Outlook pregunta si permites el acceso, acepta «Permitir». La cita se lee en este PC. El nombre que se sugiere al grabar (el asunto de la cita o el título de la reunión de Teams), si lo aceptas, es el nombre de la grabación: va como título al resumen con IA y a las copias en otras carpetas. Los asistentes se guardan en la cabecera del .md de la grabación como participantes <b>sugeridos</b>: no van al resumen con IA ni a las copias en otras carpetas hasta que los confirmes o los edites en la página de la reunión. Una vez confirmados, van con el resumen con IA (si lo usas: sale del PC según lo que elijas en Procesado) y con las copias en otras carpetas (si tienes).</div></details>
    </fieldset>`;
}

function bindReunionSettings(d) {
  const b = $('#rnProbar', d), res = $('#rnProbarRes', d), est = $('#rnEstado', d);
  if (!b || !res || !est) return;
  // cada petición lleva su número: si una lenta llega después de otra más nueva, no la pisa (ni borra sus pasos)
  let ultima = 0;
  const pedir = async (...a) => { const n = ++ultima; const r = await api(...a); return n === ultima ? r : null; };
  const pintar = r => { if (r && document.body.contains(est)) est.innerHTML = reunionEstadoHtml(r); };
  const fallo = err => `<span style="color:var(--err)">${esc(err.message)}</span>`;
  // «Probar» prueba la conexión (la agenda de hoy), no solo si hay una reunión ahora: fuera de una reunión, conectado
  // es verde. Si hay una en curso, además, lo que se sugeriría al grabar
  const probar = async () => {
    res.textContent = 'Consultando… Si Outlook pregunta si permites el acceso, pulsa «Permitir».';
    b.disabled = true;
    try {
      const r = await pedir('/api/calendario/estado?probar=1');
      pintar(r);
      if (r) res.innerHTML = r.proxima?.ahora ? reunionLinea(await api('/api/reunion/actual?outlook=1')) : '';
    } catch (err) { res.innerHTML = fallo(err); }
    b.disabled = false;
  };
  b.onclick = probar;
  est.addEventListener('click', async e => {
    const x = e.target.closest('[data-rn-accion]');
    if (!x) return;
    if (x.dataset.rnAccion === 'probar') return probar();
    if (x.dataset.rnAccion !== 'activar') return;
    x.disabled = true;
    res.textContent = 'Activando… Si Outlook pregunta si permites el acceso, pulsa «Permitir».';
    try {
      const r = await pedir('/api/calendario/activar', { activar: true });  // se guarda ya (como «Probar conexión»)
      const cb = $('#rnOutlook', d);
      if (cb) cb.checked = true;
      if (S.config) S.config.outlook_on = true;
      pintar(r);
      res.textContent = '';
    } catch (err) { res.innerHTML = fallo(err); x.disabled = false; }
  });
  pedir('/api/calendario/estado').then(pintar).catch(err => { est.innerHTML = fallo(err); });
}

function reunionCollect(d) {
  const o = $('#rnOutlook', d), a = $('#rnAviso', d);
  return o && a ? { outlook_on: o.checked, rec_aviso: a.checked } : {};
}



/* Página de la reunión: participantes sugeridos por Outlook/Teams al grabar (props.participantes_origen). Hasta
   que se confirman o se editan, el servidor no los manda al resumen con IA ni a los destinos de salida. notas.js
   pinta esta fila debajo de «Participantes» y avisa al guardarlos (reunionParticipantesGuardados). */
function reunionParticipantesHtml(a) {
  const p = (a && a.props) || {};
  if (!p.participantes || !p.participantes_origen) return '';
  const de = p.participantes_origen === 'outlook' ? 'Outlook' : 'Teams';
  return `<div class="prow" id="rnPartNota"><span class="pk"></span><span class="pv"><span class="muted" style="font-size:12.5px">Sugeridos por ${de}, sin confirmar: no se envían al resumen con IA ni a los destinos de salida hasta que los confirmes o los edites.</span>
    <button class="btn ghost sm" type="button" data-rn-confirmar>${I.check}Confirmar</button></span></div>`;
}

function reunionParticipantesGuardados(a) {
  if (!((a && a.props) || {}).participantes_origen) $('#rnPartNota')?.remove();
}

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-rn-confirmar]');
  if (!b) return;
  const a = typeof curAudio === 'function' ? curAudio() : null;
  if (!a || typeof saveProps !== 'function') return;
  b.disabled = true;
  // guardar los nombres a mano es confirmarlos: el servidor quita participantes_origen. Lo que haya en el campo (si se
  // estaba editando, el blur ya lo guarda con ese mismo valor)
  const v = ($('[data-prop="participantes"]')?.value ?? (a.props || {}).participantes ?? '').trim();
  await saveProps(a, { participantes: v });
  reunionParticipantesGuardados(a);
  if (!(a.props || {}).participantes_origen) toast('Participantes confirmados: ya pueden ir al resumen con IA y a las copias en otras carpetas');
  else b.disabled = false;
});
