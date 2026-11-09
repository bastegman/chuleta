'use strict';
/* «Subir audio › Desde el móvil» en el PC (09/11/2026; la lámina que validó Bernardo, msg 1390). «Subir audio» abre un
   menú con «Desde este PC» y «Desde el móvil». Con el móvil, la tarjeta de subir (grabar.js) sin archivos: eliges
   carpeta, proyecto e idioma y «Mostrar el código» enseña un QR. El móvil lo escanea, abre una página de Tenca por la
   wifi (movil.py) y manda las grabaciones, que entran como las subidas desde el PC y se transcriben. El código vale
   10 minutos, para un solo móvil, y solo en una red que Windows tiene como privada. Al cerrar la tarjeta, deja de
   valer. */

const MV_I = {
  movil: '<svg viewBox="0 0 24 24"><rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>',
  pc: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  wifi: '<svg viewBox="0 0 24 24"><path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.2" r=".9"/></svg>',
  escudo: '<svg viewBox="0 0 24 24"><path d="M12 3 4.5 6v5.5c0 4.6 3.1 8.4 7.5 9.5 4.4-1.1 7.5-4.9 7.5-9.5V6z"/></svg>',
  reloj: '<svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2.5 1.5M9.5 2.5h5"/></svg>',
};

/* ---------------------------------------------------------------- el menú de «Subir audio» */
function subirMenu(btn, opts = {}) {
  cerrarSubirMenu();
  const m = document.createElement('div');
  m.className = 'sb-menu';
  m.setAttribute('role', 'menu');
  m.innerHTML = `<button type="button" role="menuitem" data-sbm="pc">${MV_I.pc}<span><b>Desde este PC</b><small>Un audio que ya está en el ordenador</small></span></button>
    <button type="button" role="menuitem" data-sbm="movil">${MV_I.movil}<span><b>Desde el móvil</b><small>Con un código QR, por tu wifi</small></span></button>`;
  document.body.append(m);
  const r = btn.getBoundingClientRect();
  m.style.top = `${Math.round(r.bottom + 6)}px`;
  m.style.left = `${Math.round(Math.max(8, Math.min(r.left, innerWidth - m.offsetWidth - 8)))}px`;
  btn.setAttribute('aria-expanded', 'true');
  const items = [...m.querySelectorAll('[role="menuitem"]')];
  items[0].focus();
  const fuera = e => { if (!m.contains(e.target) && e.target !== btn && !btn.contains(e.target)) cerrarSubirMenu(); };
  m.addEventListener('keydown', e => {
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrarSubirMenu(); btn.focus(); }
    else if (e.key === 'Tab') cerrarSubirMenu();
  });
  m.addEventListener('click', e => {
    const b = e.target.closest('[data-sbm]');
    if (!b) return;
    cerrarSubirMenu();
    if (b.dataset.sbm === 'pc') elegirAudios(opts);
    else abrirSubir(null, { movil: true, carpeta: opts.carpeta || null });
  });
  setTimeout(() => document.addEventListener('mousedown', fuera, true), 0);
  S.sbMenu = { m, btn, fuera };
}
function cerrarSubirMenu() {
  const x = S.sbMenu;
  if (!x) return;
  S.sbMenu = null;
  x.m.remove();
  x.btn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('mousedown', x.fuera, true);
}

/* ---------------------------------------------------------------- la tarjeta «Recibir del móvil» */
function abrirMovilCard(g) {
  openModal(`<div class="gr-cab"><span class="gr-kicker">${MV_I.movil}Recibir del móvil</span></div>
    <div class="gr-cuerpo">
      <div class="sb-error hidden" id="sbError" role="alert"></div>
      <div class="gr-top">
        <label for="grProy">Proyecto</label>
        <div class="gr-proyecto"><select id="grProy" class="gr-proy" title="Proyecto › workstream: su carpeta, su idioma y su vocabulario"></select>
          <span class="gr-motivo" id="grMotivo"></span></div>
      </div>
      <div class="gr-plan" aria-live="polite"><div class="gr-plan-h">Así se van a guardar</div>
        <div id="grFilas"><div class="gr-cargando">Preparando…</div></div></div>
      <button type="button" class="gr-anadir hidden" id="grAnadirCtx" data-gr-cambiar="contexto">${I.plus}Contexto: de qué va y quién participa</button>
      <div class="gr-atajo">${I.info}Lo que mandes desde el móvil entra aquí con su fecha y se transcribe solo. El móvil tiene que estar en tu misma wifi.</div>
      <div class="mv" id="mvPanel" hidden></div>
    </div>
    <div class="btns gr-pie"><span class="gr-hueco"></span>
      <button class="btn" data-m="no" type="button">Cancelar</button>
      <button class="btn primary" id="rGo" type="button" disabled>${MV_I.movil}<span>Mostrar el código</span> <kbd>Intro</kbd></button>
      <button class="btn mv-solo" type="button" data-mv="parar" hidden>Dejar de recibir</button>
      <button class="btn primary mv-solo" type="button" data-mv="listo" hidden>Listo</button></div>`, d => grMontar(d), 'gr sb mvd');
  // cerrarla (Listo, Esc, un clic fuera): el código deja de valer; lo que esté llegando termina y se guarda
  S.modalCleanup = () => {
    const sub = g.subir;
    if (G === g) G = null;
    clearTimeout(sub.sondeo);
    sub.cerrada = true;
    if (!sub.sesion) return;
    api('/api/movil/cerrar', { url: sub.ultimo?.url || undefined }).then(r => {
      const llegando = (r.recibidos || []).filter(x => x.estado === 'llegando');
      if (llegando.length) toast(`«${esc(llegando[0].archivo)}» sigue llegando: se guardará al terminar.`);
      if ((r.recibidos || []).some(x => x.path)) { refreshTree(true); pollJobs(true); }
    }).catch(() => { });
  };
}

async function mvMontar(d) {
  const g = G;
  // la red, a la vez que el plan: si no es una red privada de Windows, se dice aquí y no se enseña el código
  const red = api('/api/movil/red').catch(err => ({ ok: false, texto: err.message }));
  const etiqueta = $('#rGo span', d);
  etiqueta.textContent = 'Mirando la wifi…';  // PowerShell, en frío, puede tardar unos segundos
  await voc0();
  if (G !== g) return;
  await grPedir({ recientes: true });
  if (G !== g) return;
  const r = await red;
  if (G !== g) return;
  g.subir.red = r;
  etiqueta.textContent = 'Mostrar el código';
  const b = $('#rGo', d);
  if (!r.ok) {
    mvError(r.texto || 'No se pueden recibir audios del móvil ahora.');
    b.disabled = true;
    b.title = 'Solo en una wifi privada (la de casa)';
    $('[data-m="no"]', d)?.focus();
    return;
  }
  b.disabled = false;
  b.focus();
  // de vuelta de «Nuevo proyecto…» o «Editar» con un código abierto: cómo está ahora, y «Volver al código»
  if (g.subir.sesion) {
    try { mvPintar(g, await api('/api/movil/estado')); } catch { }
    if (G !== g) return;
    mvVer(g, false);
    if (g.subir.activa) mvSondear(g);
  }
}

function mvError(texto) {
  const e = $('#sbError', G?.d);
  if (!e) return;
  e.textContent = texto;
  e.classList.toggle('hidden', !texto);
}

/* «Mostrar el código» (y «Volver al código» tras «Cambiar»): un código nuevo, o el mismo con lo elegido ahora */
async function mvMostrar() {
  const g = G, b = g && $('#rGo', g.d);
  if (!g || !b || b.disabled || g.subir.abriendo) return;  // un doble clic no abre dos códigos
  b.disabled = true;
  g.subir.abriendo = true;
  if (g.timer) { clearTimeout(g.timer); g.timer = null; grPedir(); }
  await g.pendiente;  // lo último pedido: con lo que se está viendo
  const p = g.plan;
  if (!p || G !== g) { b.disabled = false; g.subir.abriendo = false; return; }
  const datos = { folder: p.rec.folder || '', vocab: p.rec.vocab || '', language: p.rec.language || '',
    contexto: p.rec.contexto || '', preset: p.rec.preset || '' };
  const sub = g.subir;
  try {
    const nuevo = !(sub.sesion && sub.activa);
    const r = nuevo ? await api('/api/movil/abrir', datos) : await api('/api/movil/datos', datos);
    sub.abriendo = false;
    // cerrada (Esc) mientras se abría: ese código (y solo ese) no lo va a ver nadie, fuera
    if (sub.cerrada || G !== g) { if (nuevo && r.activa) api('/api/movil/cerrar', { url: r.url }).catch(() => { }); return; }
    sub.gen = (sub.gen || 0) + 1;  // un sondeo pedido antes ya no pinta (sería del código de antes)
    if (r.ok === false) { mvError(r.texto); b.disabled = false; return; }
    mvError('');
    if (r.qr) sub.qr = r.qr;
    if (nuevo) sub.vistos = new Set();  // los del código nuevo empiezan otra vez en 1
    sub.sesion = true;
    sub.destino = mvDestino(p);
    mvVer(g, true);
    mvPintar(g, r);
    mvSondear(g);
  } catch (err) {
    sub.abriendo = false;
    if (G === g) { mvError(err.message); b.disabled = false; }
  }
}

/* «Se guardan en Alfa › Comité · Español + inglés» */
function mvDestino(p) {
  const by = Object.fromEntries((p.lineas || []).map(l => [l.clave, l]));
  const donde = p.proyecto?.vocab ? p.proyecto.texto : by.carpeta?.valor || '';
  return [donde, by.idioma?.valor].filter(Boolean).join(' · ');
}

/* la vista del código (on) o la del plan, para «Cambiar» */
function mvVer(g, on) {
  const d = g.d;
  d.classList.toggle('mv-on', on);
  $('#mvPanel', d).hidden = !on;
  $$('.mv-solo', d).forEach(x => { x.hidden = !on; });
  for (const sel of ['.gr-top', '.gr-plan', '.gr-atajo', '[data-m="no"]', '#rGo']) {
    const x = $(sel, d);
    if (x) x.hidden = on;
  }
  const ctx = $('#grAnadirCtx', d);
  if (ctx && on) ctx.hidden = true;
  else if (ctx) ctx.hidden = false;
  $('#rGo span', d).textContent = g.subir.sesion && g.subir.activa ? 'Volver al código' : 'Mostrar el código';
  if (!on) { $('#rGo', d).disabled = false; $('#grProy', d)?.focus(); }
}

const MV_ESTADO = {
  llegando: x => `Llegando · ${x.pct} %`, en_cola: () => 'En la cola', guardado: () => 'Guardado',
  error: x => x.msg || 'No se ha podido guardar',
};

function mvPintar(g, r) {
  const sub = g.subir, panel = $('#mvPanel', g.d);
  if (!panel) return;
  sub.activa = !!r.activa;
  sub.ultimo = r;
  const rs = r.recibidos || [];
  const lista = rs.length ? `<ul class="mv-lista" role="list">${rs.slice().reverse().map(x => `<li class="mv-item ${x.estado}">
      <span class="mv-ic">${x.estado === 'error' ? I.info : x.estado === 'llegando' ? I.upload : I.check}</span>
      <span class="mv-nom" title="${esc(x.archivo)}">${esc(nfc(x.archivo))}${x.total ? ` <span class="muted">· ${sbTam(x.total)}</span>` : ''}</span>
      <span class="mv-est">${esc(MV_ESTADO[x.estado]?.(x) || x.estado)}</span>
      ${x.estado === 'llegando' ? `<span class="mv-barra"><i style="width:${x.pct}%"></i></span>` : ''}</li>`).join('')}</ul>`
    : '<div class="mv-vacio">Aún nada. Lo que mande el móvil sale aquí.</div>';
  const cabeza = !r.activa
    ? `<div class="mv-fin" role="status">${MV_I.reloj}<div><b>${sub.parada ? 'Ya no se reciben audios' : 'El código ha caducado'}</b><div class="muted">Lo recibido se queda. Para mandar más, pide otro código.</div></div>
        <button type="button" class="btn sm primary" data-mv="otro">Mostrar otro código</button></div>`
    : `<div class="mv-cod">
        <div class="mv-qr"><div class="mv-qrimg">${sub.qr || ''}</div><div class="mv-caduca">${MV_I.reloj}Caduca en <b data-mv-reloj></b></div></div>
        <div class="mv-der">
          <ol class="mv-pasos"><li>Abre la cámara del móvil y apunta al código.</li><li>Toca el aviso que sale: se abre una página de ${esc(APP_NAME)}.</li><li>Elige las grabaciones. Entran aquí solas y se transcriben.</li></ol>
          <div class="mv-destino">${I.folder}<span>Se guardan en <b>${esc(sub.destino || '')}</b></span><button type="button" class="gr-cambiar" data-mv="cambiar">Cambiar</button></div>
          ${r.movil ? `<div class="mv-conectado">${I.check}<span>Un móvil ya ha abierto el código. ¿No es el tuyo, o lo abriste en otra app? <button type="button" class="gr-cambiar" data-mv="otro">Mostrar otro código</button></span></div>`
            : `<div class="mv-url">¿Sin cámara? Escribe en el navegador del móvil <code>${esc(r.url || '')}</code></div>`}
        </div></div>`;
  const notas = `<div class="mv-notas"><p>${MV_I.wifi}<span>Solo con el móvil en tu misma wifi. El audio va directo del móvil a este PC: no pasa por internet.</span></p>
      <p>${MV_I.escudo}<span>La primera vez, Windows preguntará si dejas usar la red (puede decir «Python»: es el motor de ${esc(APP_NAME)}). Marca <b>Redes privadas</b> y pulsa <b>Permitir</b>.</span></p>
      <p>${I.info}<span>¿El móvil no abre la página? Si alguna vez pulsaste «Cancelar» en ese aviso, Windows ya no lo vuelve a enseñar: busca en Inicio «Permitir una aplicación a través del Firewall de Windows», pulsa «Cambiar la configuración» y marca «Privada» en Python.</span></p></div>`;
  const html = `${cabeza}<div class="mv-rec"><div class="mv-h">Recibidos</div>${lista}</div>${r.activa ? notas : ''}`;
  // solo si cambia algo más que la cuenta atrás: repintar cada segundo le quitaba el foco a «Cambiar» o a «Mostrar
  // otro código» a quien va con el teclado
  if (panel._html !== html) { panel.innerHTML = html; panel._html = html; }
  const reloj = $('[data-mv-reloj]', panel);
  if (reloj) reloj.textContent = mvReloj(r.restante);
  $('[data-mv="parar"]', g.d).hidden = !r.activa || !g.d.classList.contains('mv-on');
  // lo que acaba de entrar, al árbol y a la cola (una vez cada uno)
  sub.vistos ||= new Set();
  const nuevos = rs.filter(x => x.path && !sub.vistos.has(x.id));
  if (nuevos.length) { nuevos.forEach(x => sub.vistos.add(x.id)); refreshTree(true); pollJobs(true); }
}
const mvReloj = s => `${Math.floor((s || 0) / 60)}:${String((s || 0) % 60).padStart(2, '0')}`;

/* lo que va llegando: cada segundo, mientras la tarjeta esté abierta y el código valga */
function mvSondear(g) {
  const sub = g.subir;
  clearTimeout(sub.sondeo);
  const gen = sub.gen;
  sub.sondeo = setTimeout(async () => {
    if (G !== g || sub.cerrada) return;
    try {
      const r = await api('/api/movil/estado');
      if (G !== g || sub.cerrada || gen !== sub.gen) return;
      mvPintar(g, r);
      if (r.activa || (r.recibidos || []).some(x => x.estado === 'llegando')) mvSondear(g);
    } catch { if (G === g && gen === sub.gen) mvSondear(g); }
  }, 1000);
}

/* los botones de la vista del código */
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-mv]');
  const g = G;
  if (!b || !g?.subir?.movil || !g.d?.contains(b)) return;
  const q = b.dataset.mv;
  if (q === 'listo') return closeModal(true);
  if (q === 'cambiar') return mvVer(g, false);
  if (q === 'parar') {
    clearTimeout(g.subir.sondeo);  // un sondeo ya pedido no pinta «Aún nada» encima de lo recibido
    g.subir.gen = (g.subir.gen || 0) + 1;
    try {
      const r = await api('/api/movil/cerrar', {});
      g.subir.parada = true; g.subir.sesion = false;
      mvPintar(g, r);
      if ((r.recibidos || []).some(x => x.estado === 'llegando')) mvSondear(g);  // lo que llegaba termina y se ve
      $('[data-mv="otro"]', g.d)?.focus();
    } catch (err) { toast(esc(err.message), 'err'); }
    return;
  }
  if (q === 'otro') {
    if (g.subir.abriendo) return;
    clearTimeout(g.subir.sondeo);
    g.subir.parada = false; g.subir.sesion = false; g.subir.activa = false;
    $('#rGo', g.d).disabled = false;
    return mvMostrar();
  }
});
