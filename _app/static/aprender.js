'use strict';
/* Aprender de tus correcciones (ítem 17, 09/11/2026; aprender.py en el servidor).
   1. Al terminar de corregir una transcripción («Listo», o al volver a abrirla), encima del texto: «¿Añadir «Orvalia» al
      vocabulario de Finanzas?». Sin ventanas ni foco robado: se contesta cuando se quiera, o «Ahora no».
   2. Con «Añadir», el término va al final de la lista elegida (la de la reunión por defecto; también en una carpeta
      conectada) y, si está marcado, Tenca cambia lo que escribió Whisper en las próximas reuniones de ese proyecto.
   3. Lo que cambie Tenca va subrayado en el texto, con una barra arriba para deshacerlo en esa reunión.
   Nada se aprende sin un clic, y nada sale del PC. */

S.apr = null;              // {path, data}: las propuestas de la reunión abierta (GET /api/aprender)
S.aprOculto = new Set();   // «Ahora no»: reuniones donde no se enseña hasta recargar la página
S.aprFilas = {};           // por término (normal): {estado: 'ok'|'error', msg, ...} tras contestar
S.aprDeshecho = null;      // {path, de, proyecto, no, olvidada}: tras «Deshacer», ¿quitarlo también del diccionario?
// por término (normal): la casilla «Y en las próximas reuniones…» desmarcada (false). Viene marcada: Bernardo quiere
// que lo que confirma se aplique también en las futuras (msg 1390), y la casilla dice exactamente qué va a pasar
S.aprCasillas = {};

const aprNorm = s => fold(s || '').split(/\s+/).filter(Boolean).join(' ');

/* al abrir una reunión (viewAudio, viewLive): lo contestado en otra visita no se arrastra */
function aprReset() { S.apr = null; S.aprFilas = {}; S.aprCasillas = {}; }

/* tras cargar la transcripción, al terminar de corregir y al volver a la reunión. avisar: al pulsar «Listo», si la
   tarjeta queda fuera de la vista (más arriba), un aviso que lleva a ella; nada más */
async function aprCargar(a, avisar = false) {
  const tx = S.tx;
  if (!a || !tx || tx.path !== a.path) return;
  if (!tx.has_backup || S.viewMode === 'live' || jobFor(a.path)) {
    if (S.apr && S.apr.path === a.path) { S.apr = null; aprPintar(); }
    return;
  }
  let r;
  try { r = await api('/api/aprender?path=' + enc(a.path)); } catch { return; }  // sin propuestas: no es un error que ver
  if (!S.tx || S.tx.path !== a.path) return;
  if (!S.apr || S.apr.path !== a.path) { S.aprFilas = {}; S.aprCasillas = {}; }
  if (S.aprDeshecho && S.aprDeshecho.path !== a.path) S.aprDeshecho = null;
  // la lista que elegiste se mantiene al recargar; si no había ninguna (la de la reunión no estaba), la de ahora
  S.apr = { path: a.path, data: r, lista: S.apr && S.apr.path === a.path && S.apr.lista != null ? S.apr.lista : r.lista };
  // la elegida ya no está: la de la reunión o, si tampoco está (r.lista null: sin conexión), ninguna hasta que elijas
  if (S.apr.lista && !r.listas.some(l => l.id === S.apr.lista)) S.apr.lista = r.lista;
  aprPintar();
  const card = $('#aprBox .apr-card');
  if (avisar && card && card.getBoundingClientRect().bottom < main.getBoundingClientRect().top + 60) {
    const n = $$('#aprBox .apr-fila:not(.hecha)').length;
    toast(`✦ ${n === 1 ? 'Una palabra que has corregido puede ir' : `${n} palabras que has corregido pueden ir`} a tu vocabulario · <b>Ver</b>`, '',
      () => { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); $('[data-act="apr-si"]', card)?.focus({ preventScroll: true }); });
  }
}

function aprLista(id) { return (S.apr?.data.listas || []).find(l => l.id === id) || null; }
function aprNombreLista(id) {
  const l = aprLista(id);
  return l ? l.name : 'general';
}
/* qué se puede hacer con una propuesta para la lista elegida: añadir (y quizá la regla), solo la regla, o nada */
function aprQue(p, lista) {
  if (lista == null) return { anadir: true, reglas: [] };  // la lista de la reunión no está: primero, elegir otra
  const ya = lista ? p.en.includes(lista) : p.en_general;
  // las reglas seguras que aún no tienes en el proyecto de esa lista ('' = las reuniones sin proyecto)
  const proy = fold(lista ? aprLista(lista)?.project || '' : '');
  const reglas = p.antes.filter(x => x.regla && !(x.reglas_en || []).some(pr => fold(pr) === proy));
  if (!ya) return { anadir: true, reglas };
  return reglas.length ? { anadir: false, reglas } : null;
}
const aprY = xs => xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' y ' + xs[xs.length - 1];

function aprCardHtml() {
  const ap = S.apr;
  if (!ap || S.editing || S.aprOculto.has(ap.path) || !S.tx || S.tx.path !== ap.path) return '';
  const lista = ap.lista, l = aprLista(lista);
  const filas = ap.data.propuestas.map((p, k) => ({ p, k, q: aprQue(p, lista), hecho: S.aprFilas[aprNorm(p.termino)] }))
    .filter(f => f.q || (f.hecho && f.hecho.estado === 'ok'));
  if (!filas.length) return '';
  const pend = filas.filter(f => !f.hecho || f.hecho.estado !== 'ok');
  const dest = lista == null ? '' : lista ? ` de <b>${esc(l.name)}</b>` : ' general';
  const proy = lista ? `de ${esc(l.project)}` : 'sin proyecto';
  const una = pend.length === 1;
  const tit = una && pend[0].q.anadir ? `¿Añadir «${esc(pend[0].p.termino)}» al vocabulario${dest}?`
    : una ? `¿Cambiar ${aprY(pend[0].q.reglas.map(x => `«${esc(x.texto)}»`))} por «${esc(pend[0].p.termino)}» en las próximas reuniones?`
      : pend.length ? `¿Añadir estas palabras al vocabulario${dest}?` : 'Listo';
  const sub = !pend.length ? 'Se nota desde la próxima reunión.'
    : pend.every(f => !f.q.anadir) ? `Ya ${una ? 'está' : 'están'} en la lista, pero Whisper no ${una ? 'lo' : 'las'} escribió bien.`
      : `${una ? 'Lo has' : 'Las has'} corregido tú. Así Whisper ${una ? 'lo' : 'las'} escribirá bien la próxima vez.`;
  const falta = lista == null && ap.data.falta;  // la lista de la reunión no se encuentra: se elige otra a mano
  const opts = (lista == null ? '<option value="-" selected disabled>Elige una lista…</option>' : '')
    + `<option value=""${lista === '' ? ' selected' : ''}>Sin proyecto (vocabulario general)</option>` + Object.entries(
    ap.data.listas.reduce((g, x) => ((g[x.project] ||= []).push(x), g), {})).map(([pr, xs]) =>
    `<optgroup label="${esc(pr)}">${xs.map(x => `<option value="${esc(x.id)}"${x.id === lista ? ' selected' : ''}>${esc(x.name)} · ${esc(pr)}</option>`).join('')}</optgroup>`).join('');
  const fila = ({ p, k, q, hecho }) => {
    const antes = p.antes.map(x => `«${esc(x.texto)}»${x.veces > 1 ? ` (${x.veces} veces)` : ''}`);
    if (hecho && hecho.estado === 'ok') {
      return `<li class="apr-fila hecha" data-k="${k}"><span class="apr-ok">${I.check}</span><div class="apr-txt">${hecho.html}</div></li>`;
    }
    const reglas = q.reglas.map(x => `«${esc(x.texto)}»`);
    const regla = q.reglas.length ? (q.anadir
      ? `<label class="apr-regla"><input type="checkbox" data-apr-regla="${k}"${(S.aprCasillas[aprNorm(p.termino)] ?? !(lista === '' && ap.data.proyecto_reunion)) ? ' checked' : ''}> Y en las próximas reuniones ${proy}, cambiar ${aprY(reglas)} por «${esc(p.termino)}» (se guarda en tu diccionario)</label>`
      : '') : '';
    const que = q.anadir ? `Whisper escribió ${aprY(antes)}`
      : `Ya está en la lista, pero Whisper escribió ${aprY(antes)}`;
    const si = q.anadir ? 'Añadir' : 'Cambiarlo';
    const err = hecho && hecho.estado === 'error' ? `<div class="apr-err" role="alert">${esc(hecho.msg)}</div>` : '';
    return `<li class="apr-fila" data-k="${k}"><div class="apr-txt"><b class="apr-term">${esc(p.termino)}</b> <span class="muted">${que}</span>${regla}${err}</div>
      <div class="apr-acc"><button class="btn sm primary" type="button" data-act="apr-si" data-k="${k}"${lista == null ? ' disabled title="Elige antes una lista (abajo)"' : ''}${q.anadir ? '' : ` title="${esc(`${APP_NAME} cambiará ${aprY(q.reglas.map(x => `«${x.texto}»`))} por «${p.termino}» en las próximas reuniones ${lista ? 'de ' + l.project : 'sin proyecto'} y te lo marcará`)}"`}>${si}</button>
      <button class="btn sm ghost" type="button" data-act="apr-no" data-k="${k}" title="No volver a proponerlo">No</button></div></li>`;
  };
  const conectada = l && l.conectada ? `<span class="muted"> · está en tu carpeta de listas: se añade allí</span>` : '';
  const aviso = falta ? `<div class="apr-falta" role="status">No encuentro la lista «${esc(falta.workstream)}»${falta.proyecto ? ` de ${esc(falta.proyecto)}` : ''} de esta reunión: ¿sin conexión, o la has movido? Elige otra o vuelve a probar cuando esté.</div>` : '';
  return `<div class="card apr-card" role="region" aria-label="Aprender de tus correcciones">
    <div class="apr-head"><span class="apr-ico" aria-hidden="true">✦</span><div class="apr-hd">
      <div class="apr-tit">${tit}</div>
      <div class="apr-sub">${sub}</div></div>
      <button class="btn sm ghost apr-x" type="button" data-act="apr-cerrar" title="${pend.length ? 'Ahora no: vuelve a salir la próxima vez que abras ' + esc(APP_NAME) : 'Cerrar'}">${pend.length ? 'Ahora no' : 'Cerrar'}</button></div>
    <ul class="apr-filas">${filas.map(fila).join('')}</ul>
    ${pend.length ? `<div class="apr-dest"><label for="aprLista">Lista</label><select id="aprLista">${opts}</select>${conectada}</div>${aviso}` : ''}
  </div>`;
}

/* lo que cambió Tenca en esta reunión: una barra con «Deshacer» por regla (las palabras van subrayadas en el texto) y,
   tras deshacer, la pregunta de si deja de hacerlo en las próximas */
function aprBarraHtml() {
  if (S.editing || S.viewMode === 'live' || !S.tx) return '';
  const d = S.aprDeshecho && S.aprDeshecho.path === S.tx.path ? S.aprDeshecho : null;
  const pregunta = !d ? '' : d.olvidada
    ? `<div class="apr-bar" role="status"><span class="apr-ico" aria-hidden="true">✦</span><span>Hecho: «${esc(d.de)}» ya no está en tu diccionario y no se cambia en las próximas reuniones.</span></div>`
    : `<div class="apr-bar" role="status"><span class="apr-ico" aria-hidden="true">✦</span><span>${aprDeshechoTxt(d.de, d.hechos, d.no)} ¿Quitarlo también de tu diccionario, para que no se cambie en las próximas reuniones?
      <button class="lnk" type="button" data-act="apr-olvidar">Sí, quitarlo</button></span>
      <button class="btn sm ghost apr-x" type="button" data-act="apr-olvidar-no" title="Se queda en tu diccionario">No</button></div>`;
  const ap = S.tx.aprendido;
  if (!ap || !ap.reglas.length) return pregunta;
  const r = ap.reglas.map(x => `<span class="apr-r">«${esc(x.de)}» → <b>${esc(x.a)}</b>${x.veces > 1 ? ` <span class="muted">(${x.veces})</span>` : ''}
    <button class="lnk" type="button" data-act="apr-deshacer" data-de="${esc(x.de)}" data-a="${esc(x.a)}" title="Volver a poner lo que escribió Whisper en esta reunión">Deshacer</button></span>`).join('<span class="muted"> · </span>');
  return pregunta + `<div class="apr-bar" role="note"><span class="apr-ico" aria-hidden="true">✦</span><span>Corregido con tu diccionario personal (va subrayado): ${r}</span></div>`;
}

function aprPintar() {
  const box = $('#aprBox');
  if (!box) return;
  const html = aprBarraHtml() + aprCardHtml();
  if (box._html === html) return;
  // lo que está debajo no salta: si la tarjeta aparece o cambia de alto estando el texto a la vista, se compensa
  const antes = box.offsetHeight, foco = document.activeElement && box.contains(document.activeElement) ? document.activeElement : null;
  const fk = foco && (foco.dataset.act || foco.id) ? [foco.dataset.act, foco.dataset.k, foco.id] : null;
  box.innerHTML = html; box._html = html;
  if (main.scrollTop > box.offsetTop + antes) main.scrollTop += box.offsetHeight - antes;
  if (fk) {  // el foco no se pierde al repintar (teclado): al mismo botón o, si ya no está, a la siguiente fila
    const otro = fk[2] ? $('#' + fk[2], box) : $(`[data-act="${fk[0]}"][data-k="${fk[1]}"]`, box);
    (otro || $('[data-act="apr-si"]', box) || $('[data-act="apr-cerrar"]', box) || $('#txBody'))?.focus({ preventScroll: true });
  }
}

/* marcas de lo que cambió Tenca en una frase: [{pos, len, de}] */
function aprMarcas(seg) {
  const ap = S.tx?.aprendido;
  if (!ap || S.editing || S.viewMode === 'live' || seg.start == null) return null;
  const m = ap.marcas.filter(x => x.start === seg.start && seg.text.slice(x.pos, x.pos + x.len) === x.a);
  return m.length ? m.sort((a, b) => a.pos - b.pos) : null;
}
/* el texto de una frase con lo buscado resaltado (hlTerms) y lo que cambió Tenca subrayado */
function aprHl(seg, terms) {
  const ms = aprMarcas(seg), text = seg.text;
  if (!ms) return hlTerms(text, terms);
  // lo buscado, como hlTerms (rangos sobre el texto entero, juntos si se tocan): cada uno es un solo <mark> aunque
  // cruce un subrayado, así el contador y «siguiente» de la búsqueda no cambian
  const f = foldMap(text), rs = [];
  for (const t of terms || []) { let k = 0; while ((k = f.indexOf(t, k)) !== -1) { rs.push([k, k + t.length]); k += t.length; } }
  rs.sort((a, b) => a[0] - b[0]);
  const marks = [];
  for (const r of rs) { const l = marks[marks.length - 1]; if (l && r[0] <= l[1]) l[1] = Math.max(l[1], r[1]); else marks.push([...r]); }
  const apr = (a, b) => {  // el trozo [a, b) con lo que cambió Tenca subrayado
    let out = '', i = a;
    for (const m of ms) {
      const s = Math.max(m.pos, i), e = Math.min(m.pos + m.len, b);
      if (e <= s) continue;
      out += esc(text.slice(i, s)) + `<span class="apr-w" title="Lo cambió ${esc(APP_NAME)}: Whisper escribió «${esc(m.de)}»">${esc(text.slice(s, e))}</span>`;
      i = e;
    }
    return out + esc(text.slice(i, b));
  };
  let out = '', i = 0;
  for (const [a, b] of marks) { out += apr(i, a) + '<mark>' + apr(a, b) + '</mark>'; i = b; }
  return out + apr(i, text.length);
}

async function aprSi(k) {
  const ap = S.apr, p = ap?.data.propuestas[k];
  if (!p) return;
  const q = aprQue(p, ap.lista);
  if (!q || ap.lista == null) return;
  const caja = $(`#aprBox [data-apr-regla="${k}"]`);
  const reglas = q.reglas.length && (!q.anadir || !caja || caja.checked) ? q.reglas.map(x => x.texto) : [];
  const btns = $$(`#aprBox .apr-fila[data-k="${k}"] button`);
  btns.forEach(b => { b.disabled = true; });
  const path = ap.path, lista = ap.lista, nombre = aprNombreLista(lista), l = aprLista(lista);
  try {
    const r = await api('/api/aprender/anadir', { path, termino: p.termino, lista, reglas });
    if (!S.apr || S.apr.path !== path) return;
    if (r.prompt != null && S.config) S.config.prompt = r.prompt;  // el vocabulario general, al día en Ajustes
    const donde = lista ? `${esc(nombre)} <span class="muted">(${esc(l.project)})</span>` : 'el vocabulario general';
    const cambia = reglas.length ? ` En tu diccionario: ${esc(aprY(reglas.map(x => `«${x}»`)))} → «${esc(p.termino)}».` : '';
    let html = q.anadir ? `«${esc(p.termino)}» ${r.ya_estaba ? 'ya estaba en' : 'añadido a'} ${donde}.${cambia}`
      : `«${esc(p.termino)}»:${cambia}`;
    if (!r.cabe) html += ` <span class="apr-aviso">Ojo: la lista ya no le cabe entera a Whisper y «${esc(p.termino)}» se queda fuera. Quita términos que ya no uses.</span>`;
    if (lista) html += ` <button class="lnk" type="button" data-act="apr-ver" data-id="${esc(lista)}">Ver la lista</button>`;
    S.aprFilas[aprNorm(p.termino)] = { estado: 'ok', html };
    p.en = lista ? [...p.en, lista] : p.en; if (!lista) p.en_general = true;
    p.antes.forEach(x => { x.regla = false; });
    if (typeof loadVocab === 'function') loadVocab(S.config?.language ?? 'es-en');
  } catch (err) {
    S.aprFilas[aprNorm(p.termino)] = { estado: 'error', msg: err.message };
  }
  aprPintar();
}

async function aprNo(k) {
  const ap = S.apr, p = ap?.data.propuestas[k];
  if (!p) return;
  try {
    await api('/api/aprender/no', { termino: p.termino });
    ap.data.propuestas = ap.data.propuestas.filter(x => x !== p);
    aprPintar();
  } catch (err) { toast(esc(err.message), 'err'); }
}

async function aprDeshacer(de, a) {
  const tx = S.tx;
  if (!tx) return;
  const path = tx.path;
  try {
    const r = await api('/api/aprender/deshacer', { path, de, a });
    S.aprDeshecho = r.regla ? { path, de, proyecto: r.regla.proyecto, hechos: r.deshechos, no: r.no_deshechos } : null;
    const au = S.audios.get(path);
    S.tx = null;
    if (au) await loadTranscript(au);
    if (!r.regla) toast(aprDeshechoTxt(de, r.deshechos, r.no_deshechos));
    $('#aprBox [data-act="apr-olvidar"]')?.focus({ preventScroll: true });
  } catch (err) { toast(esc(err.message), 'err'); }
}

function aprDeshechoTxt(de, hechos, no) {
  if (!hechos) return `No había nada que deshacer: has cambiado esas frases después.`;
  return `Deshecho: aquí vuelve a estar «${esc(de)}»${no ? ` (salvo en ${no === 1 ? 'una frase' : `${no} frases`} que has cambiado tú)` : ''}.`;
}

async function aprOlvidar() {
  const d = S.aprDeshecho;
  if (!d) return;
  try {
    await api('/api/aprender/olvidar', { de: d.de, proyecto: d.proyecto });
    d.olvidada = true;
    aprPintar();
  } catch (err) { toast(esc(err.message), 'err'); }
}

main.addEventListener('click', async e => {
  const act = e.target.closest('[data-act^="apr-"]');
  if (!act || !main.contains(act)) return;
  const a = act.dataset.act, k = +act.dataset.k;
  if (a === 'apr-si') aprSi(k);
  else if (a === 'apr-no') aprNo(k);
  else if (a === 'apr-cerrar') {
    if (S.apr) S.aprOculto.add(S.apr.path);
    aprPintar();
    $('#tq')?.focus({ preventScroll: true });
  } else if (a === 'apr-deshacer') aprDeshacer(act.dataset.de, act.dataset.a);
  else if (a === 'apr-olvidar') aprOlvidar();
  else if (a === 'apr-olvidar-no') { S.aprDeshecho = null; aprPintar(); $('#tq')?.focus({ preventScroll: true }); }
  else if (a === 'apr-ver') {
    if (typeof loadVocab === 'function') await loadVocab(S.config?.language ?? 'es-en');
    const v = S.vocab.find(x => x.id === act.dataset.id);
    if (v && typeof openVocabEditor === 'function') openVocabEditor(v, () => closeModal());
    else toast('Esa lista ya no está.', 'err');
  }
});
/* la barra espaciadora reproduce o pausa en toda la página (app.js): en un botón o una casilla de la tarjeta, que haga
   lo suyo */
main.addEventListener('keydown', e => {
  if (e.key === ' ' && e.target.closest?.('#aprBox') && /^(BUTTON|INPUT|SELECT)$/.test(e.target.tagName)) e.stopPropagation();
});
main.addEventListener('change', e => {
  const c = e.target.closest?.('[data-apr-regla]');
  if (c && S.apr) {  // que no se pierda al añadir otra fila o cambiar de lista (la tarjeta se repinta)
    const p = S.apr.data.propuestas[+c.dataset.aprRegla];
    if (p) S.aprCasillas[aprNorm(p.termino)] = c.checked;
    return;
  }
  if (e.target.id !== 'aprLista' || !S.apr) return;
  S.apr.lista = e.target.value;
  aprPintar();
});

/* ---------------------------------------------------------------- Ajustes › Proyectos */
async function aprAjustes(d) {
  const box = $('#sAprendido', d);
  if (!box) return;
  let edita = -1, error = '';  // la entrada que se está cambiando (su fila pasa a campos) y su error
  const proyectos = actual => [...new Set([...(S.vocab || []).map(v => v.project), actual].filter(Boolean))];
  const fila = (r, i) => i !== edita
    ? `<tr><td>«${esc(r.de)}» → ${esc(r.a)}</td><td class="muted">${r.proyecto ? esc(r.proyecto) : 'Sin proyecto'}</td>
        <td class="apr-dic-acc"><button class="btn ghost sm" type="button" data-apr-editar="${i}">cambiar</button><button class="btn ghost sm" type="button" data-apr-olvidar="${i}">quitar</button></td></tr>`
    : `<tr class="apr-dic-ed"><td colspan="3"><div class="apr-dic-campos">
        <label>Whisper escribe<input class="input" id="aprDicDe" value="${esc(r.de)}" autocomplete="off" spellcheck="false"></label>
        <label>Lo correcto<input class="input" id="aprDicA" value="${esc(r.a)}" autocomplete="off" spellcheck="false"></label>
        <label>En las reuniones de<select id="aprDicP"><option value=""${r.proyecto ? '' : ' selected'}>Sin proyecto</option>${proyectos(r.proyecto).map(p => `<option value="${esc(p)}"${fold(p) === fold(r.proyecto) ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
        <span class="apr-dic-bt"><button class="btn sm primary" type="button" data-apr-guardar="${i}">Guardar</button><button class="btn sm ghost" type="button" data-apr-cancelar>Cancelar</button></span></div>
        ${error ? `<div class="apr-err" role="alert">${esc(error)}</div>` : ''}</td></tr>`;
  const pintar = ({ reglas = box.reglas || [], no = box.no || [] } = {}) => {
    box.reglas = reglas; box.no = no;
    box.innerHTML = (reglas.length ? `<label>Tu diccionario personal <span class="muted" style="font-weight:400">· lo que ${esc(APP_NAME)} corrige al transcribir</span></label>
      <table class="vtable apr-dic">${reglas.map(fila).join('')}</table>
      <div class="help">Cada entrada cambia lo que escribió Whisper por lo correcto en las próximas reuniones de ese proyecto, solo en frases que nadie ha tocado. Lo cambiado va subrayado y cada reunión tiene su «Deshacer». El audio no se toca, y lo que escribió Whisper se guarda aparte («Restaurar original»). Quitar o cambiar una entrada no deshace lo ya cambiado.</div>`
      : `<label>Tu diccionario personal</label><div class="help" style="margin-top:0">Aún está vacío. Se llena cuando corriges un nombre o una sigla en una transcripción y le dices que sí: desde entonces, ${esc(APP_NAME)} lo cambia solo en las próximas reuniones de ese proyecto, y aquí podrás cambiarlo o quitarlo.</div>`)
      + (no.length ? `<div class="help apr-no-lista">No te propone añadir: ${no.map((x, i) => `<span class="apr-r">«${esc(x)}» <button class="lnk" type="button" data-apr-sino="${i}" title="Que vuelva a proponerlo si lo corriges">quitar</button></span>`).join('<span class="muted"> · </span>')}</div>` : '');
  };
  try { pintar(await api('/api/aprender/reglas')); } catch { box.innerHTML = ''; }
  box.addEventListener('keydown', e => {  // Intro guarda, Escape cancela (sin cerrar Ajustes)
    if (edita < 0 || !e.target.closest('.apr-dic-ed')) return;
    if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); $('[data-apr-guardar]', box)?.click(); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); $('[data-apr-cancelar]', box)?.click(); }
  });
  box.addEventListener('click', async e => {
    const ed = e.target.closest('[data-apr-editar]'), gu = e.target.closest('[data-apr-guardar]');
    if (ed) { edita = +ed.dataset.aprEditar; error = ''; pintar(); $('#aprDicA', box)?.focus(); return; }
    if (e.target.closest('[data-apr-cancelar]')) {
      const i = edita; edita = -1; error = ''; pintar();
      $(`[data-apr-editar="${i}"]`, box)?.focus();
      return;
    }
    if (gu) {
      const r = box.reglas[+gu.dataset.aprGuardar];
      const nuevo = { de: $('#aprDicDe', box).value, a: $('#aprDicA', box).value, proyecto: $('#aprDicP', box).value };
      try {
        const res = await api('/api/aprender/regla', { antes: { de: r.de, proyecto: r.proyecto }, ...nuevo });
        edita = -1; error = '';
        pintar(res);
        const j = box.reglas.findIndex(x => x.de === nuevo.de.trim() && fold(x.proyecto) === fold(nuevo.proyecto));
        $(`[data-apr-editar="${j}"]`, box)?.focus();
      } catch (err) {
        error = err.message; pintar();
        $('#aprDicDe', box).value = nuevo.de; $('#aprDicA', box).value = nuevo.a; $('#aprDicP', box).value = nuevo.proyecto;
        $('#aprDicA', box)?.focus();
      }
      return;
    }
    const b = e.target.closest('[data-apr-olvidar], [data-apr-sino]');
    if (!b) return;
    const body = b.dataset.aprSino != null ? { no: box.no[+b.dataset.aprSino] }
      : { de: box.reglas[+b.dataset.aprOlvidar].de, proyecto: box.reglas[+b.dataset.aprOlvidar].proyecto };
    try {
      edita = -1; error = '';
      pintar(await api('/api/aprender/olvidar', body));
      ($('[data-apr-olvidar], [data-apr-sino]', box) || $('#sPrompt', d))?.focus();
    } catch (err) { toast(esc(err.message), 'err'); }
  });
}
