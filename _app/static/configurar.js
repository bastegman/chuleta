/* Configurar Chuleta (07/11/2026, propuesta de Steve sobre la queja de Bernardo, msg 1169: «Lo de config es un
   desastre»). Aquí vive lo que dice dónde se procesan las reuniones, con las mismas palabras en todas partes:
   - la línea del modo («Se transcribe en este PC (Normal) y al terminar la resume Claude con tu cuenta»), que usan el
     diálogo Grabar, la cabecera del directo, la Cola y el chip «EN VIVO»;
   - «Preparando la transcripción» con % mientras se descarga o se carga el modelo (antes: «Escuchando…» y «al día»);
   - el asistente de tres pasos y «Probar» (más abajo).
   Lo de arriba del marcador es puro (se prueba con node: tests/test_ux_configurar.py); lo de abajo toca el DOM. */

const MODELO_NOMBRE = { small: 'Rápido', medium: 'Normal', 'large-v3-turbo': 'Preciso', 'large-v3': 'Máxima' };
const MODO_NOMBRE = { local: 'Solo en este PC', agente: 'Con tu cuenta de Claude', api: 'Con una API externa' };

function modeloNombre(model) { return MODELO_NOMBRE[model] || model || ''; }
function modoNombre(m) { return MODO_NOMBRE[m] || m || ''; }
function cfgProv(c) { return c?.providers?.[c?.cloud?.provider || 'groq']?.label || 'la nube'; }
function cfgFmtGb(mb) {  // 1531 -> «1,5 GB»; 486 -> «486 MB»
  mb = +mb || 0;
  return mb >= 1000 ? `${(mb / 1000).toFixed(1).replace('.', ',')} GB` : `${Math.round(mb)} MB`;
}
function cfgFmtQueda(s) {
  s = Math.max(0, Math.round(+s || 0));
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')} min`;
  if (s >= 90) return `${Math.round(s / 60)} min`;
  return `${Math.max(5, Math.round(s / 5) * 5)} s`;
}

/* La línea del modo para un motor (preset): dónde se transcribe y quién resume, como en Ajustes */
function modoLinea(presetId, cfg) {
  const c = cfg || S.config || {}, p = S.presetsById?.[presetId] || {};
  const modo = c.procesado || 'local', prov = cfgProv(c);
  const nube = p.engine === 'cloud';
  const trans = nube ? `Se transcribe con ${prov}` : `Se transcribe en este PC (${modeloNombre(p.model || 'medium')})`;
  if (modo === 'local') return `${trans}. Sin resumen con IA`;
  const quien = modo === 'agente' ? 'Claude con tu cuenta' : prov;
  // el resumen automático: apagado en Ajustes, o (con la API) lo transcrito en el PC solo si se ha consentido
  const auto = c.summary?.auto !== false && (modo !== 'api' || nube || c.summary?.local);
  return auto ? `${trans} y al terminar la resume ${quien}` : `${trans}. El resumen, a mano con ${modo === 'agente' ? 'Claude (tu cuenta)' : prov}`;
}

/* Un trabajo que aún no transcribe porque está descargando o cargando su modelo: {tipo, nombre, pct, …} o null */
function prepEstado(j) {
  if (!j || j.status !== 'running') return null;
  const st = j.stage || '', p = S.presetsById?.[j.preset] || {}, model = p.model;
  if (/^Descargando/.test(st)) {
    const m = S.modelos?.[model] || {};
    let pct = m.frac != null ? Math.floor(m.frac * 100) : null;
    if (pct == null) { const x = st.match(/\((\d+) %\)/); pct = x ? +x[1] : null; }
    return { tipo: 'descarga', model, nombre: modeloNombre(model), pct, done_mb: m.done_mb ?? null,
      total_mb: m.total_mb || m.size_mb || p.size_mb || null, eta_s: m.eta_s ?? null };
  }
  if (/^Cargando/.test(st)) return { tipo: 'carga', model, nombre: modeloNombre(model) };
  return null;
}
/* lo que dice la Cola en vez de «al día» */
function prepCola(j) {
  const e = prepEstado(j);
  if (!e) return '';
  return e.tipo === 'descarga' ? `Preparando: descargando el modelo ${e.nombre}${e.pct != null ? ` · ${e.pct} %` : ''}`
    : `Preparando: cargando el modelo ${e.nombre}`;
}
/* la frase larga de la cabecera del directo */
function prepTexto(e, segs) {
  if (!e) return '';
  if (e.tipo === 'carga') return `Cargando el modelo ${e.nombre} en memoria${segs != null ? `… ${segs} s` : '…'}`;
  const tam = e.total_mb ? cfgFmtGb(e.total_mb) : '';
  let t = `Descargando el modelo ${e.nombre} (${tam ? tam + ', ' : ''}solo la primera vez)`;
  if (e.done_mb != null && e.total_mb) t += ` · ${cfgFmtGb(e.done_mb)} de ${tam}`;
  if (e.eta_s) t += ` · quedan unos ${cfgFmtQueda(e.eta_s)}`;
  return t + '. La grabación sigue y no se pierde nada: cuando acabe la descarga, el texto empieza desde el principio.';
}

/* Los modelos de Whisper que necesita «En este PC» o «Con tu cuenta de Claude»: el del directo y el de la cola, si son
   locales; si no (la configuración de ahora es la API), los de siempre (Normal y Rápido). Decisión de Claudia
   (07/11/2026): se descargan los dos juntos, con una sola barra, para que no haya una segunda descarga por sorpresa. */
function cfgModelosNecesarios(cfg) {
  const c = cfg || S.config || {};
  const pick = (k, def) => { const p = S.presetsById?.[c[k]]; return p && p.engine === 'local' ? p.model : (S.presetsById?.[def]?.model || (def === 'medium' ? 'medium' : 'small')); };
  return [...new Set([pick('live_preset', 'medium'), pick('preset', 'rapido')])];
}
/* Una sola barra para varias descargas: {listo, bajando, error, total, done, pct, eta} o null si aún no hay datos */
function cfgDescargaTotal(names, modelos) {
  const ms = names.map(n => (modelos || S.modelos || {})[n]);
  if (!ms.length || ms.some(m => !m)) return null;
  const tam = m => m.total_mb || m.size_mb || 0;
  const total = ms.reduce((t, m) => t + tam(m), 0);
  const done = ms.reduce((t, m) => t + (m.ready ? tam(m) : Math.min(tam(m), m.done_mb || 0)), 0);
  return { listo: ms.every(m => m.ready), bajando: ms.some(m => m.downloading),
    error: (ms.find(m => m.error && !m.downloading && !m.ready) || {}).error || null,
    total, done, pct: total ? Math.floor(100 * done / total) : 0, eta: Math.max(0, ...ms.map(m => m.eta_s || 0)) };
}
function cfgDescargaTexto(t) {
  if (!t) return 'Preparando la descarga…';
  if (t.listo) return 'Modelos descargados: ya están en este PC.';
  if (t.error && !t.bajando) return t.error;
  if (!t.bajando) return `Sin descargar · ${cfgFmtGb(t.total)} en total (se descargan una sola vez)`;
  return `Descargando · ${t.pct} % · ${cfgFmtGb(t.done)} de ${cfgFmtGb(t.total)}` + (t.eta ? ` · quedan unos ${cfgFmtQueda(t.eta)}` : '');
}

/* ---8<--- DOM (lo de abajo usa el DOM, I, api, toast… de app.js) */

S.modelos = S.modelos || {};
S.prepDesde = S.prepDesde || {};
let cfgModelosTimer = null, cfgModelosHasta = 0;
/* /api/modelos cada segundo mientras haga falta (una descarga en marcha o alguien que la enseña) */
async function modelosRefrescar(seguir = 0) {
  if (seguir) cfgModelosHasta = Math.max(cfgModelosHasta, Date.now() + seguir);
  clearTimeout(cfgModelosTimer);
  try {
    const r = await api('/api/modelos');
    S.modelos = Object.fromEntries((r.modelos || []).map(m => [m.model, m]));
    document.dispatchEvent(new CustomEvent('modelos'));
    if ((r.modelos || []).some(m => m.downloading) || Date.now() < cfgModelosHasta) cfgModelosTimer = setTimeout(modelosRefrescar, 1000);
  } catch { cfgModelosTimer = setTimeout(modelosRefrescar, 3000); }
}
/* al llegar la Cola: si algún trabajo prepara su modelo, se mira el % (y se repinta lo que lo enseña) */
function prepVigilar(jobs) {
  const prep = (jobs || []).filter(j => prepEstado(j));
  prep.forEach(j => { if (!S.prepDesde[j.id + j.stage.slice(0, 9)]) S.prepDesde[j.id + j.stage.slice(0, 9)] = Date.now(); });
  if (prep.some(j => prepEstado(j).tipo === 'descarga') && !cfgModelosTimer) modelosRefrescar();
}
function prepSegs(j) {
  const t = S.prepDesde[j.id + (j.stage || '').slice(0, 9)];
  return t ? Math.round((Date.now() - t) / 1000) : null;
}

/* La línea del modo con su icono; cambiar: enlace que enseña los modelos (directo) */
function modoLineaHtml(presetId, opts = {}) {
  const p = S.presetsById?.[presetId] || {};
  const icon = p.engine === 'cloud' ? I.nube : I.monitor;
  const tec = typeof motorDe === 'function' ? motorDe(S.presetsById?.[presetId], S.config)?.texto : '';  // «en este PC · Whisper medium»
  return `<div class="modo-linea" data-modo-linea${tec ? ` title="${esc(tec)}"` : ''}>${icon}<span>${esc(modoLinea(presetId))}</span>`
    + `${opts.cambiar ? `<a href="#" data-act="${opts.cambiar}">${opts.abierto ? 'Ocultar' : 'Cambiar'}</a>` : ''}</div>`
    + (opts.falta ? faltaModeloHtml(presetId) : '');
}
/* el modelo de ese motor aún no está en el PC: aviso antes de empezar, con «Descargar ahora» y su % */
function faltaModeloHtml(presetId) {
  const p = S.presetsById?.[presetId] || {};
  if (p.engine === 'cloud' || p.cached !== false) return '';
  const m = S.modelos?.[p.model];
  if (m?.ready) return '';
  const nombre = modeloNombre(p.model), tam = cfgFmtGb(m?.total_mb || m?.size_mb || p.size_mb);
  if (m?.downloading) {
    const pct = Math.floor((m.frac || 0) * 100);
    return `<div class="notice info falta-modelo" data-falta="${esc(p.model)}"><span>Descargando el modelo ${esc(nombre)}: ${pct} %. Puedes empezar ya: el texto saldrá cuando termine.</span></div>`;
  }
  if (m?.error) {
    return `<div class="notice falta-modelo" data-falta="${esc(p.model)}"><span>${esc(m.error)}</span><button class="btn sm" type="button" data-act="modelo-bajar" data-model="${esc(p.model)}">Reintentar</button></div>`;
  }
  return `<div class="notice falta-modelo" data-falta="${esc(p.model)}"><span>El modelo ${esc(nombre)} (${esc(tam)}) aún no está en este PC. Puedes empezar ya: se descarga mientras grabas y el texto sale cuando termine.</span><button class="btn sm" type="button" data-act="modelo-bajar" data-model="${esc(p.model)}">Descargar ahora</button></div>`;
}
async function modeloBajar(model) {
  try {
    const r = await api('/api/modelos/descargar', { model });
    S.modelos = Object.fromEntries((r.modelos || []).map(m => [m.model, m]));
    document.dispatchEvent(new CustomEvent('modelos'));
    modelosRefrescar(4000);
  } catch (err) { toast(esc(err.message), 'err'); }
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-act="modelo-bajar"]');
  if (!b) return;
  e.preventDefault();
  b.disabled = true;
  modeloBajar(b.dataset.model);
});

/* «Preparando la transcripción» (cabecera del directo) */
function prepHtml(j) {
  const e = prepEstado(j);
  if (!e) return '<div data-prep></div>';
  const pct = e.tipo === 'descarga' ? e.pct : null;
  return `<div data-prep><div class="prep" role="status" aria-live="polite"><div class="prep-h"><span>Preparando la transcripción</span>${pct != null ? `<span>${pct} %</span>` : ''}</div>`
    + (e.tipo === 'descarga' ? `<div class="prep-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"${pct != null ? ` aria-valuenow="${pct}"` : ''}><i style="width:${pct || 0}%"></i></div>` : '')
    + `<div class="prep-s">${esc(prepTexto(e, prepSegs(j)))}</div></div></div>`;
}
/* el hueco del texto mientras se prepara */
function prepHueco(j) {
  const e = prepEstado(j);
  if (!e) return '';
  return e.tipo === 'descarga' ? 'El texto aparecerá aquí cuando termine la descarga del modelo.'
    : 'El texto aparecerá en unos segundos, en cuanto se cargue el modelo.';
}

/* llega el % de una descarga: se repinta lo que lo enseña (cabecera del directo, diálogo Grabar, Cola) */
document.addEventListener('modelos', () => {
  const au = typeof curAudio === 'function' ? curAudio() : null;
  if (au && $('#lHero') && typeof updateLiveHero === 'function') updateLiveHero(au);
  const m = $('#modal .dialog');
  if (m && typeof m.pintaMotor === 'function') m.pintaMotor();
  if (!$('#jobsPanel').classList.contains('hidden') && typeof renderJobsPanel === 'function') renderJobsPanel();
});

/* ================================================================ asistente «Configurar Chuleta» (3 pasos)
   1 · elegir dónde se procesan · 2 · prepararlo (descarga, Claude Code o la clave) · 3 · «Probar» de punta a punta.
   No guarda nada hasta terminar el paso 3 (como «Aplicar ahora» en Ajustes: un clic de prueba no cambia nada). */
const W = { paso: 1, modo: 'local', cloud: {}, prueba: null, timer: null, agTimer: null, volver: null, d: null };
// los avisos de privacidad, con su texto de siempre (decisión de producto del 02/11/2026)
const AVISO_CLAUDE = 'El texto de la reunión (título, fecha, transcripción con quién habla si se sabe, notas, contexto, participantes confirmados e instrucciones; el audio no) se envía a Anthropic con esa cuenta: con la de empresa, bajo el acuerdo de tu empresa; con la personal, bajo sus términos de uso. En algunas reuniones puede ser un bloqueante: compruébalo antes. Al terminar de transcribir, las reuniones de los últimos 3 días se resumen solas; se desactiva en Ajustes.';
const AVISO_API = 'El audio (para transcribir) y el texto (para resumir) salen del PC hacia el proveedor. Comprueba la política de tu empresa y el acuerdo con tu cliente, si lo hay.';
const AVISO_NUBE = 'Las grabaciones de reuniones pueden ser información confidencial. Antes de mandar audio a un servicio externo (Groq, OpenAI), confirma que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo hay.';

function abrirConfigurar(opts = {}) {
  if (!S.config) return toast('La app aún está arrancando: espera unos segundos y vuelve a probar.', 'err');
  const c = S.config.cloud || {};
  Object.assign(W, { paso: opts.paso || 1, modo: opts.modo || S.config.procesado || 'local', prueba: null, volver: opts.volver || null, empezar: !!opts.empezar,
    cloud: { provider: c.provider || 'groq', api_key: '', base_url: c.base_url || '', azure_endpoint: c.azure_endpoint || '',
      azure_deployment: c.azure_deployment || '', azure_api_version: c.azure_api_version || '' } });
  W.guardado = false;
  openModal('<div class="wz-raiz"></div>', d => { W.d = d; wzPintar(); }, 'wz');
  S.modalCleanup = () => {
    wzParar();
    wzCancelarPrueba();
    W.d = null;
    if (!W.guardado && W.volver) setTimeout(W.volver, 0);  // abierto desde Ajustes: Esc o «Ahora no» vuelven allí
  };
}
/* una prueba en marcha que ya no vale (se cierra el asistente o cambia lo elegido): se cancela en el servidor */
function wzCancelarPrueba() {
  const r = W.prueba;
  if (r && r.id && !r.terminada) api('/api/probar/cancelar', { id: r.id }).catch(() => { });
  W.prueba = null;
  W.pieSig = W.anuncio = '';
}
function wzParar() {
  clearTimeout(W.timer); clearTimeout(W.agTimer);
  W.timer = W.agTimer = null;
}
function wzProv() { return S.config.providers?.[W.cloud.provider]?.label || 'la API'; }
function wzOpcion(v, titulo, chip, chipCls, texto, aviso) {
  const on = W.modo === v;
  return `<label class="wz-opt${on ? ' on' : ''}"><input type="radio" name="wzModo" value="${v}"${on ? ' checked' : ''}>
    <div><div class="t">${titulo} <span class="wz-chip ${chipCls}">${chip}</span></div><div class="d">${texto}</div>
    ${aviso && on ? `<div class="wz-aviso">${aviso}</div>` : ''}</div></label>`;
}
function wzCabecera() {
  return `<div class="wz-head"><img src="/logo.svg" alt="">Configurar ${esc(APP_NAME)}
    <div class="wz-steps" aria-label="Paso ${W.paso} de 3">Paso ${W.paso} de 3 ${[1, 2, 3].map(n => `<i class="${n <= W.paso ? 'on' : ''}"></i>`).join('')}</div></div>`;
}
function wzPaso1() {
  return `<h2 tabindex="-1">¿Dónde se procesan tus reuniones?</h2>
    <p class="wz-lead">Elige una opción. Puedes cambiarla cuando quieras en Ajustes.</p>
    <fieldset class="wz-opts"><legend class="sr-only">Dónde se procesan tus reuniones</legend>
      ${wzOpcion('local', 'Solo en este PC', 'Lo más privado', 'g', 'Tu PC transcribe las reuniones. No hay resumen con IA y nada sale del PC.')}
      ${wzOpcion('agente', 'Con tu cuenta de Claude', 'Necesita Claude Code', '', 'Tu PC transcribe y Claude resume, con tu suscripción personal o de empresa.', AVISO_CLAUDE)}
      ${wzOpcion('api', 'Con una API externa', 'El audio sale del PC', 'w', 'Groq, OpenAI o la API de tu empresa transcriben y resumen en la nube, con tu clave. Es lo más rápido.', AVISO_API)}
    </fieldset>`;
}
function wzDescargaHtml(strip) {
  const t = cfgDescargaTotal(cfgModelosNecesarios(), S.modelos);
  const cls = t?.listo ? ' ok' : t?.error && !t.bajando ? ' err' : '';
  const bar = t && !t.listo && !(t.error && !t.bajando) ? `<div class="prep-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${t.pct}"><i style="width:${t.pct}%"></i></div>` : '';
  const re = t?.error && !t.bajando ? ' <button class="btn sm" type="button" data-wz="bajar">Reintentar</button>' : '';
  const n = cfgModelosNecesarios().length, los = n > 1 ? 'los modelos' : 'el modelo';
  const intro = strip && t && !t.listo && !(t.error && !t.bajando)
    ? `<b>Mientras tanto, descargando ${los} de transcripción</b> (${cfgFmtGb(t.total)}, solo la primera vez). Con Claude, la transcripción se hace en tu PC y Claude solo resume.`
    : strip ? 'Con Claude, la transcripción se hace en tu PC y Claude solo resume. ' : '';
  return `<div class="wz-descarga${cls}" data-wz-descarga aria-live="polite">${intro}${bar}<div>${esc(cfgDescargaTexto(t))}${re}</div></div>`;
}
function wzPaso2() {
  if (W.modo === 'local') {
    const dos = cfgModelosNecesarios().length > 1;
    return `<h2 tabindex="-1">${dos ? 'Descarga los modelos de transcripción' : 'Descarga el modelo de transcripción'}</h2>
      <p class="wz-lead">${esc(APP_NAME)} transcribe con ${dos ? 'dos modelos que se quedan' : 'un modelo que se queda'} en tu PC: ${dos ? 'uno para el directo y otro para transcribir después' : 'se descarga una sola vez'}. ${dos ? 'Se descargan una sola vez.' : ''}</p>
      ${wzDescargaHtml(false)}
      <p class="help">Puedes pasar al paso siguiente mientras se descarga.</p>
      ${typeof modelosHtml === 'function' ? `<details class="wz-mas"><summary>Ver los modelos de este PC</summary>${modelosHtml()}</details>` : ''}`;
  }
  if (W.modo === 'agente') {
    const ag = S.config.agente || {};
    return `<h2 tabindex="-1">Conecta Claude Code</h2>
      <p class="wz-lead">${esc(APP_NAME)} usa el Claude Code de tu PC para resumir. Tu contraseña no pasa por ${esc(APP_NAME)}: entras tú, en la página de Claude.</p>
      ${wzDescargaHtml(true)}
      <ol class="wz-pasos">
        <li><span class="num${ag.encontrado ? ' ok' : ''}" data-wz-num>${ag.encontrado ? I.check : '1'}</span><div><b>Instala Claude Code (una vez)</b>
          <div>Pulsa «Abrir PowerShell» (abajo), pega esta línea y pulsa Intro. Tarda menos de un minuto y no hace falta ser administrador.</div>
          <div class="wz-code"><code>irm https://claude.ai/install.ps1 | iex</code><button class="btn sm" type="button" data-wz="copiar">${I.copy || ''}Copiar</button></div>
          <div class="help">¿Prefieres winget? <code>winget install Anthropic.ClaudeCode</code> · Si tu empresa no te deja instalar programas, pídele a IT que instale Claude Code.</div>
          <div class="wz-est${ag.encontrado ? ' ok' : ''}" data-wz-encontrado>${ag.encontrado ? 'Instalado en este PC' : 'Todavía no lo encuentro en este PC'}</div></div></li>
        <li><span class="num">2</span><div><b>Entra con tu cuenta</b>
          <div>En esa misma ventana (o con «Abrir PowerShell» otra vez) escribe <code>claude</code> y pulsa Intro. Se abre el navegador con el inicio de sesión de Claude: entra con tu cuenta; si es la de tu empresa, con su inicio de sesión de siempre.</div>
          <div class="help">Si el navegador no se abre, pulsa <code>c</code> para copiar el enlace; si te enseña un código, pégalo en PowerShell. Cuando ponga «Login successful», pulsa Intro y cierra la ventana. Vale una cuenta Pro, Max, Ultra, Team, Enterprise o de Console.</div></div></li>
        <li><span class="num">3</span><div><b>Pruébalo</b>
          <div>Pulsa Siguiente y luego «Probar»: ${esc(APP_NAME)} graba 3 s de tu voz, los transcribe en tu PC y le pide a Claude que los resuma.</div></div></li>
      </ol>
      <p class="help">¿Usas Claude Code en VS Code? ${esc(APP_NAME)} también encuentra esa copia y su sesión. La app de escritorio de Claude no basta: su sesión es solo suya.</p>`;
  }
  const P = S.config.providers || {}, p = P[W.cloud.provider] || {}, c = S.config.cloud || {};
  const misma = W.cloud.provider === (c.provider || 'groq');
  const ph = misma && c.has_key ? `Guardada: ${c.api_key} (escribe para cambiarla)` : 'Pega aquí la clave';
  const keys = p.keys ? p.keys.replace('https://', '') : '';
  const campos = W.cloud.provider === 'azure'
    ? `<label>Dirección del recurso (endpoint)</label><input class="input" id="wzAzEp" placeholder="https://<recurso>.openai.azure.com" value="${esc(W.cloud.azure_endpoint)}">
       <div class="grid2"><div><label>Nombre del despliegue de Whisper</label><input class="input" id="wzAzDep" value="${esc(W.cloud.azure_deployment)}"></div>
       <div><label>Versión de la API</label><input class="input" id="wzAzVer" value="${esc(W.cloud.azure_api_version)}"></div></div>`
    : W.cloud.provider === 'custom'
      ? `<label>Dirección de la API (te la da tu empresa)</label><input class="input" id="wzBase" placeholder="https://…/v1" value="${esc(W.cloud.base_url)}">` : '';
  const deQue = W.cloud.provider === 'custom' ? 'de tu API' : `de ${p.label || 'la API'}`;
  return `<h2 tabindex="-1">Pega la clave ${esc(deQue)}</h2>
    <p class="wz-lead">${keys ? `Consíguela en <a href="#" data-wz="claves">${esc(keys)}</a>. ` : ''}Se guarda solo en este PC, fuera de OneDrive.</p>
    <div data-wz-prov${misma && W.cloud.provider === 'groq' && !W.provAbierto ? ' hidden' : ''}><label>Proveedor</label><select id="wzProv">${Object.entries(P).map(([k, v]) => `<option value="${esc(k)}"${k === W.cloud.provider ? ' selected' : ''}>${esc(v.label)}</option>`).join('')}</select></div>
    ${campos}
    <label>Clave ${esc(deQue)}</label><input class="input" type="password" id="wzKey" autocomplete="off" spellcheck="false" placeholder="${esc(ph)}" value="${esc(W.cloud.api_key)}">
    <div class="wz-err" data-wz-err></div>
    ${misma && W.cloud.provider === 'groq' && !W.provAbierto ? '<div class="help">¿Usas OpenAI, Azure o la API de tu empresa? <a href="#" data-wz="prov">Cambiar de proveedor</a></div>' : ''}
    <div class="notice">${AVISO_NUBE}</div>`;
}
function wzPaso3() {
  const lead = { local: `${esc(APP_NAME)} graba 3 s de tu voz y los transcribe en tu PC, igual que hará con tus reuniones.`,
    agente: `${esc(APP_NAME)} graba 3 s de tu voz, los transcribe en tu PC y le pide a Claude que los resuma, igual que hará con tus reuniones.`,
    api: `${esc(APP_NAME)} graba 3 s de tu voz y los manda a ${esc(wzProv())} para transcribirlos y resumirlos, igual que hará con tus reuniones.` }[W.modo];
  return `<h2 tabindex="-1">Prueba que todo funciona</h2>
    <p class="wz-lead">${lead}</p>
    <div class="wz-di">Cuando te lo pida, di en voz alta: <b>«Hola, esto es una prueba»</b>.</div>
    <div class="wz-probar"><button class="btn primary" type="button" data-wz="probar">Probar</button><span class="muted" data-wz-probando></span><button class="btn ghost sm" type="button" data-wz="cancelar-prueba" hidden>Cancelar la prueba</button></div>
    <div class="wz-check" data-wz-pasos>${wzPasosHtml(W.prueba)}</div>
    <div class="sr-only" aria-live="polite" data-wz-anuncio></div>
    <div data-wz-res>${wzResHtml(W.prueba)}</div>`;
}
function wzPasosHtml(r) {
  if (!r) return '';
  return r.pasos.map(p => {
    const ic = { ok: I.check, aviso: '!', error: '✕', en_curso: '<i class="wz-spin"></i>', espera: '' }[p.estado] || '';
    const habla = p.estado === 'en_curso' && /^Habla ahora/.test(p.detalle || '');
    const det = p.estado === 'error' ? 'Ha fallado: lo tienes explicado abajo' : p.detalle;  // la frase, una vez
    return `<div class="wz-row ${p.estado}${habla ? ' habla' : ''}"><span class="ic">${ic}</span><span class="t">${esc(p.texto)}${det ? `<small>${esc(det)}</small>` : ''}</span><span class="sec">${p.segundos != null && p.estado !== 'espera' ? `${p.segundos} s` : ''}</span></div>`;
  }).join('');
}
function wzResHtml(r) {
  if (!r || !r.terminada) return '';
  if (r.listo) return `<div class="wz-res ok" role="alert" tabindex="-1"><b>Listo</b>${esc(r.resultado.replace(/^Listo\.\s*/, ''))}</div>`;
  const a = r.fallo?.accion;
  return `<div class="wz-res err" role="alert" tabindex="-1">${esc(r.fallo?.frase || 'La prueba ha fallado.')}
    ${a ? `<div class="acts"><button class="btn sm" type="button" data-wz-accion="${esc(a.tipo)}"${a.url ? ` data-url="${esc(a.url)}"` : ''}>${esc(a.etiqueta)}</button></div>` : ''}</div>`;
}
function wzPie() {
  const r = W.prueba;
  if (W.paso === 1) return `<button class="btn ghost" type="button" data-wz="cerrar">Ahora no</button><span class="sp"></span><button class="btn primary" type="button" data-wz="sig">Siguiente</button>`;
  if (W.paso === 2) return `<button class="btn ghost" type="button" data-wz="atras">Atrás</button><span class="sp"></span>${W.modo === 'agente' ? `<button class="btn" type="button" data-wz="powershell">Abrir PowerShell</button>` : ''}<button class="btn primary" type="button" data-wz="sig">Siguiente</button>`;
  const fallo = r && r.terminada && !r.listo, bien = r && r.terminada && r.listo;
  return `<button class="btn ghost" type="button" data-wz="atras">Atrás</button><span class="sp"></span>
    ${fallo || !r ? '<button class="btn ghost" type="button" data-wz="guardar">Guardar sin probar</button>' : ''}
    <button class="btn${bien ? '' : ' primary'}" type="button" data-wz="terminar"${bien ? '' : ' disabled'}>Terminar</button>
    ${bien ? `<button class="btn primary" type="button" data-wz="grabar">Grabar mi primera reunión</button>` : ''}`;
}
function wzPintar() {
  const d = W.d;
  if (!d) return;
  wzParar();
  W.pieSig = '';
  d.innerHTML = `${wzCabecera()}<div class="wz-body">${[wzPaso1, wzPaso2, wzPaso3][W.paso - 1]()}</div><div class="wz-foot">${wzPie()}</div>`;
  $('.wz-body h2', d)?.focus();
  if (W.paso === 2 && W.modo !== 'api') {
    const pm = $('#procModelos', d);
    if (pm) { pm.hidden = false; if (typeof bindModelos === 'function') bindModelos(d); }
    modelosRefrescar(3000);
  }
  if (W.paso === 2 && W.modo === 'agente') wzMirarClaude();
  if (W.paso === 3 && W.prueba && !W.prueba.terminada) wzSeguir(W.prueba.id);
  if (W.paso === 3 && W.empezar && !W.prueba) { W.empezar = false; wzProbar(); }  // «Probar» de la tarjeta: ya
}
function wzRefrescarPie() {  // solo si cambia (repintarlo cada 400 ms le quitaba el foco a quien estuviera en él)
  const f = $('.wz-foot', W.d), r = W.prueba;
  const sig = `${W.paso}|${r ? (r.terminada ? (r.listo ? 'ok' : 'mal') : 'va') : 'no'}`;
  if (f && sig !== W.pieSig) { f.innerHTML = wzPie(); W.pieSig = sig; }
}
/* paso 2 con Claude: ¿ya está instalado? (cada 3 s mientras se ve este paso) */
async function wzMirarClaude() {
  clearTimeout(W.agTimer);
  if (!W.d || W.paso !== 2 || W.modo !== 'agente') return;
  try {
    const r = await api('/api/agente/estado');
    const e = $('[data-wz-encontrado]', W.d), n = $('[data-wz-num]', W.d);
    // origen: «Claude Code de VS Code» si es la copia de la extensión (rama claude-empresa)
    const de = r.origen && r.origen !== r.label ? ` (${r.origen})` : '';
    if (e) { e.textContent = r.encontrado ? `Instalado en este PC${de}` : 'Todavía no lo encuentro en este PC'; e.classList.toggle('ok', !!r.encontrado); }
    if (n) { n.classList.toggle('ok', !!r.encontrado); n.innerHTML = r.encontrado ? I.check : '1'; }
    if (S.config.agente) S.config.agente.encontrado = !!r.encontrado;
  } catch { /* se reintenta */ }
  W.agTimer = setTimeout(wzMirarClaude, 3000);
}
/* lo elegido, tal como lo usan /api/probar (candidata) y /api/settings */
function wzCandidata() {
  const out = { procesado: W.modo };
  if (W.modo === 'agente') out.agente = { tipo: S.config.agente?.tipo || 'claude', modelo: S.config.agente?.modelo };
  if (W.modo === 'api') {
    const c = { provider: W.cloud.provider };
    const misma = W.cloud.provider === (S.config.cloud?.provider || 'groq');
    if (!misma) c.model = (S.config.providers?.[W.cloud.provider]?.models || [])[0] || '';
    if (W.cloud.api_key) c.api_key = W.cloud.api_key;
    if (W.cloud.provider === 'custom') c.base_url = W.cloud.base_url;
    if (W.cloud.provider === 'azure') Object.assign(c, { azure_endpoint: W.cloud.azure_endpoint, azure_deployment: W.cloud.azure_deployment, azure_api_version: W.cloud.azure_api_version });
    out.cloud = c;
  }
  return out;
}
function wzLeerCampos() {
  const antes = JSON.stringify(W.cloud);
  wzLeerCampos2();
  if (JSON.stringify(W.cloud) !== antes) wzCancelarPrueba();  // otra clave u otra dirección: la prueba ya no vale
}
function wzLeerCampos2() {
  const d = W.d, v = id => $(id, d)?.value?.trim();
  if (v('#wzKey') != null) W.cloud.api_key = v('#wzKey') || '';
  if (v('#wzBase') != null) W.cloud.base_url = v('#wzBase') || '';
  if (v('#wzAzEp') != null) Object.assign(W.cloud, { azure_endpoint: v('#wzAzEp') || '', azure_deployment: v('#wzAzDep') || '', azure_api_version: v('#wzAzVer') || '' });
}
async function wzBajar() {  // los dos modelos del PC, ya (se ve en el paso 2 y la prueba los espera)
  for (const m of cfgModelosNecesarios()) {
    const st = S.modelos?.[m];
    if (st && (st.ready || st.downloading)) continue;
    try { await api('/api/modelos/descargar', { model: m }); } catch (err) { toast(esc(err.message), 'err'); }
  }
  modelosRefrescar(4000);
}
async function wzProbar() {
  const b = $('[data-wz="probar"]', W.d);
  if (b) b.disabled = true;
  try {
    const r = await api('/api/probar', { candidata: wzCandidata() });
    W.prueba = { id: r.id, terminada: false, pasos: [] };
    wzSeguir(r.id);
  } catch (err) {
    W.prueba = { terminada: true, listo: false, pasos: [], fallo: { frase: err.message, accion: null } };
    wzPintarPrueba();
  }
}
async function wzSeguir(id) {
  clearTimeout(W.timer);
  if (!W.d || W.paso !== 3 || !W.prueba || W.prueba.id !== id) return;
  let r;
  try { r = await api('/api/probar?id=' + encodeURIComponent(id)); } catch (err) { r = { id, terminada: true, listo: false, pasos: W.prueba?.pasos || [], fallo: { frase: err.message } }; }
  if (!W.prueba || W.prueba.id !== id || (r.modo && r.modo !== W.modo)) return;  // de otra prueba o de otro modo: fuera
  const acaba = r.terminada && !W.prueba.terminada;
  W.prueba = r;
  wzPintarPrueba();
  if (acaba) $('.wz-res', W.d)?.focus();
  if (!r.terminada) W.timer = setTimeout(() => wzSeguir(id), 400);
}
function wzPintarPrueba() {
  const d = W.d;
  if (!d) return;
  const r = W.prueba, p = $('[data-wz-pasos]', d), res = $('[data-wz-res]', d), b = $('.wz-probar [data-wz="probar"]', d);
  if (p) p.innerHTML = wzPasosHtml(r);
  if (res) res.innerHTML = wzResHtml(r);
  if (b) { b.disabled = !!(r && !r.terminada); b.textContent = r && !r.terminada ? 'Probando…' : r ? 'Probar otra vez' : 'Probar'; }
  const t = $('[data-wz-probando]', d);
  if (t) t.textContent = r && !r.terminada ? `${Math.round((Date.now() / 1000) - (r.empezada || Date.now() / 1000))} s` : '';
  const c = $('[data-wz="cancelar-prueba"]', d);
  if (c) c.hidden = !(r && !r.terminada);
  // lo que oye un lector de pantalla: el paso en curso y cómo acaba (no cada segundo ni cada %)
  const cur = r && (r.pasos || []).find(x => x.estado === 'en_curso');
  const anuncio = !r ? '' : r.terminada ? (r.listo ? 'Prueba terminada: todo bien.' : 'La prueba ha fallado.')
    : cur ? `${cur.texto}${/^Habla ahora/.test(cur.detalle || '') ? ': habla ahora.' : '…'}` : '';
  const an = $('[data-wz-anuncio]', d);
  if (an && anuncio !== W.anuncio) { an.textContent = anuncio; W.anuncio = anuncio; }
  wzRefrescarPie();
}
async function wzGuardar(despues) {
  // al salir de la API con trabajo en la nube: lo que espera allí se cancela (lo hace el servidor; aquí se avisa)
  const enNube = S.config.procesado === 'api' && W.modo !== 'api'
    && S.jobs.some(j => isActive(j) && j.kind !== 'summary' && (j.engine === 'cloud' || j.preset === 'nube'));
  const n = S.jobs.filter(j => isActive(j) && j.kind !== 'summary' && (j.engine === 'cloud' || j.preset === 'nube')).length;
  if (enNube) {
    const volver = W.volver;
    wzParar();
    S.modalCleanup = null;  // la confirmación ocupa el hueco: cerrarla no es cerrar el asistente
    W.d = null;
    if (!await ask('¿Cambiar de opción?', `<p>Hay ${n} ${n === 1 ? 'transcripción esperando' : 'transcripciones esperando'} en ${esc(provName())}. Si cambias ahora, se cancelan y las podrás relanzar en este PC.</p>`, 'Cambiar')) {
      return abrirConfigurar({ paso: 3, modo: W.modo, volver });
    }
  }
  try {
    const c = await api('/api/settings', Object.assign(wzCandidata(), W.prueba?.id && W.prueba.terminada ? { prueba: W.prueba.id } : {}));
    setConfig(c);
    localStorage.setItem('procElegido', '1');
    const volver = W.volver;
    W.guardado = true;
    closeModal(true);
    toast(`Guardado: ${esc(modoNombre(c.procesado))}. ${esc(modoLinea(c.live_preset || 'medium'))}.`);
    if (c.descarga) modelosRefrescar(4000);
    refreshView();
    if (despues === 'grabar') openRec();
    else if (volver) volver();
  } catch (err) { toast(esc(err.message), 'err'); }
}
async function wzAccion(tipo, b) {
  if (tipo === 'reintentar') return wzProbar();
  if (tipo === 'abrir_powershell') return api('/api/abrir-powershell', {}).catch(err => toast(esc(err.message), 'err'));
  if (tipo === 'abrir_url') return api('/api/acerca/abrir', { que: 'claves', proveedor: W.cloud.provider }).catch(() => window.open(b.dataset.url, '_blank'));
  // con Claude, el modelo se elige en Ajustes › Procesado (el paso 2 no lo tiene); con la API, la clave y la dirección,
  // en el paso 2
  // a una sección de Ajustes: si el asistente se abrió desde Ajustes, es una vuelta (con lo escrito allí) y su cierre
  // ya no programa la vuelta a Procesado (que acababa ganando a «Avanzado»)
  const aAjustes = sec => { const v = !!W.volver; W.volver = null; closeModal(false); return openSettings({ sec, volver: v }); };
  if (tipo === 'procesado' && W.modo === 'agente') return aAjustes('procesado');
  if (tipo === 'asistente' || tipo === 'procesado') { W.paso = 2; return wzPintar(); }
  if (tipo === 'reintentar_descarga') { await wzBajar(); return wzProbar(); }
  if (tipo === 'avanzado') return aAjustes('avanzado');
  if (tipo === 'diagnostico') { try { const r = await api('/api/diagnostico'); await copy(r.texto, 'Diagnóstico copiado: revísalo antes de pegarlo donde pidas ayuda'); } catch (err) { toast(esc(err.message), 'err'); } }
}
document.addEventListener('change', e => {
  if (!W.d || !W.d.contains(e.target)) return;
  if (e.target.name === 'wzModo') { W.modo = e.target.value; wzCancelarPrueba(); wzPintar(); $(`input[name="wzModo"][value="${W.modo}"]`, W.d)?.focus(); }
  if (e.target.id === 'wzProv') { wzLeerCampos(); W.cloud.provider = e.target.value; W.cloud.api_key = ''; W.provAbierto = true; wzCancelarPrueba(); wzPintar(); }
});
document.addEventListener('click', async e => {
  if (!W.d || !W.d.contains(e.target)) return;
  const accion = e.target.closest('[data-wz-accion]');
  if (accion) { e.preventDefault(); return wzAccion(accion.dataset.wzAccion, accion); }
  const b = e.target.closest('[data-wz]');
  if (!b) return;
  e.preventDefault();
  const q = b.dataset.wz;
  if (q === 'cerrar') return closeModal(false);
  if (q === 'atras') { wzLeerCampos(); W.paso = Math.max(1, W.paso - 1); return wzPintar(); }
  if (q === 'sig') {
    if (W.paso === 1) { W.paso = 2; if (W.modo !== 'api') wzBajar(); return wzPintar(); }
    if (W.paso === 2) {
      wzLeerCampos();
      if (W.modo === 'api') {
        const c = S.config.cloud || {}, misma = W.cloud.provider === (c.provider || 'groq');
        const err = !W.cloud.api_key && !(misma && c.has_key) ? 'Pega la clave para seguir.'
          : W.cloud.provider === 'custom' && !W.cloud.base_url ? 'Escribe la dirección de la API.'
            : W.cloud.provider === 'azure' && (!W.cloud.azure_endpoint || !W.cloud.azure_deployment) ? 'Faltan la dirección del recurso y el nombre del despliegue.' : '';
        if (err) { const x = $('[data-wz-err]', W.d); if (x) x.textContent = err; return; }
      }
      W.paso = 3; return wzPintar();
    }
  }
  if (q === 'prov') { W.provAbierto = true; return wzPintar(); }
  if (q === 'claves') return api('/api/acerca/abrir', { que: 'claves', proveedor: W.cloud.provider }).catch(() => window.open(S.config.providers?.[W.cloud.provider]?.keys, '_blank'));
  if (q === 'copiar') return copy('irm https://claude.ai/install.ps1 | iex', 'Copiado: pégalo en PowerShell y pulsa Intro');
  if (q === 'powershell') return api('/api/abrir-powershell', {}).catch(err => toast(esc(err.message), 'err'));
  if (q === 'bajar') return wzBajar();
  if (q === 'probar') return wzProbar();
  if (q === 'cancelar-prueba') { wzCancelarPrueba(); return wzPintar(); }
  if (q === 'guardar' || q === 'terminar') return wzGuardar();
  if (q === 'grabar') return wzGuardar('grabar');
});
document.addEventListener('keydown', e => {
  if (!W.d || !document.body.contains(W.d) || e.key !== 'Enter' || e.target.closest('textarea, select, a, button, summary, details')) return;
  const s = $('.wz-foot [data-wz="sig"]', W.d);
  if (s) { e.preventDefault(); s.click(); }
});
document.addEventListener('modelos', () => {
  const box = W.d && $('[data-wz-descarga]', W.d);
  if (box) box.outerHTML = wzDescargaHtml(W.modo === 'agente');
});

/* Inicio: tras actualizar, una vez, si la configuración nunca se ha probado (decisión de Claudia, 07/11/2026) */
function avisoProbarHtml() {
  if (!S.config || S.config.ultima_prueba?.ok || localStorage.getItem('avisoProbar')) return '';
  // tras actualizar, no en una instalación nueva (ahí está la tarjeta de bienvenida): ya se eligió o ya hay reuniones
  const nueva = S.config.procesado === 'local' && !localStorage.getItem('procElegido');
  if (nueva || !(typeof allAudios === 'function' && allAudios().length || S.config.procesado !== 'local' || localStorage.getItem('procElegido'))) return '';
  return `<div class="notice info aviso-probar" id="avisoProbar" style="margin:0 0 14px">${I.info}<span>${esc(APP_NAME)} puede probar ahora tu configuración de punta a punta: graba 3 s de tu voz, los transcribe y, si toca, los resume.</span>
    <button class="btn sm primary" type="button" data-act="cfg-probar">Probar ahora</button><button class="btn sm ghost" type="button" data-act="cfg-aviso-no">Ahora no</button></div>`;
}

/* Ajustes › Procesado: la tarjeta con lo que hay ahora, la última prueba, «Probar» y «Cambiar…» (abren el asistente) */
function cfgCuando(t) {
  if (!t) return '';
  const d = new Date(t * 1000), hoy = new Date();
  const hora = d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === hoy.toDateString()) return `hoy a las ${hora}`;
  return `el ${d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' }).replace('.', '')} a las ${hora}`;
}
function cfgEstadoHtml() {
  const c = S.config || {}, modo = c.procesado || 'local', u = c.ultima_prueba;
  const live = S.presetsById?.[c.live_preset] || {};
  const transcribe = live.engine === 'cloud' ? `<b>${esc(cfgProv(c))}</b>` : `<b>este PC</b>, modelo ${esc(modeloNombre(live.model || 'medium'))}`;
  const agM = (c.agente?.modelos || []).find(m => m[0] === c.agente?.modelo);
  const resume = modo === 'local' ? 'no se resume con IA'
    : modo === 'agente' ? `<b>Claude${agM ? ` (${esc(String(agM[1]).split(' ·')[0])})` : ''}</b>, con tu cuenta`
      : `<b>${esc(cfgProv(c))}</b>${c.summary?.model ? ` (${esc(c.summary.model)})` : ''}`;
  const st = !u ? '<div class="st">Sin probar todavía</div>'
    : u.ok ? `<div class="st ok">Probado ${esc(cfgCuando(u.cuando))}</div>`
      : `<div class="st err">La última prueba falló (${esc(cfgCuando(u.cuando))}): ${esc(u.frase || '')}</div>`;
  return `<div class="cfg-estado"><div class="cfg-txt"><div class="m">${esc(modoNombre(modo))}</div>
    <div class="r">Transcribe: ${transcribe}</div><div class="r">Resume: ${resume}</div>${st}</div>
    <div class="cfg-acts"><button class="btn sm" type="button" data-cfg="probar">Probar</button><button class="btn sm" type="button" data-cfg="cambiar">Cambiar…</button></div></div>`;
}

/* El botón de un error fuera del asistente (la Cola y la reunión): el mismo que en «Probar» (explicar.py) */
function cfgAccionHtml(j) {
  const a = j?.aviso?.accion;
  if (!a || a.tipo === 'reintentar') return '';  // reintentar ya lo tienen («Reintentar», «Rehacer»…)
  const model = S.presetsById?.[j.preset]?.model || '';
  return ` <button class="btn sm cfg-accion" type="button" data-cfg-accion="${esc(a.tipo)}"${a.url ? ` data-url="${esc(a.url)}"` : ''}${model ? ` data-model="${esc(model)}"` : ''}>${esc(a.etiqueta)}</button>`;
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-cfg-accion]');
  if (!b) return;
  e.preventDefault();
  e.stopPropagation();
  const t = b.dataset.cfgAccion;
  if (t === 'abrir_powershell') return api('/api/abrir-powershell', {}).catch(err => toast(esc(err.message), 'err'));
  if (t === 'abrir_url') return api('/api/acerca/abrir', { que: 'claves', proveedor: S.config?.cloud?.provider }).catch(() => window.open(b.dataset.url, '_blank'));
  if (t === 'asistente') return abrirConfigurar({ paso: 2, modo: S.config?.procesado });
  if (t === 'avanzado' || t === 'procesado') return openSettings({ sec: t });
  if (t === 'reintentar_descarga' && b.dataset.model) return modeloBajar(b.dataset.model);
  if (t === 'diagnostico') {
    try { const r = await api('/api/diagnostico'); await copy(r.texto, 'Diagnóstico copiado: revísalo antes de pegarlo donde pidas ayuda'); } catch (err) { toast(esc(err.message), 'err'); }
  }
});
