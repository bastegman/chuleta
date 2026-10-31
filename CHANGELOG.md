# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

### Instalación de empresa: carpeta propia, Aplicaciones instaladas y desinstalador
- **Carpeta propia**: sin instalación anterior, `INSTALAR.bat` copia la app de la carpeta descomprimida (Descargas…)
  a `%USERPROFILE%\Chuleta` (fuera de Descargas y de OneDrive; si esa carpeta ya tiene otras cosas, `Chuleta (2)`…;
  `-Destino <carpeta>` para otra nueva o vacía) con `_app`, `LEEME.txt`, `LICENSE`, los `.bat` y las listas
  `*\Vocabulario\*.txt` del zip, y crea allí los accesos directos. La ventana enseña el paso («Instalando en …») y al
  final dónde ha quedado y que la carpeta descomprimida ya se puede borrar. Una carpeta ya instalada (con
  `_app\python.txt`) o la que ya es `%USERPROFILE%\Chuleta` no se copian.
- **Detección de la instalación anterior**, por este orden: la entrada de Aplicaciones instaladas (`InstallLocation`,
  si sigue teniendo `_app\server.py`), `Chuleta.lnk`/`Notas.lnk` en el escritorio y en Inicio, y
  `%USERPROFILE%\Chuleta`. Se actualiza en su sitio como antes (mismo copiado, ahora en `Copy-AppFiles`) y el detalle
  dice cómo se encontró. Con `-NoShortcut` o `-Desktop` (pruebas) no se lee ni se escribe nada del sistema.
- **Aplicaciones instaladas**: `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chuleta` (sin
  administrador) con `DisplayName`, `DisplayVersion` (la de `_app\version.txt`; si no hay, la fecha), `Publisher`,
  `DisplayIcon`, `InstallLocation`, `InstallDate` (`yyyyMMdd`), `URLInfoAbout`, `UninstallString` y
  `QuietUninstallString` (REG_SZ) y `EstimatedSize` (KB de `_app`), `NoModify` y `NoRepair` (REG_DWORD). Si una
  política de la empresa no lo deja, solo se avisa: la app funciona igual.
- **Desinstalador** (`_app\desinstalar.ps1`): ventana con el estilo del instalador («Se quitará la aplicación
  Chuleta de este PC. Tus reuniones, transcripciones y notas en … NO se borran.»), casilla desmarcada para borrar
  también `%LOCALAPPDATA%\AudiosApp` (con el aviso de que las huellas de voz son datos biométricos y borrarlas es
  definitivo), «Desinstalar»/«Cancelar» y, al terminar, «Chuleta se ha desinstalado. Tus reuniones siguen en …» con
  «Abrir la carpeta». Apaga la app con `/api/apagar` (si está grabando, 409: avisa y no toca nada), espera a que no
  quede ningún proceso suyo, quita los accesos directos que apuntan a esa instalación, la entrada y `_app` (solo
  ficheros de la app, por extensión de código o por nombre exacto: lo demás se queda y se dice; el desinstalador se
  borra el último para poder reintentar) y los `.bat` de la raíz; deja `LEEME.txt` y `LICENSE` junto a las reuniones
  (así una reinstalación vuelve a la misma carpeta). Se copia a `%TEMP%` y se relanza desde allí; `-Silencioso` sin
  ventana (no borra `AudiosApp` salvo con `-BorrarDatos`); registro en `%TEMP%\chuleta-desinstalar.log`.
- **Tests y E2E**: `tests/test_desinstalar.py` (paquete, parseo con PowerShell, BOM/CRLF, valores y tipos del
  registro, orden de detección, ningún `Remove-Item` sobre la raíz ni extensiones de reunión y, en Windows,
  desinstalación real sobre una carpeta temporal comparando las reuniones por sha256). El E2E de Windows instala desde
  `Descargas-test` en `%USERPROFILE%\Chuleta`, vuelca la entrada del registro, crea una reunión falsa, actualiza por
  el registro y por `Notas.lnk`, y desinstala cancelando, con la app «grabando», en silencio con la app abierta y,
  tras reinstalar, con «Borrar también mis ajustes»; la reunión se compara por sha256 en cada fase.

### Renombrado a Chuleta
- La app pasa a llamarse **Chuleta** (`common.APP_NAME`): título de la ventana y de la pestaña, raíz del árbol,
  mensajes y avisos, cabecera `Server`, nombre del zip (`Chuleta - instalador.zip`) y su carpeta raíz (`Chuleta/`),
  `-AppName` del instalador y el acceso directo «Chuleta» («Chuleta: grabar, transcribir y resumir reuniones»).
  Ficheros renombrados conservando la historia: `ABRIR NOTAS.bat` → `ABRIR CHULETA.bat` (raíz y `_app/export`),
  `_app/Notas.bat` → `_app/Chuleta.bat`, `_app/static/notas.ico`/`.png` → `chuleta.ico`/`.png` (`make_icons.py`,
  `index.html`, la ventana y el instalador los usan).
- **No cambia, a propósito**, para que las instalaciones y los datos existentes sigan valiendo: la carpeta de datos
  `%LOCALAPPDATA%\AudiosApp`, la cabecera `X-Audios`, la respuesta de `/api/ping` (`"audios"`), las claves de los
  `.md` (`proyecto`, `workstream`), el AppUserModelID `Bernardo.Chuleta` (ya era este), la pestaña **Notas** de cada
  reunión y «Tomar Notas» (son la función de tomar notas, no la marca), `notas.js`, y las variables de entorno
  `NOTAS_*` que solo usan los tests y la CI.
- **Instalador**: encuentra la instalación anterior también por su acceso directo `Notas.lnk` (escritorio y, si allí
  no hay ninguno, menú Inicio), la actualiza en su carpeta como siempre y **renombra** `Notas.lnk` a `Chuleta.lnk`
  (nunca borra nada; si ya existiera un `Chuleta.lnk`, conserva el antiguo y lo dice en el detalle). Copia también
  `_app/version.txt` y `ABRIR CHULETA.bat`; `ABRIR NOTAS.bat` y `_app/Notas.bat` de la copia antigua se quedan (siguen
  funcionando) y el detalle avisa de que ya no hacen falta. Los mensajes llevan sus tildes («instalación»,
  «librerías», «Transcripción») y el «Listo» enseña la versión instalada.
- **Versión real**: `scripts/build_zip.py` escribe `_app/version.txt` con `AAAA-MM-DD · <sha corto>` (fecha del
  commit y `git rev-parse --short HEAD`; sin git, la fecha de hoy), lo mete en el zip y pone la misma cadena en la
  primera línea del `LEEME.txt` generado. `server.py` la enseña como versión si el fichero existe (si no, la fecha y
  hora del código, como antes); la recarga automática de la ventana al actualizar sigue basada en el mtime. Una copia
  instalada que vuelve a exportar conserva su versión.
- **Ajustes → Acerca de** (al final): nombre, versión, licencia (leída de `LICENSE`), enlace al repositorio
  (`github.com/bastegman/chuleta`), carpeta de datos con «Abrir» (`POST /api/acerca/abrir`) y «Copiar diagnóstico»
  (`GET /api/diagnostico`: versión, Python, sistema, motor y modelo, carpetas y las últimas 30 líneas de `server.log`
  con la API key, las claves adicionales y cualquier token con pinta de clave enmascarados).
- E2E de Windows (`scripts/e2e/lookfeel_e2e.ps1`, workflow `lookfeel-e2e`): instalación limpia que crea
  `Chuleta.lnk` y abre la ventana «Chuleta»; segunda instalación sobre una copia con `Notas.lnk` (la detecta, la
  actualiza, renombra el acceso directo y arranca con el título nuevo); tercera con los dos accesos directos
  (conserva `Notas.lnk`); e instalación que falla a propósito.

### Cambiado
- El árbol de reuniones (`/api/tree`, que la página sondea cada 6 s) ya no recorre el disco si ninguna carpeta ha
  cambiado de fecha y ninguna grabación abierta ha crecido (un `stat` por carpeta); cada 30 s se repasa todo en
  segundo plano por si un `.md` o `.srt` se editó desde fuera, y los endpoints que escriben siguen forzando el
  escaneo completo. Medido con 1.000 reuniones: cada sondeo pasaba de 93 ms (bloqueando el hilo HTTP) a 1 ms;
  el primer pintado y el escaneo tras un cambio siguen en ~100 ms.
- Nueva identidad **Chuleta**: logo (chuleta con hueso) en la cabecera, en el icono de la app y en el favicon;
  paleta burdeos/rosa sobre neutros cálidos con contraste AA en el texto, también en modo oscuro; iconos de línea
  (SVG) en vez de emojis en la navegación, los botones, las pestañas, las propiedades, Ajustes y las etiquetas de
  formulario (los iconos que elige el usuario para carpetas y reuniones siguen siendo emojis, y los avisos
  emergentes no cambian); etiquetas de hablante con un color estable por persona; botones, chips, diálogos y
  tablas con radios, sombras y estados de foco coherentes. Los demás textos «Notas» cambian en el renombrado (arriba).
- **Ventana propia** (`_app/window.py`, pywebview sobre WebView2, el motor de Edge que trae Windows) en vez de
  `msedge --app`: la barra de tareas ve un proceso nuestro con nuestro icono (256 px por `WM_SETICON`) y el
  AppUserModelID `Bernardo.Chuleta`, no una ventana más de Edge. Cerrar la ventana la **oculta**: la app sigue en
  segundo plano (Ctrl+Alt+R y las grabaciones siguen) y el acceso directo la vuelve a enseñar (`POST /api/mostrar`).
  Si pywebview no está o no puede abrir la ventana, se abre Edge como antes. Medido en `windows-latest` (Python 3.10
  y 3.13): con `msedge --app` la ventana lleva el AUMID `MSEdge.127.0.0.1_/` (con `--user-data-dir` propio,
  `MSEdge.127.0.0.1_/.<carpeta>.Default`) e iconos de 16/32 px sacados del favicon; con la ventana propia, 16 y 256.
- **Sin ventana de consola**: el acceso directo apunta a `pythonw.exe server.py` (el mismo Python de `python.txt`),
  con el icono y el `System.AppUserModel.ID` del .lnk, para que «Anclar a la barra de tareas» ancle el acceso
  directo y no `pythonw.exe` a secas. `ABRIR CHULETA.bat` / `_app/Chuleta.bat` siguen arrancando con consola (modo
  diagnóstico). Sin consola, `print` se pierde: el arranque y el apagado hablan solo por `server.log`, y un error de
  arranque (puerto ocupado por otro programa, excepción al iniciar) se anota ahí y sale en un aviso de Windows.
- **Ajustes → ⏻ Apagar Chuleta** (`POST /api/apagar`): la salida explícita ahora que no hay consola. Se niega si hay
  una grabación en curso; si no, cancela la cola, mata los procesos de Whisper, cierra el servidor y la ventana y
  termina el proceso al cabo de 0,5 s; la página muestra «Chuleta se ha apagado». El instalador lo usa para cerrar la
  copia abierta antes de recurrir a matar procesos.
- El nombre visible de la app sale de `common.APP_NAME` (título de la ventana y de la pestaña, raíz del árbol,
  mensajes, nombre del zip instalable); `app.js` lo recibe por `/api/config` (`app_name`) y `instalar.ps1` por
  `-AppName`. Rutas y la carpeta de datos (`_app`, `AudiosApp`) no cambian. Preparaba el renombrado (hecho: arriba).
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
- El icono de la app (acceso directo, barra de tareas, pestaña): `chuleta.ico` se genera ahora con `make_icons.py`
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
- **Perfil de reunión** (`_app/static/perfil.js`, ajustes `perfil`): carpeta de destino, idioma, tema de vocabulario
  y contexto libre con los que empieza cada grabación (● Grabar y Ctrl+Alt+R), en Ajustes → Perfil de reunión junto
  al idioma del resumen, las instrucciones extra y los motores del directo y de la cola. Carpeta y tema admiten
  «automático» (como hasta ahora: la última carpeta usada o la del proyecto del tema; el tema que sugiera la reunión
  en curso o el último elegido): una instalación anterior no cambia. El diálogo de grabar carga el perfil, deja
  ajustarlo para esa reunión y tiene «Guardar como perfil por defecto»; donde el perfil fija algo, la sugerencia de
  Teams/Outlook no lo pisa. Por reunión, `idioma` (código o `auto`) y `contexto` van en la cabecera del `.md`, se
  cambian en la página de la reunión (también en el directo) y `live.run` los relee en el siguiente trozo, como el
  vocabulario; el repaso final usa el idioma que tenga el `.md` al terminar. El contexto entra en el prompt de
  Whisper detrás del vocabulario (tope propio de 100 tokens; si no cabe, cede antes que un solo término) y en el del
  resumen. Mezclas «Español + portugués» e «Inglés + portugués» como la de español + inglés (`lang_pair`,
  `style_for`; frases de estilo nuevas, sin validar aún con audio real). El idioma elegido en ● Grabar es de esa
  reunión y ya no cambia el ajuste general (eso lo hace el perfil). El tema se elige en dos desplegables
  (Proyecto / Tema) con «Sin vocabulario» y «Nuevo…»; sin listas, se explica dónde viven
  (`<Proyecto>\Vocabulario\<Tema>.txt`) e «Importar listas de otra carpeta…» (`POST /api/vocab/import`, sin pisar;
  también en Ajustes → Vocabulario). `GET/POST /api/perfil`, `perfil` en `/api/config`.
- **Destinos de salida** (`_app/salidas.py`, `perfil.outputs`): cero, una o varias carpetas (un vault de Obsidian, la
  del proyecto en OneDrive, una biblioteca de SharePoint sincronizada…) donde cada reunión se escribe como documento
  al terminar la transcripción y el resumen y al guardar cambios en las notas o en la transcripción (agrupados cada
  4 s), con formato por destino: Markdown (`<fecha> - <nombre>.md`: un solo documento con cabecera YAML de
  propiedades, resumen, notas y transcripción con minutos y quién habla; los sidecars junto al audio ya son la
  versión «en tres ficheros»), texto plano o Word `.docx` generado con la librería estándar (zip + `document.xml`
  con estilos Título / Heading 1-2, sin dependencias), y subcarpeta por proyecto opcional. Nunca se mueve el
  original; solo se sobrescriben los documentos que llevan la marca `generado_por` (cabecera del `.md`, segunda
  línea del `.txt`, `docProps/core.xml` del `.docx`) y cuyo hash coincide con el registro
  `%LOCALAPPDATA%\AudiosApp\salidas.json`; uno ajeno, o uno nuestro cambiado en el destino, se deja como está y se
  anota en `server.log`.
- **Rendimiento, con medición antes/después** (`scripts/rendimiento_bench.py`, documentado en el README):
  - Las peticiones a Groq/OpenAI (directo, repaso final, cola, resumen y traducción) van por una `requests.Session`
    por hilo con keep-alive (`engines.Http`), con timeouts por fase (10 s para conectar y el de lectura de cada
    petición) y sin reintentos en el adaptador (los decide el `KeyPool`). Antes cada petición abría conexión
    nueva (DNS + TCP + TLS): medido contra `api.groq.com` sin clave, 86 → 57 ms por petición (p50; p95 108 → 61),
    que en el directo se paga cada 3-6 s.
  - El proceso de Whisper en local sigue cargado `worker_keepalive_min` minutos tras el último trabajo (30 por
    defecto; 0 = 5 min, como antes) y, al arrancar la app, se precarga el modelo local del directo si ya está
    descargado (`worker_preload`, activado; en segundo plano y con prioridad baja, sin bloquear la interfaz).
    Medido: arrancar el proceso y cargar `small` cuesta 2,0-2,9 s y `medium` 4,7-6,2 s, que antes pagaba el primer
    trozo de cada reunión; mientras está cargado ocupa ~450 MB (`small`) o ~1 GB (`medium`). «Apagar» sigue
    matando los dos procesos.
  - La latencia de cada trozo del directo en la nube se desglosa por fases en `server.log` y en la cabecera del
    directo («texto a los 1,8 s · red 1,1 s · cupo 0,3 s»): preparación, espera por cupo, red (conexión + subida +
    inferencia; la inferencia aparte si el servidor manda `*-processing-ms`) y pintado. Así se ve si el cuello
    está en Groq, en la red o en este lado.
  - La transcripción en la nube de un archivo escribe el FLAC de cada parte justo antes de subirla, en vez de
    todas antes de la primera subida: la primera subida empieza ~0,7 s antes en una hora de audio (el total no
    cambia: lo que manda es decodificar, 4,2 s por hora, y la API).
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
- `pywebview==6.2.1` en `requirements.txt` y en el instalador (arrastra `pythonnet`, `clr-loader`, `proxy_tools` y
  `bottle`; `proxy_tools` solo publica sdist, Python puro, se instala sin compilador). Si no importa, el instalador
  solo avisa.
- `scripts/lookfeel_spike.py` y `scripts/lookfeel_webview_probe.py` con el workflow `lookfeel-spike.yml`: miden en un
  runner de Windows el AUMID y los iconos del HWND de la ventana (Edge `--app` en cuatro variantes y la ventana
  propia). Son herramientas de medición; no forman parte de la app.
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
- `scripts/build_zip.py` genera `dist/Chuleta - instalador.zip` sin vocabulario.
- `server.py` se reparte en módulos planos (el instalador solo copia ficheros sueltos de `_app`): `common.py`
  (utilidades puras y log) y `recording.py` (atajo global, pitidos, avisos, proceso de la grabadora y detección
  de reunión). El árbol, la cola y la API siguen en `server.py` (≈2.800 líneas): separarlos exige un módulo de
  estado compartido (ROOT, LINKS, COMPLETE, DATA_DIR cambian en tiempo de ejecución) y se deja para otra entrega.

### Conocido, sin cambiar
- Las mezclas español + portugués e inglés + portugués, el contexto en el prompt y los documentos `.docx` de los
  destinos de salida están probados con dobles (el `.docx` abriendo el zip y su XML), no con audio real ni en Word.
  Al mover, renombrar o borrar una reunión, los documentos ya escritos en los destinos se quedan como están.
- La ventana propia, el acceso directo a `pythonw.exe`, el AUMID del .lnk y «Apagar» están probados en el runner
  `windows-latest` (ventana, hide/show, iconos, HTTP) y con dobles en los tests, pero aún no a mano en un Windows con
  barra de tareas: ver la lista de comprobación del informe del 31/10/2026.
- El instalador sigue siendo una consola de PowerShell (la ventana WinForms propia queda pendiente).
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
