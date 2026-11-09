'use strict';
/* El nombre y la fecha que propone un audio por su archivo (09/11/2026, «Subir audio»): «WhatsApp Ptt 2026-11-08 at
   10.15.32», «PTT-20261108-WA0003», «2026-11-08 10-15-32 - Comité» (de Tenca), «Nueva grabación 12»… Sin fecha en el
   nombre, la de cuándo se guardó. Aparte de grabar.js porque lo usa también la página del móvil (movil.py, «Desde el
   móvil»): sin nada más de la app. */
function sbDesdeArchivo(f) {
  const stem = String(f.name || '').replace(/\.[^.]+$/, '');
  const ok = (y, mo, d, h = 0, mi = 0, se = 0) => y >= 2000 && y < 2100 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31 && h < 24 && mi < 60 && se < 60;
  let fecha = null, origen = 'archivo', resto = stem;
  const m = /(20\d{2})-?(\d{2})-?(\d{2})(?:[ _T-]|\s+at\s+)(\d{2})[.:_-]?(\d{2})(?:[.:_-]?(\d{2}))?(?!\d)/i.exec(stem);
  if (m && ok(+m[1], +m[2], +m[3], +m[4], +m[5], +(m[6] || 0))) {
    fecha = new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    origen = 'nombre';
    resto = stem.slice(0, m.index) + ' ' + stem.slice(m.index + m[0].length);
  } else {
    const d = /(?:^|\D)(20\d{2})-?(\d{2})-?(\d{2})(?!\d)/.exec(stem);
    if (d && ok(+d[1], +d[2], +d[3])) {  // solo el día (los de WhatsApp en Android): la hora, la del archivo
      const t = new Date(f.lastModified || Date.now());
      fecha = new Date(+d[1], d[2] - 1, +d[3], t.getHours(), t.getMinutes(), t.getSeconds());
      origen = 'nombre';
    }
  }
  if (!fecha) { fecha = new Date(f.lastModified || Date.now()); origen = f.lastModified ? 'guardado' : 'ahora'; }
  let nombre = /^(PTT|AUD)-\d{8}-WA\d+$/i.test(stem) || /^WhatsApp (Ptt|Audio)\b/i.test(stem) ? 'Mensaje de voz de WhatsApp'
    : resto.replace(/\s+/g, ' ').replace(/^[\s\-_·]+|[\s\-_·]+$/g, '');
  // lo que pone la grabadora sin decir nada («AUDIO_», «REC 12», «Recording», «Grabación 3», «Voice 001»): sin nombre
  if (/^(AUDIO|REC|VID|Recording|Grabaci[oó]n|Voice|Voz)?[\s_-]*\d*$/i.test(nombre)) nombre = '';
  return { file: f, nombre: nombre.slice(0, 120), fecha: fecha.getTime(), origen };
}
