'use strict';
/* Perfil de reunión: carpeta de destino, idioma, tema de vocabulario y contexto libre, con un perfil por defecto
   (Ajustes → Perfil de reunión, o «Guardar como perfil por defecto» al grabar) y ajustes por reunión (diálogo
   «● Grabar» y página de la reunión: el directo los relee en el siguiente trozo). También el selector de tema en
   dos pasos (Proyecto / Tema) y la importación de listas de vocabulario de otra carpeta.
   Se carga después de app.js y notas.js y usa sus utilidades ($, $$, api, esc, I, S, LANGS, langSelect, folderOpts,
   liveFit, toast, loadVocab, refreshTree, linkFolder, openRec, openSettings). app.js y notas.js lo invocan con
   `typeof x === 'function'`: sin este fichero la app funciona igual, con el selector de tema de siempre y sin perfil. */

const PERFIL_AUTO = '__auto';  // valor del <select> para «automático» (null en el servidor)

/* ---------------------------------------------------------------- idioma */
function perfilLangLabel(code) {
  const hit = LANGS.find(([v]) => v === (code || ''));
  return hit ? hit[1] : (code || '');
}
/* Opciones del idioma de UNA reunión: «Por defecto» quita la clave del .md (vale el del perfil); «auto» = detectar.
   sel: lo que tiene el .md ('' o undefined = por defecto, 'auto', o un código). def: el idioma del perfil. */
function perfilLangOptions(sel, def) {
  const cur = sel || '';
  const opts = [['', `Por defecto · ${perfilLangLabel(def)}`], ['auto', perfilLangLabel('')]]
    .concat(LANGS.filter(([v]) => v).map(([v, l]) => [v, l]));
  return opts.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(l)}</option>`).join('');
}
/* Idioma con el que se transcribe una reunión concreta (para preseleccionar la caja «Transcribir»): el de su .md o,
   si no lo fija, el del perfil. No el último elegido en otra caja: eso es de aquella reunión. */
function perfilLangOf(a) {
  const v = (a && a.props && a.props.idioma) || '';
  if (v === 'auto') return '';
  return v || (S.config.language ?? '');
}

/* ---------------------------------------------------------------- precedencias */
/* Qué se aplica a una grabación nueva y de dónde sale cada campo (igual que resolve_perfil en el servidor):
   lo elegido en el diálogo > lo que fija el perfil > la sugerencia de la reunión en curso > lo último usado.
   perfil: {folder, vocab, context, language} con null = automático; sug: {name, vocab}; last: {folder, vocab}. */
function perfilResolve(perfil, sug, last, elegido) {
  perfil = perfil || {}; sug = sug || {}; last = last || {}; elegido = elegido || {};
  const out = {}, origen = {};
  const pick = (k, cands) => {
    for (const [o, v] of cands) if (v != null) { out[k] = v; origen[k] = o; return; }
    out[k] = ''; origen[k] = 'ultimo';
  };
  pick('vocab', [['elegido', elegido.vocab], ['perfil', perfil.vocab], ['reunion', sug.vocab || null], ['ultimo', last.vocab || '']]);
  pick('folder', [['elegido', elegido.folder], ['perfil', perfil.folder], ['ultimo', last.folder || '']]);
  pick('language', [['elegido', elegido.language], ['perfil', perfil.language ?? '']]);
  pick('context', [['elegido', elegido.context], ['perfil', perfil.context || '']]);
  pick('name', [['elegido', elegido.name || null], ['reunion', sug.name || null], ['ultimo', '']]);
  return { ...out, origen };
}

/* ---------------------------------------------------------------- tema en dos pasos (Proyecto / Tema) */
function perfilGroups(vocab) {
  const g = new Map();
  (vocab || []).forEach(v => { if (!g.has(v.project)) g.set(v.project, []); g.get(v.project).push(v); });
  return [...g.entries()];
}
/* Opciones del <select> Tema: TODAS las listas (así reunion.js y app.js pueden fijar cualquier id con .value),
   ocultas las de otros proyectos. '' = sin vocabulario. */
function perfilTemaOptions(vocab, proj, sel) {
  return '<option value="">Sin vocabulario</option>' + (vocab || []).map(v =>
    `<option value="${esc(v.id)}" data-proj="${esc(v.project)}"${v.id === sel ? ' selected' : ''}${v.project !== proj ? ' hidden' : ''}>${esc(v.name)}${v.desc ? ' · ' + esc(v.desc) : ''}</option>`).join('');
}
function perfilVocabEmptyHtml() {
  return `<div class="help" style="margin-top:0">Todavía no hay listas. Si ya tienes tus listas en una carpeta (en OneDrive, por ejemplo), conéctala: cada archivo <code>.txt</code> es un tema, con los nombres, siglas y productos de esas reuniones separados por comas o uno por línea, los más importantes primero. También puedes crearlas aquí.</div>`;
}
const VOCAB_CONECTAR = '__conectar';  // opción del <select> Proyecto: «Usar las listas de una carpeta…»
const VOCAB_COPIAR_TITLE = 'Copia a la carpeta de la app las listas <Proyecto>\\Vocabulario\\*.txt de otra carpeta (la de un compañero, un USB, otra copia de la app…) sin pisar las que ya tengas. Para usar tus listas sin copiarlas, «Usar las listas de una carpeta…»';
/* Bloque «Tema» del diálogo Grabar. sel: id de la lista preseleccionada ('' = sin vocabulario). */
function perfilTemaHtml(sel) {
  const vocab = S.vocab || [];
  const label = `<label>${I.topic}Tema <span class="muted" style="font-weight:400">· el vocabulario que recibe Whisper</span></label>`;
  if (!vocab.length) {
    return `${label}${perfilVocabEmptyHtml()}
      <div class="row" style="gap:8px;margin-top:8px"><button class="btn sm" type="button" id="rVocabConectar" title="Elige la carpeta donde tienes tus listas: se leen al grabar, sin copiarlas">${I.folder}Usar las listas de una carpeta…</button>
        <button class="btn sm" type="button" id="rVocabNew">${I.plus}Crear un proyecto con su vocabulario</button>
        <button class="btn ghost sm" type="button" id="rVocabImport" title="${esc(VOCAB_COPIAR_TITLE)}">Copiar listas de otra carpeta…</button></div>`;
  }
  const cur = vocab.find(v => v.id === sel), proj = cur ? cur.project : '';
  return `${label}
    <div class="row pf-tema" style="gap:6px;flex-wrap:nowrap">
      <select id="rProj" title="Proyecto"><option value="">Sin vocabulario</option>${perfilGroups(vocab).map(([p]) => `<option value="${esc(p)}"${p === proj ? ' selected' : ''}>${esc(p)}</option>`).join('')}<option value="${VOCAB_CONECTAR}">Usar las listas de una carpeta…</option></select>
      <select id="rVocab" title="Tema del proyecto">${perfilTemaOptions(vocab, proj, sel)}</select>
      <button class="btn sm" type="button" id="rVocabNew" title="Crear un proyecto o tema nuevo con su lista de nombres y siglas">${I.plus}Nuevo…</button>
    </div><div class="help" id="rVocabFit"></div>`;
}
/* Enlaza Proyecto ↔ Tema (se llama antes de que app.js ponga su onchange en #rVocab, así «sync» corre primero). */
function perfilBindTema(d) {
  const proj = $('#rProj', d), tema = $('#rVocab', d), vocab = S.vocab || [];
  if (!proj || !tema) return;
  const sync = () => {
    const v = vocab.find(x => x.id === tema.value);
    if (v && proj.value !== v.project) proj.value = v.project;
    if (!tema.value) proj.value = '';
    [...tema.options].forEach(o => { o.hidden = !!o.value && o.dataset.proj !== proj.value; });
    proj.dataset.prev = proj.value;
  };
  proj.dataset.prev = proj.value;
  proj.onchange = () => {
    if (proj.value === VOCAB_CONECTAR) {  // como «Otra carpeta…» en Carpeta: no es un proyecto, abre el selector
      proj.value = proj.dataset.prev || '';
      vocabConectar(openRec);
      return;
    }
    proj.dataset.prev = proj.value;
    const first = vocab.find(v => v.project === proj.value);
    tema.value = first ? first.id : '';
    sync();
    tema.dispatchEvent(new Event('change'));  // app.js: carpeta del proyecto y cuánto le cabe a Whisper
  };
  tema.addEventListener('change', sync);
  sync();
}

/* ---------------------------------------------------------------- diálogo Grabar: idioma y contexto de ESTA reunión */
function perfilCamposHtml(language, context) {
  return `<div class="grid2">
      <div><label>${I.globe}Idioma de la reunión</label>${langSelect('rLang', language)}</div>
      <div><label>${I.text}Contexto <span class="muted" style="font-weight:400">· opcional</span></label><input class="input" id="rCtx" maxlength="500" value="${esc(context || '')}" placeholder="De qué va y quién participa, en una o dos frases" autocomplete="off"></div>
    </div>
    <div class="row pf-save"><button class="btn ghost sm" type="button" id="rPerfilSave" title="Guarda la carpeta, el idioma, el tema y el contexto de arriba como perfil por defecto: Ctrl+Alt+R y las próximas grabaciones empezarán así">${I.check}Guardar como perfil por defecto</button>
      <span class="muted" style="font-size:12px">Idioma, tema y contexto van con esta reunión: los usan Whisper y el resumen, y se cambian sobre la marcha en su página.</span></div>`;
}
function perfilBindRec(d) {
  perfilBindTema(d);
  // tema fijo en el perfil (y que siga existiendo): la sugerencia de Teams no lo pisa
  const fijo = S.config.perfil?.vocab;
  if (fijo != null && (fijo === '' || (S.vocab || []).some(v => v.id === fijo))) d.dataset.pfVocab = '1';
  const imp = $('#rVocabImport', d);
  if (imp) imp.onclick = () => perfilImportar($('#rProj', d)?.value || '', openRec);
  const con = $('#rVocabConectar', d);
  if (con) con.onclick = () => vocabConectar(openRec);
  const save = $('#rPerfilSave', d);
  if (save) save.onclick = async () => {
    const body = { language: $('#rLang', d)?.value ?? '', context: $('#rCtx', d)?.value.trim() || '' };
    const folder = $('#rFolder', d)?.value;
    if (folder != null && folder !== '__pick') body.folder = folder;
    if ($('#rVocab', d)) body.vocab = $('#rVocab', d).value;
    save.disabled = true;
    try {
      const r = await api('/api/perfil', body);
      S.config.perfil = r.perfil; S.config.language = S.selLang = r.perfil.language;
      toast('Perfil por defecto guardado · Ctrl+Alt+R y las próximas grabaciones empiezan así');
    } catch (err) { toast(esc(err.message), 'err'); }
    save.disabled = false;
  };
}
/* Lo que el diálogo manda a /api/rec/start además de lo de siempre. */
function perfilRecExtra(d) {
  const c = $('#rCtx', d);
  return c ? { contexto: c.value.trim() } : {};
}

/* ---------------------------------------------------------------- importar listas de otra carpeta */
/* Selector de carpetas de Windows (o pegar la ruta) → POST /api/vocab/import → recarga y vuelve a `after`. */
async function perfilImportar(project, after) {
  toast('Elige la carpeta con las listas en la ventana de Windows que se acaba de abrir…');
  let path;
  try { path = (await api('/api/link/pick', { title: 'Carpeta con las listas de vocabulario' })).path; }
  catch (err) { path = prompt(`${err.message}\n\nPega aquí la ruta de la carpeta con las listas (<Proyecto>\\Vocabulario\\*.txt):`); }
  if (!path) return;
  try {
    const r = await api('/api/vocab/import', { path, project: project || '' });
    await loadVocab(S.config.language ?? 'es-en');
    await refreshTree(true);
    toast(perfilImportText(r));
    if (after) after();
  } catch (err) { toast(esc(err.message), 'err'); }
}
function perfilImportText(r) {
  const n = r.copiadas || 0, o = r.omitidas || 0;
  if (!n) return `No había listas nuevas${o ? ` (${o} ya existían o no tenían un nombre válido)` : ''}`;
  return `${n} ${n === 1 ? 'lista copiada' : 'listas copiadas'} · ${esc((r.proyectos || []).join(', '))}${o ? ` · ${o} ya existían` : ''}`;
}

/* ---------------------------------------------------------------- carpetas de listas conectadas (msg 1148) */
/* «Usar las listas de una carpeta…»: selector de carpetas de Windows (o pegar la ruta) → POST /api/vocab/link/add.
   Las listas se leen en vivo al grabar y al transcribir: nada se copia. after: a dónde volver (openRec, o redibujar). */
async function vocabConectar(after) {
  toast('Elige la carpeta donde tienes tus listas en la ventana de Windows que se acaba de abrir…');
  let path;
  try { path = (await api('/api/link/pick', { title: 'Carpeta con tus listas de vocabulario (.txt)' })).path; }
  catch (err) { path = prompt(`${err.message}\n\nPega aquí la ruta de la carpeta con tus listas (.txt):`); }
  if (!path) return;
  try {
    const r = await api('/api/vocab/link/add', { path });
    S.config.vocab_links = r.vocab_links || S.config.vocab_links;
    await loadVocab(S.config.language ?? 'es-en');
    toast(vocabConectadaText(r), r.listas ? '' : 'err');
    if (after) after();
  } catch (err) { toast(esc(err.message), 'err'); }
}
function vocabConectadaText(r) {
  const n = r.listas || 0, s = (r.avisos || []).length;
  const listas = `${n} ${n === 1 ? 'lista' : 'listas'}`;
  const salta = s ? ` · ${s} ${s === 1 ? 'se salta' : 'se saltan'}: lo ves en Ajustes › Vocabulario` : '';
  if (r.ya) return `«${esc(r.name)}» ya estaba conectada · ${listas}${salta}`;
  return `${I.check}Conectada «${esc(r.name)}» · ${listas} · se leen al grabar, sin copiarlas${salta}`;
}
async function vocabDesconectar(rel, after) {
  try {
    const r = await api('/api/vocab/link/remove', { rel });
    S.config.vocab_links = r.vocab_links || [];
    // el servidor pone en automático el tema del perfil (y el último elegido) si era de esa carpeta: la configuración
    // de aquí también, para que Ajustes no guarde el tema quitado (perfilVocabRedraw)
    try { setConfig(await api('/api/config')); } catch { /* la siguiente lectura la trae */ }
    await loadVocab(S.config.language ?? 'es-en');
    toast('Carpeta quitada · no se ha borrado nada de ella');
    if (after) after();
  } catch (err) { toast(esc(err.message), 'err'); }
}
/* Una lista que se salta, con qué hacer (GET /api/vocab → carpetas[].avisos). */
function vocabAvisoText(a) {
  if (a.motivo === 'nube') return `«${a.lista}» está solo en OneDrive y no se descarga sola: en el Explorador, clic derecho › «Mantener siempre en este dispositivo»`;
  if (a.motivo === 'grande') return `«${a.lista}» pesa ${a.kb} KB: no parece una lista (se leen hasta 64 KB)`;
  return `«${a.lista}»: ${a.motivo}`;
}
/* Ajustes › Vocabulario: las carpetas conectadas, con cuántas listas dan, lo que se salta, «Abrir» y «quitar». */
function vocabCarpetasHtml(carpetas) {
  carpetas = carpetas || [];
  const ayuda = `<div class="help">Cada <code>.txt</code> es un tema, con los términos separados por comas o uno por línea. Valen tres formas: los <code>.txt</code> sueltos (el proyecto es el nombre de la carpeta), una subcarpeta <code>Vocabulario</code>, o una carpeta por proyecto con su <code>Vocabulario</code> dentro. Lo que cambies en esos archivos se usa en la siguiente grabación. Quitar una carpeta no borra nada.</div>`;
  if (!carpetas.length) return `<div class="help" style="margin-top:0">Ninguna. Con «Usar las listas de una carpeta…» eliges la carpeta donde ya tienes tus listas (en OneDrive, por ejemplo) y ${esc(APP_NAME)} las lee de allí, sin copiarlas.</div>${ayuda}`;
  // la ruta, debajo del nombre y partible por cualquier sitio: una ruta larga de OneDrive no ensancha Ajustes
  return `<table class="vtable vc-carpetas">${carpetas.map(c => `<tr><td style="width:100%">${I.folder}${esc(nfc(c.name))}
      <div style="white-space:normal;overflow-wrap:anywhere;font-weight:400"><code title="${esc(c.path)}">${esc(c.path)}</code></div></td>
      <td style="white-space:nowrap">${c.existe ? `${c.listas} ${c.listas === 1 ? 'lista' : 'listas'}` : '<span class="warn-txt">No se encuentra</span>'}</td>
      <td style="white-space:nowrap">${c.existe ? `<button class="btn ghost sm" type="button" data-vopen="${esc(c.rel)}" title="Abrir la carpeta en el Explorador">${I.ext}Abrir</button>` : ''}<button class="btn ghost sm" type="button" data-vunlink="${esc(c.rel)}" title="Deja de leerla: no se borra nada">quitar</button></td></tr>${c.existe ? '' : `<tr><td colspan="3" class="warn-txt" style="font-size:12.5px;white-space:normal;font-weight:400">No se encuentra la carpeta: ¿la has movido o renombrado, o falta la VPN?</td></tr>`}${(c.avisos || []).map(a =>
      `<tr><td colspan="3" class="warn-txt" style="font-size:12.5px;white-space:normal;font-weight:400">${esc(vocabAvisoText(a))}</td></tr>`).join('')}`).join('')}</table>${ayuda}`;
}

/* ---------------------------------------------------------------- página de la reunión y directo */
function perfilPropsHtml(a) {
  const p = a.props || {};
  return `<div class="prow"><span class="pk">${I.globe}Idioma</span><span class="pv"><select class="pstate" data-prop="idioma" title="Idioma con el que se transcribe esta reunión (en directo, desde el siguiente trozo)">${perfilLangOptions(p.idioma, S.config.language)}</select></span></div>
    <div class="prow"><span class="pk">${I.text}Contexto</span><span class="pv"><input class="pinput" data-prop="contexto" maxlength="500" value="${esc(p.contexto || '')}" placeholder="De qué va y quién participa (opcional)" autocomplete="off" title="Lo recibe Whisper detrás del vocabulario y lo tiene en cuenta el resumen"></span></div>`;
}
function perfilLiveHtml(a) {
  const p = a.props || {};
  return `<div class="lh-ws pf-live">${I.globe}Idioma <select class="pstate" data-prop="idioma" title="Idioma de esta reunión: el directo lo usa desde el siguiente trozo">${perfilLangOptions(p.idioma, S.config.language)}</select>
    ${I.text}<input class="pinput pf-ctx" data-prop="contexto" maxlength="500" value="${esc(p.contexto || '')}" placeholder="Contexto: de qué va y quién participa (opcional)" autocomplete="off" title="Lo recibe Whisper detrás del vocabulario, desde el siguiente trozo, y lo tiene en cuenta el resumen"></div>`;
}
/* Mensaje al guardar idioma o contexto desde la página (notas.js). live: hay un directo en marcha. */
function perfilSavedText(prop, live, transcribed) {
  const que = prop === 'idioma' ? 'Idioma' : 'Contexto';
  if (live) return `${que}: el directo lo usa desde el siguiente trozo`;
  return transcribed ? `${que} guardado · se aplicará si la vuelves a transcribir (y al resumen)` : `${que} guardado · se usará al transcribirla`;
}

/* ---------------------------------------------------------------- Ajustes: Perfil de reunión */
function perfilVocabOptions(vocab, sel) {
  const opt = (v, l) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  return opt(PERFIL_AUTO, 'Automático · el que sugiera la reunión en curso (Teams/Outlook) o el último elegido')
    + opt('', 'Sin vocabulario · el general de Ajustes')
    + perfilGroups(vocab).map(([pr, vs]) => `<optgroup label="${esc(pr)}">${vs.map(v => opt(v.id, v.name + (v.desc ? ' · ' + v.desc : ''))).join('')}</optgroup>`).join('');
}
/* Ajustes abierto y las listas han cambiado (quitar una carpeta de listas): #pfVocab, otra vez con las que hay. Se queda
   lo elegido si sigue existiendo; si no, lo guardado (el servidor ya lo ha puesto en automático). Si no, «Guardar»
   mandaba el tema quitado y daba «Ese tema de vocabulario ya no existe» (revisión de la integración, 09/11/2026). */
function perfilVocabRedraw(d) {
  const v = $('#pfVocab', d);
  if (!v) return;
  const vocab = S.vocab || [], p = (S.config || {}).perfil || {};
  const vale = v.value === PERFIL_AUTO || v.value === '' || vocab.some(x => x.id === v.value);
  v.innerHTML = perfilVocabOptions(vocab, vale ? v.value : (p.vocab == null ? PERFIL_AUTO : p.vocab));
}
function perfilFieldsetHtml() {
  const c = S.config || {}, p = c.perfil || {}, vocab = S.vocab || [], presets = c.presets || [];
  const folderSel = p.folder == null ? PERFIL_AUTO : p.folder, vocabSel = p.vocab == null ? PERFIL_AUTO : p.vocab;
  const opt = (v, l, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  const prov = c.providers?.[c.cloud?.provider]?.label || 'nube';
  return `<fieldset><legend>${I.clipboard}Perfil de reunión</legend>
      <div class="help" style="margin-top:4px">Con lo que empieza cada grabación (● Grabar y Ctrl+Alt+R). En el diálogo de grabar se ajusta para una reunión concreta y, desde ahí, se puede guardar como perfil.</div>
      <div class="grid2">
        <div><label>${I.folder}Carpeta de destino</label><select id="pfFolder">${opt(PERFIL_AUTO, 'Automática · la última usada (o la del proyecto del tema)', folderSel)}${folderOpts(folderSel)}</select></div>
        <div><label>${I.globe}Idioma de la reunión</label>${langSelect('sLang', c.language ?? 'es-en')}</div>
      </div>
      <label>${I.topic}Tema de vocabulario</label>
      <select id="pfVocab">${perfilVocabOptions(vocab, vocabSel)}</select>
      <label>${I.text}Contexto de las reuniones <span class="muted" style="font-weight:400">· opcional, una o dos frases</span></label>
      <textarea id="pfContext" rows="2" maxlength="500" placeholder="p. ej. Reuniones semanales del proyecto Alfa con el equipo del cliente: seguimiento de la migración y del plan de pruebas">${esc(p.context || '')}</textarea>
      <div class="help"><b>Español + inglés</b>, <b>Español + portugués</b> e <b>Inglés + portugués</b> detectan el idioma cada pocos segundos pero solo entre esos dos y dejan cada frase en su idioma; si una reunión es solo en uno, fijarlo es algo más preciso. El contexto lo recibe Whisper detrás del vocabulario (si no cabe todo, se recorta antes que el vocabulario) y lo tiene en cuenta el resumen.</div>
      <div class="grid2">
        <div><label>Idioma del resumen</label><select id="sSumLang">${[['es', 'Español'], ['en', 'Inglés'], ['same', 'El de la reunión']].map(([v, l]) => opt(v, l, c.summary?.lang || 'es')).join('')}</select></div>
        <div><label>Motor del directo</label><select id="lPreset">${presets.map(pr => opt(pr.id, `${pr.live_label} · ~${String(pr.live_speed).replace('.', ',')}× · ${liveFit(pr)[1]}`, c.live_preset || 'medium')).join('')}</select></div>
      </div>
      <label>Instrucciones extra para el resumen (opcional)</label><textarea id="sExtra" placeholder="p. ej. Céntrate en riesgos, dependencias y fechas">${esc(c.summary?.extra || '')}</textarea>
      <label>Motor al transcribir después <span class="muted" style="font-weight:400">· «Solo grabar», «Volver a transcribir» y audios que arrastres</span></label>
      <select id="pfPreset">${presets.map(pr => opt(pr.id, `${pr.label} · ${pr.engine === 'cloud' ? prov : pr.model}`, c.preset)).join('')}</select>
      <label>${I.copy}Destinos de salida <span class="muted" style="font-weight:400">· opcional: cada reunión, como documento, en otras carpetas</span></label>
      <div id="pfOuts">${perfilOutsHtml(p.outputs || [])}</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="pfOutAdd">${I.plus}Añadir carpeta…</button></div>
      <div id="pfAvisos"></div>
      <div class="help">Una carpeta fuera de ${esc(APP_NAME)}: un vault de Obsidian, una biblioteca de SharePoint sincronizada, la de documentos del proyecto en OneDrive… No vale la carpeta de ${esc(APP_NAME)}, una carpeta vinculada ni una con grabaciones: allí cada documento chocaría con las notas de su reunión. Al terminar la transcripción y el resumen, y al guardar cambios en las notas o en la transcripción, cada reunión se escribe allí como <code>&lt;fecha&gt; - &lt;nombre&gt;.md</code> (un documento con propiedades, resumen, notas y transcripción con minutos), <code>.txt</code> o <code>.docx</code>; si ese nombre ya lo usa otra reunión o un fichero tuyo, como <code>&lt;fecha&gt; - &lt;nombre&gt; (código).md</code>. El audio y los originales se quedan donde se graban: nunca se mueve nada, y solo se sobrescriben los documentos que generó ${esc(APP_NAME)} en este PC; uno ajeno, uno nuestro que hayas cambiado allí o uno de otro PC que no coincida se deja como está (abajo verás el aviso). Al borrar una reunión, sus documentos que nadie haya cambiado van también a la papelera.</div>
    </fieldset>`;
}
/* ---------------------------------------------------------------- destinos de salida (lista editable de Ajustes) */
const PERFIL_FORMATS = [['md', 'Markdown (.md)'], ['txt', 'Texto (.txt)'], ['docx', 'Word (.docx)']];
let perfilOuts = [];
function perfilOutsHtml(outs) {
  if (!outs.length) return '<div class="help" style="margin-top:0">Ninguno · los textos se quedan junto al audio.</div>';
  return `<table class="vtable pf-outs">${outs.map((o, i) => `<tr><td><code title="${esc(o.path)}">${esc(o.path)}</code></td>
      <td><select data-out-fmt="${i}" title="Formato del documento">${PERFIL_FORMATS.map(([v, l]) => `<option value="${v}"${v === o.format ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></td>
      <td><label class="chk" style="margin:0;font-weight:400;font-size:12.5px;white-space:nowrap"><input type="checkbox" data-out-proj="${i}"${o.by_project ? ' checked' : ''}> subcarpeta por proyecto</label></td>
      <td><button class="btn ghost sm" type="button" data-out-del="${i}">quitar</button></td></tr>`).join('')}</table>`;
}
function bindPerfilOuts(d) {
  const box = $('#pfOuts', d), add = $('#pfOutAdd', d);
  if (!box || !add) return;
  perfilOuts = (S.config.perfil?.outputs || []).map(o => ({ path: o.path, format: o.format || 'md', by_project: !!o.by_project }));
  const draw = () => { box.innerHTML = perfilOutsHtml(perfilOuts); };
  box.addEventListener('click', e => { const b = e.target.closest('[data-out-del]'); if (b) { perfilOuts.splice(+b.dataset.outDel, 1); draw(); } });
  box.addEventListener('change', e => {
    const f = e.target.closest('[data-out-fmt]'), p = e.target.closest('[data-out-proj]');
    if (f) perfilOuts[+f.dataset.outFmt].format = f.value;
    if (p) perfilOuts[+p.dataset.outProj].by_project = p.checked;
  });
  add.onclick = async () => {
    toast('Elige la carpeta en la ventana de Windows que se acaba de abrir…');
    let path;
    try { path = (await api('/api/link/pick', { title: 'Carpeta donde guardar los documentos de las reuniones' })).path; }
    catch (err) { path = prompt(`${err.message}\n\nPega aquí la ruta de la carpeta:`); }
    if (!path) return;
    // se comprueba al momento (sin guardar): la carpeta de la app, una vinculada o una con grabaciones no valen
    try { path = (await api('/api/perfil', { comprobar: true, outputs: [{ path, format: 'md' }] })).outputs[0].path; }
    catch (err) { toast(esc(err.message), 'err'); return; }
    if (!perfilOuts.some(o => o.path === path && o.format === 'md')) perfilOuts.push({ path, format: 'md', by_project: false });
    draw();
  };
}
/* Lo que no se ha podido escribir en los destinos (GET /api/perfil → avisos), con qué hacer. */
function perfilAvisoText(accion) {
  const t = {
    'cambiado': 'no se ha sobrescrito: lo has cambiado allí, o es de otro PC o de antes de reinstalar y no coincide. Bórralo allí para que se vuelva a escribir',
    'junto-al-audio': 'no se escribe: chocaría con las notas de una grabación de esa carpeta. Elige otra carpeta para los documentos',
    'dentro-de-la-app': `no se escribe: esa carpeta es de ${APP_NAME} o de una carpeta vinculada, donde están las grabaciones. Quita ese destino y elige otra carpeta`,
    'ajeno': 'no se ha tocado: ya hay un fichero tuyo con ese nombre',
    'sin-carpeta': 'la carpeta no está disponible ahora (¿sin VPN o con el disco desconectado?): se escribirá cuando vuelva a cambiar la reunión',
  }[accion];
  if (t) return t;
  return String(accion || '').startsWith('error') ? 'no se ha podido escribir (¿abierto en Word?): se reintenta cuando vuelva a cambiar la reunión' : String(accion || '');
}
function perfilAvisosHtml(avisos) {
  if (!avisos || !avisos.length) return '';
  return `<div class="notice" style="margin-top:8px">Documentos sin escribir en los destinos:<ul style="margin:6px 0 0 18px;padding:0">${avisos.slice(0, 8).map(a =>
    `<li><code title="${esc(a.path)}">${esc(a.path)}</code> · ${esc(perfilAvisoText(a.accion))}</li>`).join('')}</ul></div>`;
}
function bindPerfilSettings(d) {
  const fsel = $('#pfFolder', d);
  bindPerfilOuts(d);
  const box = $('#pfAvisos', d);
  if (box) api('/api/perfil').then(r => { if (document.body.contains(box)) box.innerHTML = perfilAvisosHtml(r.avisos); }).catch(() => { });
  if (fsel) {
    fsel.dataset.prev = fsel.value;
    fsel.onchange = async () => {
      if (fsel.value !== '__pick') { fsel.dataset.prev = fsel.value; return; }
      fsel.value = fsel.dataset.prev || PERFIL_AUTO;
      const rel = await linkFolder();  // se vincula y pasa a ser la carpeta fija
      if (rel && document.body.contains(fsel)) {
        fsel.innerHTML = `<option value="${PERFIL_AUTO}">Automática · la última usada (o la del proyecto del tema)</option>` + folderOpts(rel);
        fsel.dataset.prev = rel;
      }
    };
  }
}
function collectPerfilSettings(d) {
  const f = $('#pfFolder', d), v = $('#pfVocab', d);
  if (!f || !v) return {};
  return { preset: $('#pfPreset', d).value,
    perfil: { folder: f.value === PERFIL_AUTO ? null : f.value, vocab: v.value === PERFIL_AUTO ? null : v.value,
      context: $('#pfContext', d).value.trim(), outputs: perfilOuts } };
}
