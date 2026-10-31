/* Reuniones: sugerencia de nombre, tema y participantes al grabar (título de la ventana de reunión de Teams y, si se
   activa en Ajustes, la cita en curso del Outlook clásico) y los ajustes del aviso flotante «Grabar».
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
    partes.push(s.vocab_label ? `tema sugerido: <b>${esc(s.vocab_label)}</b>` : 'ningún tema encaja');
    if (o.blocked?.length) partes.push(`Outlook no dejó leer: ${esc(o.blocked.join(', '))}`);
    return partes.join(' · ');
  }
  const teams = t ? `Teams: <b>${esc(t.title)}</b>${s.vocab_label ? ` · tema sugerido: <b>${esc(s.vocab_label)}</b>` : ''}`
    : 'sin ventana de reunión de Teams abierta';
  return r.reason ? `<span style="color:var(--warn)">${esc(r.reason)}</span> · ${teams}` : teams.replace(/^s/, 'S');
}

function reunionFieldsetHtml() {
  const c = S.config || {};
  return `<fieldset><legend>${I.calendar}Reuniones</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="rnOutlook" ${c.outlook_on ? 'checked' : ''}> Leer mi calendario de Outlook para sugerir el nombre, el tema y los participantes al grabar</label>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="rnProbar">Probar</button><span id="rnProbarRes" class="muted" style="font-size:12.5px"></span></div>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:12px"><input type="checkbox" id="rnAviso" ${c.rec_aviso !== false ? 'checked' : ''}> Aviso flotante con «Grabar» al detectar una llamada</label>
      <div class="help">El título de la ventana de reunión de Teams se lee siempre y no activa nada. Con Outlook, la app mira qué cita hay a esta hora en tu calendario: necesita el Outlook clásico de escritorio abierto y la librería pywin32 (la instala INSTALAR.bat). Si Outlook pregunta si permites el acceso, acepta «Permitir». Todo se queda en este PC: los nombres de los asistentes solo se escriben en la cabecera del .md de la grabación, y puedes borrarlos ahí.</div>
    </fieldset>`;
}

function bindReunionSettings(d) {
  const b = $('#rnProbar', d), res = $('#rnProbarRes', d);
  if (!b || !res) return;
  b.onclick = async () => {
    res.textContent = 'Consultando…';
    b.disabled = true;
    try { res.innerHTML = reunionLinea(await api('/api/reunion/actual?outlook=1')); }
    catch (err) { res.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
    b.disabled = false;
  };
}

function reunionCollect(d) {
  const o = $('#rnOutlook', d), a = $('#rnAviso', d);
  return o && a ? { outlook_on: o.checked, rec_aviso: a.checked } : {};
}

/* Diálogo Grabar: precarga nombre, tema y participantes si hay reunión en curso, con una nota y «Quitar». */
async function reunionPrefill(d) {
  let r;
  try { r = await api('/api/reunion/actual'); } catch { return; }
  const s = (r && r.sugerencia) || {};
  if (!s.source || !document.body.contains(d)) return;
  const name = $('#rName', d), sel = $('#rVocab', d);
  const prev = { name: name ? name.value : '', vocab: sel ? sel.value : '' };
  if (name && !name.value && s.name) name.value = s.name;
  let vocabOk = false;
  if (sel && s.vocab && [...sel.options].some(o => o.value === s.vocab)) {
    sel.value = s.vocab;
    sel.dispatchEvent(new Event('change'));  // carpeta del proyecto y aviso de cuánto cabe, como si lo eligieras tú
    vocabOk = true;
  }
  d.dataset.rnParticipantes = (s.participants || []).join(', ');
  d.dataset.rnSource = s.source;
  const partes = [];
  if (name && s.name) partes.push(`nombre «${esc(s.name)}»`);
  if (vocabOk) partes.push(`tema <b>${esc(s.vocab_label)}</b>`);
  if (s.participants?.length) partes.push(`${s.participants.length} participantes (irán a las notas de la reunión)`);
  if (!partes.length) return;
  const nota = document.createElement('div');
  nota.className = 'help';
  nota.id = 'rnNota';
  nota.innerHTML = `Sugerido por ${s.source === 'outlook' ? 'Outlook' : 'Teams'}: ${partes.join(' · ')}. Puedes cambiarlo. <a href="#" id="rnQuitar">Quitar</a>`;
  (name && name.closest('.grid2') || $('#rForm', d)).after(nota);
  $('#rnQuitar', nota).onclick = e => {
    e.preventDefault();
    if (name) name.value = prev.name;
    if (sel && vocabOk) { sel.value = prev.vocab; sel.dispatchEvent(new Event('change')); }
    delete d.dataset.rnParticipantes;
    delete d.dataset.rnSource;
    nota.remove();
  };
}

function reunionExtra(d) {
  return { participantes: d.dataset.rnParticipantes || '', sugerido: d.dataset.rnSource || '' };
}
