/* Dónde se transcribe, a la vista (07/11/2026). Bernardo eligió «Con tu cuenta de Claude» y no transcribía, al grabar
   «parecía que seguía usando Groq» y veía «Descargando modelo medium» sin más (msgs 1169-1170). Aquí:
   - el motor de cada transcripción con su sitio: «en este PC · Whisper medium» o «en la nube · Groq»;
   - en Ajustes, los modelos de Whisper de este PC con su descarga (MB y %, o el error con lo que hacer);
   - con «Solo en este PC» y «Con tu cuenta de Claude» transcribe Whisper en el PC; Claude solo resume.
   Sobre /api/modelos y /api/config. Sin dependencias: su CSS va aquí para no tocar app.css. */

(function modelosCss() {
  const css = `
.motor { display: inline-flex; align-items: center; gap: 5px; font-weight: 500; font-size: 12.5px; color: var(--muted); margin-left: 6px; text-transform: none; letter-spacing: normal; }
.motor svg { width: 15px; height: 15px; }
.motor.nube { color: var(--accent); }
.motor-nota { font-size: 12.5px; color: var(--muted); margin: 2px 0 6px; }
.modelos { margin-top: 12px; }
.md-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); }
.md-row:first-child { border-top: 0; }
.md-row .md-s { grid-column: 1; font-size: 12.5px; color: var(--muted); }
.md-row .md-b { grid-column: 2; grid-row: 1 / span 2; }
.md-row .md-bar { grid-column: 1; height: 4px; border-radius: 2px; background: var(--line); overflow: hidden; }
.md-row .md-bar i { display: block; height: 100%; background: var(--accent); }
.md-row .ok { color: var(--ok); }
.md-row .err, .motor-err { color: var(--err); }`;
  const st = document.createElement('style');
  st.id = 'modelosCss';
  st.textContent = css;
  document.head.appendChild(st);
})();

/* ---------------------------------------------------------------- helpers puros (se prueban con node) */
function modelosFmtMb(n) {  // 1531 -> «1.531» (toLocaleString('es-ES') no agrupa los de 4 cifras)
  return String(Math.round(+n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}
function modelosFmtLeft(s) {
  s = Math.max(0, Math.round(+s || 0));
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')} min`;
  if (s >= 90) return `${Math.round(s / 60)} min`;
  return `${Math.max(5, Math.round(s / 5) * 5)} s`;
}
/* {donde: 'pc'|'nube', texto} del motor de un preset: el sitio donde se transcribe de verdad */
function motorDe(p, cfg) {
  if (!p) return null;
  if (p.engine === 'cloud') {
    const prov = cfg?.providers?.[cfg?.cloud?.provider]?.label || 'la nube';
    return { donde: 'nube', texto: `en la nube · ${prov}` };
  }
  return { donde: 'pc', texto: `en este PC · Whisper ${p.model}` };
}
/* estado de un modelo en una frase (para Ajustes y el diálogo de grabar) */
function modeloEstadoTexto(m) {
  if (!m) return '';
  if (m.ready) return '✓ descargado';
  if (m.downloading) {
    const pct = Math.floor((m.frac || 0) * 100);
    return `descargando: ${modelosFmtMb(m.done_mb)} de ${modelosFmtMb(m.total_mb)} MB (${pct} %)` + (m.eta_s ? ` · quedan ~${modelosFmtLeft(m.eta_s)}` : '');
  }
  if (m.error) return `✖ ${m.error}`;
  return `sin descargar · ${modelosFmtMb(m.size_mb)} MB (se descarga una sola vez)`;
}

/* ---------------------------------------------------------------- motor visible (directo y diálogo de grabar) */
function motorHtml(presetId) {
  const p = S.presetsById?.[presetId];
  const m = motorDe(p, S.config);
  if (!m) return '';
  return `<span class="motor ${m.donde}" data-motor="${m.donde}">${m.donde === 'nube' ? I.nube : I.monitor}${esc(m.texto)}</span>`;
}
/* una línea bajo el selector: dónde se transcribe, si el modelo aún no está y, con Claude, que Claude solo resume */
function motorNotaHtml(presetId, enMarcha) {
  const p = S.presetsById?.[presetId];
  const m = motorDe(p, S.config);
  if (!m) return '';
  let t = `Se transcribe ${m.texto}`;
  if (m.donde === 'pc' && p.cached === false && !enMarcha) t += ` · el modelo aún no está en este PC (${modelosFmtMb(p.size_mb)} MB): se descarga al empezar, con su progreso`;
  if (S.config?.procesado === 'agente') t += ` · ${S.config.agente?.label || 'Claude Code'} solo hace el resumen al terminar`;
  return `<div class="motor-nota" data-motor-nota="${m.donde}">${esc(t)}.</div>`;
}

/* ---------------------------------------------------------------- Ajustes: Whisper en este PC */
function modelosHtml() {
  const api = S.config?.procesado === 'api';
  return `<div id="procModelos" class="modelos"${api ? ' hidden' : ''}>
    <div class="help" style="margin-top:0"><b>Whisper en este PC</b> · con «Solo en este PC» y «Con tu cuenta de Claude» transcribe Whisper en tu ordenador; Claude no transcribe, solo resume. Cada modelo se descarga una sola vez; el del directo empieza a bajar en cuanto eliges una de esas dos opciones.</div>
    <div id="mdLista" class="help">Cargando…</div>
  </div>`;
}
function modeloFilaHtml(m) {
  const uso = (m.uso || []).length ? ` <span class="muted">· lo usa ${m.uso.join(' y ')}</span>` : '';
  let btn = '';
  if (m.downloading) btn = '<button class="btn ghost sm md-b" type="button" data-md="cancelar">Cancelar</button>';
  else if (!m.ready) btn = `<button class="btn sm md-b" type="button" data-md="descargar">${m.error ? 'Reintentar' : 'Descargar'}</button>`;
  const cls = m.ready ? 'ok' : m.error && !m.downloading ? 'err' : '';
  const bar = m.downloading ? `<div class="md-bar"><i style="width:${(100 * (m.frac || 0)).toFixed(1)}%"></i></div>` : '';
  return `<div class="md-row" data-model="${esc(m.model)}"><div><b>${esc(m.live_label || m.label || m.model)}</b>${uso}</div>${btn}
    <div class="md-s"><span class="${cls}">${esc(modeloEstadoTexto(m))}</span></div>${bar}</div>`;
}
function bindModelos(d) {
  const box = $('#mdLista', d);
  if (!box) return;
  let timer = null;
  const draw = r => {
    const ms = [...(r.modelos || [])].sort((a, b) => (b.uso || []).length - (a.uso || []).length);
    box.innerHTML = ms.map(modeloFilaHtml).join('') || 'Sin modelos locales.';
  };
  const poll = async () => {
    clearTimeout(timer);
    if (!document.body.contains(box)) return;  // Ajustes cerrado: se deja de preguntar
    try {
      const r = await api('/api/modelos');
      draw(r);
      if (r.modelos.some(m => m.downloading)) timer = setTimeout(poll, 1000);
    } catch (err) { box.innerHTML = `<span class="motor-err">✖ ${esc(err.message)}</span>`; }
  };
  box.addEventListener('click', async e => {
    const b = e.target.closest('[data-md]');
    if (!b) return;
    const model = b.closest('[data-model]').dataset.model;
    b.disabled = true;
    try { draw(await api(b.dataset.md === 'cancelar' ? '/api/modelos/cancelar' : '/api/modelos/descargar', { model })); }
    catch (err) { toast(esc(err.message), 'err'); }
    timer = setTimeout(poll, 400);
  });
  d.modelosPoll = poll;  // al cambiar «Dónde se procesan» (la descarga del modelo del directo puede haber empezado)
  poll();
}

/* Ajustes: los selectores de motor del perfil (perfil.js) con las opciones y los valores de ahora. Se pintan al abrir
   Ajustes; al cambiar «Dónde se procesan» o al guardar la primera clave (que pone la nube), sus opciones y su valor
   quedaban los de antes. Mismo texto que perfil.js; dataset.ini, para que «Guardar» solo mande lo que se toque. */
function motoresRefrescar(d) {
  const c = S.config || {}, presets = c.presets || [];
  const prov = c.providers?.[c.cloud?.provider]?.label || 'nube';
  const opt = (v, l, sel) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  const l = $('#lPreset', d), p = $('#pfPreset', d);
  if (l) {
    l.innerHTML = presets.map(pr => opt(pr.id, `${pr.live_label} · ~${String(pr.live_speed).replace('.', ',')}× · ${liveFit(pr)[1]}`, c.live_preset || 'medium')).join('');
    l.dataset.ini = l.value;
  }
  if (p) {
    p.innerHTML = presets.map(pr => opt(pr.id, `${pr.label} · ${pr.engine === 'cloud' ? prov : pr.model}`, c.preset)).join('');
    p.dataset.ini = p.value;
  }
}
