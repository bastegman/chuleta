'use strict';
/* Perfil de reunión: idioma y contexto de cada reunión (página de la reunión y directo, que los relee en el siguiente
   trozo), lo de Ajustes (carpeta, contexto y destinos de salida) y la importación de listas de vocabulario de otra
   carpeta. Lo que se usa al grabar ya no se calcula aquí: lo decide el servidor (/api/grabar/plan, grabar.js), el mismo
   cálculo que Ctrl+Alt+R (antes la regla estaba aquí y en el servidor, dos veces).
   Se carga después de app.js y notas.js y usa sus utilidades ($, $$, api, esc, I, S, LANGS, langSelect, folderOpts,
   liveFit, toast, loadVocab, refreshTree, linkFolder, openSettings). app.js y notas.js lo invocan con
   `typeof x === 'function'`: sin este fichero la app funciona igual, sin perfil. */

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

/* ---------------------------------------------------------------- listas por proyecto */
function perfilGroups(vocab) {
  const g = new Map();
  (vocab || []).forEach(v => { if (!g.has(v.project)) g.set(v.project, []); g.get(v.project).push(v); });
  return [...g.entries()];
}
function perfilVocabEmptyHtml() {
  return `<div class="help" style="margin-top:0">Todavía no hay listas. Cada lista es un archivo de texto <code>&lt;Proyecto&gt;\\Vocabulario\\&lt;Tema&gt;.txt</code> en la carpeta de la app (junto a <code>_app</code>) o en una carpeta vinculada, con los nombres, siglas y productos de esas reuniones separados por comas, los más importantes primero.</div>`;
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

/* ---------------------------------------------------------------- Ajustes (rediseño B2, 07/11/2026)
   El «Perfil de reunión» de antes era un bloque de 466 palabras con grabar, resumir, motores y destinos mezclados.
   Ahora cada pieza va a su sección: las reglas de grabar a «Al grabar» (ajustes.js), los motores a «Procesado», el
   idioma y las instrucciones del resumen a «Resumen» y los destinos de salida a «Proyectos». Los ids no cambian (los
   lee collect() de app.js y collectPerfilSettings). */
const perfilOpt = (v, l, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
/* Opciones de «Reuniones sin proyecto» y de «Proyecto al grabar», con lo guardado aunque ahora no salga. */
function perfilFolderOpts(sel) {
  const hay = sel === PERFIL_AUTO || flatFolders().some(f => f.path === sel);
  return perfilOpt(PERFIL_AUTO, 'Automática · la última usada', sel)
    + (hay ? '' : `<option value="${esc(sel)}" selected>${esc(perfilCarpetaTxt(sel))}${S.tree ? ' (no se encuentra)' : ''}</option>`)
    + folderOpts(sel);
}
function perfilVocabOpts(sel) {
  const vocab = S.vocab || [], hay = sel === PERFIL_AUTO || sel === '' || vocab.some(v => v.id === sel);
  const falta = S.vocabError ? 'no se han podido leer las listas' : 'no se encuentra';
  return perfilOpt(PERFIL_AUTO, 'Automático · con su motivo', sel) + perfilOpt('', 'Sin proyecto', sel)
    + (hay ? '' : `<option value="${esc(sel)}" selected>${esc(perfilListaTxt(sel))} (${falta})</option>`)
    + perfilGroups(vocab).map(([pr, vs]) => `<optgroup label="${esc(pr)}">${vs.map(v => perfilOpt(v.id, `${pr} › ${v.name}`, sel)).join('')}</optgroup>`).join('');
}
/* Una carpeta guardada, en palabras: la vinculada por su nombre y el resto de la ruta con «\». */
function perfilCarpetaTxt(rel) {
  const l = (S.config?.links || []).find(x => rel === x.rel || rel.startsWith(x.rel + '/'));
  const resto = l ? rel.slice(l.rel.length) : rel;
  return (l ? nfc(l.name) : '') + resto.replace(/\//g, '\\');
}
/* Una lista guardada que ya no sale entre las listas, en palabras: «Proyecto › Workstream». */
function perfilListaTxt(id) {
  const partes = String(id).replace(/^:v[^/]*\//, '').split('/');
  const n = (partes.pop() || '').replace(/\.txt$/i, '');
  return partes.length ? `${partes[0]} › ${n}` : n;
}
/* Al grabar: reuniones sin proyecto, idioma por defecto, proyecto si no se elige y contexto. Filas de una línea.
   Una carpeta o una lista guardadas que ahora no salen (una vinculada desconectada, OneDrive sin conexión, las listas
   sin poder leerse) se enseñan tal cual y elegidas: si no, el desplegable enseñaba «Automática» y cualquier «Guardar»
   las borraba sin avisar (revisión 2 de B2, P1-3). */
function perfilReglasHtml() {
  const c = S.config || {}, p = c.perfil || {};
  const folderSel = p.folder == null ? PERFIL_AUTO : p.folder, vocabSel = p.vocab == null ? PERFIL_AUTO : p.vocab;
  return `<div class="ag-regla"><label for="pfFolder">Reuniones sin proyecto<small class="ag-sub">y de proyectos sin carpeta propia</small></label><div><select id="pfFolder">${perfilFolderOpts(folderSel)}</select></div></div>
    <div class="ag-regla"><label for="sLang">Idioma por defecto<small class="ag-sub">si el proyecto no tiene el suyo</small></label><div>${langSelect('sLang', c.language ?? 'es-en')}</div></div>
    <div class="ag-regla"><label for="pfVocab">Proyecto al grabar<small class="ag-sub">la reunión, su título o la última</small></label><div><select id="pfVocab">${perfilVocabOpts(vocabSel)}</select></div></div>
    <div class="ag-regla"><label for="pfContext">Contexto</label><div><input class="input" id="pfContext" maxlength="500" value="${esc(p.context || '')}" placeholder="Opcional: de qué van tus reuniones y quién participa" autocomplete="off"></div></div>`;
}
/* Procesado: con qué motor empieza el directo y con cuál se transcribe después. */
function perfilMotoresHtml() {
  const c = S.config || {}, presets = c.presets || [];
  const prov = c.providers?.[c.cloud?.provider]?.label || 'nube';
  return `<fieldset><legend>${I.monitor}Motores</legend>
      <div class="grid2">
        <div><label for="lPreset">Motor del directo</label><select id="lPreset">${presets.map(pr => perfilOpt(pr.id, `${pr.live_label} · ~${String(pr.live_speed).replace('.', ',')}× · ${liveFit(pr)[1]}`, c.live_preset || 'medium')).join('')}</select></div>
        <div><label for="pfPreset">Motor al transcribir después</label><select id="pfPreset">${presets.map(pr => perfilOpt(pr.id, `${pr.label} · ${pr.engine === 'cloud' ? prov : pr.model}`, c.preset)).join('')}</select></div>
      </div>
      <div class="help">El de después es el de «Solo grabar», «Volver a transcribir» y los audios que arrastres. El del directo se puede cambiar en mitad de la grabación desde su pantalla.</div>
    </fieldset>`;
}
/* Resumen: en qué idioma y con qué instrucciones (quién resume se elige en Procesado). */
function perfilResumenHtml() {
  const c = S.config || {};
  return `<fieldset><legend>${I.spark}Resumen</legend>
      <label for="sSumLang">Idioma del resumen</label><select id="sSumLang">${[['es', 'Español'], ['en', 'Inglés'], ['same', 'El de la reunión']].map(([v, l]) => perfilOpt(v, l, c.summary?.lang || 'es')).join('')}</select>
      <label for="sExtra">Instrucciones extra para el resumen <span class="muted" style="font-weight:400">· opcional</span></label><textarea id="sExtra" placeholder="p. ej. Céntrate en riesgos, dependencias y fechas">${esc(c.summary?.extra || '')}</textarea>
      <div class="help">Quién resume y si lo hace solo al terminar se elige en <a href="#" data-sec-ir="procesado">Procesado</a>.</div>
    </fieldset>`;
}
/* Proyectos: copias de cada reunión, como documento, en otras carpetas (los «destinos de salida»). */
function perfilSalidasHtml() {
  const p = (S.config || {}).perfil || {};
  return `<fieldset><legend>${I.copy}Copias en otras carpetas</legend>
      <div class="help" style="margin-top:4px">Opcional: cada reunión, como documento, en un vault de Obsidian, una biblioteca de SharePoint sincronizada o la carpeta de documentos del proyecto.</div>
      <div id="pfOuts">${perfilOutsHtml(p.outputs || [])}</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" type="button" id="pfOutAdd">${I.plus}Añadir carpeta…</button></div>
      <div id="pfAvisos"></div>
      <details class="ag-como"><summary>¿Cómo funciona?</summary><div class="help">Una carpeta fuera de ${esc(APP_NAME)}: no vale la carpeta de ${esc(APP_NAME)}, una carpeta vinculada ni una con grabaciones: allí cada documento chocaría con las notas de su reunión. Al terminar la transcripción y el resumen, y al guardar cambios en las notas o en la transcripción, cada reunión se escribe allí como <code>&lt;fecha&gt; - &lt;nombre&gt;.md</code> (un documento con propiedades, resumen, notas y transcripción con minutos), <code>.txt</code> o <code>.docx</code>; si ese nombre ya lo usa otra reunión o un fichero tuyo, como <code>&lt;fecha&gt; - &lt;nombre&gt; (código).md</code>. El audio y los originales se quedan donde se graban: nunca se mueve nada, y solo se sobrescriben los documentos que generó ${esc(APP_NAME)} en este PC; uno ajeno, uno nuestro que hayas cambiado allí o uno de otro PC que no coincida se deja como está (abajo verás el aviso). Al borrar una reunión, sus documentos que nadie haya cambiado van también a la papelera.</div></details>
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
  const draw = () => { box.innerHTML = perfilOutsHtml(perfilOuts); d.ajMarcar?.(); };  // (también tras elegir carpeta)
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
  return `<div class="notice" style="margin-top:8px">Copias que no se han podido escribir:<ul style="margin:6px 0 0 18px;padding:0">${avisos.slice(0, 8).map(a =>
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
        fsel.innerHTML = `<option value="${PERFIL_AUTO}">Automática · la última usada</option>` + folderOpts(rel);
        fsel.dataset.prev = rel;
        d.ajMarcar?.();  // es un cambio sin guardar como otro (revisión 8: no salía «Hay cambios sin guardar»)
        d.ajDibujarEnlaces?.();  // y ya es una carpeta vinculada: sale en su lista, con «quitar»
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
