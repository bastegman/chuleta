'use strict';
/* Notas: funciones al estilo Notion. Usa las utilidades y el estado S de app.js.
   - Notas por reunion (editor de bloques con "/"), guardadas en <audio>.md junto al audio
   - Propiedades: titulo, icono, etiquetas, estado, participantes y proyecto/workstream (cabecera del .md)
   - Vista "Reuniones" (tabla con filtros/orden/agrupar) y calendario
   - Busqueda rapida Ctrl+P, favoritos, visto recientemente
   - Importar arrastrando archivos, eliminar a la papelera, aviso de grabacion, reunion detectada */

/* ---------------------------------------------------------------- titulos, iconos */
const ESTADOS = [['', 'Sin estado'], ['Por revisar', 'Por revisar'], ['Revisada', 'Revisada'], ['Enviada', 'Enviada'], ['Archivada', 'Archivada']];
const ESTADO_CLS = { 'Por revisar': 'pending', 'Revisada': 'ok', 'Enviada': 'cloud', 'Archivada': '' };
const EMOJIS = '📝 📊 💼 🤝 📞 🎯 🧭 🗂 📁 📌 ⭐ 🔥 💡 ✅ ⚠️ 🔒 🛡️ 🌐 🖥️ ☁️ 🧾 💶 📈 📉 🗓️ ⏱️ 👥 👤 🏢 🏬 🚀 🧩 🔧 ⚙️ 🧪 📣 🗣️ 🎙️ 🎧 📚 🇪🇸 🇵🇹 🇬🇧'.split(' ');
const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const CONSENT_TEXT = 'Aviso: voy a grabar y transcribir esta reunión para tomar notas. Si alguien prefiere que no, decídmelo.\n'
  + 'Heads-up: I\'m recording and transcribing this meeting for note-taking — let me know if you\'d rather I didn\'t.\n'
  + 'Aviso: vou gravar e transcrever esta reunião para tomar notas. Se alguém preferir que não, digam-me.';

const titleOf = a => nfc((a && a.props && a.props.titulo) || '');
// el icono viene del .md (que puede estar en una carpeta compartida) y se pinta como HTML: siempre escapado (P0-2)
const iconOf = a => esc((a && a.props && a.props.icono) || '');
const pageTitle = a => titleOf(a) || longTitle(a);
const fIcon = f => (S.config && S.config.folder_icons && S.config.folder_icons[f.path]) || (f.linked ? '🔗' : '');
const aDay = a => { const d = aDate(a) || new Date(a.mtime * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };

/* ---------------------------------------------------------------- recientes y favoritos */
function pushRecent(kind, path) {
  if (!['a', 'f', 'd'].includes(kind) || !path) return;
  const r = JSON.parse(localStorage.getItem('recent') || '[]').filter(x => !(x.k === kind && x.p === path));
  r.unshift({ k: kind, p: path, t: Date.now() });
  localStorage.setItem('recent', JSON.stringify(r.slice(0, 15)));
}
function itemOf(k, p) { return k === 'a' ? S.audios.get(p) : k === 'f' ? S.folders.get(p) : S.docs.get(p); }
function recents(n = 8) {
  return JSON.parse(localStorage.getItem('recent') || '[]').map(x => ({ ...x, item: itemOf(x.k, x.p) })).filter(x => x.item).slice(0, n);
}
function itemLabel(k, it) {
  if (k === 'a') return { icon: iconOf(it) || I.mic, text: titleOf(it) || shortTitle(it), sub: titleOf(it) ? shortTitle(it) : nfc(it.label || '') };
  if (k === 'f') return { icon: fIcon(it) || I.folder, text: nfc(it.name), sub: '' };
  return { icon: I.doc, text: nfc(it.name), sub: '' };
}
const isFav = (k, p) => (S.config.favorites || []).some(f => f.kind === k && f.path === p);
async function toggleFav(k, p) {
  try {
    const on = !isFav(k, p);
    const r = await api('/api/favorite', { kind: k, path: p, on });
    S.config.favorites = r.favorites;
    renderTree();
    $$(`[data-act="fav"][data-path="${CSS.escape(p)}"]`).forEach(b => { b.classList.toggle('on', on); b.innerHTML = on ? I.starOn : I.star; });
    toast(on ? '★ Añadido a favoritos' : 'Quitado de favoritos');
  } catch (err) { toast(esc(err.message), 'err'); }
}
function favHtml() {
  const favs = (S.config.favorites || []).map(f => ({ ...f, item: itemOf(f.kind, f.path) })).filter(f => f.item);
  if (!favs.length) return '';
  return '<div class="tsec">' + I.star + 'Favoritos</div>' + favs.map(f => {
    const L = itemLabel(f.kind, f.item);
    const sel = S.route.kind === f.kind && S.route.path === f.path;
    return `<div class="trow fav${sel ? ' sel' : ''}" data-${f.kind === 'a' ? 'audio' : f.kind === 'f' ? 'folder' : 'doc'}="${esc(f.path)}" data-favrow="1" style="padding-left:14px">`
      + `<span class="ticon">${L.icon}</span><span class="tl">${esc(L.text)}</span></div>`;
  }).join('');
}

function recentCards() {
  const r = recents(6);
  if (!r.length) return '';
  return `<h2>Visto recientemente</h2><div class="rgrid">${r.map(x => {
    const L = itemLabel(x.k, x.item);
    return `<a class="card rcard" href="${href(x.k, x.p)}"><div class="ri">${L.icon}</div><div class="rt">${esc(L.text)}</div><div class="rs">${esc(L.sub || '')}</div></a>`;
  }).join('')}</div>`;
}

/* primer arranque (p. ej. en el PC de otra persona): conectar la nube y grabar la primera reunion */
function welcomeCard(all) {
  const key = S.config.procesado === 'api' ? S.config.cloud?.has_key : true;  // sin API no hay nada que conectar
  const elegir = S.config.procesado === 'local' && !localStorage.getItem('procElegido');  // nadie ha elegido aun
  if (key && all.length && !elegir) return '';
  // con el asistente (configurar.js): una frase y un botón; sin él, la lista de antes
  if (typeof abrirConfigurar === 'function' && (elegir || !key)) {
    return `<div class="card welcome"><div class="wi">${I.spark}</div><div>
      <b>${elegir ? `Bienvenido a ${APP_NAME}` : 'Falta la clave de la API'}</b>
      <div style="margin:4px 0 12px">${elegir ? 'Antes de tu primera reunión, elige dónde se transcriben y se resumen. Son tres pasos y al final lo pruebas todo.'
    : `Elegiste «Con una API externa», pero aún no hay clave: sin ella no se transcribe nada. Son tres pasos y al final lo pruebas todo.`}</div>
      <button class="btn sm primary" data-act="cfg-abrir">${I.gear}Configurar ${esc(APP_NAME)}</button>
      ${all.length ? '' : '<div class="help" style="margin-top:10px">También puedes arrastrar aquí audios que ya tengas.</div>'}</div></div>`;
  }
  return `<div class="card welcome"><div class="wi">${I.spark}</div><div>
    <b>${all.length ? 'Conecta la nube para ir mucho más rápido' : `Bienvenido a ${APP_NAME}`}</b>
    <ol>${key ? '' : '<li><b>Conecta Groq</b> (o la API de tu empresa) en ⚙ Ajustes → Procesado: una reunión de 1 h se transcribe en un par de minutos y se resume sola.</li>'}
    ${elegir ? '<li><b>Elige dónde se procesan tus reuniones</b> en ⚙ Ajustes: solo en este PC (como ahora), con tu cuenta de Claude o con una API externa. Las dos últimas resumen con IA y avisan de lo que sale del PC.</li>' : ''}
    ${all.length ? '' : '<li><b>Graba</b> con el botón <b>● Grabar</b> de arriba o con <b>Ctrl+Alt+R</b> desde cualquier programa: graba tu micro y el sonido del ordenador (Teams, Zoom…). También puedes arrastrar aquí audios que ya tengas, o guardar en esta carpeta las grabaciones de OBS.</li>'}</ol>
    ${key && !elegir ? '' : '<button class="btn sm primary" data-act="settings">' + I.gear + 'Abrir Ajustes</button>'}</div></div>`;
}

/* acciones nuevas (los botones llevan data-act y los recoge este manejador) */
main.addEventListener('click', async e => {
  const act = e.target.closest('[data-act]');
  if (!act || !main.contains(act)) return;
  const a = act.dataset.act, p = act.dataset.path;
  if (a === 'pick-icon') { const au = S.audios.get(p); if (au) pickEmoji(act, ic => saveProps(au, { icono: ic }).then(() => renderAudioHead(au, true))); }
  else if (a === 'folder-icon') pickEmoji(act, async ic => { await api('/api/folder-icon', { path: p, icon: ic }); S.config.folder_icons = { ...(S.config.folder_icons || {}), [p]: ic }; if (!ic) delete S.config.folder_icons[p]; renderTree(); refreshView(); });
  else if (a === 'fav') toggleFav(act.dataset.kind, p);
  else if (a === 'tag-del') { const au = curAudio(); if (au) saveProps(au, { etiquetas: ((au.props || {}).etiquetas || []).filter(t => t !== act.dataset.tag) }).then(() => renderAudioHead(au, true)); }
  else if (a === 'delete') deleteItem(p);
  else if (a === 'consent') copy(CONSENT_TEXT, 'Aviso copiado: pégalo en el chat de la reunión');
  else if (a === 'palette') { e.preventDefault(); openPalette(); }
  else if (a === 'seg-to-notes') addSegToNotes();
  else if (a === 'rec-mute') {
    try { const r = await api('/api/rec/mic', { mute: !S.rec.mic_muted }); S.rec.mic_muted = r.mic_muted; toast(r.mic_muted ? '🔇 Tu micro está silenciado (se sigue grabando el ordenador)' : '🎙️ Micro reactivado'); const au = curAudio(); if (au) renderLiveHero(au); }
    catch (err) { toast(esc(err.message), 'err'); }
  }
});

/* ---------------------------------------------------------------- selector de emoji */
function closePop() { $$('.emoji-pop, .slash-pop').forEach(p => p.remove()); }
function pickEmoji(anchor, onPick) {
  closePop();
  const pop = document.createElement('div');
  pop.className = 'emoji-pop';
  pop.innerHTML = EMOJIS.map(e => `<button data-e="${e}">${e}</button>`).join('') + '<button data-e="" class="none">Quitar icono</button>';
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.min(r.left, innerWidth - 300) + 'px';
  pop.style.top = (r.bottom + 6) + 'px';
  pop.addEventListener('mousedown', e => e.stopPropagation());
  pop.addEventListener('click', e => { const b = e.target.closest('[data-e]'); if (b) { onPick(b.dataset.e); closePop(); } });
  setTimeout(() => document.addEventListener('mousedown', closePop, { once: true }), 0);
}

/* ---------------------------------------------------------------- vocabulario por workstream */
/* <proyecto>/Vocabulario/<workstream>.txt. Se elige al grabar (o en la reunion) y Whisper usa ese
   vocabulario en vez del general de Ajustes */
S.vocab = [];
async function loadVocab(language) {
  try {
    const r = await api('/api/vocab' + (language != null ? '?language=' + enc(language) : ''));
    S.vocab = r.items; S.vocabLast = r.last; S.vocabCarpetas = r.carpetas || [];  // carpetas de listas conectadas
    S.vocabError = null;
    return r;
  } catch (err) {
    // se sigue con las de antes (o ninguna) y se apunta: Ajustes no confunde «no se han podido leer» con «no hay»
    S.vocabError = err.message || 'error';
    return { items: S.vocab, last: S.vocabLast || '', carpetas: S.vocabCarpetas || [] };
  }
}
function vocabOf(a) {
  const p = a.props || {};
  if (!p.workstream) return null;
  // primero por el id de la lista (props.vocabulario: lo guarda este PC, no la cabecera del .md; como find_vocab en
  // server.py, solo si la lista se llama como dice la cabecera): con dos proyectos que se llaman igual, los nombres
  // no dicen cuál es
  const byId = p.vocabulario ? S.vocab.find(v => v.id === p.vocabulario) : null;
  if (byId && fold(byId.name) === fold(p.workstream) && fold(byId.project) === fold(p.proyecto || '')) return byId;
  const same = S.vocab.filter(v => fold(v.name) === fold(p.workstream));
  return same.find(v => fold(v.project) === fold(p.proyecto || '')) || (same.length === 1 ? same[0] : null);
}
function vocabOptions(sel, none = 'Ninguno · vocabulario general de Ajustes') {
  const groups = {};
  S.vocab.forEach(v => (groups[v.project] ||= []).push(v));
  return `<option value="">${esc(none)}</option>` + Object.entries(groups).map(([p, vs]) => `<optgroup label="${esc(p)}">${vs.map(v =>
    `<option value="${esc(v.id)}"${v.id === sel ? ' selected' : ''}>${esc(v.name)}${v.desc ? ' · ' + esc(v.desc) : ''}</option>`).join('')}</optgroup>`).join('');
}
/* cuanto ocupa y si le cabe entero a Whisper (lee como mucho 223 tokens, frase de estilo incluida) */
function vocabFit(v) {
  if (!v) return S.config.prompt ? 'Se usa el vocabulario general de ⚙ Ajustes.' : 'Sin vocabulario: Whisper no recibe nombres ni siglas.';
  if (!v.terms) return 'Escribe los términos separados por comas.';
  const used = v.tokens + v.style_tokens;
  return `${v.terms} términos · ${Math.min(used, v.limit)}/${v.limit} tokens` + (v.dropped.length
    ? ` · <span class="warn-txt">⚠ no caben los últimos ${v.dropped.length}: ${esc(v.dropped.join(', '))}</span> <span class="muted">(Whisper no lee más; pon antes los importantes)</span>`
    : ' · <span class="ok-txt">✓ cabe entero</span>');
}
/* crear / editar un workstream (y con el, un proyecto nuevo). after: a donde volver al terminar */
function openVocabEditor(v, after = openSettings, project = '') {
  const isNew = !v, projects = [...new Set(S.vocab.map(x => x.project))];
  openModal(`<h3>${I.book}${isNew ? (project ? `Nuevo workstream en ${esc(project)}` : 'Nuevo proyecto o workstream') : `${esc(v.project)} · ${esc(v.name)}`}</h3>
    <div class="grid2">
      <div><label>Proyecto</label><input class="input" id="veProj" list="veProjs" value="${esc(v ? v.project : project)}" placeholder="p. ej. Proyecto Alfa, Comunidad de vecinos…" ${isNew ? 'autofocus' : ''}>
        <datalist id="veProjs">${projects.map(p => `<option value="${esc(p)}">`).join('')}</datalist></div>
      <div><label>Workstream</label><input class="input" id="veName" value="${esc(v ? v.name : (project ? '' : 'General'))}" placeholder="p. ej. Legal, Finanzas, Tecnología…"></div>
    </div>
    <label>Descripción <span class="muted" style="font-weight:400">· opcional, se ve al elegirlo</span></label>
    <input class="input" id="veDesc" value="${esc(v ? v.desc || '' : '')}" placeholder="p. ej. Reunión semanal del equipo">
    <label>Términos <span class="muted" style="font-weight:400">· separados por comas, los más importantes primero</span></label>
    <textarea id="veTerms" rows="9" placeholder="Nombre del proyecto, siglas, sistemas, nombres y apellidos de los participantes…">${esc(v ? v.text || '' : '')}</textarea>
    <div class="help" id="veFit">&nbsp;</div>
    <div class="btns">${v ? '<button class="btn danger" data-m="del" style="margin-right:auto">Borrar</button>' : ''}<button class="btn" data-m="no">Cancelar</button><button class="btn primary" data-m="ok">Guardar</button></div>`, d => {
    let t = null;
    const check = async () => {
      try { $('#veFit', d).innerHTML = vocabFit(await api('/api/vocab/check', { terms: $('#veTerms', d).value, language: S.config.language ?? 'es-en' })); } catch { }
    };
    $('#veTerms', d).addEventListener('input', () => { clearTimeout(t); t = setTimeout(check, 400); });
    check();
    d.addEventListener('click', async e => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      if (b.dataset.m === 'no') return after();
      try {
        if (b.dataset.m === 'del') {
          await api('/api/vocab/delete', { id: v.id });
          toast(`«${esc(v.name)}» a la papelera (se puede recuperar)`);
        } else {
          const r = await api('/api/vocab/save', { id: v?.id, project: $('#veProj', d).value, name: $('#veName', d).value,
            desc: $('#veDesc', d).value, terms: $('#veTerms', d).value });
          toast(`🔤 Guardado · ya se puede elegir al grabar`);
          S.vocabJustSaved = r.id;
        }
        await loadVocab(S.config.language ?? 'es-en');
        await refreshTree(true);
        after();
      } catch (err) { toast(esc(err.message), 'err'); }
    });
  });
}
function wsSelect(a) {
  const v = vocabOf(a), p = a.props || {};
  const lost = p.workstream && !v ? `<option value="__lost" selected>${esc(p.workstream)} (no se encuentra el archivo)</option>` : '';
  return `<select class="pstate pws" data-prop="workstream" title="Vocabulario que recibe Whisper al transcribir esta reunión">${vocabOptions(v ? v.id : (lost ? '__lost' : ''), 'Ninguno')}${lost}</select>`;
}
/* el tema elegido en la página va por el id de su lista: por nombres, con dos proyectos que se llaman igual, el
   servidor tomaría la del proyecto que contiene la reunión y no la elegida ('' = sin tema) */
function wsPatch(v) { return { vocabulario: v ? v.id : '' }; }
main.addEventListener('change', e => {
  const a = curAudio();
  if (!a || e.target.dataset.prop !== 'workstream' || e.target.value === '__lost') return;
  const v = S.vocab.find(x => x.id === e.target.value);
  saveProps(a, wsPatch(v)).then(() => toast(!v ? 'Sin tema: se usará el vocabulario general'
    : liveJob(a) ? `🗂 ${esc(v.name)}: el directo usa este vocabulario desde el siguiente trozo`
    : (a.txt || a.srt) ? `🗂 ${esc(v.name)} guardado · se aplicará si la vuelves a transcribir` : `🗂 ${esc(v.name)}: se usará al transcribirla`));
});

/* ---------------------------------------------------------------- propiedades de la reunion */
async function saveProps(a, patch) {
  try {
    const r = await api('/api/notes', { path: a.path, props: patch });
    a.props = r.props; a.notes_mtime = r.mtime;
    renderTree();
    const t = $('#pTitleTxt');
    if (S.playing === a.path) $('#pTitle').textContent = fullTitle(a);
    if (t && document.activeElement !== t) t.textContent = titleOf(a);
  } catch (err) { toast(esc(err.message), 'err'); }
}
function titleRowHtml(a) {
  const fav = isFav('a', a.path);
  return `<div class="ptitle-row">
    <button class="picon" data-act="pick-icon" data-path="${esc(a.path)}" title="Cambiar icono">${iconOf(a) || '<span class="picon-add">' + I.face + '</span>'}</button>
    <h1 class="ptitle" id="pTitleTxt" contenteditable="plaintext-only" spellcheck="false" data-ph="${esc(longTitle(a))}" title="Haz clic para poner un título">${esc(titleOf(a))}</h1>
    <button class="pfav${fav ? ' on' : ''}" data-act="fav" data-kind="a" data-path="${esc(a.path)}" title="Favorito">${fav ? I.starOn : I.star}</button>
  </div>`;
}
function propsHtml(a) {
  const p = a.props || {}, st = p.estado || '';
  const tags = (p.etiquetas || []).map(t => `<span class="tag-chip">${esc(t)}<button data-act="tag-del" data-tag="${esc(t)}" title="Quitar">×</button></span>`).join('');
  return `<div class="props">
    <div class="prow"><span class="pk">${I.calendar}Fecha</span><span class="pv">${esc(longTitle(a))}</span></div>
    <div class="prow"><span class="pk">${I.clock}Duración</span><span class="pv">${fmtDur(estDuration(a))} <span class="muted">· ${fmtSize(a.size)} · ${esc(a.ext.slice(1).toUpperCase())}</span></span></div>
    <div class="prow"><span class="pk">${I.folder}Carpeta</span><span class="pv"><a href="${a.folder ? href('f', a.folder) : '#/'}">${esc(a.folder ? pathLabel(a.folder) : 'Sin clasificar')}</a>
      <button class="btn ghost sm" data-act="move" data-path="${esc(a.path)}">cambiar</button></span></div>
    <div class="prow"><span class="pk">${I.topic}Tema</span><span class="pv">${wsSelect(a)}</span></div>
    ${typeof perfilPropsHtml === 'function' ? perfilPropsHtml(a) : ''}
    <div class="prow"><span class="pk">${I.state}Estado</span><span class="pv"><select class="pstate ${ESTADO_CLS[st] || ''}" data-prop="estado">${ESTADOS.map(([v, l]) => `<option value="${esc(v)}"${v === st ? ' selected' : ''}>${l}</option>`).join('')}</select></span></div>
    <div class="prow"><span class="pk">${I.tag}Etiquetas</span><span class="pv tags">${tags}<input class="tag-add" data-tag-add list="tagList" placeholder="+ añadir" autocomplete="off" spellcheck="false"></span></div>
    <div class="prow"><span class="pk">${I.users}Participantes</span><span class="pv"><input class="pinput" data-prop="participantes" value="${esc(p.participantes || '')}" placeholder="Vacío" autocomplete="off"></span></div>
    ${window.reunionParticipantesHtml?.(a) || ''}
    <div class="prow"><span class="pk">${I.chat}Transcripción</span><span class="pv">${statusPill(a)}${a.cloud ? ` <span class="pill cloud">${I.cloud}Solo en OneDrive</span>` : ''}</span></div>
  </div><datalist id="tagList"></datalist>`;
}
async function fillTagList() {
  try { const r = await api('/api/tags'); const dl = $('#tagList'); if (dl) dl.innerHTML = r.tags.map(t => `<option value="${esc(t)}">`).join(''); } catch { }
}
const curAudio = () => S.route.kind === 'a' ? S.audios.get(S.route.path) : null;
main.addEventListener('change', e => {
  const a = curAudio();
  if (a && e.target.dataset.prop === 'estado') { e.target.className = 'pstate ' + (ESTADO_CLS[e.target.value] || ''); saveProps(a, { estado: e.target.value }); }
  if (a && e.target.dataset.prop === 'idioma') {  // perfil.js: el directo lo relee en el siguiente trozo
    saveProps(a, { idioma: e.target.value }).then(() => { if (typeof perfilSavedText === 'function') toast(perfilSavedText('idioma', !!liveJob(a), !!(a.txt || a.srt))); });
  }
});
main.addEventListener('keydown', e => {
  const a = curAudio();
  if (!a) return;
  if (e.target.id === 'pTitleTxt' && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  if (e.target.dataset && (e.target.dataset.prop === 'participantes' || e.target.dataset.prop === 'contexto') && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  if (e.target.dataset && e.target.dataset.tagAdd !== undefined && e.key === 'Enter') {
    e.preventDefault();
    const t = ' '.concat(e.target.value).trim();
    if (!t) return;
    const tags = [...new Set([...((a.props || {}).etiquetas || []), t])];
    e.target.value = '';
    saveProps(a, { etiquetas: tags }).then(() => { renderAudioHead(a, true); $('[data-tag-add]')?.focus(); fillTagList(); });
  }
});
/* titulo, participantes y contexto: se guardan al dejar de escribir (y al salir del campo) */
let propTimer = null;
main.addEventListener('input', e => {
  if (e.target.id !== 'pTitleTxt' && !(e.target.dataset && (e.target.dataset.prop === 'participantes' || e.target.dataset.prop === 'contexto'))) return;
  clearTimeout(propTimer);
  propTimer = setTimeout(() => e.target.dispatchEvent(new FocusEvent('focusout', { bubbles: true })), 900);
});
main.addEventListener('focusout', e => {
  const a = curAudio();
  if (!a) return;
  if (e.target.id === 'pTitleTxt') {
    const t = e.target.innerText.replace(/\s+/g, ' ').trim();
    if (t !== titleOf(a)) saveProps(a, { titulo: t });
  } else if (e.target.dataset && e.target.dataset.prop === 'participantes') {
    const v = e.target.value.trim();
    if (v !== ((a.props || {}).participantes || '')) saveProps(a, { participantes: v }).then(() => window.reunionParticipantesGuardados?.(a));
  } else if (e.target.dataset && e.target.dataset.prop === 'contexto') {  // perfil.js: Whisper y el resumen lo usan
    const v = e.target.value.trim();
    if (v !== ((a.props || {}).contexto || '')) saveProps(a, { contexto: v }).then(() => { if (typeof perfilSavedText === 'function') toast(perfilSavedText('contexto', !!liveJob(a), !!(a.txt || a.srt))); });
  }
});

/* ---------------------------------------------------------------- pestanas Resumen / Transcripcion / Notas */
const defaultTab = a => S.tab || (isLive(a) ? 'tx' : a.has_summary ? 'sum' : (a.txt || a.srt) ? 'tx' : 'notes');
function tabsHtml(a) {
  const tab = defaultTab(a), sj = sumJobFor(a.path);
  return `<div class="ptabs">
    <button data-tab="sum" class="${tab === 'sum' ? 'on' : ''}">${I.spark}Resumen${sj ? ' <i class="tab-dot busy"></i>' : a.has_summary ? ' <i class="tab-dot"></i>' : ''}</button>
    <button data-tab="tx" class="${tab === 'tx' ? 'on' : ''}">${I.chat}Transcripción</button>
    <button data-tab="notes" class="${tab === 'notes' ? 'on' : ''}">${I.notes}Notas${a.has_notes ? ' <i class="tab-dot"></i>' : ''}</button>
    <span class="sp"></span><span class="muted ptab-hint">${tab === 'notes' ? 'Se guardan solas en un .md junto al audio' : tab === 'sum' ? `Hecho con IA en ${provName()}: no carga tu portátil` : ''}</span>
  </div>`;
}
function applyTab(a) {
  const tab = defaultTab(a);
  $('#aTabs') && ($('#aTabs').innerHTML = tabsHtml(a));
  $('#aTx')?.classList.toggle('hidden', tab !== 'tx');
  $('#aNotes')?.classList.toggle('hidden', tab !== 'notes');
  $('#aSum')?.classList.toggle('hidden', tab !== 'sum');
  if (tab === 'notes' && (!S.notes || S.notes.path !== a.path)) loadNotes(a);
  if (tab === 'sum' && (!S.sum || S.sum.path !== a.path)) loadSummary(a);
}

/* ---------------------------------------------------------------- resumen con IA */
const sumJobFor = path => S.jobs.find(j => j.path === path && j.kind === 'summary' && (j.status === 'queued' || j.status === 'running'));
function linkStamps(html) {  // [12:34] -> enlace que salta a ese minuto del audio
  return html.replace(/\[((?:\d+:)?\d{1,2}:\d{2})\]/g, (m, t) => `<a class="tsl" data-ts="${t}" title="Escuchar desde aquí">${m}</a>`);
}
const stampSecs = t => t.split(':').reduce((acc, x) => acc * 60 + +x, 0);
async function loadSummary(a) {
  const box = $('#aSum');
  if (!box) return;
  try {
    const r = await api('/api/summary?path=' + enc(a.path));
    if (!curAudio() || curAudio().path !== a.path) return;
    S.sum = { path: a.path, ...r };
    renderSummary(a);
  } catch (err) { box.innerHTML = `<div class="muted">${esc(err.message)}</div>`; }
}
function renderSummary(a) {
  const box = $('#aSum'), s = S.sum;
  if (!box || !s || s.path !== a.path) return;
  const j = sumJobFor(a.path), has = a.txt || a.srt;
  const last = S.jobs.filter(x => x.path === a.path && x.kind === 'summary').pop();
  const progress = j ? `<div class="card jobline sumline"><div><div class="st">${I.spark}${esc(j.status === 'queued' ? 'En cola para resumir' : j.stage)}</div>
      <div class="sub">Se hace en ${provName()}${S.config?.procesado === 'agente' ? ' en una sola llamada' : ' por partes, al ritmo que permite tu plan'} · ${Math.round((j.progress || 0) * 100)} %</div></div>
      <button class="btn sm danger" data-act="cancel-job" data-id="${j.id}">Cancelar</button><div class="pbar${j.progress < 0.02 ? ' indet' : ''}"><i style="width:${(j.progress || 0) * 100}%"></i></div></div>` : '';
  const err = !j && last && last.status === 'error' ? `<div class="notice">No se pudo resumir: ${esc(last.error)}</div>` : '';
  // con Azure OpenAI solo se configura el deployment de Whisper: el resumen aún no funciona ahí (no es que falte configurar)
  const azure = S.config?.procesado === 'api' && S.config?.cloud?.provider === 'azure' && !s.ready;
  const azureNote = '<div class="muted">El resumen con IA aún no funciona con Azure OpenAI: con Azure solo se transcribe. Para resumir, elige Groq, OpenAI u otra API compatible con OpenAI en Ajustes.</div>';
  if (!s.exists) {
    box.innerHTML = progress + err + `<div class="card sum-empty">
      <div class="se-i">${I.spark}</div>
      <div><b>Resumen con IA de la reunión</b><div class="muted">Resumen, puntos clave, decisiones, próximos pasos y participantes, con el minuto de cada punto. Usa también tus notas.</div></div>
      ${!has ? '<div class="muted">Primero hay que transcribir la reunión.</div>'
        : azure ? azureNote
          : !s.ready ? `<button class="btn" data-act="settings">${S.config?.procesado === 'local' ? 'Elegir dónde se procesa (Ajustes)' : S.config?.procesado === 'agente' ? `Comprobar ${esc(S.config.agente?.label || 'Claude Code')} en Ajustes` : 'Configurar la nube en Ajustes'}</button>`
            : j ? '' : `<button class="btn primary" data-act="summarize" data-path="${esc(a.path)}">${I.spark}Resumir esta reunión</button>`}
    </div>`;
    return;
  }
  const m = s.meta || {};
  box.innerHTML = progress + err + `<div class="sum-tools">
      <span class="muted">${I.spark}Generado con IA${m.modelo ? ` (${esc(m.modelo)})` : ''}${m.generado ? ` · ${esc(m.generado)}` : ''} · revísalo antes de compartirlo</span>
      <span class="sp"></span>
      <button class="btn sm" data-act="sum-copy">${I.copy}Copiar</button>
      <button class="btn sm" data-act="sum-to-notes">${I.plus}A mis notas</button>
      ${j || azure ? '' : `<button class="btn sm" data-act="summarize" data-path="${esc(a.path)}" title="Vuelve a hacerlo (por ejemplo, tras corregir la transcripción o añadir notas)">${I.refresh}Regenerar</button>`}
    </div>
    ${azure ? azureNote : ''}
    <div class="card docview sumview">${linkStamps(md(s.text).replace(/<li>\[ \]\s?/g, '<li class="todo">☐ ').replace(/<li>\[x\]\s?/gi, '<li class="todo done">☑ '))}</div>`;
}
main.addEventListener('click', async e => {
  const ts = e.target.closest('#aSum .tsl');
  if (ts) { const a = curAudio(); if (a) { const t = stampSecs(ts.dataset.ts); loadAudio(a, t, true); toast(`▶ ${fmtClock(t)}`); } return; }
  const act = e.target.closest('[data-act]');
  if (!act || !main.contains(act)) return;
  const a = curAudio();
  if (act.dataset.act === 'summarize' && a) {
    try {
      const okKey = 'sumOk:' + (S.config?.procesado || 'api');  // el permiso es por destino: el de Groq no vale para Anthropic
      if (!localStorage.getItem(okKey) && !await ask(`Resumir con IA en ${provName()}`,
        `El texto de la transcripción, tus notas, el contexto y los participantes confirmados se envían a <b>${esc(provName())}</b> para hacer el resumen (el audio no; los participantes que sugirió Outlook o Teams sin confirmar, tampoco). Si es una reunión de trabajo, asegúrate de que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay.`, 'Entendido, resumir')) return;
      localStorage.setItem(okKey, '1');
      await api('/api/summarize', { path: a.path });
      toast(`✨ Resumiendo en ${esc(provName())}… tarda unos minutos en reuniones largas`);
      pollJobs(true);
      setTimeout(() => renderSummary(a), 600);
    } catch (err) { toast(esc(err.message), 'err'); }
  } else if (act.dataset.act === 'sum-copy' && S.sum) copy(S.sum.text, 'Resumen copiado');
  else if (act.dataset.act === 'sum-to-notes' && a && S.sum) {
    const r = await api('/api/notes?path=' + enc(a.path));
    const body = (r.body ? r.body.trim() + '\n\n---\n\n' : '') + '# ✨ Resumen\n\n' + S.sum.text.replace(/^## /gm, '## ');
    await api('/api/notes', { path: a.path, body });
    S.notes = null; a.has_notes = true;
    toast('Resumen añadido a 📝 Notas (ahí lo puedes editar)');
  } else if (act.dataset.act === 'settings') openSettings({ sec: 'procesado' });  // los avisos de Inicio son de Procesado
});
/* progreso del resumen y recarga al terminar (lo llama el sondeo de trabajos de app.js) */
function onSummaryJobs(prev) {
  const a = curAudio();
  for (const j of S.jobs) {
    if (j.kind !== 'summary') continue;
    const p = prev.get(j.id);
    if (p && p.status !== j.status && j.status === 'done') {
      const au = S.audios.get(j.path);
      toast(`✨ Resumen listo: ${esc(au ? fullTitle(au) : j.name)}`, 'ok', () => { S.tab = 'sum'; go('a', j.path); });
      if (au) au.has_summary = true;
      if (a && a.path === j.path) { S.sum = null; if (!S.tab) S.tab = 'sum'; applyTab(a); }
    }
  }
  if (a && $('#aSum') && !$('#aSum').classList.contains('hidden') && S.sum && S.sum.path === a.path) renderSummary(a);
  if (a && $('#aTabs') && !$('#aTabs').contains(document.activeElement)) $('#aTabs').innerHTML = tabsHtml(a);
}
main.addEventListener('click', e => {
  const t = e.target.closest('[data-tab]');
  if (!t) return;
  const a = curAudio();
  if (!a) return;
  S.tab = t.dataset.tab;
  applyTab(a);
  if (S.tab === 'notes') setTimeout(() => $('#ned .nt')?.focus(), 50);
});

/* ---------------------------------------------------------------- editor de notas (bloques) */
const BLOCKS = [['p', 'Texto', 'T'], ['h1', 'Título 1', 'H1'], ['h2', 'Título 2', 'H2'], ['h3', 'Título 3', 'H3'],
  ['ul', 'Lista', '•'], ['ol', 'Lista numerada', '1.'], ['todo', 'Tarea', '☐'], ['quote', 'Cita', '❝'],
  ['hr', 'Separador', '—'], ['time', 'Marca de tiempo (minuto actual)', '◔']];
const LISTS = ['ul', 'ol', 'todo'];

function mdToBlocks(md) {
  const out = [], lines = (md || '').replace(/\r/g, '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    let l = lines[i];
    if (!l.trim()) continue;
    while (/ {2}$/.test(l) && i + 1 < lines.length && lines[i + 1].trim()) l = l.replace(/ {2}$/, '\n') + lines[++i];
    let m;
    if ((m = l.match(/^(#{1,3})\s+([\s\S]*)$/))) out.push({ t: 'h' + m[1].length, x: m[2] });
    else if ((m = l.match(/^\s*[-*]\s+\[( |x|X)\]\s?([\s\S]*)$/))) out.push({ t: 'todo', x: m[2], c: m[1] !== ' ' });
    else if ((m = l.match(/^\s*[-*•]\s+([\s\S]*)$/))) out.push({ t: 'ul', x: m[1] });
    else if ((m = l.match(/^\s*\d+[.)]\s+([\s\S]*)$/))) out.push({ t: 'ol', x: m[1] });
    else if ((m = l.match(/^>\s?([\s\S]*)$/))) out.push({ t: 'quote', x: m[1] });
    else if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) out.push({ t: 'hr', x: '' });
    else out.push({ t: 'p', x: l });
  }
  return out.length ? out : [{ t: 'p', x: '' }];
}
function blocksToMd(bs) {
  const lines = [];
  let prev = null, n = 0;
  for (const b of bs) {
    const x = (b.x || '').replace(/\n/g, '  \n');
    if (b.t !== 'hr' && !x.trim()) { prev = null; continue; }
    if (prev && !(LISTS.includes(b.t) && prev === b.t)) lines.push('');
    n = b.t === 'ol' ? (prev === 'ol' ? n + 1 : 1) : 0;
    lines.push({ h1: '# ', h2: '## ', h3: '### ', ul: '- ', quote: '> ' }[b.t] !== undefined ? { h1: '# ', h2: '## ', h3: '### ', ul: '- ', quote: '> ' }[b.t] + x
      : b.t === 'ol' ? `${n}. ${x}` : b.t === 'todo' ? `- [${b.c ? 'x' : ' '}] ${x}` : b.t === 'hr' ? '---' : x);
    prev = b.t;
  }
  return lines.join('\n').trim();
}

async function loadNotes(a) {
  const box = $('#aNotes');
  if (!box) return;
  box.innerHTML = '<div class="muted" style="padding:18px 2px">Cargando notas…</div>';
  try {
    const r = await api('/api/notes?path=' + enc(a.path));
    if (!curAudio() || curAudio().path !== a.path) return;
    const blocks = mdToBlocks(r.body);
    S.notes = { path: a.path, blocks, saved: blocksToMd(blocks), timer: null };
    renderNotes();
  } catch (err) { box.innerHTML = `<div class="muted">${esc(err.message)}</div>`; }
}
const blockPh = b => ({ h1: 'Título 1', h2: 'Título 2', h3: 'Título 3', ul: 'Lista', ol: 'Lista', todo: 'Tarea', quote: 'Cita' }[b.t] || 'Escribe, o pulsa «/» para títulos, listas, tareas…');
function blockHtml(b, i, num) {
  if (b.t === 'hr') return `<div class="nb nb-hr" data-i="${i}" tabindex="0"><hr></div>`;
  const pre = b.t === 'ul' ? '<span class="nb-pre">•</span>' : b.t === 'ol' ? `<span class="nb-pre">${num}.</span>`
    : b.t === 'todo' ? `<input type="checkbox" class="nb-chk"${b.c ? ' checked' : ''}>` : '';
  return `<div class="nb nb-${b.t}${b.t === 'todo' && b.c ? ' done' : ''}" data-i="${i}">${pre}`
    + `<div class="nt" contenteditable="plaintext-only" spellcheck="true" data-ph="${esc(blockPh(b))}">${esc(b.x)}</div></div>`;
}
function renderNotes() {
  const box = $('#aNotes'), n = S.notes;
  if (!box || !n) return;
  let num = 0;
  const only = n.blocks.length === 1 && !n.blocks[0].x;
  box.innerHTML = `<div class="ned${only ? ' empty-doc' : ''}" id="ned">${n.blocks.map((b, i) => blockHtml(b, i, b.t === 'ol' ? ++num : (num = 0))).join('')}</div>
    <div class="ned-foot"><span id="notesState" class="muted">${n.saved === blocksToMd(n.blocks) ? 'Guardado ✓' : ''}</span>
    <span class="muted">«/» comandos · «# » título · «- » lista · «[] » tarea · «/marca» o Ctrl+Shift+M: minuto actual · clic en [12:34] salta al audio</span></div>`;
}
function focusBlock(i, off) {
  const bs = S.notes.blocks;
  i = Math.max(0, Math.min(bs.length - 1, i));
  const el = $(`#ned .nb[data-i="${i}"] .nt`) || $(`#ned .nb[data-i="${i}"]`);
  if (!el) return;
  if (!el.classList.contains('nt')) { el.focus(); return; }
  setCaret(el, off === 'end' ? bs[i].x.length : off);
}
function rerenderFocus(i, off) { renderNotes(); focusBlock(i, off); scheduleNotesSave(); }
function caretOffset(el) {
  const s = getSelection();
  if (!s.rangeCount || !el.contains(s.anchorNode)) return 0;
  const r = s.getRangeAt(0), pre = r.cloneRange();
  pre.selectNodeContents(el);
  pre.setEnd(r.startContainer, r.startOffset);
  return pre.toString().length;
}
function setCaret(el, off) {
  el.focus();
  const s = getSelection(), r = document.createRange(), w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let t, pos = off, done = false;
  while ((t = w.nextNode())) { if (pos <= t.length) { r.setStart(t, pos); done = true; break; } pos -= t.length; }
  if (!done) { r.selectNodeContents(el); r.collapse(false); } else r.collapse(true);
  s.removeAllRanges(); s.addRange(r);
}
function setNotesState(t) { const el = $('#notesState'); if (el) el.textContent = t; }
function scheduleNotesSave() {
  if (!S.notes) return;
  clearTimeout(S.notes.timer);
  setNotesState('Sin guardar…');
  S.notes.timer = setTimeout(saveNotes, 700);
}
async function saveNotes() {
  const n = S.notes;
  if (!n) return;
  clearTimeout(n.timer); n.timer = null;
  const body = blocksToMd(n.blocks);
  if (body === n.saved) { setNotesState('Guardado ✓'); return; }
  setNotesState('Guardando…');
  try {
    const r = await api('/api/notes', { path: n.path, body });
    n.saved = body;
    const a = S.audios.get(n.path);
    if (a) { const had = a.has_notes; a.has_notes = !!body; a.notes_mtime = r.mtime; if (had !== a.has_notes && curAudio() === a) $('#aTabs').innerHTML = tabsHtml(a); }
    setNotesState(blocksToMd(n.blocks) === body ? 'Guardado ✓' : 'Sin guardar…');
  } catch (err) { setNotesState('No se han podido guardar'); toast(esc(err.message), 'err'); }
}
/* al cambiar de pagina o cerrar: se guarda sin esperar */
function flushNotes() {
  const n = S.notes;
  if (!n || !n.timer) return;
  clearTimeout(n.timer); n.timer = null;
  const body = blocksToMd(n.blocks);
  if (body !== n.saved) {
    fetch('/api/notes', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-Audios': '1' }, body: JSON.stringify({ path: n.path, body }) });
    n.saved = body;
  }
}
addEventListener('beforeunload', flushNotes);
const timeNow = path => S.playing === path ? audio.currentTime : (S.audios.get(path) && isLive(S.audios.get(path)) ? clockNow(path) : 0);
function insertTime(el) {
  const i = +el.closest('.nb').dataset.i, b = S.notes.blocks[i], off = caretOffset(el);
  const stamp = `[${fmtClock(timeNow(S.notes.path))}] `;
  b.x = b.x.slice(0, off) + stamp + b.x.slice(off);
  rerenderFocus(i, off + stamp.length);
}

/* menu "/" */
let slash = null;
function closeSlash() { slash = null; $$('.slash-pop').forEach(p => p.remove()); }
function openSlash(blk, q) {
  const items = BLOCKS.filter(([t, l]) => fold(l).includes(fold(q)) || t.startsWith(q.toLowerCase()) || (t === 'time' && 'marca'.startsWith(fold(q))));
  if (!items.length) return closeSlash();
  slash = { i: +blk.dataset.i, items, sel: Math.min(slash ? slash.sel : 0, items.length - 1) };
  $$('.slash-pop').forEach(p => p.remove());
  const pop = document.createElement('div');
  pop.className = 'slash-pop';
  pop.innerHTML = '<div class="sp-h">Bloques</div>' + items.map(([t, l, ic], k) => `<div class="si${k === slash.sel ? ' on' : ''}" data-t="${t}"><span class="sic">${ic}</span>${l}</div>`).join('');
  document.body.append(pop);
  const r = blk.getBoundingClientRect();
  pop.style.left = (r.left + 24) + 'px';
  pop.style.top = Math.min(r.bottom + 4, innerHeight - pop.offsetHeight - 10) + 'px';
  pop.addEventListener('mousedown', e => { e.preventDefault(); const it = e.target.closest('[data-t]'); if (it) applySlash(it.dataset.t); });
}
function applySlash(t) {
  if (!slash) return;
  const i = slash.i, b = S.notes.blocks[i];
  closeSlash();
  b.x = b.x.replace(/^\/\S*/, '');
  if (t === 'time') { const stamp = `[${fmtClock(timeNow(S.notes.path))}] `; b.x = stamp + b.x; rerenderFocus(i, stamp.length); }
  else if (t === 'hr') { b.t = 'hr'; b.x = ''; S.notes.blocks.splice(i + 1, 0, { t: 'p', x: '' }); rerenderFocus(i + 1, 0); }
  else { b.t = t; b.c = false; rerenderFocus(i, 0); }
}

const SHORTCUTS = [[/^#\s$/, 'h1'], [/^##\s$/, 'h2'], [/^###\s$/, 'h3'], [/^[-*]\s$/, 'ul'], [/^1[.)]\s$/, 'ol'], [/^\[\s?\]\s$/, 'todo'], [/^>\s$/, 'quote']];
main.addEventListener('input', e => {
  const el = e.target.closest && e.target.closest('#ned .nt');
  if (!el || !S.notes) return;
  const blk = el.closest('.nb'), i = +blk.dataset.i, b = S.notes.blocks[i];
  let x = el.innerText.replace(/ /g, ' ');
  if (x === '\n') { x = ''; el.innerHTML = ''; }
  x = x.replace(/\n$/, '');
  if (b.t === 'p') {
    for (const [re, t] of SHORTCUTS) if (re.test(x)) { b.t = t; b.x = ''; closeSlash(); return rerenderFocus(i, 0); }
    if (x === '---') { b.t = 'hr'; b.x = ''; S.notes.blocks.splice(i + 1, 0, { t: 'p', x: '' }); return rerenderFocus(i + 1, 0); }
  }
  b.x = x;
  $('#ned')?.classList.remove('empty-doc');
  if (/^\/\S*$/.test(x)) openSlash(blk, x.slice(1)); else closeSlash();
  scheduleNotesSave();
});
main.addEventListener('keydown', e => {
  const el = e.target.closest && e.target.closest('#ned .nt, #ned .nb-hr');
  if (!el || !S.notes) return;
  const blk = el.closest('.nb'), i = +blk.dataset.i, bs = S.notes.blocks, b = bs[i];
  if (slash) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); slash.sel = (slash.sel + (e.key === 'ArrowDown' ? 1 : -1) + slash.items.length) % slash.items.length; openSlash(blk, b.x.slice(1)); return; }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); applySlash(slash.items[slash.sel][0]); return; }
    if (e.key === 'Escape') { e.preventDefault(); closeSlash(); return; }
  }
  if (b.t === 'hr') {  // separador: Supr/Retroceso lo quita, flechas para salir
    if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); bs.splice(i, 1); if (!bs.length) bs.push({ t: 'p', x: '' }); rerenderFocus(Math.max(0, i - 1), 'end'); }
    else if (e.key === 'ArrowDown' || e.key === 'Enter') { e.preventDefault(); if (i === bs.length - 1) bs.push({ t: 'p', x: '' }); rerenderFocus(i + 1, 0); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); focusBlock(i - 1, 'end'); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'm') { e.preventDefault(); insertTime(el); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && b.t === 'todo') { e.preventDefault(); b.c = !b.c; rerenderFocus(i, caretOffset(el)); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveNotes(); return; }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const off = caretOffset(el);
    if ((LISTS.includes(b.t) || b.t === 'quote') && !b.x.trim()) { b.t = 'p'; return rerenderFocus(i, 0); }  // Intro en lista vacia: se sale de la lista
    const after = b.x.slice(off);
    b.x = b.x.slice(0, off);
    bs.splice(i + 1, 0, { t: LISTS.includes(b.t) ? b.t : 'p', x: after, c: false });
    return rerenderFocus(i + 1, 0);
  }
  if (e.key === 'Backspace' && !getSelection().toString() && caretOffset(el) === 0) {
    if (b.t !== 'p') { e.preventDefault(); b.t = 'p'; return rerenderFocus(i, 0); }
    if (i > 0) {
      e.preventDefault();
      const prev = bs[i - 1];
      if (prev.t === 'hr') { bs.splice(i - 1, 1); return rerenderFocus(i - 1, 0); }
      const off = prev.x.length;
      prev.x += b.x; bs.splice(i, 1);
      return rerenderFocus(i - 1, off);
    }
  }
  if (e.key === 'ArrowUp' && caretOffset(el) === 0 && i > 0) { e.preventDefault(); focusBlock(i - 1, 'end'); }
  else if (e.key === 'ArrowDown' && caretOffset(el) >= b.x.length && i < bs.length - 1) { e.preventDefault(); focusBlock(i + 1, 0); }
});
main.addEventListener('click', e => {
  if (!S.notes) return;
  const chk = e.target.closest('#ned .nb-chk');
  if (chk) { const i = +chk.closest('.nb').dataset.i; S.notes.blocks[i].c = chk.checked; chk.closest('.nb').classList.toggle('done', chk.checked); scheduleNotesSave(); return; }
  const el = e.target.closest('#ned .nt');
  if (el) {  // clic sobre una marca [12:34]: salta a ese minuto del audio
    const off = caretOffset(el), re = /\[(?:(\d+):)?(\d{1,2}):(\d{2})\]/g;
    let m;
    while ((m = re.exec(el.innerText))) {
      if (off >= m.index && off <= m.index + m[0].length) {
        const a = S.audios.get(S.notes.path), t = (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]);
        if (a) { loadAudio(a, t, true); toast(`▶ ${fmtClock(t)}`); }
        break;
      }
    }
    return;
  }
  if (e.target.closest('#aNotes') && !e.target.closest('.ned-foot')) {  // clic en el hueco: al final
    const bs = S.notes.blocks;
    if (bs[bs.length - 1].t !== 'p' || bs[bs.length - 1].x) { bs.push({ t: 'p', x: '' }); renderNotes(); }
    focusBlock(bs.length - 1, 'end');
  }
});
/* "+ A mis notas": la frase que suena (o la ultima resaltada) como cita con su minuto */
async function addSegToNotes() {
  const tx = S.tx, a = curAudio();
  if (!tx || !a) return;
  const i = S.curIdx >= 0 ? S.curIdx : -1;
  if (i < 0) return toast('Reproduce el audio o pulsa una frase y luego «A mis notas»');
  if (!S.notes || S.notes.path !== a.path) {
    const r = await api('/api/notes?path=' + enc(a.path));
    S.notes = { path: a.path, blocks: mdToBlocks(r.body), saved: null, timer: null };
  }
  const s = tx.segs[i], bs = S.notes.blocks;
  if (bs.length === 1 && !bs[0].x) bs.pop();
  bs.push({ t: 'quote', x: `[${fmtClock(s.start)}] ${s.text}` });
  scheduleNotesSave();
  toast('Añadido a 📝 Notas');
}

/* ---------------------------------------------------------------- vista "Reuniones" (base de datos) */
S.tbl = Object.assign({ q: '', folder: '*', tag: '*', estado: '*', tx: '*', group: 'month', sort: 'desc' }, JSON.parse(localStorage.getItem('tbl') || '{}'));
function dbTabs(kind) {
  return `<div class="db-tabs"><a href="#/r/" class="${kind === 'r' ? 'on' : ''}">${I.table}Tabla</a><a href="#/c/" class="${kind === 'c' ? 'on' : ''}">${I.calendar}Calendario</a></div>`;
}
function allTags() { const s = new Set(); allAudios().forEach(a => ((a.props || {}).etiquetas || []).forEach(t => s.add(t))); return [...s].sort((x, y) => fold(x).localeCompare(fold(y))); }
function txState(a) { const st = statusOf(a); return st === 'done' ? 'done' : st === 'partial' ? 'partial' : (st === 'rec' || st === 'running' || st === 'queued') ? 'busy' : 'pending'; }
function tableRows() {
  const t = S.tbl, q = fold(t.q.trim());
  let list = allAudios().filter(a => {
    const p = a.props || {};
    if (t.folder !== '*' && !(a.folder === t.folder || a.folder.startsWith(t.folder + '/'))) return false;
    if (t.tag !== '*' && !(p.etiquetas || []).includes(t.tag)) return false;
    if (t.estado !== '*' && (p.estado || '') !== t.estado) return false;
    if (t.tx !== '*' && txState(a) !== t.tx) return false;
    if (q && !fold([titleOf(a), longTitle(a), a.name, a.folder, (p.etiquetas || []).join(' '), p.participantes || '', p.workstream || '', a.label].join(' ')).includes(q)) return false;
    return true;
  });
  const key = a => a.date || new Date(a.mtime * 1000).toISOString();
  list.sort(t.sort === 'dur' ? (x, y) => estDuration(y) - estDuration(x) : (x, y) => (t.sort === 'asc' ? 1 : -1) * key(x).localeCompare(key(y)));
  return list;
}
function groupKey(a) {
  const g = S.tbl.group;
  if (g === 'month') { const d = aDate(a) || new Date(a.mtime * 1000); return `${MESES[d.getMonth()]} ${d.getFullYear()}`; }
  if (g === 'folder') return a.folder ? pathLabel(a.folder) : 'Sin clasificar';
  if (g === 'estado') return (a.props || {}).estado || 'Sin estado';
  return '';
}
function rowHtml(a) {
  const p = a.props || {}, d = aDate(a);
  return `<tr data-go="a" data-path="${esc(a.path)}" draggable="true" data-drag="${esc(a.path)}">
    <td class="nm"><span class="ticon">${iconOf(a) || I.mic}</span>${esc(titleOf(a) || longTitle(a))}${titleOf(a) ? '' : (a.label ? ` <small class="muted">${esc(nfc(a.label))}</small>` : '')}</td>
    <td class="nw">${d ? `${d.getDate()} ${MES[d.getMonth()]} ${d.getFullYear()} · ${pad(d.getHours())}:${pad(d.getMinutes())}` : '—'}</td>
    <td class="nw num">${fmtDur(estDuration(a))}</td>
    <td class="muted">${esc(a.folder ? folderName(a.folder) : 'Sin clasificar')}</td>
    <td>${(p.etiquetas || []).map(x => `<span class="tag-chip sm">${esc(x)}</span>`).join('')}</td>
    <td>${p.estado ? `<span class="pill ${ESTADO_CLS[p.estado] || ''}">${esc(p.estado)}</span>` : ''}</td>
    <td>${statusPill(a)}</td></tr>`;
}
function viewTable() {
  const t = S.tbl, list = tableRows();
  const sel = (k, opts) => `<select data-tbl="${k}">${opts.map(([v, l]) => `<option value="${esc(v)}"${v === t[k] ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  let body = '', g = null;
  for (const a of list) {
    const k = groupKey(a);
    if (S.tbl.group !== 'none' && k !== g) { g = k; body += `<tr class="grp"><td colspan="7">${esc(k)} <span class="muted">${list.filter(x => groupKey(x) === k).length}</span></td></tr>`; }
    body += rowHtml(a);
  }
  main.innerHTML = `<div class="page wide">
    <div class="db-head"><h1>${I.list}Reuniones <span class="sub">${list.length}</span></h1>${dbTabs('r')}</div>
    <div class="db-tools">
      <input class="input" data-tbl="q" placeholder="Filtrar por título, etiqueta, participante…" value="${esc(t.q)}" autocomplete="off" spellcheck="false">
      ${sel('folder', [['*', 'Todas las carpetas'], ...flatFolders().filter(f => f.path).map(f => [f.path, '  '.repeat(f.depth - 1) + f.name])])}
      ${sel('tag', [['*', 'Todas las etiquetas'], ...allTags().map(x => [x, x])])}
      ${sel('estado', [['*', 'Cualquier estado'], ...ESTADOS])}
      ${sel('tx', [['*', 'Transcripción: todas'], ['done', 'Transcritas'], ['partial', 'A medias'], ['pending', 'Sin transcribir'], ['busy', 'En marcha']])}
      ${sel('group', [['month', 'Agrupar por mes'], ['folder', 'Agrupar por carpeta'], ['estado', 'Agrupar por estado'], ['none', 'Sin agrupar']])}
      ${sel('sort', [['desc', 'Más recientes primero'], ['asc', 'Más antiguas primero'], ['dur', 'Más largas primero']])}
    </div>
    <div class="card db-table"><table><thead><tr><th>Nombre</th><th>Fecha</th><th>Duración</th><th>Carpeta</th><th>Etiquetas</th><th>Estado</th><th>Transcripción</th></tr></thead>
      <tbody>${body || '<tr><td colspan="7" class="empty">Nada coincide con los filtros</td></tr>'}</tbody></table></div></div>`;
}
main.addEventListener('input', e => { if (e.target.dataset && e.target.dataset.tbl === 'q') tblChange(e.target); });
main.addEventListener('change', e => { if (e.target.dataset && e.target.dataset.tbl && e.target.dataset.tbl !== 'q') tblChange(e.target); });
function tblChange(el) {
  S.tbl[el.dataset.tbl] = el.value;
  localStorage.setItem('tbl', JSON.stringify(S.tbl));
  const caret = el.selectionStart;
  viewTable();
  if (el.dataset.tbl === 'q') { const q = $('[data-tbl="q"]'); q.focus(); q.setSelectionRange(caret, caret); }
}

/* ---------------------------------------------------------------- calendario */
function viewCalendar(ym) {
  const now = new Date();
  let [y, m] = /^\d{4}-\d{2}$/.test(ym || '') ? ym.split('-').map(Number) : [now.getFullYear(), now.getMonth() + 1];
  const first = new Date(y, m - 1, 1), start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));  // la semana empieza en lunes
  const byDay = {};
  allAudios().forEach(a => (byDay[aDay(a)] = byDay[aDay(a)] || []).push(a));
  Object.values(byDay).forEach(l => l.sort((p, q) => (p.date || '').localeCompare(q.date || '')));
  const today = aDay({ date: null, mtime: Date.now() / 1000 });
  const prev = m === 1 ? `${y - 1}-12` : `${y}-${pad(m - 1)}`, next = m === 12 ? `${y + 1}-01` : `${y}-${pad(m + 1)}`;
  let cells = '';
  for (let k = 0; k < 42; k++) {
    const d = new Date(start); d.setDate(start.getDate() + k);
    const key = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, list = byDay[key] || [];
    cells += `<div class="cday${d.getMonth() !== m - 1 ? ' out' : ''}${key === today ? ' today' : ''}"><div class="cnum">${d.getDate()}</div>`
      + list.slice(0, 4).map(a => { const dd = aDate(a); return `<div class="cev st-${statusOf(a)}" data-go="a" data-path="${esc(a.path)}" title="${esc(fullTitle(a))}">`
        + `<span class="ct">${dd ? `${pad(dd.getHours())}:${pad(dd.getMinutes())}` : ''}</span>${iconOf(a) ? iconOf(a) + ' ' : ''}${esc(titleOf(a) || nfc(a.label || '') || fmtDur(estDuration(a)))}</div>`; }).join('')
      + (list.length > 4 ? `<div class="cmore" data-act="cal-day" data-day="${key}">+${list.length - 4} más</div>` : '') + '</div>';
  }
  main.innerHTML = `<div class="page wide">
    <div class="db-head"><h1>${I.calendar}${MESES[m - 1][0].toUpperCase() + MESES[m - 1].slice(1)} ${y}</h1>${dbTabs('c')}
      <div class="cal-nav"><a class="btn sm" href="${href('c', prev)}">‹</a><a class="btn sm" href="#/c/">Hoy</a><a class="btn sm" href="${href('c', next)}">›</a></div></div>
    <div class="cal card"><div class="cwk">${['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom'].map(x => `<div>${x}</div>`).join('')}</div><div class="cgrid">${cells}</div></div></div>`;
}
main.addEventListener('click', e => {
  const more = e.target.closest('[data-act="cal-day"]');
  if (!more) return;
  S.tbl.q = ''; S.tbl.group = 'none';
  const list = allAudios().filter(a => aDay(a) === more.dataset.day);
  openModal(`<h3>${esc(more.dataset.day)}</h3><div class="card alist">${list.map(a => `<div class="aitem" data-path="${esc(a.path)}" data-m="go"><span class="p"><span class="dot ${statusOf(a)}"></span></span><span class="t">${esc(fullTitle(a))}</span><span></span><span class="d">${fmtDur(estDuration(a))}</span><span></span></div>`).join('')}</div>`,
    d => d.addEventListener('click', ev => { const it = ev.target.closest('[data-path]'); if (it) { closeModal(true); go('a', it.dataset.path); } }));
});

/* ---------------------------------------------------------------- busqueda rapida (Ctrl+P) */
const ACTIONS = [
  ['<i class="recdot"></i>', 'Grabar ahora', () => openRec()], [I.list, 'Reuniones', () => go('r', '')], [I.calendar, 'Calendario', () => go('c', '')],
  [I.home, 'Inicio', () => go('home', '')], [I.gear, 'Ajustes', () => openSettings({ sec: 'algrabar' })], [I.plus, 'Nueva carpeta', () => openNewFolder('')],
  [I.clipboard, 'Copiar aviso de grabación', () => copy(CONSENT_TEXT, 'Aviso copiado: pégalo en el chat de la reunión')]];
function paletteItems(q) {
  const f = fold(q.trim()), out = [];
  if (!f) {
    recents(7).forEach(r => out.push({ kind: r.k, path: r.p, ...itemLabel(r.k, r.item), group: 'Visto recientemente' }));
    ACTIONS.forEach(([ic, l, fn]) => out.push({ icon: ic, text: l, fn, group: 'Acciones' }));
    return out;
  }
  const score = s => { const x = fold(s); return x.startsWith(f) ? 3 : x.includes(' ' + f) ? 2 : x.includes(f) ? 1 : 0; };
  const cands = [];
  allAudios().forEach(a => {
    const p = a.props || {};
    const s = Math.max(score(titleOf(a)) * 2, score(longTitle(a)), score(shortTitle(a)), score(a.label || ''), score((p.etiquetas || []).join(' ')), score(p.participantes || ''), score(p.workstream || ''), score(a.folder) * 0.5);
    if (s) cands.push({ s: s + (a.date || '') / 1e20, kind: 'a', path: a.path, ...itemLabel('a', a), sub: [shortTitle(a), a.folder && folderName(a.folder)].filter(Boolean).join(' · ') });
  });
  S.folders.forEach(fo => { if (fo.path && score(fo.name)) cands.push({ s: score(fo.name) * 1.5, kind: 'f', path: fo.path, ...itemLabel('f', fo), sub: `${fo.total} reuniones` }); });
  S.docs.forEach(d => { if (score(d.name)) cands.push({ s: score(d.name), kind: 'd', path: d.path, ...itemLabel('d', d) }); });
  cands.sort((x, y) => y.s - x.s).slice(0, 25).forEach(c => out.push({ ...c, group: 'Páginas' }));
  ACTIONS.filter(([, l]) => fold(l).includes(f)).forEach(([ic, l, fn]) => out.push({ icon: ic, text: l, fn, group: 'Acciones' }));
  out.push({ icon: I.search, text: `Buscar «${q.trim()}» dentro de todas las transcripciones y notas`, fn: () => go('s', q.trim()), group: 'Texto completo' });
  return out;
}
function openPalette() {
  let items = [], sel = 0;
  openModal(`<div class="pal"><div class="pal-in">${I.search}<input id="palQ" placeholder="Buscar reuniones, carpetas, etiquetas… o una acción" autocomplete="off" spellcheck="false"></div>
    <div id="palList" class="pal-list"></div><div class="pal-foot">↑↓ moverse · Intro abrir · Esc cerrar</div></div>`, d => {
    const input = $('#palQ', d), list = $('#palList', d);
    const draw = () => {
      items = paletteItems(input.value);
      sel = Math.min(sel, items.length - 1);
      let g = null;
      list.innerHTML = items.map((it, k) => (it.group !== g ? `<div class="pal-g">${esc((g = it.group))}</div>` : '')
        + `<div class="pal-it${k === sel ? ' on' : ''}" data-k="${k}"><span class="pi">${it.icon || ''}</span><span class="pt">${esc(it.text)}</span>${it.sub ? `<span class="ps">${esc(it.sub)}</span>` : ''}</div>`).join('')
        || '<div class="empty">Sin resultados</div>';
      list.querySelector('.pal-it.on')?.scrollIntoView({ block: 'nearest' });
    };
    const pick = k => { const it = items[k]; if (!it) return; closeModal(true); if (it.fn) it.fn(); else go(it.kind, it.path); };
    input.addEventListener('input', () => { sel = 0; draw(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; draw(); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
    });
    list.addEventListener('mousemove', e => { const it = e.target.closest('[data-k]'); if (it && +it.dataset.k !== sel) { sel = +it.dataset.k; list.querySelectorAll('.pal-it').forEach(x => x.classList.toggle('on', +x.dataset.k === sel)); } });
    list.addEventListener('click', e => { const it = e.target.closest('[data-k]'); if (it) pick(+it.dataset.k); });
    draw();
  }, 'palette');
}

/* ---------------------------------------------------------------- eliminar, importar, aviso */
async function deleteItem(path) {
  const a = S.audios.get(path), d = S.docs.get(path);
  if (!a && !d) return;
  // destinos de salida: sus documentos que nadie ha cambiado alli van tambien a la papelera; los editados se quedan
  const outs = a && (S.config.perfil?.outputs || []).length
    ? ' También van a la papelera sus copias en otras carpetas que nadie haya cambiado allí; las que hayas editado se quedan donde están.' : '';
  if (!await ask('¿Mandar a la papelera?', a
    ? `Se manda a la <b>papelera de Windows</b> la grabación con su transcripción y sus notas (.wav, .txt, .srt, .md). Se puede recuperar desde la papelera.${outs}`
    : 'Se manda a la <b>papelera de Windows</b>. Se puede recuperar desde ahí.', 'Mandar a la papelera')) return;
  if (S.playing === path) { audio.pause(); audio.removeAttribute('src'); audio.load(); S.playing = null; $('#player').classList.add('hidden'); document.body.classList.remove('playing'); }
  try {
    const r = await api('/api/delete', { path });
    toast('🗑️ En la papelera de Windows' + (r.copias ? ` (con ${r.copias} ${r.copias === 1 ? 'copia' : 'copias'} en otras carpetas)` : ''));
    const parent = (a || d).parent ? (a || d).parent.path : '';
    await refreshTree(true);
    go(parent ? 'f' : 'home', parent, null, true);
  } catch (err) { toast(esc(err.message), 'err'); }
}
const dropFolder = () => S.route.kind === 'f' ? S.route.path : S.route.kind === 'a' ? ((S.audios.get(S.route.path) || {}).folder || '') : '';
let dropTimer = null;
function showDrop(on) {
  let ov = $('#dropOv');
  if (on && !ov) { ov = document.createElement('div'); ov.id = 'dropOv'; ov.className = 'drop-ov'; document.body.append(ov); }
  if (ov) { ov.classList.toggle('hidden', !on); ov.innerHTML = `<div>⤓ Suelta aquí para añadir a <b>${esc(folderName(dropFolder()))}</b><br><small>Audios (wav, mp3, m4a…) o textos (.txt, .md)</small></div>`; }
}
addEventListener('dragover', e => {
  if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
  e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
  showDrop(true); clearTimeout(dropTimer); dropTimer = setTimeout(() => showDrop(false), 400);
});
addEventListener('drop', e => {
  if (!e.dataTransfer || !e.dataTransfer.files.length) return;
  e.preventDefault(); showDrop(false);
  importFiles([...e.dataTransfer.files], dropFolder());
});
async function importFiles(files, folder) {
  let last = null;
  for (const f of files) {
    toast(`Añadiendo ${esc(f.name)} (${fmtSize(f.size)})…`);
    try {
      const r = await fetch(`/api/import?folder=${enc(folder)}&name=${enc(f.name)}`, { method: 'POST', headers: { 'X-Audios': '1' }, body: f }).then(x => x.json());
      if (r.error) throw new Error(r.error);
      last = r.path;
    } catch (err) { toast(`${esc(f.name)}: ${esc(err.message)}`, 'err'); }
  }
  if (!last) return;
  await refreshTree(true);
  toast(`✓ ${files.length === 1 ? 'Añadido' : files.length + ' archivos añadidos'} a ${esc(folderName(folder))}`);
  go(S.audios.has(last) ? 'a' : 'd', last);
}

/* reunion detectada (Teams/Zoom usando el micro) */
function renderMeetChip() {
  const c = $('#meetChip'), m = S.rec && S.rec.meeting;
  c.classList.toggle('hidden', !m || !!S.rec.recording);
  if (m) c.innerHTML = `${I.phone}${esc(m)} en llamada · <b><i class="recdot"></i>Tomar Notas</b>`;
  // con qué grabaría: el mismo plan que Ctrl+Alt+R y la tarjeta (/api/grabar/plan), una vez por llamada
  if (m && !S.rec.recording && S.meetPlanDe !== m) {
    S.meetPlanDe = m;
    api('/api/grabar/plan', {}).then(p => { c.title = `Graba: ${p.resumen}`; }).catch(() => { });
  }
  if (!m) S.meetPlanDe = null;
}
$('#meetChip').onclick = () => toggleRec();

/* ---------------------------------------------------------------- navegacion lateral */
function renderSideNav() {
  const k = S.route.kind;
  $$('#sideNav a').forEach(a => a.classList.toggle('on', a.dataset.nav === (k === 'home' ? 'home' : k)));
}
$('#sideNav').addEventListener('click', e => { const a = e.target.closest('[data-nav="find"]'); if (a) { e.preventDefault(); openPalette(); } });
