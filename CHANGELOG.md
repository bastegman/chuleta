# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

«Seguridad», «Privacidad», «Directo», «Perfil y destinos» y «Windows» recogen los arreglos de la auditoría del
31/10/2026 sobre la versión de ese día (`5c55e54`); «Instalación y desinstalación», los suyos y la instalación de
empresa. Entre paréntesis va el número de cada hallazgo, el mismo que citan los commits; el informe de la auditoría no
está en este repositorio. «Desarrollo y CI» es para quien mantiene el repositorio: no cambia nada en la app instalada.

### Voces dobles en la grabación (07/11/2026, msgs 1154 y 1156)
- La grabadora pone cada fuente (micro y salidas) en una línea de tiempo común según cuándo llega su audio. Antes cada
  fuente se tomaba por la cabeza de su cola: tras un corte (reabrir el dispositivo al cambiar de cascos o de salida,
  o audio perdido con el PC cargado) esa fuente quedaba adelantada para siempre, y con altavoces el eco de la sala
  (~30 ms) pasaba a ser una repetición de cientos de ms. Medido con la grabadora real y dispositivos simulados: un
  corte de 300 ms dejaba el eco a +330 ms; ahora se queda a +30 ms. Los cortes se recolocan y van a `recorder.log`.
- «Todas las salidas»: una salida que es copia retrasada de otra (un dispositivo virtual, el «espejo» de un driver)
  deja de sumarse en cuanto se detecta (correlación cruzada a 8 kHz, ≥ 0,7 con el mismo retardo dos veces seguidas;
  unos 4 s). Vuelve a sumarse si deja de parecerse o si suena sola. El medidor lo dice: «X (igual que Y: no se suma)».
- Eco de los altavoces en el micro: mientras suena la reunión, el micro se baja 14-20 dB (lo justo para dejar el eco
  ~32 dB por debajo) salvo que hables encima; el retardo y la atenuación del eco se miden solos. Y si una salida
  lleva tu micro («Escuchar este dispositivo», tono lateral), esa copia se baja mientras hablas, sin quitar el resto
  de la salida. Cada atenuación solo se activa cuando la correlación demuestra la copia en su sentido (con
  auriculares no hay eco y no toca nada) y se desactiva sola. El nivel del micro ya no se reajusta mientras suena la
  reunión (el eco se tomaba por tu voz y se subía). Medidores: «atenuando el eco de los altavoces», «lleva tu micro».
- Para medir una grabación: `python3 scripts/medir_eco.py GRABACION.wav` dice si hay voces dobles y con qué retardo
  (fijo: copia digital o eco estable; cambiante: eco que se mueve o los cortes de antes).

### Transcribir con los tres modos: descarga de Whisper con progreso y la verdad sobre el motor (07/11/2026)
Lo que vio el 06/11/2026: con Groq «fallaba la conexión», con «Con tu cuenta de Claude» no transcribía, al grabar
«Modelo» parecía seguir en Groq y «Descargando modelo medium» no acababa.
- **Whisper se descarga con progreso de verdad.** Antes lo bajaba faster-whisper dentro del proceso de Whisper la
  primera vez que hacía falta: un único «Descargando modelo medium (solo la primera vez)…» durante minutos (medium son
  1.531 MB), una hora de tope por intento sin detectar que se había parado, y desde un proceso sin los certificados de
  Windows. Ahora lo descarga la app (`engines.WhisperModels`) con los mismos certificados y proxies que la nube: MB y %
  con el tiempo que queda, reanuda donde se quedó, comprueba tamaño y huella (sha256) de cada modelo (revisión fija de
  Hugging Face), da por parada una conexión sin datos en 60 s y lo reintenta, mira el espacio libre antes y, si falla,
  lo dice en una frase con qué hacer. Lo que ya descargaron versiones anteriores se sigue usando sin bajar nada. El
  proceso de Whisper solo carga carpetas locales (`HF_HUB_OFFLINE`).
- **Ajustes › Dónde se procesan tus reuniones** dice si lo elegido está aplicado o no, con un botón «Aplicar ahora»
  junto a las opciones, y al cerrar Ajustes sin aplicarlo avisa de que sigue la opción de antes (antes había que bajar
  hasta «Guardar»: elegir la cuenta de Claude, pulsar «Comprobar» y cerrar dejaba la API externa sin decirlo, y el
  directo seguía en Groq). Un clic de prueba no cambia nada; si hay transcripciones en la nube en marcha, lo dice antes
  de aplicar. Con «Solo en este PC» y «Con tu cuenta de Claude» enseña los modelos de Whisper del PC con su estado y
  «Descargar», «Cancelar» o «Reintentar», deja claro que Claude no transcribe (solo resume) y, al aplicar, empieza a
  bajar el modelo del directo, no en mitad de la primera reunión (salvo que se haya cancelado en Ajustes).
- **Transcribir desde la cola en este PC volvía a funcionar solo con un PyAV antiguo.** faster-whisper 1.2.1 (su
  última versión) abre el audio con `av.open(…, metadata_errors="ignore")`, y PyAV 19 (29/09/2026, lo que instala hoy
  `pip`) ya no admite ese argumento: en una instalación nueva, cualquier transcripción local desde la cola acababa en
  «TypeError: open() got an unexpected keyword argument 'metadata_errors'». El directo no lo usaba y seguía bien. Ahora
  el proceso de Whisper lee el audio con el decodificador de la app (mismo remuestreo a 16 kHz mono).
- **Elegir «API externa» con la clave ya puesta vuelve a los motores que se usaban con la API** (la nube, si no hay
  otros), igual que la primera clave; salir de la API los devuelve al PC, como antes. Y «Guardar» ya no manda los selectores de motor del perfil si no se han
  tocado: pintados con el modo de antes, después de «Probar conexión» devolvían el directo a Whisper en el PC. Esos
  selectores se repintan al cambiar de modo.
- **«Modelo» dice dónde se transcribe**: junto al directo y en el diálogo de grabar sale «en este PC · Whisper
  medium» o «en la nube · Groq» y, con la cuenta de Claude, que Claude solo hace el resumen. Si el último directo de
  una grabación falló, la cabecera dice por qué en lugar de «está parada».
- Descargas: una respuesta vacía o un proxy que no reanuda y corta ya no reintentan sin fin (los reintentos cuentan
  desde lo más lejos que se ha llegado); «Descargar» justo después de «Cancelar» sigue con la misma descarga. El audio
  con un fotograma dañado se salta ese fotograma en vez de cortar ahí la transcripción.
- **Errores de la nube en una frase con qué hacer**: certificado (proxy de empresa), proxy, sin DNS, tiempo agotado,
  clave no válida (con dónde copiarla), 403, modelo inexistente, trozo demasiado grande, servicio caído, y una página
  web de un proxy o un portal en lugar de la API. Antes: «sin conexión con el servicio (SSLError)» o «HTTP 401: …».
- Desarrollo y CI: `tests/test_fix_transcripcion.py` (descargas contra un servidor HTTP local que corta, se para,
  ignora `Range`, devuelve 403, HTML o bytes dañados) y la E2E `e2e-procesado.yml` en Windows: elige cada modo en
  Ajustes, descarga y carga Whisper medium de verdad con su progreso, transcribe en directo y desde la cola, y comprueba
  lo que enseña «Modelo»; Groq contra un falso local y Claude Code con uno falso en el PATH (nunca claves reales).

### Quién habla: poner nombre ya no te convierte en «Tú» (07/11/2026)
- Poner nombre a un hablante dice quién es ese hablante en esta reunión: una persona que ya se llama así (sin
  distinguir mayúsculas ni tildes: elegir «Ana» de la lista ya no crea otra Ana) o una nueva. Antes, si ese hablante
  salía como «Tú» (por el micro o por tu huella), «Guardar» le cambiaba el nombre a TU persona y, con «Recordar», le
  guardaba la voz de la otra persona: desde ahí esa persona salía como tú en otras reuniones y sus frases iban al
  resumen con tu nombre. Ahora tu persona solo cambia de nombre en Ajustes > Voces y solo recibe huellas de un
  hablante que eres tú. Corregir una errata del nombre en todas sus reuniones es una casilla aparte («solo corrijo el
  nombre»). Escribir tu propio nombre en otro hablante crea otra persona (un compañero puede llamarse igual): tú te
  marcas con «Soy yo», que ya no usa el nombre del cuadro (tu persona se llama «Yo» hasta que la cambies en Ajustes).
- La persona asignada manda sobre la heurística del micro: un hablante al que pones nombre deja de salir como «Tú»,
  igual en pantalla que en el resumen. «No soy yo» quita un «Tú» mal puesto; «Soy yo» lo mueve al hablante bueno (y el
  que lo era solo por el micro vuelve a «Persona N»). «Soy yo» ya no renombra tu persona con el nombre que hubiera en
  el cuadro. «Fusionar con…» desde «Tú» ya no funde tu persona dentro de otra (esa persona pasaba a ser «Tú» con tus
  huellas): en ese caso el desplegable es «Es…» y reasigna. Tu persona ya no se fusiona con ninguna otra, en ningún
  sentido (al revés te quedabas con la voz de la otra); el servidor lo rechaza.
- Lo que dejó el fallo en `voces.json`: si tu persona guarda huellas que no se parecen entre sí (la voz de otra
  persona guardada como tuya), Ajustes > Voces lo marca con ⚠ y, mientras, tu voz no se reconoce sola por huella
  (pregunta «¿Tú?»). Al decir que un hablante que salía como tú es otra persona (o «No soy yo»), se quitan de tu
  persona las huellas casi iguales a esa voz (coseno ≥ 0,8). Remedio completo: «Olvidar» tu fila y volver a guardarte
  con «Soy yo».
- Mientras se identifican las voces de una reunión no se pueden corregir sus hablantes (lo dicho se perdería), y una
  etiqueta abierta sobre una identificación anterior pide volver a abrirse antes de guardar nada (con otra
  identificación, «Persona 2» puede ser otra voz y «Recordar» guardaría la huella de otro).
- Si el micro y la huella no cuadran, pregunta en vez de decidir: «¿Ana?» si el micro dice «Tú» y la huella dice Ana;
  «¿Tú?» si la huella dice que eres tú pero en esa grabación ese hablante casi nunca habla por tu micro.
- La línea de tiempo del micro (de donde sale «Tú») iba unos 0,9 s adelantada respecto al audio (la grabadora estampa
  cada nivel con lo ya escrito, y escribe 1 s por detrás) y con ~1 de cada 6 pasos vacíos: con turnos cortos «Tú» no
  salía. Se corrige al leerla, también en las reuniones ya grabadas al «Volver a identificar».
- «Volver a identificar» conserva lo que ya corregiste a mano (nombres, «Soy yo», «No soy yo»), casando hablantes
  nuevos y antiguos por tiempo; si falla o se cancela, el sidecar queda como estaba. Las huellas en memoria de la
  identificación anterior se descartan antes de rediarizar (con «Recordar» se podía guardar la voz de otro hablante).
- «Copiar texto» copia también quién habla, como la pantalla: un párrafo por turno con «Nombre: » delante al cambiar de
  hablante (sin voces, igual que antes).

### Dónde se procesan tus reuniones (02/11/2026)
- Ajustes empieza por una elección: **Solo en este PC** (Whisper local, nada sale del ordenador, sin resumen con IA),
  **Con tu cuenta de Claude** (transcripción local y resumen con Claude usando la suscripción del usuario, personal o
  de empresa, a través de Claude Code: paso a paso dentro de la app y botón «Comprobar») o **API externa** (lo de
  siempre: Groq, OpenAI, Azure OpenAI o una API compatible). Las dos últimas llevan su aviso de que el contenido de la
  reunión sale del PC (`procesado` en `settings.json`; una instalación anterior con API key sigue en «API externa» y
  sin key pasa a «Solo en este PC»). Decisión de producto: una sola versión, pública, con las tres alternativas y los
  avisos, en vez de una versión «solo local» y otra con nube.
- Con «Con tu cuenta de Claude» el resumen se hace con `claude -p` (`engines.ClaudeCode`): sin herramientas, sin
  guardar la sesión, sin servidores MCP ni hooks del usuario, las instrucciones por la entrada estándar y la respuesta
  en JSON; una reunión larga cabe en una sola llamada. La app no ve ni guarda credenciales. `/api/agente/comprobar`
  busca `claude` (PATH y las carpetas de sus instaladores en Windows), lee su versión y hace una petición mínima. La
  nube (transcripción, directo y repaso final) solo se ofrece con «API externa»; al cambiar a otra opción, los motores
  de la cola y del directo vuelven a ser locales. Con la cuenta de Claude, elegir la opción (con su aviso) es el
  consentimiento: también se resumen solas las reuniones transcritas en local. Preparado para añadir Codex CLI y
  Gemini CLI (`engines.AGENTES`). El diagnóstico dice dónde se procesa.
- Tras una revisión independiente del código: cambiar a «Solo en este PC» o «Con tu cuenta de Claude» corta de verdad
  la nube (lo que estuviera en cola con la nube no sale, un directo en nube pasa a local desde el siguiente trozo y los
  trabajos de nube y resúmenes en cola se cancelan; `_cloud_ready` y `_run_summary` vuelven a mirar Ajustes al
  empezar); con el `.cmd` de npm en Windows, cancelar o el tope de tiempo matan también a `claude.exe` (`taskkill /T`);
  la salida de Claude Code con «verbose» activado (un array de mensajes) se entiende igual; se busca `claude.exe` y
  `claude.cmd` también en el PATH del registro (recién instalado, con la app ya abierta) y en las carpetas de winget;
  «Comprobar» no guarda el formulario (solo comprueba con el modelo elegido) y la versión sale limpia («2.1.314»); el
  resumen automático se puede apagar también con la cuenta de Claude, y su aviso dice que es automático; la entrada
  se escribe desde un hilo propio (una transcripción larga no se pierde con Python anterior a 3.13.10); `claude` se
  lanza en una carpeta vacía y sin `ANTHROPIC_API_KEY` en el entorno (el gasto va a la cuenta, no a la API); el
  permiso de «Resumir» es por destino (el de Groq no vale para Anthropic); un `"agente": null` en `settings.json` no da
  el fichero por roto; Inicio invita a elegir dónde se procesa si nadie lo ha hecho.

### Invitar al autor a un café (02/11/2026)
- En Ajustes › Acerca de, en el README y en el LEEME hay un enlace discreto de PayPal para invitar al autor a un
  café (`/api/acerca/abrir` con `que: "donar"` lo abre en el navegador del sistema). Sin ventanas emergentes ni
  recordatorios.

### Arranque, primera pantalla y detección de llamadas (pruebas del 02/11/2026)
Lo que falló en la primera prueba de la versión del 01/11/2026 en un portátil de empresa, con su causa y su arreglo.
- **La interfaz ya no depende de importar Whisper.** Al abrirse, la página pide `/api/config`, que comprobaba qué
  modelos locales estaban descargados importando `faster_whisper`; en ese PC, recién actualizado, la importación tardó
  8 minutos (y acabó fallando), y como Python solo deja importar a un hilo a la vez, esa petición se quedó esperando
  y ningún botón hacía nada salvo Calendario. Ahora la comprobación lee la caché de Hugging Face sin importar nada
  (`engines.HF_REPOS`, `hf_hub_cache`) y guarda el resultado 30 s; contar tokens (`/api/vocab/check`) tampoco espera
  a que cargue el tokenizador: estima mientras se carga en otro hilo.
- **Un WAV viejo a medio cerrar ya no arranca un directo.** La primera vez que la app ve un WAV con la cabecera abierta
  (OBS cerrado sin finalizar) tomaba el momento del arranque como «última vez que creció» y lo daba por grabación en
  curso: cada arranque lanzaba un directo automático sobre un fichero de hace días (y, al terminar, un repaso final en
  la nube). Ahora cuenta su fecha de modificación: solo es grabación en curso si ha crecido en los últimos 45 s.
- **El directo no se cae si falla la VAD de Whisper.** El detector de pausas (`faster_whisper.vad`) se importaba en
  cada trozo sin red de seguridad; una importación rota o pisada por otro hilo (el `KeyError: 'faster_whisper'` de
  importlib de ese día) tumbaba la sesión. Ahora, si no se puede importar, corta las pausas con un detector sencillo
  por energía, lo avisa una vez en el log y vuelve a intentarlo a los 10 minutos.
- **Si el servidor tarda, la página lo dice y los botones no revientan.** A los 3 s sin `/api/config`, «Arrancando… el
  servidor tarda en contestar (N s)» con dónde mirar; ⚙ Ajustes y ● Grabar avisan de que la app aún arranca; cambiar
  de vista espera a la configuración y se pinta al llegar. Un error de JavaScript sale como aviso (cada mensaje una
  vez) en vez de tragarse en silencio. En el servidor, toda petición de la API que pase de 2 s queda en
  `server.log` con su duración («petición lenta: GET /api/config 480.2 s»).
- **Aviso flotante y chip «en llamada» solo con llamada de verdad.** Saltaban nada más arrancar porque el registro de
  privacidad de Windows decía que Teams tenía el micro abierto, sin reunión. Ahora una entrada solo cuenta si empezó
  hace menos de 12 h (las que Windows no cierra al matar la app quedan «en uso» para siempre), si el proceso de esa app
  sigue en marcha (`tasklist`) y si se ve en dos lecturas seguidas del vigilante (16 s); cada entrada descartada o
  aceptada se anota una vez en el log con su antigüedad.
- **Una versión anterior abierta ya no se queda con la ventana.** El acceso directo tomaba cualquier copia que
  contestara al `ping` por «la app ya está abierta» y le pedía la ventana: una versión anterior (la del 30/10 no exige
  token) servía los ficheros nuevos con su API vieja, sin que nada lo dijera. Ahora el `ping` lleva la versión; si la
  que corre es otra, se le pide que se apague y, si no sabe (anteriores al 31/10) o no lo hace, en Windows se termina
  su `pythonw.exe` (nunca si está grabando: entonces se avisa y no se arranca). La página, además, reconoce una API
  anterior (sin `app_name` ni `perfil`, o el título sin sustituir) y lo dice con el mensaje de qué cerrar.
- El aviso de Windows al parar con Ctrl+Alt+R dice los segundos si no se llegó al minuto («41 s», no «1 min»).
- **Tests de la interfaz en un navegador real** (`tests/test_ui_smoke.py`, Playwright + Chromium, workflow
  `ui-smoke` en Ubuntu y Windows): arranque con token, ⚙ Ajustes, Reuniones, Calendario, Inicio y el diálogo de Grabar
  sin errores de JavaScript; sin token, 403; con una API anterior, el aviso; con `/api/config` lento, la espera. Hasta
  hoy ningún test abría la interfaz: una pantalla con los botones muertos pasaba la suite entera.

### Seguridad
- **Una página web abierta en el navegador ya no puede manejar Chuleta** (P0-1). Bastaba con tenerla abierta: dentro
  del cuerpo de un POST `text/plain` podía colar otra petición, con su propio `Host` y su `X-Audios`, que la API local
  ejecutaba (cambiar el proveedor de la nube por un servidor ajeno, que recibía la API key y el audio, borrar
  reuniones o apagar la app). Ahora un cuerpo que no se ha leído (un 403, un error antes de leerlo) se lee y se
  descarta si no pasa de 1 MiB y, si no, la conexión se cierra. Los POST con un `Origin` ajeno o con `Sec-Fetch-Site`
  distinto de `same-origin` o `none` se rechazan, y los que traen `Transfer-Encoding` o un `Content-Length` no válido,
  también, con la conexión cerrada.
- **El icono de una reunión ya no puede ejecutar código** (P0-2). Un `.md` con un `icono` preparado, en una carpeta
  que la app lee (por ejemplo, una carpeta vinculada y compartida), se pintaba sin escapar al dibujar el árbol, sin
  ningún clic. Ahora el icono se escapa allí donde se pinta, `parse_notes` y `notes_save` solo aceptan un emoji o un
  texto corto (como mucho 8 code points, sin `<`, `>`, `&`, comillas ni caracteres de control), las páginas HTML
  llevan Content-Security-Policy (sin scripts en línea) y `/media` solo sirve audio y vídeo.
- **Token de sesión** (P1-02). En cada arranque la app crea un token aleatorio en
  `%LOCALAPPDATA%\AudiosApp\sesion.token` y lo borra al apagarse. `/api/*` y `/media` lo exigen en la cabecera
  `X-Chuleta-Token` o en la cookie (HttpOnly, SameSite=Strict) que recibe la interfaz al abrir `/?t=<token>`, que
  redirige a `/`; sin él responden 403 («Sesión no válida: vuelve a abrir Chuleta con su acceso directo»). En un
  Windows con varias sesiones a la vez (escritorio remoto, AVD, Citrix), `127.0.0.1` es de todas: así otro usuario del
  mismo equipo ya no puede leer las reuniones ni manejar la grabadora. La ventana y Edge lo reciben en la dirección
  con la que se abren (`/?t=`); el acceso directo, el aviso flotante, el instalador y el desinstalador lo leen de ese
  fichero, y el acceso directo ya no da por buena una Chuleta abierta en otra sesión de Windows.
- **La app habla consigo misma sin proxy** (P1-13). Las llamadas a `127.0.0.1` (`server.py` y `aviso.py`) ya no pasan
  por el proxy del sistema, cuya excepción `<local>` no cubre esa dirección: con un proxy fijo de empresa, el acceso
  directo vuelve a encontrar la app abierta (antes arrancaba una segunda copia) y «● Grabar» del aviso flotante
  funciona. Además, el puerto se reserva antes de arrancar nada más: una segunda copia que se abre a la vez espera a
  la primera y le pide su ventana, sin crear otro atajo ni otros vigilantes.
- El audio que sirve `/media` lleva `Cache-Control: no-store` y `X-Content-Type-Options: nosniff`, así que no se queda
  en la caché de WebView2, cuyo perfil vive en la carpeta de datos (P2-13).
- Las conexiones keep-alive ociosas con la API local se cierran a los 60 s: un programa que dejara conexiones abiertas
  ya no retiene un hilo de la app por cada una.

### Privacidad
- **El resumen automático ya no envía a la nube lo transcrito en local** (P1-03). Antes lo hacía, sin preguntar, en
  cuanto había una API key. Ahora resume por su cuenta solo las reuniones transcritas enteras en la nube (las de los
  últimos 3 días, como antes). Lo transcrito en local, o un directo con algún tramo en local, necesita la casilla
  nueva de Ajustes → Resumen con IA, «Resumir también las reuniones transcritas en local», apagada por defecto, que
  dice qué se envía: el título (o el nombre del archivo), la fecha y la duración, la transcripción con quién habla,
  las notas, el contexto, los participantes confirmados y las instrucciones del resumen; el audio, nunca. A mano
  («Resumir esta reunión») se puede resumir siempre. A quien actualiza y estaba afectado (resumen automático activado,
  nube con API key y un modelo local en la cola o en el directo, o reuniones ya transcritas en local), Inicio le avisa
  una vez, con un enlace a esa casilla. El origen de cada texto se guarda 7 días en
  `%LOCALAPPDATA%\AudiosApp\transcritas_en_la_nube.json`, sigue a la reunión al moverla o renombrarla (a ella o a su
  carpeta) y se borra con ella; otro audio que llegue a esa ruta no lo hereda.
- **Participantes sugeridos** (P1-04, P2-12). Los que propone Outlook o Teams al grabar se guardan como sugeridos, con
  una nota detrás de los nombres en la cabecera del `.md`:
  `participantes: Ana, Luis (sugeridos por Outlook, sin confirmar)`. No van al resumen con IA ni a los destinos de
  salida hasta que se confirman («Confirmar» en la página de la reunión) o se editan. Las versiones anteriores leen
  esa cabecera y enseñan los nombres con la nota. Las grabaciones de antes no llevan la nota, así que sus
  participantes cuentan como confirmados: no hay migración.
- **Varias copias sobre la misma carpeta** (P2-29). Una copia de una versión anterior que abra la misma carpeta (otro
  PC, una carpeta de OneDrive compartida) toma los participantes sugeridos por confirmados (con la nota) y los envía a
  su resumen y, si los tiene, a sus destinos; la del 30/10, además, no conoce `idioma` ni `contexto` y, con ellos,
  toma la cabecera entera por notas. Hay que actualizar todas las copias a la vez. Esta versión conserva tal cual las
  claves de la cabecera que no conoce, así que no entierra en las notas las de una versión más nueva.
- La ayuda de Ajustes → Reuniones ya no dice que la cita «no se envía a ningún sitio»: el nombre que se sugiere al
  grabar (el asunto de la cita o el título de la reunión de Teams), si se acepta, es el nombre de la grabación y va
  como título al resumen con IA y a los destinos (P2-12).
- **«Copiar diagnóstico», con lista blanca** (P1-06, P2-11). Antes copiaba las 30 últimas líneas de `server.log` con
  solo las claves enmascaradas, y esas líneas llevan títulos de reuniones, proyectos, temas, nombres de personas y
  rutas. Ahora lleva la versión, Python y el sistema, el motor y el modelo (uno propio sale como `<texto>`), cómo es
  la carpeta de la app sin su ruta (en OneDrive, en una unidad de red, con caracteres no ASCII, su longitud), si los
  datos están en la carpeta de siempre y cuántas carpetas vinculadas y destinos hay. De las últimas 30 líneas del
  registro solo pasa lo fijo de cada mensaje de la app, con sus cifras, sus modelos y sus códigos de error (HTTP,
  Errno/WinError, HRESULT, los de la API y solo nombres de excepciones que existen). Los nombres, las rutas y el texto
  libre salen como `<texto>`, también lo que la app ya ha olvidado (una reunión borrada, una carpeta desvinculada), y
  las claves no van. Los mensajes nuevos de esta versión (seguridad, Windows, directo, destinos) tienen también su
  plantilla. Junto al botón se dice qué lleva y se avisa de que el registro completo sí lleva nombres y rutas.
- Cada trozo de audio que se sube a la nube se borra en cuanto se sube, y también si la subida falla o se cancela; lo
  que quedara en `%LOCALAPPDATA%\AudiosApp\tmp` (de una sesión cortada) se borra al arrancar y al apagar (P2-14).
- **Un `settings.json` dañado ya no se pisa con los valores por defecto** (P2-17). Se copia tal cual como
  `settings.json.roto-<fecha>`, se rescatan la API key, las claves adicionales de todos los proveedores, las carpetas
  vinculadas y los destinos de salida, y Ajustes avisa de lo recuperado y de dónde está la copia; si ni siquiera se
  puede copiar, en esa sesión no se escribe encima. Un BOM al principio (el Bloc de notas lo pone) ya no lo estropea,
  y antes de sustituir el fichero se fuerza a disco (`fsync`).

### Directo
- **Claves adicionales por proveedor** (P1-05). Cada clave adicional es del proveedor con el que se guardó (las de
  Groq siguen en `cloud.extra_keys`; las demás, en `extra_keys_openai`, `extra_keys_azure` y `extra_keys_custom`,
  cifradas igual) y solo se usa con él: al cambiar de proveedor ya no se mandan a otro servicio. Ajustes enseña las
  del proveedor elegido y dice de quién son las demás.
- **«Otra (compatible)», con su modelo del directo** (P1-15): un campo libre en Ajustes; vacío, el directo usa el
  modelo de la cola. Antes pedía siempre `whisper-large-v3-turbo`, y una pasarela sin ese modelo rechazaba cada trozo.
- **Ponerse al día sin atascarse** (P1-14). Si el directo en la nube va con retraso (más de dos ventanas pendientes),
  junta lo pendiente en una petición de hasta 10 min (un WAV de 19,2 MB) y se pone al día; al terminar, lo que quede
  sale en trozos de como mucho 10 min. Nunca se manda más de 25 MB, ni más peticiones por minuto de las que declare el
  proveedor (`x-ratelimit-limit-requests`, en OpenAI, Azure o una pasarela; en Groq el límite es por día). Antes el
  retraso crecía sin tope y al final se subía todo de una vez (error 413).
- **Un trozo que falla no tumba la sesión** (P1-16). En la nube se salta y se anota, también al ponerse al día y el
  último; si el directo acaba sin texto o con un error y la grabación ya terminó, se encola igual el repaso final (y
  con él los destinos y el resumen). El directo hace 2 intentos por trozo, con esperas de como mucho 30 s.
- **El repaso final pendiente sobrevive a un apagado** (P2-09). Se apunta en
  `%LOCALAPPDATA%\AudiosApp\repasos_pendientes.json`: si se apaga con el repaso en cola o en marcha, se retoma al
  volver a abrir la app. Cancelarlo a mano deja el texto del directo y hace con él los destinos y el resumen (el
  automático, solo si ese texto es entero de la nube o lo permite la casilla de Ajustes).
- Una clave que responde 401 o 403 se marca como no válida durante 1 h y se pasa a la siguiente (transcripción y
  chat); «Probar conexión» prueba cada clave y dice cuál no vale (P2-18).
- Una respuesta 200 que no es JSON (por ejemplo, el portal de acceso de un proxy) da un error claro y no tumba el
  directo (P2-19).
- El directo relee los ajustes de la nube en cada trozo: sin clave se pausa sin mandar nada y sigue al pegarla
  (P2-20).
- El cupo se lleva por clave y por modelo: el directo con `whisper-large-v3-turbo` y el repaso final con
  `whisper-large-v3` no se descuentan uno al otro (P2-23).
- **Traducción**: solo traduce lo dicho en los últimos 30 s, así que activada a mitad de reunión no empieza por el
  minuto 0 (P2-21); enseña el modelo y el proveedor reales (`openai/gpt-oss-20b` en Groq, `gpt-4o-mini` en OpenAI, el
  modelo del resumen en una pasarela propia) y, sin servicio de chat (Azure), sale desactivada (P2-24). Al terminar el
  directo, la última traducción tarda como mucho 10 s en total, esperas incluidas (antes podía retener el final del
  directo varios minutos); si tras esperar al límite por minuto no quedaría tiempo para pedirla, ya no espera en balde
  (P2-22).
- Con Azure OpenAI, la pantalla del resumen dice que el resumen aún no funciona ahí (con Azure solo se transcribe), en
  vez de mandar a «Configurar la nube» (P2-25).

### Perfil y destinos
- **Destinos que chocaban con las grabaciones** (P1-11, P2-01). Un destino nuevo no puede ser la carpeta de Chuleta,
  una carpeta vinculada (ni nada dentro) ni una carpeta con grabaciones: Ajustes lo comprueba al añadirlo y dice por
  qué. Además, nunca se escribe encima de un fichero de una grabación (`<grabación>.md`, `.txt`, `.srt`,
  `.resumen.md`, `.hablantes.*`) ni en una carpeta de la app, aunque el destino se guardara antes. Lo que no se ha
  podido escribir sale como aviso en Ajustes → Perfil de reunión, con qué hacer.
- **Sin registro, tampoco se pisa un documento** (P2-02). En otro PC o tras reinstalar (sin `salidas.json`), un
  documento con la marca de la app que no dice lo mismo queda «cambiado» y no se sobrescribe; si dice lo mismo (las
  fechas de generación no cuentan), se vuelve a registrar y se actualiza como siempre.
- **Dos reuniones con el mismo nombre** (P2-03). Si `<nombre>.md` ya es de otra reunión (o es un fichero tuyo), el
  documento va a `<nombre> (<código>).md` (o `.txt`, `.docx`), estable para esa reunión, y `salidas.json` guarda de
  qué reunión es cada documento. En otro PC o tras reinstalar, una reunión que ya escribía «<nombre> (<código>)»
  vuelve a encontrar ese mismo documento aunque se haya movido o se haya renombrado su carpeta, y ya no crea otro al
  lado: la reconoce por la línea `origen` del propio documento (también en `.docx`), que se pone al día la próxima vez
  que se escribe en el PC donde se movió.
- Cada escritura usa su propio `.tmp`, reintenta si Word u OneDrive tienen el documento abierto y nunca deja el `.tmp`
  en el destino; un cerrojo por destino evita que dos escrituras a la vez lo corrompan (P2-04, P2-05).
- Un destino ya guardado que ahora no está disponible (sin VPN, un disco desconectado) no impide guardar los ajustes
  ni «Probar» (P2-06).
- Al apagar se escriben las reescrituras que esperaban su temporizador de 4 s (P2-07). «Restaurar original»,
  «Quién habla», cambiar un hablante y renombrar, fusionar u olvidar personas también reescriben los documentos
  (P2-08).
- Borrar una reunión manda a la papelera sus documentos de los destinos que nadie ha cambiado allí; los editados se
  quedan, y el diálogo lo dice (P2-10).
- **Transcribir ya no cambia el idioma del perfil** (P1-10), ni la caja de transcribir propone el último idioma
  elegido en otra reunión.
- **El tema de cada reunión, por su lista** (P2-26). Al elegir el tema (al grabar o en la página de la reunión) se
  guarda qué lista es en `%LOCALAPPDATA%\AudiosApp\temas.json`, que es de cada PC; la cabecera del `.md` sigue
  llevando solo `proyecto` y `workstream`, que leen todas las versiones, así que nada de esto añade claves. Con dos
  proyectos (o carpetas vinculadas) que se llaman igual, Whisper usa la lista elegida o, sin ese dato, la del proyecto
  que contiene la reunión, y si aun así no está claro, el vocabulario general. Nunca se toma la lista de otro proyecto
  (antes, si solo había un tema con ese nombre, se usaba aunque fuera de otro). «Notas», el nombre anterior de la app,
  vale como la raíz.
- Si se renombra la carpeta de un proyecto desde el Explorador de Windows, sus reuniones siguen usando la lista de su
  tema siempre que no haya dudas: la reunión está en la carpeta de ese proyecto y es la única lista con ese nombre
  fuera de la raíz. Si otro proyecto tiene un tema con el mismo nombre, se usa el vocabulario general de Ajustes, para
  no cargar nunca la lista de otro cliente. Es mejor renombrar las carpetas de proyecto desde la app, que lleva el
  tema de todas sus reuniones. Renombrar desde la app un proyecto con el nombre de otro que ya existe (por ejemplo,
  una carpeta vinculada que se llama igual) no deja sin tema a las reuniones de ninguno de los dos ni hace que las de
  uno usen la lista del otro.
- Con el tema guardado por su lista, una cabecera escrita por otro PC (el nombre que allí tiene una carpeta vinculada
  que aquí se llama distinto) ya no manda la reunión al vocabulario general: si ningún proyecto se llama como dice la
  cabecera, vale la lista guardada, siempre que el tema sea el mismo.
- Renombrar o borrar el tema o la carpeta fijos del perfil actualiza el perfil, el último tema y la última carpeta y
  las carpetas por proyecto; un tema fijo que ya no existe vale como «automático» (P2-27).
- «Transcribir todos los pendientes» usa por defecto el idioma de cada reunión (P2-28).
- La sugerencia de Teams u Outlook ya no pisa el tema ni la carpeta que ya elegiste en el diálogo Grabar (P2-30).
- Renombrar una carpeta se lleva con ella las copias del original, el estado «completa», las huellas en memoria y el
  registro de los destinos (P2-31).
- El diálogo Grabar ya no dice que Ctrl+Alt+R usa «esta misma configuración» (usa el perfil), y la carpeta de la app
  se llama por su nombre, no «Audios» (P2-33, P2-34).

### Windows
- **Chuleta ya no impide apagar, reiniciar ni cerrar sesión** (P1-09). Cuando es Windows quien cierra la ventana (o
  otro programa le pide cerrarse), ya no se cancela el cierre ni sale el aviso de que la app sigue en segundo plano, y
  se hace el mismo apagado limpio que Ajustes → Apagar Chuleta.
- **Outlook encuentra la cita con el formato regional español** (P1-07): el filtro de la fecha va en el formato
  regional del usuario («31/10/2026 14:05» en español). Antes caía siempre al de EE. UU. y, con el formato día/mes, la
  cita en curso no se encontraba.
- **Rutas con tildes o ñ** (P1-08): el selector de carpetas devuelve bien las rutas con tildes, ñ u otras letras fuera
  de cp1252 también con la app abierta desde el acceso directo (`pythonw`), al vincular una carpeta, importar listas o
  añadir un destino.
- **El aviso de Windows y las comillas tipográficas** (P1-01). El script de PowerShell del aviso es fijo, y el título
  y el texto le llegan por las variables de entorno `CHULETA_AVISO_TITULO` y `CHULETA_AVISO_TEXTO`. Un proyecto o un
  tema con `’`, `$`, comillas invertidas o saltos de línea ya no rompe el aviso ni puede ejecutar nada.
- **Las correcciones de la transcripción no se pierden** (P1-12). Al cambiar de reunión, lo corregido se guarda en el
  momento con una petición normal (sin el tope de 64 KiB de keepalive), con dos reintentos y, si sigue fallando, un
  aviso con «reintentar»; una versión vieja no pisa a una nueva, y el servidor guarda las correcciones de una en una.
  Al cerrar o recargar la página solo se usa keepalive si cabe (si no, se pide confirmar) y la página sigue en modo
  edición. «Listo» solo dice «Cambios guardados» cuando el servidor lo confirma. «Restaurar original» descarta las
  correcciones que no se habían podido guardar (ni el cierre o la recarga de la ventana ni la recarga automática las
  vuelven a mandar), espera a que acabe un guardado en curso y, si la restauración falla, sigues en modo edición con
  tus correcciones guardándose. Si la app se ha actualizado y no consigue guardar tus correcciones, la página no se
  recarga hasta que se guardan, y el aviso de error sale una vez (otro, solo si el error cambia o después de un
  guardado correcto).
- **«Avisarme si estoy en una llamada» apaga todos los avisos de llamada** (P2-32): desmarcado, ni aviso de Windows,
  ni aviso flotante, ni chip.
- **Sin WebView2, Edge** (P2-35): si falta el runtime de WebView2, pywebview usaría el motor de Internet Explorer y la
  ventana salía en blanco; ahora no se abre esa ventana y la interfaz se abre en Edge (modo app).

### Instalación y desinstalación
- **Carpeta propia**: sin instalación anterior, `INSTALAR.bat` copia la app de la carpeta descomprimida (Descargas…) a
  `%USERPROFILE%\Chuleta` (fuera de Descargas y de OneDrive; si esa carpeta ya tiene otras cosas, `Chuleta (2)`…;
  `-Destino <carpeta>` para otra nueva o vacía) con `_app`, `LEEME.txt`, `LICENSE`, los `.bat` y las listas
  `*\Vocabulario\*.txt` del zip, y crea allí los accesos directos. La ventana enseña el paso («Instalando en …») y al
  final dónde ha quedado y que la carpeta descomprimida ya se puede borrar. Una carpeta ya instalada (con
  `_app\python.txt`) o la que ya es `%USERPROFILE%\Chuleta` no se copian: se actualizan en su sitio. Una carpeta que
  no se puede listar no se da por libre, y de una descomprimida con grabaciones no se dice que se puede borrar.
- **Detección de la instalación anterior**, si la carpeta desde la que se instala no estaba ya instalada, por este
  orden: la entrada de Aplicaciones instaladas (`InstallLocation`, si sigue teniendo `_app\server.py`),
  `Chuleta.lnk`/`Notas.lnk` en el escritorio y en Inicio, y `%USERPROFILE%\Chuleta`. Se actualiza en su sitio como
  antes (mismo copiado, ahora en `Copy-AppFiles`) y el detalle dice cómo se encontró. Con una instalación anterior,
  `-Destino` no se usa (y se dice): para cambiarla de carpeta hay que desinstalarla antes. Con `-NoShortcut` o
  `-Desktop` (pruebas) no se lee ni se escribe nada del sistema.
- **Aplicaciones instaladas**: `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chuleta` (sin administrador)
  con `DisplayName`, `DisplayVersion` (la de `_app\version.txt`; si no hay, la fecha), `Publisher`, `DisplayIcon`,
  `InstallLocation`, `InstallDate` (`yyyyMMdd`), `URLInfoAbout`, `UninstallString` y `QuietUninstallString` (REG_SZ) y
  `EstimatedSize` (KB de `_app`), `NoModify` y `NoRepair` (REG_DWORD). La clave se crea aunque el perfil no tenga
  todavía `...\Uninstall` (un perfil nuevo). Si una política de la empresa no lo deja, solo se avisa: la app funciona
  igual.
- **Desinstalador** (`_app\desinstalar.ps1`): ventana con el estilo del instalador («Se quitará la aplicación Chuleta
  de este PC. Tus reuniones, transcripciones y notas en … NO se borran.»), casilla desmarcada para borrar también
  `%LOCALAPPDATA%\AudiosApp` (con el aviso de que las huellas de voz son datos biométricos y borrarlas es definitivo),
  «Desinstalar»/«Cancelar» y, al terminar, «Chuleta se ha desinstalado. Tus reuniones siguen en …» con
  «Abrir la carpeta». Apaga la app con `/api/apagar` (si está grabando, 409: avisa y no toca nada), espera a que no
  quede ningún proceso suyo (si a los 15 s sigue viva la grabadora, no toca nada) y quita, por este orden, los accesos
  directos que apuntan a esa instalación, `AudiosApp` (con la casilla), `_app` (solo ficheros de la app, por extensión
  de código o por nombre exacto; lo demás, también una carpeta o un enlace dentro de `static`, `export` o
  `__pycache__`, se queda y se dice) y los `.bat` de la raíz; la entrada de Aplicaciones instaladas y el propio
  desinstalador, los últimos y solo si nada ha fallado, para poder reintentar. `_app` se borra carpeta por carpeta,
  sin entrar en enlaces (una junction o un symlink se quedan), y `AudiosApp` con un borrado que tampoco entra en
  ellos; deja `LEEME.txt` y `LICENSE` junto a las reuniones (así una reinstalación vuelve a la misma carpeta). Se
  copia a `%TEMP%` y se relanza desde allí (esa copia se borra por cualquier camino, también al cancelar);
  `-Silencioso` sin ventana (no borra `AudiosApp` salvo con `-BorrarDatos`); registro en
  `%TEMP%\chuleta-desinstalar.log`.
- **Instalador con ventana propia** (WinForms, sin dependencias): logo, nombre, pasos con ✓, ✗ y ▶, barra, detalle y,
  si falla, «Copiar el detalle»; la consola se esconde mientras la ventana está abierta. Con `-Consola`, `-NoShortcut`
  o `-NoLaunch` (pruebas, CI) sigue siendo la consola de siempre. Todo lo que escribe queda también en
  `%LOCALAPPDATA%\AudiosApp\instalar.log`.
- **Con la app abierta**: el instalador y el desinstalador le hablan con el token de sesión (`X-Chuleta-Token`, leído
  de `%LOCALAPPDATA%\AudiosApp\sesion.token`). Un 401 o un 403 cuenta como abierta pero sin API, nunca como éxito, y
  un 409 al apagarla es que está grabando: no se toca nada. Una app ocupada que no contesta en 5 s pero escucha en su
  puerto cuenta como abierta y se intenta apagar con 20 s de margen; el plan B (cerrar su proceso) nunca se usa con la
  grabadora viva.
- La marca de Internet (Zone.Identifier) solo se quita a los ficheros del paquete: `_app` y `INSTALAR.bat`,
  `ABRIR CHULETA.bat`, `LEEME.txt` y `LICENSE`. Las reuniones y los demás ficheros tuyos conservan la suya (P2-16).
- pywebview pasa a las librerías opcionales, aparte y sin bloquear: sin ella, la app se abre en Edge. Si fallan las
  principales, el mensaje nombra el paquete y la causa: la red o el proxy de la empresa, un Python demasiado nuevo, un
  pip que solo acepta paquetes compilados (`only-binary`) o el repositorio de paquetes de la empresa, del que solo se
  enseña el servidor, nunca la URL, que puede llevar credenciales (P2-36).
- Al actualizar se copia también `LICENSE`, así que «Acerca de» y los zips que se comparten llevan la licencia
  (P2-37).

### Desarrollo y CI
- **Workflows de Windows también en main** (P2-38): `lookfeel-e2e` (E2E del instalador y del desinstalador),
  `reunion-windows` (escritorio: ventana de Teams simulada, aviso flotante) y `windows-real`, nuevo (la suite con el
  formato regional `es-ES`, la ventana real ante `WM_QUERYENDSESSION` y `WM_ENDSESSION` y sin WebView2, y el aviso con
  PowerShell 5.1), se lanzan con cada push a main que toque lo que prueban (filtro `paths:`) y a mano.
  `lookfeel-spike` queda archivado: solo a mano. `tests.yml` parsea `_app/*.ps1` con Windows PowerShell 5.1
  (`ParseFile`, con un control que demuestra que detecta un error) y lleva `timeout-minutes`. Todo workflow que
  ejecuta la suite instala antes `numpy` y `requests`, y `tests/test_ci.py` lo comprueba.
- **Agnosticismo en la CI** (P2-39, parcial). `test_sin_rastro_de_clientes` revisa todo lo que git tiene en el índice
  (también CHANGELOG, tests, scripts y workflows) y corre en su propio job, `agnosticismo`, el único que recibe el
  secreto de Actions `NOTAS_PALABRAS_VETADAS`: expresiones regulares separadas por comas o una por línea, sin
  distinguir mayúsculas, con `^` y `$` por línea. Si encuentra algo, falla y da fichero:línea y número de patrón,
  nunca el texto. Sin el secreto, en un fork o con Dependabot, ningún job falla: el propio test deja el aviso
  «Agnosticismo sin ejecutar» en el resumen del run (`NOTAS_AVISAR_SIN_VETADAS=1`, solo en ese paso). En local:
  `NOTAS_EXIGIR_VETADAS=1 NOTAS_PALABRAS_VETADAS="$(cat ~/chuleta-vetadas.txt)" .venv/bin/python tests/run.py` (en
  Windows, con PowerShell, ver `test_sin_rastro_de_clientes` en `tests/test_package.py`). Pendiente: crear el secreto
  (Settings → Secrets and variables → Actions → New repository secret), que solo puede hacer quien administra el
  repositorio, y decidir si su falta en los push del repo propio debe poner la CI en rojo; hoy no lo hace.
- `tests/test_ci.py` lee los workflows sin PyYAML y nunca lee algo distinto de lo que leería YAML: da `ValueError`,
  con la línea, ante claves fuera de `[A-Za-z0-9_.-]` (también en elementos de lista), tabuladores fuera de comillas y
  de comentarios, «: » dentro de un valor sin comillas, escalares sin comillas partidos en varias líneas dentro de
  `[ ]` o `{ }` y saltos de línea que no son `\n` ni `\r\n`. Lee listas y mapas en línea, los bloques `|`, `>` y `>-`
  y las listas compactas (`- - x`). Si un workflow nuevo usa algo que no sabe leer, `test_lee_todos_los_workflows`
  falla diciendo la línea: hay que poner el valor entre comillas o ampliar el lector.
- Sondas de Windows (workflow `lookfeel-spike`): la de WebView2 vuelve a ver el servidor (llamaba a `wait_ping` sin la
  carpeta de datos y acababa siempre en «sin ping en 40 s»). Las dos abren la interfaz con el token de la sesión
  (`?t=`), como la app, en vez de recibir 403 en toda la API. Tras el renombrado, el spike busca la cabecera de iconos
  de `chuleta.ico` y la sonda carga `static/chuleta.ico`: antes las 4 variantes de Edge abortaban sin abrir la ventana
  y el icono no se cargaba.
- E2E de Windows (`scripts/e2e/lookfeel_e2e.ps1`): la «API sin responder» que veía no era de la app. En PowerShell
  `$base` y `$Base` son la misma variable, y la fase de la reunión falsa pisaba la dirección de la API. Ahora esa
  variable es `$reunionBase` y `$Base` es constante; un test prohíbe las variables del nivel del script con dos
  grafías en `instalar.ps1`, `desinstalar.ps1` y los scripts del E2E.
- **Tests y E2E**: `tests/test_desinstalar.py` (paquete, parseo con PowerShell, BOM/CRLF, valores y tipos del
  registro, orden de detección, ningún `Remove-Item` sobre la raíz ni extensiones de reunión y, en Windows,
  desinstalación real sobre una carpeta temporal comparando las reuniones por sha256). El E2E de Windows instala desde
  `Descargas-test` en `%USERPROFILE%\Chuleta`, vuelca la entrada del registro, crea una reunión falsa, actualiza por
  el registro y por `Notas.lnk`, y desinstala cancelando, con la app «grabando», en silencio con la app abierta y,
  tras reinstalar, con «Borrar también mis ajustes»; la reunión se compara por sha256 en cada fase.
- `tests/test_integracion.py`: lo que solo existe con todos los arreglos juntos (el repaso final retomado tras
  reiniciar frente al origen del texto, los participantes sugeridos frente a las versiones publicadas, las plantillas
  del diagnóstico para los mensajes nuevos).
- `.gitignore` en lista blanca: en una copia de trabajo, las reuniones que se graben o importen en la raíz (el
  servidor de desarrollo la toma como carpeta de reuniones) no van al repositorio (P2-15).

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
  (`GET /api/diagnostico`: solo datos técnicos, con lista blanca; ver «Privacidad»).
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
  propiedades, resumen, notas y transcripción con minutos y quién habla; los sidecars junto al audio ya son la versión
  «en tres ficheros»), texto plano o Word `.docx` generado con la librería estándar (zip + `document.xml` con estilos
  Título / Heading 1-2, sin dependencias), y subcarpeta por proyecto opcional. Nunca se mueve el original; solo se
  sobrescriben los documentos que llevan la marca `generado_por` (cabecera del `.md`, segunda línea del `.txt`,
  `docProps/core.xml` del `.docx`) y cuyo hash coincide con el registro `%LOCALAPPDATA%\AudiosApp\salidas.json`; uno
  ajeno, o uno nuestro cambiado en el destino, se deja como está, se anota en `server.log` y se avisa en Ajustes (ver
  «Perfil y destinos»).
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
- **Directo en la nube casi en streaming** (`_app/live.py`, `_app/engines.py`, `_app/static/directo.js`): con Groq el
  directo manda trozos de 3–6 s en vez de 8–15: corta en la primera pausa de la voz a partir de 3 s y, si nadie calla,
  a los 6 s con 0,5 s de solape que se deduplica por tiempos; solo se envía la voz (los silencios de los extremos se
  recortan y una ventana sin voz no se manda ni cuenta). El modelo del directo es configurable (con Groq,
  `cloud.live_model`, por defecto `whisper-large-v3-turbo`; con «Otra (compatible)», ver «Directo»); el repaso final
  sigue con `cloud.model`. Se mide la latencia de cada trozo (fin del audio → texto escrito) y se enseña la mediana y
  el p95 de la sesión. Lo local (20–45 s) y «ponerse al día» (120–300 s) no cambian.
- **Cupo y ritmo del plan gratuito de Groq**: por clave se guardan `x-ratelimit-remaining-requests`,
  `x-ratelimit-reset-requests` y el último `retry-after` (la primera vez se anotan en `server.log` todas las
  cabeceras `x-ratelimit-*`), nunca más de una petición cada 3 s por clave (20/min) y la ventana del directo crece
  según lo que queda del día: ≥600 peticiones → 3–6 s; 300–600 → 6–10 s; <300 → 10–15 s; <60 → el directo se pausa
  con aviso (la grabación sigue, lo que falte se transcribe al terminar en trozos grandes y el repaso final se hace
  igual). La cabecera del directo y Ajustes enseñan «Groq: quedan N peticiones hoy · se renuevan en …».
- **Claves adicionales** (`cloud.extra_keys`, cifradas como la API key; desde la auditoría, cada proveedor tiene las
  suyas: ver «Directo»): cuando a la clave activa le quedan menos de 20 peticiones del día, o Groq responde 429 con
  `retry-after` > 60 s, se pasa a la siguiente; el resumen, la traducción y el repaso final usan la misma rotación. En
  Ajustes → nube, «Claves adicionales (opcional)» con el aviso de que cada clave es de una cuenta distinta y el audio
  y el texto se procesan bajo esa cuenta. Las claves no se escriben nunca en el log ni salen en claro por la API.
- **Seguir la reunión**: el último trozo llegado queda resaltado y el texto baja solo; «Modo seguir» (letra grande,
  para leer de reojo); y, por reunión, «Traducir al español» (apagado por defecto): cada 10–15 s manda lo último dicho
  al modelo de chat del proveedor (`openai/gpt-oss-20b` en Groq; ver «Directo») y lo pinta en una columna lateral (una
  ayuda para seguir la reunión; no se guarda). API: `POST /api/live/traducir`; el estado del directo (`directo` en
  `/api/jobs`) lleva cupo, ventana, latencia y traducción.
- `scripts/directo_bench.py`: audio sintético con `say` (español y portugués, con pausas) y reproducción de un WAV
  por el chunker con un transcriptor falso de latencia configurable (`--real` usa Groq con `GROQ_API_KEY`).
  `tests/test_directo.py` y `tests/test_directo_frontend.py`.
- **Reunión en curso al grabar** (`_app/calendario.py`, `_app/static/reunion.js`): al pulsar Grabar (o Ctrl+Alt+R) la
  app sugiere el nombre, el tema de vocabulario y los participantes a partir de dos fuentes locales: el título de la
  ventana de reunión de Teams (nivel 1, siempre y sin activar nada: `EnumWindows` sobre los procesos
  `ms-teams.exe`/`Teams.exe`/`msteams.exe`, descartando la ventana principal) y, si se enciende en Ajustes →
  Reuniones, la cita en curso del Outlook clásico de escritorio por COM (nivel 2, `pywin32`, opcional: asunto, horas,
  organizador, asistentes sin correos, categorías y si es de Teams; `Sort` → `IncludeRecurrences` → `Restrict` con la
  fecha en el formato regional del usuario y sin segundos, consulta con tope de 8 s en un hilo, y motivos legibles si
  Outlook no está abierto, es el nuevo Outlook, está ocupado o se rechaza su aviso de seguridad). El tema es la lista
  de vocabulario con más términos (y nombre de proyecto/tema) presentes en asunto, categorías, organizador y
  asistentes, con un umbral para no sugerir a ciegas. Los participantes sugeridos van a la cabecera del `.md` de la
  grabación nueva, marcados como sugeridos: no van al resumen con IA ni a los destinos de salida hasta que se
  confirman (ver «Privacidad»). Botón «Probar» en Ajustes que dice qué reunión ve en ese momento.
  `GET /api/reunion/actual`; ajustes `outlook_on` (apagado por defecto) y `rec_aviso`.
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
  `bottle`; `proxy_tools` solo publica sdist, Python puro, se instala sin compilador). Si no se instala o no importa,
  el instalador solo avisa (va con las opcionales: ver «Instalación y desinstalación»).
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
- Al renombrar una reunión, su documento en los destinos se escribe con el nombre nuevo y el del nombre anterior se
  queda allí (se puede borrar a mano). Al moverla o renombrar su carpeta se sigue escribiendo en el mismo documento, y
  al borrarla, sus documentos intactos van a la papelera.
- La ventana propia, el acceso directo a `pythonw.exe`, el AUMID del .lnk y «Apagar» están probados en el runner
  `windows-latest` (ventana, hide/show, iconos, HTTP; el instalador y el desinstalador de punta a punta en
  `lookfeel-e2e`; la ventana real ante el apagado de Windows y sin WebView2 en `windows-real`) y con dobles en los
  tests, pero aún no a mano en un Windows con barra de tareas.
- Al apagar Windows, la ventana espera como mucho 5 s al apagado limpio de la app, y las reescrituras pendientes de
  los destinos de salida pueden tardar hasta 15 s: alguna puede quedarse sin hacer hasta la próxima vez que se escriba
  esa reunión.
- En un Windows con varias sesiones a la vez, solo una puede tener Chuleta abierta en el puerto 8765: en las demás
  sale el aviso de que el puerto lo tiene otro programa.
- «Quién habla» y el cifrado con DPAPI se han probado en macOS (sherpa-onnx real; DPAPI con un doble); DPAPI real, en
  el job `windows-latest` de GitHub Actions. El instalador, la grabadora y la interfaz de voces aún no se han
  ejecutado a mano en Windows.
- Si DPAPI falla en Windows, los secretos se guardan en claro (`plain:`) para no dejar la app sin ajustes; solo se
  avisa en `server.log`.
- El aviso de Windows (toast) sale firmado por «Windows PowerShell» porque lo lanza PowerShell con el AppUserModelID
  de PowerShell (`TOAST_PS` en `_app/recording.py`), no con el de la app (`Bernardo.Chuleta`, que ya lleva el acceso
  directo); se deja como está.
- La lectura de Outlook se ha probado con un doble del modelo de objetos y, en Windows, solo el camino
  «sin Outlook abierto» (pywin32 real) y el formato regional español de la fecha del filtro (`windows-real`). Con un
  Outlook clásico de verdad (citas periódicas, aviso de seguridad, nombres de asistentes) aún no se ha ejecutado: se
  comprueba con el botón «Probar» de Ajustes → Reuniones.
- La ventana de reunión de Teams se reconoce por el sufijo «| Microsoft Teams» de su título; un chat abierto en
  ventana aparte o una llamada 1:1 se tomarían como reunión (es una sugerencia, siempre editable).
