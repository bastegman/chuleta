# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

### Cambiado
- La app es agnóstica: ejemplos, placeholders, avisos y el manual ya no nombran a ningún cliente, empresa ni
  persona. Los avisos de privacidad hablan de «la política de tu empresa y el acuerdo con tu cliente, si lo hay».
- La etiqueta «Workstream» de la interfaz pasa a «Tema». Los ficheros `.md` siguen usando las claves `proyecto`
  y `workstream`, así que las reuniones existentes no cambian.
- Los textos de la interfaz nombran al proveedor configurado («Hecho con IA en Groq/OpenAI/…») en vez de dar
  por hecho Groq.
- El idioma por defecto en instalaciones nuevas es «Detectar». La opción «Español + inglés» sigue ahí y los
  ajustes ya guardados no cambian.
- `requirements.txt`, `LICENSE` (MIT), `README.md` y este fichero.

### Corregido
- `fullTitle` (app.js) perdía el título de la reunión en la barra del reproductor, los avisos, el directo, la
  búsqueda, la cola y el calendario (salía « · 30 oct · 15:03»).
- La cola trataba el resumen con IA como una transcripción: mientras se resumía no se podía editar, restaurar,
  mover, borrar ni volver a transcribir la reunión («se está transcribiendo»). `Jobs.active_for` solo mira ya
  transcripciones; borrar y mover avisan con su propio mensaje si hay un resumen en marcha (el `.resumen.md` se
  escribiría en la ruta vieja).
- El zip de «Crear instalador para otra persona» no llevaba `_app/export/`, así que una copia instalada desde un
  zip no podía volver a exportar. Ahora lo incluye (con el marcador `{FECHA}` intacto) y empaqueta todos los
  `.py/.bat/.ps1` de `_app` en vez de una lista fija.
- `decode_16k` materializaba todo el audio decodificado antes de remuestrear (cientos de MB por hora de reunión
  en la nube); ahora va frame a frame.
- Las carpetas `Vocabulario` salían en el árbol y en la búsqueda como documentos; ahora quedan fuera (y se siguen
  leyendo para los temas).
- `list_vocab` no recorría las carpetas vinculadas; ahora sí, y al exportar sus listas van como
  `<proyecto>/Vocabulario/<tema>.txt`.
- El resumen en inglés salía con las cabeceras en español; ahora van en el idioma elegido (con «el de la
  reunión» se pide traducirlas).
- Renombrar la carpeta en la que la grabadora propia está escribiendo daba un error 500; ahora avisa.
- Si la API de chat rechaza `max_completion_tokens` (pasarelas antiguas), se reintenta con `max_tokens`.
- Ctrl+K enfoca la caja de búsqueda de arriba (como dice su `kbd`) y Ctrl+P abre la paleta.
- Ajuste muerto `rec_live` eliminado.

### Configuración
- Modelo de nube por defecto `whisper-large-v3` (el más preciso; antes `-turbo`), como recomienda el LEEME.
- `qwen/qwen3.8-27b` en la lista de modelos de resumen queda marcado `TODO verificar`.
- Instalador: busca Python de 3.10 a 3.13 probando primero `py -3.13`, `-3.12`, `-3.11` y `-3.10`, y avisa si
  solo hay uno más nuevo; si pip falla distingue «librería sin versión para este Python» de «proxy o red»; al
  actualizar una copia anterior copia también `_app/export/`, `LEEME.txt` y los `.bat` de la raíz. Los ficheros
  que una versión nueva retire no se borran de la copia antigua (si algún día se retira un módulo, se dirá aquí
  y habrá que borrarlo a mano).

### Añadido
- **Quién habla** (opcional, apagado por defecto): diarización en local con `sherpa-onnx` 2.0.1 (segmentación
  pyannote 3.0 + huellas TitaNet small, ≈47 MB de modelos que se descargan al activar la opción, sha256 anclado)
  en un subproceso de prioridad baja al terminar cada transcripción (`_app/voices.py`). Sidecar
  `<grabación>.hablantes.json` junto al audio (se mueve y borra con él; sin datos de voz) y `voces.json` en
  `%LOCALAPPDATA%\AudiosApp` cifrado con DPAPI con las personas y hasta 5 huellas cada una, solo con «Recordar» y
  consentimiento. En la transcripción, párrafo y etiqueta por hablante («Persona 2», «Javier», «Tú» por la actividad
  del micro, «varios»); clic → nombre, «Soy yo», «Recordar», «Fusionar con…», «No es esta persona», «Olvidar».
  El resumen con IA recibe los nombres («[12:34] Javier: …») y «Participantes» se sugiere con los reconocidos.
  «Exportar con hablantes» → `<grabación>.hablantes.txt`. Ajustes → «🗣️ Voces (opcional)» con el interruptor,
  la descarga de modelos y la lista de personas. API: `GET /api/voces`, `GET /api/hablantes`,
  `POST /api/voces/activar|identificar|persona|fusionar|olvidar|reasignar|exportar`.
- La API key de la nube se guarda cifrada con DPAPI en `settings.json` (migración transparente al primer
  guardado; si no se puede descifrar queda vacía, se anota en `server.log` y hay que volver a pegarla).
- `sherpa-onnx==2.0.1` en `requirements.txt` y en el instalador; si no importa, el instalador solo avisa y la app
  funciona sin la opción de voces.
- `_app/export/ (plantillas del instalador con la fecha como `{FECHA}`), que faltaba en el zip exportado.
- `server.log` con rotación (3 × 1 MB) en `%LOCALAPPDATA%\AudiosApp`, además de la consola; las trazas de error
  también van ahí.
- El servidor arranca en macOS/Linux para desarrollar (`python3 _app/server.py --port 8766 --data /tmp/x
  --no-browser`): atajo global, pitidos, avisos, detección de reunión, Explorador y papelera quedan desactivados
  o con alternativa (`open`/`xdg-open`; la «papelera» es `DATA_DIR/papelera/<fecha>/`).
- Tests (`tests/`, `python tests/run.py`), `flake8` (`setup.cfg`) y CI en GitHub Actions (Windows y Ubuntu ×
  Python 3.10–3.13).
- `scripts/build_zip.py` genera `dist/Notas - instalador.zip` sin vocabulario.
- `server.py` se reparte en módulos planos (el instalador solo copia ficheros sueltos de `_app`): `common.py`
  (utilidades puras y log) y `recording.py` (atajo global, pitidos, avisos, proceso de la grabadora y detección
  de reunión). El árbol, la cola y la API siguen en `server.py` (≈2.800 líneas): separarlos exige un módulo de
  estado compartido (ROOT, LINKS, COMPLETE, DATA_DIR cambian en tiempo de ejecución) y se deja para otra entrega.

### Conocido, sin cambiar
- «Quién habla» y el cifrado con DPAPI se han probado en macOS (sherpa-onnx real; DPAPI con un doble) y todavía no a
  mano en Windows: la primera ejecución real de DPAPI es el job `windows-latest` de GitHub Actions.
- Si DPAPI falla en Windows, los secretos se guardan en claro (`plain:`) para no dejar la app sin ajustes; solo se
  avisa en `server.log`.
- El aviso de Windows (toast) sale firmado por «Windows PowerShell»: cambiarlo exige registrar un
  AppUserModelID propio en el acceso directo (propiedad del `.lnk` vía COM); se deja como está.
