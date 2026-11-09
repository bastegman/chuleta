'use strict';
/* La página del móvil de «Subir audio › Desde el móvil» (movil.py la sirve por la wifi, solo mientras el código está
   abierto en el PC). Eliges grabaciones y van al PC una a una, con su progreso; cada una con el nombre y la fecha que
   propone su archivo (nombres-audio.js), como al subirla desde el PC. Lo demás (carpeta, proyecto, idioma) lo eligió
   el PC antes de enseñar el código. Sin dependencias y sin nada de fuera. */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const b = document.body, base = b.dataset.base, app = b.dataset.app || 'Tenca';
  const exts = (b.dataset.exts || '').split(',').filter(Boolean), max = Number(b.dataset.max || 0);
  // la hora del PC manda (el móvil puede ir adelantado o atrasado): el desfase, al cargar
  const desfase = Date.now() - Number(b.dataset.ahora || Date.now());
  const caduca = Number(b.dataset.caduca || 0) + desfase;
  const input = $('#mArchivos'), lista = $('#mLista'), estado = $('#mEstado'), reloj = $('#mCaduca');
  let caducado = false, cola = Promise.resolve();

  if (exts.length) input.accept = ['audio/*', 'video/mp4', 'video/quicktime', ...exts].join(',');

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tam = n => n >= 1e9 ? `${(n / 1e9).toFixed(1).replace('.', ',')} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1).replace('.', ',')} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;
  const ext = n => { const m = /\.[^.\\/]+$/.exec(n || ''); return m ? m[0].toLowerCase() : ''; };

  function cerrar(texto) {
    caducado = true;
    estado.classList.add('fin');
    estado.innerHTML = `<i class="m-punto"></i><span>${esc(texto)}</span>`;
    $('#mElegir').classList.add('off');
    input.disabled = true;
  }
  function tic() {
    if (caducado) return;
    const s = Math.max(0, Math.round((caduca - Date.now()) / 1000));
    if (!s) return cerrar(`Este código ha caducado. Pide uno nuevo en ${app}.`);
    reloj.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    setTimeout(tic, 1000 - (Date.now() % 1000) + 5);
  }
  tic();

  /* una fila de la lista: su estado se cambia con pintar() */
  function fila(f) {
    const li = document.createElement('li');
    li.className = 'm-item';
    li.innerHTML = `<div class="m-nom"></div><div class="m-sub"></div><div class="m-barra" hidden><i></i></div>`;
    $('.m-nom', li).textContent = f.name;
    lista.prepend(li);
    return li;
  }
  function pintar(li, clase, texto, pct) {
    li.className = `m-item ${clase}`;
    $('.m-sub', li).innerHTML = texto;
    const barra = $('.m-barra', li);
    barra.hidden = pct == null;
    if (pct != null) $('i', barra).style.width = `${pct}%`;
  }

  function enviar(f, li) {
    return new Promise(listo => {
      if (caducado) { pintar(li, 'mal', 'No se ha enviado: el código ya no vale.'); return listo(); }
      const d = typeof sbDesdeArchivo === 'function' ? sbDesdeArchivo(f) : { nombre: '', fecha: f.lastModified || Date.now() };
      const q = new URLSearchParams({ archivo: f.name, nombre: d.nombre || '', fecha: String(d.fecha) });
      const x = new XMLHttpRequest();
      x.open('POST', `${base}subir?${q}`);
      x.upload.onprogress = e => {
        if (e.lengthComputable) pintar(li, 'va', `Enviando · ${Math.min(99, Math.round(100 * e.loaded / e.total))} %`, 100 * e.loaded / e.total);
      };
      x.upload.onload = () => pintar(li, 'va', 'Comprobando en el PC…', 100);
      x.onload = () => {
        let j = {};
        try { j = JSON.parse(x.responseText); } catch { j = { error: 'El PC ha contestado algo raro.' }; }
        if (x.status === 410) cerrar(j.error || 'Este código ya no vale.');
        if (x.status >= 400 || j.error) pintar(li, 'mal', esc(j.error || 'No se ha podido enviar.'));
        else pintar(li, 'ok', `<b>Enviado</b> · ya está en el PC${j.aviso ? ` (${esc(j.aviso)})` : ''}`);
        listo();
      };
      x.onerror = () => {
        pintar(li, 'mal', `No llega al PC. ¿Sigues en la misma wifi y con el código abierto en ${esc(app)}?`);
        listo();
      };
      pintar(li, 'va', `Enviando · 0 % <span class="m-tam">${tam(f.size || 0)}</span>`, 0);
      x.send(f);
    });
  }

  input.addEventListener('change', () => {
    const fs = [...input.files];
    input.value = '';
    for (const f of fs) {
      const li = fila(f);
      if (exts.length && !exts.includes(ext(f.name))) {
        pintar(li, 'mal', `No es un audio que ${esc(app)} sepa leer.`);
        continue;
      }
      if (!f.size) { pintar(li, 'mal', 'Está vacío.'); continue; }
      // demasiado grande: se dice aquí (el PC lo rechazaría antes de leerlo y el móvil solo vería «No llega al PC»)
      if (max && f.size > max) { pintar(li, 'mal', `Es demasiado grande para mandarlo desde el móvil (${tam(f.size)}; como mucho, ${tam(max)}).`); continue; }
      pintar(li, 'espera', 'En espera…');
      cola = cola.then(() => enviar(f, li));  // de uno en uno: con la wifi de casa, mejor que todos a la vez
    }
  });
})();
