# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

### Cambiado
- Nueva identidad **Chuleta**: logo (chuleta con hueso) en la cabecera, en el icono de la app y en el favicon;
  paleta burdeos/rosa sobre neutros cálidos con contraste AA en el texto, también en modo oscuro; iconos de línea
  (SVG) en vez de emojis en la navegación, los botones, las pestañas, las propiedades, Ajustes y las etiquetas de
  formulario (los iconos que elige el usuario para carpetas y reuniones siguen siendo emojis, y los avisos
  emergentes no cambian); etiquetas de hablante con un color estable por persona; botones, chips, diálogos y
  tablas con radios, sombras y estados de foco coherentes. Los demás textos «Notas» se cambian en el renombrado.
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
- El icono de la app (acceso directo, barra de tareas, pestaña): `notas.ico` se genera ahora con `make_icons.py`
  desde el SVG, con los tamaños de 16 y 20 px dibujados aparte (sin anillas, renglones de 1 px) en vez de ser una
  reducción borrosa del de 256, y todas las entradas en BMP (el anterior las llevaba en PNG, que Windows no siempre
  pinta bien en tamaños pequeños). La página usa el `.ico` y el PNG de 256 como favicon en vez del SVG.
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
- **Directo en la nube casi en streaming** (`_app/live.py`, `_app/engines.py`, `_app/static/directo.js`): con
  Groq el directo manda trozos de 3–6 s en vez de 8–15: corta en la primera pausa de la voz a partir de 3 s y, si
  nadie calla, a los 6 s con 0,5 s de solape que se deduplica por tiempos; solo se envía la voz (los silencios de
  los extremos se recortan y una ventana sin voz no se manda ni cuenta). El modelo del directo es configurable
  (`cloud.live_model`, por defecto `whisper-large-v3-turbo`); el repaso final sigue con `cloud.model`. Se mide la
  latencia de cada trozo (fin del audio → texto escrito) y se enseña la mediana y el p95 de la sesión. Lo local
  (20–45 s) y «ponerse al día» (120–300 s) no cambian.
- **Cupo y ritmo del plan gratuito de Groq**: por clave se guardan `x-ratelimit-remaining-requests`,
  `x-ratelimit-reset-requests` y el último `retry-after` (la primera vez se anotan en `server.log` todas las
  cabeceras `x-ratelimit-*`), nunca más de una petición cada 3 s por clave (20/min) y la ventana del directo crece
  según lo que queda del día: ≥600 peticiones → 3–6 s; 300–600 → 6–10 s; <300 → 10–15 s; <60 → el directo se pausa
  con aviso (la grabación sigue, lo que falte se transcribe al terminar en trozos grandes y el repaso final se hace
  igual). La cabecera del directo y Ajustes enseñan «Groq: quedan N peticiones hoy · se renuevan en …».
- **Claves adicionales** (`cloud.extra_keys`, cifradas como la API key): cuando a la clave activa le quedan menos
  de 20 peticiones del día, o Groq responde 429 con `retry-after` > 60 s, se pasa a la siguiente; el resumen, la
  traducción y el repaso final usan la misma rotación. En Ajustes → nube, «Claves adicionales (opcional)» con el
  aviso de que cada clave es de una cuenta distinta y el audio y el texto se procesan bajo esa cuenta. Las claves
  no se escriben nunca en el log ni salen en claro por la API.
- **Seguir la reunión**: el último trozo llegado queda resaltado y el texto baja solo; «Modo seguir» (letra grande,
  para leer de reojo); y, por reunión, «Traducir al español» (apagado por defecto): cada 10–15 s manda lo último
  dicho a `openai/gpt-oss-20b` y lo pinta en una columna lateral (una ayuda para seguir la reunión; no se guarda).
  API: `POST /api/live/traducir`; el estado del directo (`directo` en `/api/jobs`) lleva cupo, ventana, latencia y
  traducción.
- `scripts/directo_bench.py`: audio sintético con `say` (español y portugués, con pausas) y reproducción de un WAV
  por el chunker con un transcriptor falso de latencia configurable (`--real` usa Groq con `GROQ_API_KEY`).
  `tests/test_directo.py` y `tests/test_directo_frontend.py`.
- **Reunión en curso al grabar** (`_app/calendario.py`, `_app/static/reunion.js`): al pulsar Grabar (o Ctrl+Alt+R)
  la app sugiere el nombre, el tema de vocabulario y los participantes a partir de dos fuentes locales: el título
  de la ventana de reunión de Teams (nivel 1, siempre y sin activar nada: `EnumWindows` sobre los procesos
  `ms-teams.exe`/`Teams.exe`/`msteams.exe`, descartando la ventana principal) y, si se enciende en Ajustes →
  Reuniones, la cita en curso del Outlook clásico de escritorio por COM (nivel 2, `pywin32`, opcional: asunto,
  horas, organizador, asistentes sin correos, categorías y si es de Teams; `Sort` → `IncludeRecurrences` →
  `Restrict` con la fecha en el formato regional del usuario y sin segundos, consulta con tope de 8 s en un hilo,
  y motivos legibles si Outlook no está abierto, es el nuevo Outlook, está ocupado o se rechaza su aviso de
  seguridad). El tema es la lista de vocabulario con más términos (y nombre de proyecto/tema) presentes en asunto,
  categorías, organizador y asistentes, con un umbral para no sugerir a ciegas. Los participantes sugeridos van a
  la cabecera del `.md` de la grabación nueva (en este PC). Botón «Probar» en Ajustes que dice qué reunión ve en
  ese momento. `GET /api/reunion/actual`; ajustes `outlook_on` (apagado por defecto) y `rec_aviso`.
- **Aviso flotante al detectar una llamada** (`_app/aviso.py`, proceso aparte): ventanita abajo en el centro del
  monitor principal (sobre la barra de tareas, DPI aware, sin borde, siempre visible y que no roba el foco:
  `WS_EX_NOACTIVATE`) con el título de la reunión, «● Grabar» y «✕». «Grabar» empieza con la configuración de
  Ctrl+Alt+R y el nombre/tema sugeridos; si ya se grababa no toca nada. Se cierra sola a los 20 s, al grabar
  (también con Ctrl+Alt+R), al terminar la llamada o si la app se cierra. Sale una vez por llamada y, mientras
  está abierta, sustituye al toast de Windows de «¿Quieres tomar notas?».
- `pywin32==312` en `requirements.txt` y en el instalador (opcional y aparte, como `sherpa-onnx`: si no se instala,
  solo se avisa). Tests con dobles (`tests/test_reunion.py`) y de escritorio en Windows
  (`tests/test_reunion_windows.py`, workflow `reunion-windows`: ventana de Teams simulada, aviso con captura de
  pantalla y pulsación de «Grabar»).
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
- «Quién habla» y el cifrado con DPAPI se han probado en macOS (sherpa-onnx real; DPAPI con un doble); DPAPI real,
  en el job `windows-latest` de GitHub Actions. El instalador, la grabadora y la interfaz de voces aún no se han
  ejecutado a mano en Windows.
- Si DPAPI falla en Windows, los secretos se guardan en claro (`plain:`) para no dejar la app sin ajustes; solo se
  avisa en `server.log`.
- El aviso de Windows (toast) sale firmado por «Windows PowerShell»: cambiarlo exige registrar un
  AppUserModelID propio en el acceso directo (propiedad del `.lnk` vía COM); se deja como está.
- La lectura de Outlook se ha probado con un doble del modelo de objetos y, en el job de Windows, solo el camino
  «sin Outlook abierto» (pywin32 real). Con un Outlook clásico de verdad (citas periódicas, aviso de seguridad,
  nombres de asistentes) aún no se ha ejecutado: se comprueba con el botón «Probar» de Ajustes → Reuniones.
- La ventana de reunión de Teams se reconoce por el sufijo «| Microsoft Teams» de su título; un chat abierto en
  ventana aparte o una llamada 1:1 se tomarían como reunión (es una sugerencia, siempre editable).
