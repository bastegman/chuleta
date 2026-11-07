/* Quién habla (voces): etiquetas de hablante en la transcripción, popover para nombrar / recordar / fusionar,
   barra de estado en la reunión y el apartado «Voces» de Ajustes. Todo contra /api/voces* y /api/hablantes. */
S.spk = null;  // { path, mtime, exists, hablantes, tu, who[], n, known, personas, usuario, enabled, job }

const spkInfo = h => (S.spk && S.spk.hablantes && S.spk.hablantes[h]) || null;
const spkLabel = h => h === 'varios' ? 'varios' : (spkInfo(h) || {}).label || (h ? 'Persona ' + h.replace(/^h/, '') : '');
function spkChip(h) {
  if (!h) return '';
  const i = spkInfo(h), cls = h === 'varios' ? ' many' : i && i.usuario && !i.provisional ? ' me' : i && i.breve ? ' brief' : i && i.provisional ? ' doubt' : i && i.persona ? ' known' : '';
  return `<button class="spk${cls}${/^h\d+$/.test(h) ? ' c' + ((parseInt(h.slice(1), 10) - 1) % 5 + 1) : ''}" data-spk="${esc(h)}" title="${h === 'varios' ? 'Hablan varias personas a la vez' : 'Quién habla · clic para poner nombre, recordar o corregir'}">${esc(spkLabel(h))}</button>`;
}
const fmtSecs = s => s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`;

async function loadSpeakers(a) {
  if (!a) return;
  const want = a.path;
  let r;
  try { r = await api('/api/hablantes?path=' + enc(a.path)); } catch { r = { exists: false }; }
  if (S.route.kind !== 'a' || S.route.path !== want) return;
  const hadWho = S.spk && S.spk.path === want && JSON.stringify(S.spk.who || []);
  S.spk = { path: want, mtime: a.spk_mtime, ...r };
  renderVoicesBar(a);
  if (S.tx && S.tx.path === want && hadWho !== JSON.stringify(S.spk.who || []) && !S.editing) renderTxBody();
}

function vozJobFor(path) { return S.jobs.find(j => j.path === path && j.kind === 'voces' && isActive(j)); }
function renderVoicesBar(a) {
  const el = $('#aVoz');
  if (!el) return;
  const j = vozJobFor(a.path), s = S.spk && S.spk.path === a.path ? S.spk : null;
  const last = S.jobs.filter(x => x.path === a.path && x.kind === 'voces').pop();
  const on = S.config.voices && S.config.voices.enabled;
  let html = '';
  if (j) {
    html = `<span class="vz-st">${I.voice}${esc(j.status === 'queued' ? 'En cola para identificar voces' : j.stage)}${j.status === 'running' ? ` (${Math.round(j.progress * 100)} %)` : ''}</span>
      <span class="sp"></span><button class="btn ghost sm danger" data-act="cancel-job" data-id="${j.id}">Cancelar</button>`;
  } else if (s && s.exists) {
    html = `<span class="vz-st">${I.voice}Voces: ${s.n} ${s.n === 1 ? 'persona' : 'personas'} · ${s.known} reconocida${s.known === 1 ? '' : 's'}${Object.values(s.hablantes || {}).some(x => x.usuario && !x.provisional) ? ' · Tú' : ''}</span>
      <span class="muted">· clic en una etiqueta para poner nombre</span><span class="sp"></span>
      ${S.tx && S.tx.kind === 'srt' ? `<button class="btn ghost sm" data-act="voz-export" title="Guarda <reunión>.hablantes.txt junto al audio">${I.text}Exportar con hablantes</button>` : ''}
      ${on ? `<button class="btn ghost sm" data-act="voz-identify" title="Repetir la identificación">${I.refresh}Volver a identificar</button>` : ''}`;
  } else if (on && (a.txt || a.srt) && !a.recording && !jobFor(a.path)) {
    html = `${last && last.status === 'error' ? `<span class="vz-st err">No se pudieron identificar las voces: ${esc(last.error || '')}</span>` : '<span class="muted">' + I.voice + 'Quién habla: aún sin identificar</span>'}
      <span class="sp"></span><button class="btn sm" data-act="voz-identify">${I.voice}Identificar voces</button>`;
  }
  el.innerHTML = html ? `<div class="card voz-bar">${html}</div>` : '';
}

/* trabajos de voces: al terminar, recargar las etiquetas de la reunión abierta (lo llama onJobs de app.js) */
function onVoicesJobs(prev) {
  const a = curAudio();
  for (const j of S.jobs) {
    if (j.kind !== 'voces') continue;
    const p = prev.get(j.id);
    if (p && p.status !== j.status && !isActive(j) && a && a.path === j.path) { S.spk = null; loadSpeakers(a); }
  }
  if (a && $('#aVoz')) renderVoicesBar(a);
}

/* ---------------------------------------------------------------- popover de un hablante */
/* Lo que se manda al servidor desde la etiqueta (funciones puras, probadas en tests/test_fix_voces.py). Escribir un
   nombre dice QUIÉN es este hablante (alguien que ya se llama así, o una persona nueva): nunca renombra a la persona
   que tenía salvo que se marque «solo corrijo el nombre», y nunca a la tuya (msg 1170: «asume que soy yo»). */
// `creado`: la identificación sobre la que se abrió la etiqueta (si se volvió a identificar, «h2» puede ser otra voz)
function spkNameBody(a, h, cur, nombre, opts) {
  const own = cur && !cur.es_usuario;
  return { id: own ? cur.id : undefined, renombrar: !!(own && opts.corregir), path: a.path, h, nombre, recordar: !!opts.recordar, consentimiento: !!opts.consentimiento, creado: opts.creado };
}
function spkMeBody(a, h, s, recordar) {
  // sin el nombre del cuadro (puede ser el de la persona que ya tenía): tu persona se llama «Yo» hasta que la cambies
  return { path: a.path, h, es_usuario: true, recordar: !!recordar, consentimiento: !!recordar, creado: s.creado };
}
function spkPickBody(a, h, i, target, creado) {
  // «Es…»: un hablante sin persona o que eres tú se reasigna. «Fusionar con…» solo si ya es de otra persona, y nunca
  // con la tuya (dentro de otra, esa pasaría a ser «Tú» con tus huellas; al revés, tú te quedarías con su voz)
  return i.persona && !i.usuario ? ['/api/voces/fusionar', { id_a: target, id_b: i.persona }] : ['/api/voces/reasignar', { path: a.path, h, persona: target, creado }];
}

function closeSpkPop() { $$('.spk-pop').forEach(p => p.remove()); }
function openSpeakerPop(h, anchor) {
  closePop(); closeSpkPop();
  const a = curAudio(), s = S.spk;
  if (!a || !s || h === 'varios') return;
  const i = spkInfo(h) || {}, personas = s.personas || [];
  const cur = i.persona ? personas.find(p => p.id === i.persona) : null;
  const other = cur && !cur.es_usuario, doubtOther = i.provisional && other, merge = i.persona && !i.usuario;
  const pop = document.createElement('div');
  pop.className = 'spk-pop';
  pop.innerHTML = `<div class="sp-h">${esc(spkLabel(h))} <span class="muted">· ${fmtSecs(i.segundos || 0)} de voz${i.score != null ? ` · parecido ${String(Math.round(i.score * 100))} %` : ''}${i.breve ? ' · habla poco' : ''}</span></div>
    ${doubtOther ? `<div class="notice info">Parece <b>${esc(cur.nombre)}</b>, pero no es seguro. Si lo es, pulsa <b>Confirmar</b> (se guarda esta voz como otra huella suya).</div>` : ''}
    ${i.usuario && i.provisional ? '<div class="notice info">Parece tu voz, pero no es seguro. Si eres tú, pulsa <b>Soy yo</b>; si no, escribe su nombre.</div>' : ''}
    ${i.usuario && !i.provisional ? '<div class="notice info">Sale como «Tú» por tu micrófono o tu voz guardada. Si no eres tú, escribe su nombre o pulsa <b>No soy yo</b>.</div>' : ''}
    <label>Nombre</label>
    <div class="row"><input class="input" id="spName" value="${esc(other ? cur.nombre : '')}" placeholder="p. ej. Javier" autocomplete="off" spellcheck="false" list="spNames">
      <datalist id="spNames">${personas.filter(p => !p.es_usuario).map(p => `<option value="${esc(p.nombre)}">`).join('')}</datalist>
      <button class="btn sm primary" data-sa="name">${doubtOther ? 'Confirmar' : 'Guardar'}</button></div>
    ${other && !i.provisional ? `<label class="chk sub hidden" id="spFixRow"><input type="checkbox" id="spFix"> Es la misma persona: solo corrijo el nombre de «${esc(cur.nombre)}» (en todas sus reuniones)</label>` : ''}
    <label class="chk"><input type="checkbox" id="spRem" ${doubtOther ? 'checked' : ''}> Recordar en próximas reuniones <span class="muted">(guarda una huella de su voz, solo en este PC)</span></label>
    <label class="chk sub"><input type="checkbox" id="spConsent"> Tengo el consentimiento de esta persona para guardar su huella de voz en este PC</label>
    <div class="row sp-actions">
      <button class="btn sm${i.usuario && !i.provisional ? ' on' : ''}" data-sa="me" title="Este hablante soy yo${s.usuario ? '' : ' (se crea tu persona para reconocerte también en reuniones presenciales)'}">${i.usuario && !i.provisional ? '✓ Soy yo' : 'Soy yo'}</button>
      <select id="spMerge" class="sm" title="${merge ? 'Si esta persona y otra ya guardada son la misma' : 'Este hablante es una persona ya guardada'}"><option value="">${merge ? 'Fusionar con…' : 'Es…'}</option>${personas.filter(p => p.id !== i.persona && !(merge && p.es_usuario)).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}${p.es_usuario ? ' (tú)' : ''}</option>`).join('')}</select>
      ${i.persona || i.usuario ? `<button class="btn sm" data-sa="notme">${i.usuario ? 'No soy yo' : 'No es esta persona'}</button>` : ''}
      ${other ? '<button class="btn sm ghost danger" data-sa="forget" title="Borra su nombre y sus huellas de este PC">Olvidar persona</button>' : ''}
    </div>`;
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 440)) + 'px';
  pop.style.top = Math.min(r.bottom + 6, innerHeight - 320) + 'px';
  pop.addEventListener('mousedown', e => e.stopPropagation());
  setTimeout(() => document.addEventListener('mousedown', closeSpkPop, { once: true }), 0);
  const refresh = async msg => { closeSpkPop(); S.spk = null; await loadSpeakers(a); if (msg) toast(msg); };
  const remember = () => $('#spRem', pop).checked;
  const consent = () => $('#spConsent', pop).checked;
  const run = async fn => { try { await fn(); } catch (err) { toast(esc(err.message), 'err'); } };
  const fixRow = $('#spFixRow', pop);
  $('#spName', pop).addEventListener('keydown', e => { if (e.key === 'Enter') $('[data-sa="name"]', pop).click(); });
  $('#spName', pop).addEventListener('input', e => { if (fixRow) fixRow.classList.toggle('hidden', !e.target.value.trim() || e.target.value.trim() === cur.nombre); });
  $('#spMerge', pop).onchange = () => run(async () => {
    const target = $('#spMerge', pop).value;
    if (!target) return;
    const [url, body] = spkPickBody(a, h, i, target, s.creado);
    await api(url, body);
    await refresh(url.endsWith('fusionar') ? 'Personas fusionadas: las reuniones anteriores ya muestran el nombre único' : 'Hablante asignado');
  });
  pop.addEventListener('click', e => {
    const b = e.target.closest('[data-sa]');
    if (!b) return;
    const act = b.dataset.sa;
    run(async () => {
      if (act === 'name') {
        const nombre = $('#spName', pop).value.trim();
        if (!nombre) return toast(i.usuario ? 'Escribe su nombre (o pulsa «Soy yo» si eres tú)' : 'Escribe un nombre');
        if (remember() && !consent()) return toast('Para guardar la huella marca la casilla de consentimiento', 'err');
        const corregir = !!(fixRow && !fixRow.classList.contains('hidden') && $('#spFix', pop).checked);
        const r = await api('/api/voces/persona', spkNameBody(a, h, cur, nombre, { corregir, recordar: remember(), consentimiento: consent(), creado: s.creado }));
        await refresh(remember() ? `🗣 ${esc(nombre)}: huella guardada (${r.huellas}). Se reconocerá en las próximas reuniones.` : corregir ? `🗣 Nombre corregido en todas sus reuniones: ${esc(nombre)}` : `🗣 ${esc(nombre)} en esta reunión. Marca «Recordar» si quieres reconocerle otras veces.`);
      } else if (act === 'me') {
        await api('/api/voces/persona', spkMeBody(a, h, s, remember()));
        await refresh(remember() ? 'Marcado como «Tú» y huella guardada: también te reconocerá en reuniones presenciales' : 'Marcado como «Tú» en esta reunión');
      } else if (act === 'notme') {
        await api('/api/voces/reasignar', { path: a.path, h, persona: null, creado: s.creado });
        await refresh(i.usuario ? 'Ya no sale como «Tú»: vuelve a ser una persona sin nombre en esta reunión' : 'Desasignado: vuelve a ser una persona sin nombre en esta reunión');
      } else if (act === 'forget') {
        if (!confirm(`¿Olvidar a «${cur.nombre}»? Se borran su nombre y sus huellas de voz de este PC. Las reuniones pasadas lo mostrarán como «Persona N».`)) return;
        await api('/api/voces/olvidar', { id: cur.id });
        await refresh('Persona olvidada');
      }
    });
  });
  $('#spName', pop).focus();
}

main.addEventListener('click', async e => {
  const chip = e.target.closest('.spk');
  if (chip) { e.preventDefault(); e.stopPropagation(); openSpeakerPop(chip.dataset.spk, chip); return; }
  const act = e.target.closest('[data-act]');
  if (!act || !main.contains(act)) return;
  const a = curAudio();
  if (!a) return;
  if (act.dataset.act === 'voz-identify') {
    try { await api('/api/voces/identificar', { path: a.path }); toast('🗣 Identificando voces en segundo plano (unos 2 s por minuto de reunión)…'); pollJobs(true); }
    catch (err) { toast(esc(err.message), 'err'); }
  } else if (act.dataset.act === 'voz-export') {
    try {
      const r = await api('/api/voces/exportar', { path: a.path });
      toast(`<b>✓</b> Guardado ${esc(r.path.split('/').pop())} junto al audio (clic para abrirlo)`, 'ok', () => api('/api/open', { path: a.path, ext: '.hablantes.txt' }).catch(() => { }));
    } catch (err) { toast(esc(err.message), 'err'); }
  }
});

/* ---------------------------------------------------------------- Ajustes → Voces */
function vocesFieldsetHtml() {
  const v = S.config.voices || {};
  return `<fieldset><legend>${I.voice}Voces (opcional)</legend>
      <label style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" id="vzOn" ${v.enabled ? 'checked' : ''}> Reconocer quién habla en cada reunión (en este PC, sin enviar nada fuera)</label>
      <div id="vzModels" class="help" style="margin-top:4px"></div>
      <div id="vzPeople" class="help" style="margin-top:6px">${v.enabled ? 'Cargando…' : 'Al activarlo se descargan dos modelos (≈47 MB, una sola vez) y, al terminar cada transcripción, un proceso de prioridad baja separa las voces (≈2 s por minuto de reunión). En la transcripción verás «Persona 1», «Persona 2»…: ponles nombre y, si tienes su consentimiento, pulsa «Recordar» para reconocerlas en las próximas reuniones.'}</div>
    </fieldset>`;
}

function bindVocesSettings(d) {
  let timer = null;
  const draw = st => {
    const m = $('#vzModels', d), p = $('#vzPeople', d);
    if (!m || !p) return;
    if (!st.enabled) { m.innerHTML = ''; return; }
    if (st.available === false) m.innerHTML = `<span style="color:var(--err)">✖ La librería de voces (sherpa-onnx) no carga en este PC: vuelve a ejecutar INSTALAR.bat. ${esc(st.why || '')}</span>`;
    else if (st.models.downloading) m.innerHTML = `${esc(st.models.stage || 'Descargando modelos…')} ${Math.round((st.models.frac || 0) * 100)} %`;
    else if (st.models.error) m.innerHTML = `<span style="color:var(--err)">✖ No se pudieron descargar los modelos: ${esc(st.models.error)}</span> <button class="btn ghost sm" type="button" id="vzRetry">Reintentar</button>`;
    else if (st.models.ready) m.innerHTML = `<span style="color:var(--ok)">✓ Modelos listos</span> · se guardan en %LOCALAPPDATA%\\AudiosApp\\models\\voces`;
    else m.innerHTML = 'Modelos pendientes de descarga.';
    const ps = st.personas || [];
    p.innerHTML = `${ps.length ? `<table class="vtable vz-table"><tr><th>Persona</th><th>Tú</th><th>Huellas</th><th>Última vez</th><th></th></tr>${ps.map(x => `<tr>
        <td><input class="input sm" data-vzname="${esc(x.id)}" value="${esc(x.nombre)}" spellcheck="false"></td>
        <td>${x.es_usuario ? '✓' : `<button class="btn ghost sm" type="button" data-vzme="${esc(x.id)}" title="Esta persona soy yo">Soy yo</button>`}</td>
        <td>${x.huellas}${x.mezcla ? ' <span class="vz-mix" title="Sus huellas no se parecen entre sí: puede llevar la voz de otra persona. Olvídala y vuelve a guardarla desde una reunión.">⚠</span>' : ''}</td><td>${x.ultima ? esc(x.ultima.slice(0, 10)) : '—'}</td>
        <td><button class="btn ghost sm danger" type="button" data-vzforget="${esc(x.id)}">Olvidar</button></td></tr>`).join('')}</table>
      ${ps.some(x => x.es_usuario && x.mezcla) ? '<div class="help" style="margin-top:6px;color:var(--err)">⚠ Tu persona tiene huellas que no se parecen entre sí: puede llevar la voz de otra persona (pasaba al poner nombre a un «Tú» antes del 07/11/2026). Mientras, tu voz no se reconoce sola: se pregunta. Pulsa «Olvidar» en tu fila y, en tu próxima reunión, «Soy yo» con «Recordar».</div>' : ''}
      <div class="row" style="margin-top:8px"><button class="btn ghost sm danger" type="button" id="vzForgetAll">Olvidar todas</button></div>` : '<div>Todavía no hay personas guardadas: aparecerán cuando pongas nombre a un hablante en una reunión y pulses «Recordar».</div>'}
      <div style="margin-top:8px">Qué se guarda y dónde: por reunión, <code>&lt;grabación&gt;.hablantes.json</code> junto al audio (tramos y «Persona N», sin datos de voz); las huellas de voz (vectores de 192 números por persona, máximo 5) solo en <code>%LOCALAPPDATA%\\AudiosApp\\voces.json</code>, cifrado con la cuenta de Windows y fuera de OneDrive. Son datos biométricos: guarda huellas solo con el consentimiento de cada persona; olvidarlas las borra de verdad. Nada se envía a la nube${st.store_error ? `<br><span style="color:var(--err)">voces.json no se pudo leer y se apartó (${esc(st.store_error)})</span>` : ''}.</div>`;
  };
  const poll = async () => {
    clearTimeout(timer);
    if (!document.body.contains(d)) return;
    try { const st = await api('/api/voces'); draw(st); if (st.enabled && st.models.downloading) timer = setTimeout(poll, 1500); } catch { }
  };
  if (S.config.voices && S.config.voices.enabled) poll();
  $('#vzOn', d).onchange = async e => {
    const on = e.target.checked;
    if (on) {
      e.target.checked = false;
      const ok = await ask('Reconocer quién habla', `<p>${APP_NAME} separará las voces de cada reunión <b>en este PC</b> (nada sale a internet) y, cuando tú lo pidas, guardará una <b>huella de voz</b> por persona para reconocerla en las próximas reuniones.</p>
        <ul><li>Se descargan dos modelos (≈47 MB, una sola vez) a <code>%LOCALAPPDATA%\\AudiosApp\\models\\voces</code>.</li>
        <li>Las huellas van a <code>%LOCALAPPDATA%\\AudiosApp\\voces.json</code>, cifrado con tu cuenta de Windows, fuera de OneDrive, y se pueden olvidar en cualquier momento.</li>
        <li>Una huella de voz es un <b>dato biométrico</b>: solo se guarda si marcas que tienes el consentimiento de esa persona. Ese consentimiento es responsabilidad tuya.</li></ul>`, 'Activar');
      openSettings();
      if (!ok) return;
      try { const st = await api('/api/voces/activar', { on: true }); S.config.voices = { enabled: true, models_ready: st.models.ready }; toast('🗣 Voces activadas' + (st.models.ready ? '' : ': descargando modelos…')); openSettings(); }
      catch (err) { toast(esc(err.message), 'err'); }
    } else {
      try { await api('/api/voces/activar', { on: false }); S.config.voices = { enabled: false, models_ready: S.config.voices?.models_ready }; toast('Voces desactivadas (las personas guardadas se conservan hasta que las olvides)'); draw({ enabled: false }); }
      catch (err) { toast(esc(err.message), 'err'); }
    }
  };
  d.addEventListener('change', async e => {
    const n = e.target.closest('[data-vzname]');
    if (!n) return;
    try { await api('/api/voces/persona', { id: n.dataset.vzname, nombre: n.value }); toast('Nombre guardado: las reuniones pasadas y futuras lo usan'); } catch (err) { toast(esc(err.message), 'err'); poll(); }
  });
  d.addEventListener('click', async e => {
    const me = e.target.closest('[data-vzme]'), fg = e.target.closest('[data-vzforget]'), all = e.target.closest('#vzForgetAll'), rt = e.target.closest('#vzRetry');
    try {
      if (me) { await api('/api/voces/persona', { id: me.dataset.vzme, es_usuario: true }); poll(); }
      else if (fg) { if (confirm('¿Olvidar esta persona? Se borran su nombre y sus huellas de voz de este PC.')) { await api('/api/voces/olvidar', { id: fg.dataset.vzforget }); poll(); } }
      else if (all) { if (confirm('¿Olvidar TODAS las personas y sus huellas de voz? No se puede deshacer.')) { await api('/api/voces/olvidar', { all: true }); poll(); } }
      else if (rt) { await api('/api/voces/activar', { on: true }); poll(); }
    } catch (err) { toast(esc(err.message), 'err'); }
  });
}
