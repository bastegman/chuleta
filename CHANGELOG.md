# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

«Seguridad», «Privacidad», «Directo», «Perfil y destinos» y «Windows» recogen los arreglos de la auditoría del
31/10/2026 sobre la versión de ese día (`5c55e54`); «Instalación y desinstalación», los suyos y la instalación de
empresa. Entre paréntesis va el número de cada hallazgo, el mismo que citan los commits; el informe de la auditoría no
está en este repositorio. «Desarrollo y CI» es para quien mantiene el repositorio: no cambia nada en la app instalada.

### Tenca encima de Chuleta: la carpeta con su nombre de verdad (09/11/2026)

- **Rutas**: la tarjeta de grabar, el aviso de Ctrl+Alt+R, la cabecera del directo («Se guarda en … · Cambiar»), los
  avisos de mover una reunión y Ajustes › Proyectos enseñan la raíz con el nombre que tiene la carpeta en el disco
  (`server.root_name()`): «Chuleta\Proyecto\…» en una instalación encima de Chuleta, que no renombra la carpeta, y no
  «Tenca\…», que no existe en el Explorador. En una instalación nueva no cambia nada (la carpeta se llama Tenca). La
  cabecera del directo decía «\Proyecto\…», sin la raíz: ahora, igual que la tarjeta. Las opciones «(raíz)» de los
  selectores de carpeta, también. El nombre de la app (título, bandeja, avisos) sigue siendo Tenca.
- **El proyecto de las listas de la raíz** se llama como su carpeta, igual que cualquier otro proyecto: «Chuleta» encima
  de Chuleta. Así sus documentos «por proyecto» siguen en la subcarpeta «Chuleta» de siempre (con `4553b9c` empezaban
  a ir a una «Tenca» nueva) y la carpeta y el idioma que tenía asignados vuelven a ese nombre una vez al arrancar
  (`migrate_app_name`, marca `nombre_v`; los que `4553b9c` pasó a «Tenca» también). Las reuniones y ajustes guardados
  con «Tenca», «Chuleta» o «Notas» se siguen reconociendo como de la raíz.
- **Arranque con Windows**: si la migración de «Chuleta.lnk» la hace la app (porque el instalador no pudo), copia
  también el «desactivado» de Aplicaciones de inicio (StartupApproved), como ya hacía el instalador; y reconoce su
  acceso directo por la ruta de su `_app` seguida de la barra (`…\_app` ya no casa con `…\_app2`).
- **Instalador**: un `Notas.lnk`/`Chuleta.lnk` que abre un `.bat` con ese nombre que existe sin `server.py` al lado
  no se da por roto (puede ser de otro programa) y se queda. Sin instalación anterior, si `%USERPROFILE%\Tenca` (un zip
  descomprimido ahí) y `%USERPROFILE%\Chuleta` (desinstalada, con reuniones) fueron de la app, va a la que tiene
  reuniones.
- **Desarrollo y CI**: los avisos de `test_reunion_windows` esperan 25 s (el primero arranca Python y Tk en frío y en
  los runners de Windows pasaba de 12 s alguna vez); el E2E de Windows comprueba que las fuentes y el logotipo llegan a
  la copia instalada y, en la fase v, que la raíz y «Se guarda en» llevan el nombre de la carpeta de Chuleta.

### Renombrado a Tenca (09/11/2026)

La app pasa a llamarse **Tenca** (Bernardo, msg 1249). Lo de abajo, con «Chuleta», es el historial de antes.

- **Nombre**: `common.APP_NAME = "Tenca"`: título de la ventana y de la pestaña, raíz del árbol, mensajes, avisos y
  toasts, el icono de la bandeja, el diagnóstico, la cabecera `Server`, el zip (`Tenca - instalador.zip`) y su carpeta
  raíz (`Tenca/`), y el acceso directo «Tenca». Los textos que aún llevaban el nombre escrito a mano (el calendario de
  Outlook, «Configurar …», la prueba de «Probar») salen ahora de `APP_NAME`. Ficheros renombrados conservando la
  historia: `ABRIR CHULETA.bat` → `ABRIR TENCA.bat` (raíz y `_app/export`), `_app/Chuleta.bat` → `_app/Tenca.bat`,
  `_app/static/chuleta.ico`/`.png` → `tenca.ico`/`.png`. Repositorio: `github.com/bastegman/tenca`.
- **Instalar encima de Chuleta** no deja nada con el nombre viejo ni duplicados: encuentra la instalación también por
  la entrada «Chuleta» de Aplicaciones instaladas, por `Chuleta.lnk` y por `%USERPROFILE%\Chuleta`; la actualiza en
  su carpeta; el primer `Chuleta.lnk` de esa instalación en el escritorio y en el menú Inicio pasa a llamarse
  `Tenca.lnk` y los que sobren se quitan (nunca dos); «Arrancar con Windows», si estaba puesto, queda como una sola
  copia `Tenca.lnk` en la carpeta Inicio de Windows; la entrada «Chuleta» de Aplicaciones instaladas se quita después
  de escribir la de «Tenca» (solo si es de la app: la de otro programa que se llame igual no se toca); y
  `ABRIR CHULETA.bat` (o `ABRIR NOTAS.bat`) de la raíz se quita si es de la app. Si «Arrancar con Windows» estaba
  desactivado en el Administrador de tareas (Aplicaciones de inicio), sigue desactivado. Lo que apunta a otra copia de
  la app en otra carpeta se queda y se dice; un acceso directo que no es de la app no se toca.
  Al final dice «Chuleta ahora se llama Tenca: tus reuniones, voces y ajustes siguen donde estaban».
- **No se mueve nada**: la carpeta de las reuniones conserva su nombre (`%USERPROFILE%\Chuleta`; renombrarla rompería
  OBS, los accesos del usuario y los anclajes) y los datos siguen en `%LOCALAPPDATA%\AudiosApp` (ajustes, claves,
  voces). Las reuniones y listas de vocabulario con `proyecto: Chuleta` siguen siendo de la raíz
  (`APP_FORMER_NAMES`), y la carpeta y el idioma asignados a ese proyecto en Ajustes pasan de «Chuleta» a «Tenca» la
  primera vez que arranca (se guardaban por el nombre de la app; «Notas» no, que ya no se usaba). Una reinstalación tras desinstalar Chuleta vuelve a su carpeta.
- **Anclajes de la barra de tareas**: Windows no deja que un programa los cambie. Siguen funcionando y agrupando la
  ventana (el AppUserModelID no cambia); el instalador detecta un «Chuleta» anclado de esta instalación y dice cómo
  volver a anclarla.
- **La app** también migra «Arrancar con Windows»: si encuentra en la carpeta Inicio la copia `Chuleta.lnk` de esta
  instalación (lleva la ruta de su `_app`), la sustituye por `Tenca.lnk` al arrancar; activarlo o desactivarlo desde la
  bandeja quita también la antigua. Un `Chuleta.lnk` que no es de esta copia, o un `Notas.lnk` (otro programa), no se
  toca. **«Compartir Tenca»** no mete en el zip lo que la copia instalada conserva con el nombre anterior
  (`ABRIR CHULETA.bat`, `Chuleta.bat`, `chuleta.ico`/`.png`).
- **Desinstalador**: quita también los accesos directos y la entrada con el nombre anterior si son de esta instalación;
  su registro pasa a `%TEMP%\tenca-desinstalar.log`.
- **No cambia, a propósito** (no lo ve el usuario y cambiarlo rompería algo): el AppUserModelID `Bernardo.Chuleta` (la
  ventana dejaría de agruparse en los anclajes hechos), la cabecera `X-Chuleta-Token` (el instalador nuevo apaga con
  ella la Chuleta abierta) y las variables de entorno `CHULETA_*` y `NOTAS_*`.

### Marca Tenca (09/11/2026)

- Icono y logotipo nuevos (la tenca sobre tres renglones), paleta Boldo y ocre con contraste AA medido en claro y oscuro,
  Manrope y Fraunces dentro de la app (OFL, `fuentes-OFL.txt`), Acerca de con un tributo a la tenca (*Mimus thenca*) y
  «Invitar a un café» al pie de Ajustes. El desinstalador quita también las fuentes y su licencia.

### Ajustes por lo que haces: «Al grabar» primero, «Proyectos» y «Calendario y Teams» (07/11/2026)
Lo que pidió: Ajustes era «un listado sin fin» y no se sabía si lo configurado es lo que se usa (msg 1196).
- Ajustes abre en **«Al grabar»**: arriba, «Así se graba ahora una reunión de [proyecto]», el mismo cálculo que la
  tarjeta de grabar y Ctrl+Alt+R, que cambia al momento con lo que escribas debajo aunque aún no lo hayas guardado.
  Debajo, solo las reglas, una por línea: reuniones sin proyecto, idioma por defecto, proyecto al grabar, contexto,
  micrófono y audio del PC, texto en directo y «Llamada detectada». La ayuda larga, plegada.
- **«Proyectos»** junta lo que antes eran Vocabulario y Carpetas: una tabla con la carpeta y el idioma de cada proyecto
  y workstream, que dice si son suyos o heredados («la del proyecto», «el de Al grabar») y se edita en la propia fila;
  las listas de vocabulario, las carpetas fuera de la app y las copias en otras carpetas (los antiguos destinos de
  salida).
- Cada cosa a su sección: los motores a **Procesado**, el idioma y las instrucciones del resumen a **Resumen**, OBS a
  **Avanzado** y Outlook y el aviso flotante a **Calendario y Teams**, que dice en una frase si Teams funciona (y la
  reunión que ve ahora) y el estado de Outlook.
- La tabla de **Proyectos** dice la carpeta que se usará de verdad, del mismo cálculo que la tarjeta de grabar: la
  asignada, en negro; sin asignar, en gris, «su carpeta» (la que tiene su `Vocabulario`), «la de Al grabar» o «la
  última usada», con la ruta; y si la asignada ya no existe, lo avisa. Antes decía «la última usada» aunque se fuera a
  usar la del proyecto. Al editarla, «Sin asignar» dice cuál quedaría.
- Con las ramas de esta semana: la tarjeta de grabar ofrece «Usar las listas de una carpeta…» en Proyecto y en el
  panel del vocabulario (vocab-carpeta); «Se guarda en» enseña la carpeta «AAAA-MM-DD Nombre» que se creará dentro de
  la del proyecto (carpetas); y «Con tu cuenta de Claude» dice qué copia respondió («Claude Code de VS Code»), con qué
  modelo y, si la cuenta no deja el elegido, el aviso (claude-empresa).
- «Con tu cuenta de Claude», en Ajustes › Procesado y en el paso 2 de «Configurar»: «Abrir PowerShell», la línea para
  instalar, `claude` en esa misma ventana y el inicio de sesión en el navegador (con el de tu empresa, si es la suya),
  hasta «Login successful». Que la copia de VS Code también vale y la app de escritorio de Claude no basta.
- Botones de los errores de Claude Code: «pide acceso a … (IT)» lleva «Copiar diagnóstico» (PowerShell no lo arregla);
  «ha caducado», «Abrir PowerShell» con el programa suelto y «Probar otra vez» con la copia de VS Code; «no puede usar
  el modelo elegido», «Revisar en Ajustes».
- Un solo nombre para los antiguos destinos de salida: «copias en otras carpetas», como en Ajustes, también en la
  tarjeta de grabar, al confirmar participantes, al borrar una reunión y en «Copiar diagnóstico».
- **Calendario y Teams**, sin repetir nada: Outlook sale una vez, en su fila, con «Activar» o «Desactivar» y, activado,
  «Probar»; mientras no se guarde, lo dice («Se activa al pulsar Guardar»). Con el estado del calendario (`GET
  /api/calendario/estado`), la frase es ese estado, el chip toma su color (en aviso si dice que algo falla),
  «Activar» y «Desactivar» guardan al momento (y «Guardar» ya no los pisa mientras Outlook pregunta) y «Probar con mi
  calendario» se puede usar antes de activarlo. Fuera de Windows no se ofrece activarlo. La ayuda larga, plegada en
  «¿Cómo funciona?», bajo las filas de Teams y Outlook; el aviso flotante, en su recuadro «Aviso al detectar una
  llamada» (se llamaba «Reuniones»). El aviso flotante dice que depende
  de «Llamada detectada» (Al grabar) y, con ella apagada, no se puede marcar: era una segunda casilla casi igual que no
  decía de cuál colgaba. La línea de Teams ya no consulta Outlook cada vez que se abre Ajustes.
- «Llamada detectada» (Al grabar) vuelve a decir lo que hace: «avisarme si estoy en una llamada y no estoy
  grabando». Decía «aviso con «Grabar»», que es el aviso flotante, otro ajuste.
- «Al grabar» cabe en una pantalla a 1500×920 (antes 828 px para 655), también con «Una carpeta por reunión» y con
  contexto: las pistas de cada regla van bajo su nombre, «¿Cómo funciona?» en la línea de arriba, sin la línea del pie
  (repetía el enlace a Proyectos), y la carpeta y el contexto del plan en una línea (enteros, al pasar el ratón). El
  micrófono y la salida llevan nombres cortos («El de Windows», «Todo lo que suena»), y cuál es ahora el de Windows, al
  pasar el ratón. Los avisos de esa pantalla, en una línea.
- En la tabla de Proyectos, editar una fila ya no cambia los anchos de la tabla ni corta los desplegables por la
  mitad.
- Un nombre de reunión escrito con signos que no admite un archivo (`\ / : * ? " < > |`, p. ej. «Comité: Q4») ya no
  hace fallar «Empezar a grabar» con «Nombre no válido»: se graba sin esos signos y la tarjeta dice antes cómo se
  guardará («se guarda como «Comité Q4»»). Ctrl+Alt+R, igual.
- En **Proyectos**, lo que cambias en una fila se guarda al momento («Guardando…», «Carpeta guardada», «Idioma
  guardado» en la propia fila) y «Listo» la cierra. Antes hacía falta el «Guardar» de la fila, y el «Guardar» de abajo
  la perdía diciendo «Ajustes guardados». Dos cambios seguidos, o cambiar de fila enseguida, ya no pierden el primero.
  Ahora también se elige la carpeta de cada workstream, y una carpeta asignada que ya no existe se avisa también al
  editarla. En negro, lo asignado; en gris, lo heredado.
- **«Así se graba ahora»** cambia también con lo que aún no has guardado en otras secciones (los motores de
  Procesado, resumir al terminar, el vocabulario de las reuniones sin proyecto y «Una carpeta por reunión») y se
  recalcula al volver de Proyectos. También con la opción marcada en «Dónde se procesan» aunque aún no la hayas
  aplicado: «Guardar» la aplica, así que cuenta como «Hay cambios sin guardar». «Transcribe» dice que sale de Ajustes ›
  Procesado, donde están los motores (o de Al grabar, si es «Texto en directo» apagado). Sin reunión, la carpeta de la
  reunión se enseña como «AAAA-MM-DD Nombre de la reunión», no con la hora de ese minuto. Al recalcular, solo cambian
  las líneas que cambian (un lector de pantalla no vuelve a leer el plan entero).
- Si no se pueden leer las listas de vocabulario (OneDrive sin conexión, por ejemplo), Al grabar, Proyectos y las
  listas lo dicen con «Probar otra vez»; antes Proyectos decía «Todavía no hay proyectos. Crea uno…».
- La carpeta de las reuniones sin proyecto y el proyecto al grabar guardados, si ahora no se encuentran (una carpeta
  vinculada desconectada, una lista que no se lee), se ven como «(no se encuentra)» y se conservan con cualquier
  «Guardar». Antes el desplegable enseñaba «Automática» y el «Guardar» de otra sección los borraba sin avisar.
- ⚙ abre siempre en «Al grabar». Al volver de editar una lista o del asistente, en la sección en la que estabas. Un
  enlace de dentro (el origen de una línea del plan, «Proyectos» en la ayuda) lleva el foco a esa sección.
- Deshacer un cambio en Ajustes ya quita «Hay cambios sin guardar»: los campos de la nube, que se pintan después,
  contaban siempre como cambiados.
- Los idiomas se llaman igual en el plan y en los desplegables, en español («Portugués», «Alemán», «Solo inglés»,
  «Detectar el idioma»); el desplegable añade a veces una aclaración entre paréntesis («(mezcla)»). Antes decían
  «Português» o «Deutsch» en un sitio y «Portugués» o «Alemán» en otro, e «Inglés» frente a «Solo inglés».
- Los asistentes de una cita de Outlook de empresa («Apellido, Nombre») se unen con «; », como en el resto de la app:
  con «, » eran el doble de personas en la tarjeta y en la pista de quién habla.
- «Workstream» y «proyecto» en vez de «tema»: en el editor de listas, en la página de la reunión y en el directo
  («Proyecto»), en la frase de «Probar» («proyecto sugerido»), en el LEEME, en «Incluir mis listas de vocabulario» y en
  los errores de las listas («Ese workstream ya no existe»).
- Lo escrito y sin guardar no sale de Ajustes. Las confirmaciones («Apagar», «Activar» las voces, «Quitar» una
  carpeta vinculada) se abren encima, sin cerrarlo, y con «Cancelar» o Escape Ajustes sigue como estaba. «Probar otra
  vez» de las listas e «Importar listas…» se hacen ahí mismo. Lo que cambian (las carpetas vinculadas, «Reuniones sin
  proyecto», las listas, las voces) se pone al día sin tocar lo escrito.
- Antes de abrir algo que ocupa el sitio de Ajustes (editar o crear una lista, «Editar la lista» de Proyectos, el
  asistente con «Probar» o «Cambiar…») con cambios sin guardar, se pregunta: «Tienes cambios sin guardar», con
  «Guardar y seguir», «Descartar» y «Volver» (también con Escape o un clic fuera). Si no se puede guardar, se queda en
  Ajustes y lo dice. Sin cambios, se sigue sin preguntar. Cuentan también los destinos de «Copias en otras carpetas» y
  las claves adicionales del directo. Lo que ya guardaron «Aplicar ahora» o «Probar conexión» no cuenta: la clave
  probada se enseña como «Guardada», y lo escrito mientras se guardaba sigue contando. Tras «Probar conexión», ir a
  otro proveedor y volver enseña lo probado; antes volvía lo de cuando se abrió Ajustes, y «Guardar» lo guardaba.
  Mientras esa pregunta o una confirmación están abiertas, el teclado no sale de ellas, y al cerrarlas se vuelve a
  donde se estaba en Ajustes.
- Al volver de esos subdiálogos, o al abrir Ajustes de cualquier forma, se ve lo guardado. Eso incluye una lista de
  «Proyecto al grabar» renombrada o borrada en su editor: sale con su nombre nuevo o en «Automático», y «Guardar»
  funciona. Antes, lo escrito se reponía al volver, y en cada revisión salía un caso en que lo repuesto ya no casaba
  con lo guardado. El último: una API key escrita volvía oculta tras guardar otro modo en el asistente, y «Guardar» la
  mandaba.
- Desde el asistente abierto en Ajustes, «Revisar en Ajustes» e «Ir a Avanzado» llevan a esa sección. «Revisar en
  Ajustes» la abre con «Con tu cuenta de Claude» marcada.
- La carpeta de las reuniones sin proyecto que no se encuentra no tira el resto de lo escrito en el plan (el contexto),
  y el plan dice por qué graba en otra («No se encuentra la carpeta de las reuniones sin proyecto (…): se guarda
  aquí»). Un proyecto sin carpeta propia (con sus listas en una carpeta conectada) lo dice en su línea, con dónde
  asignársela («Ajustes › Proyectos o, al grabar, con «Cambiar» y «Todas las de …»»). Estos avisos salen en Ajustes, en
  la tarjeta de grabar y, delante y sin repetirse, en el aviso de Windows de Ctrl+Alt+R.
- «Proyecto al grabar» fijo cambia también el proyecto del plan de arriba, para que se vea su efecto.
- En la fila que se edita en Proyectos, la carpeta va con su ruta («Alfa › Reuniones»): cerrado, el desplegable decía
  solo «Reuniones».
- Con «API externa» y sin su clave, el plan lo dice en «Transcribe» y «Resume» («Falta la API key»). Los errores que
  mandaban a «Ajustes ⚙ → Transcripción en la nube» mandan a «Procesado».
- Al abrir Ajustes, una sola petición del plan para «Al grabar» y la tabla de Proyectos. «Vocabulario de las reuniones
  sin proyecto» tiene nombre para un lector de pantalla.
- «Activar» y «Desactivar» de Outlook dicen a un lector de pantalla qué activan; con el teclado, el foco sigue en el
  botón tras activar.
- Con la copia de Claude Code de VS Code y una organización que no admite API keys, el botón es «Probar otra vez»
  (se entra en el panel de VS Code), no «Abrir PowerShell».
- No se puede dejar en Ajustes «Sin micrófono» y «Sin el audio del PC» a la vez (no se grabaría nada): el plan lo avisa
  y al guardar se dice por qué.
- Si no se puede calcular el plan o la carpeta de cada proyecto, Ajustes lo dice en una frase con «Probar otra vez»,
  sin texto técnico ni una tabla que parezca correcta.
- Al abrir Ajustes, el foco va a la sección abierta: a 900×600 el plan ya no se queda fuera de la vista.
- Accesibilidad: cada desplegable y casilla de Ajustes y de los paneles «Cambiar» de la tarjeta tiene su nombre para
  un lector de pantalla (los de la tabla de Proyectos dicen de qué proyecto son, y cada «Editar», cuál edita). Los
  avisos de abajo («Ajustes guardados» y los demás) también se leen en voz alta.
- «Otra carpeta (fuera de Chuleta)…» en «Reuniones sin proyecto» cuenta como cambio sin guardar: lo dice «Hay cambios
  sin guardar» y la carpeta sale ya en la lista de carpetas vinculadas, con su «quitar». Antes se vinculaba y se
  enseñaba elegida sin avisar de que faltaba guardarla.
- Si la app no puede leer la lista de micrófonos y salidas, Ajustes y la tarjeta de grabar lo dicen en una frase
  («No se ha podido leer la lista de micrófonos y salidas de audio») en vez de callarlo o de enseñar el final del error
  de la grabadora, que queda al pasar el ratón.

### Grabar: una tarjeta que dice qué va a pasar, y lo mismo con Ctrl+Alt+R (07/11/2026)
Lo que pidió: grabar «no es nada amigable» y no estaba claro si lo que se configura es lo que de verdad se usa
(msg 1196).
- «● Grabar» abre una tarjeta: la reunión (el nombre sale de Teams, de Outlook o de lo que escribas, con tus reuniones
  recientes como sugerencia), el proyecto › workstream y, debajo, **«Así se va a grabar»**: dónde se guarda, idioma,
  vocabulario, quién transcribe, quién resume y el audio, cada línea con **de dónde sale** (esta reunión, el
  workstream, el proyecto o Ajustes). Sustituye al formulario de 15 controles.
- «Cambiar» en cada línea pregunta **para qué reuniones**: solo esta (lo de siempre por defecto), todas las del proyecto
  o todas (Ajustes). Sustituye a «Guardar como perfil por defecto», que mezclaba las dos cosas. Lo que se cambia solo
  para una reunión ya no pasa a ser el predeterminado de la siguiente.
- El proyecto se propone, en este orden, por la misma reunión grabada antes, por el título y por la última grabación,
  siempre con el motivo a la vista («como la última «Comité» (vie 30 oct)»). «La última usada» ya no es un valor por
  defecto escondido.
- **Un solo cálculo** en la app (`/api/grabar/plan`) para la tarjeta, Ctrl+Alt+R y el aviso de Teams: lo que enseña la
  tarjeta es exactamente lo que se manda a grabar, y Ctrl+Alt+R graba con el mismo plan (sin los cambios de esa
  reunión). Antes la regla estaba escrita dos veces, una en la página y otra en el servidor. El aviso de Windows al
  empezar con Ctrl+Alt+R dice la reunión, el proyecto y el idioma.
- La reunión de Teams u Outlook que llega tarde se espera como mucho 2 s; si ya has escrito o elegido algo, se propone
  («Teams dice «…» · Usar») y no lo pisa.
- Desarrollo y CI: `tests/test_plan.py` (capas, origen de cada valor, el orden del proyecto, «Usarlo en» y la prueba de
  paridad Ctrl+Alt+R = tarjeta) y `test_ui_smoke.test_tarjeta_de_grabar` (en Chromium a 900×600: cabe, no pisa lo
  escrito y el cuerpo de /api/rec/start es el plan pintado).

### Calendario de Outlook: funciona con el clásico y, si no puede, dice por qué (07/11/2026)
Lo que vio: «La conexión al calendario no está funcionando» (msg 1196), con el Outlook clásico (msg 1210).
- **Las citas se leían con una hora de más (dos en verano)**, así que la reunión en curso quedaba fuera de la hora
  buscada y la app decía «Outlook no tiene ninguna reunión a esta hora» con Outlook conectado. pywin32 entrega las
  fechas de COM con la zona UTC pegada pero sin convertirlas (`PyTime.cpp` de pywin32 312), y la app las convertía a
  la hora local. Ahora se toma la hora tal cual. «Probar» la compara con la UTC de la cita y con la de Windows.
- **«Probar con mi calendario» prueba la conexión**, no solo si hay una reunión ahora: lee la agenda de hoy y dice
  «Outlook clásico: conectado · próxima reunión 10:00» (en verde aunque no haya reunión), con los pasos debajo.
- **El estado, siempre a la vista** en la fila de Outlook de Ajustes → Calendario y Teams (`GET
  /api/calendario/estado`), en una frase: conectado, o la causa y qué hacer. Apagado y con el Outlook clásico
  instalado, ofrece «Activar». Las otras causas: el
  clásico cerrado, el Outlook nuevo, sin Outlook clásico, falta pywin32, Outlook abierto como administrador u Outlook
  con un aviso abierto.
- **Asistentes y aviso de seguridad de Outlook.** El organizador, los asistentes y el cuerpo de una cita están
  protegidos: según el antivirus y la directiva de la empresa, Outlook pregunta («Un programa está intentando obtener
  acceso…») o lo deniega. Ahora se leen solo de la cita elegida y después del asunto y la hora, así que el nombre y la
  hora llegan aunque Outlook pregunte. La app sola (en mitad de una llamada) solo los lee cuando «Probar» o «Activar»
  ya han visto que Outlook no pregunta; si pregunta, se leen al pulsar «Probar». La frase solo dice que es la empresa
  si lo dice su directiva.
- COM: `CoUninitialize` también cuando falla la conexión, IDispatch sin la caché `gen_py` de pywin32 y un reintento si
  Outlook se acaba de abrir. El filtro de fechas: si Outlook no entiende el formato regional (da error o devuelve citas
  de otro día), se prueba con el ISO y con el de EE. UU.
- `server.log`: cada cambio de estado y el detalle de «Probar», sin asuntos ni nombres; «Copiar diagnóstico» lo enseña.
- Desarrollo y CI: tests con un COM falso que imita los errores reales, el `CoInitialize` por hilo, las periódicas y el
  aviso de seguridad. En Windows, con pywin32 real: las fechas de COM con la zona de Madrid (`windows-real`), los
  procesos y el registro. `lookfeel-e2e` comprueba que el pywin32 que instala INSTALAR.bat se importa y llega a COM.

### Cambiar la carpeta en pleno directo (07/11/2026)
Lo que pidió: «Quiero poder cambiar la carpeta durante la reunión. Es posible?» (msg 1198).
- En la cabecera del directo, **«Se guarda en \\Proyecto\\AAAA-MM-DD Nombre\\ · Cambiar»** (sustituye a la miga). Se
  elige entre las carpetas principales de tus proyectos u **«Otra carpeta…»** con el selector de Windows (si está
  fuera de Chuleta, se conecta como con «Conectar carpeta»).
- La grabación sigue escribiendo donde empezó (en Windows no se puede mover un archivo abierto): la línea pasa a «Se
  moverá a … al terminar · Deshacer», también en la página de la reunión. Cuando ya no queda nada en marcha (la
  grabación, el directo, el repaso final, las voces y el resumen), Chuleta mueve la reunión entera, con su carpeta, y
  avisa «Movida a … · Abrir carpeta»; la página que la tenía abierta pasa sola a la ruta nueva.
- Lo elegido se guarda: si cierras Chuleta antes (o a medio mover), se termina al volver a abrirla. Si un archivo está
  en uso (OneDrive, el antivirus), no se mueve nada y se reintenta con espera durante unos 20 minutos; si no se puede,
  la reunión se queda donde estaba y se dice. Unas notas o una corrección que se guarden justo al moverla esperan un
  instante y van a la ruta nueva; renombrar o borrar a la vez, también esperan. Desde la revisión de la integración
  del 08/11/2026, también las correcciones de quién habla (nombre, «No es esta persona», «Es el mismo que…»,
  «Fusionar»), «Exportar» hablantes y «Restaurar el original» de la transcripción, que antes se escribían en la ruta
  vieja y se perdían. «Deshacer» manda mientras no se haya movido.
- Arrastrar en el árbol una reunión que se está grabando o procesando ya no da el aviso de OBS: se moverá al terminar.
  Renombrarla, cuando termine.
- Cambiar el tema (el proyecto) en pleno directo hace lo mismo: si la reunión estaba en la carpeta de su proyecto, se
  moverá a la del proyecto nuevo al terminar (si la elegiste a mano, o elegiste otra con «Cambiar», se queda esa).

### Una carpeta por reunión, dentro de la de su proyecto (07/11/2026)
Lo que pidió: «Que se cree una carpeta por reu. Esto que sea automático dentro de la main folder asignada por el
proyecto o workstream» (msg 1158).
- Cada grabación nueva va en su carpeta, **`AAAA-MM-DD Nombre`** (sin nombre, `AAAA-MM-DD HH-MM`; si ese día ya hay otra
  igual, `… (2)`), con el audio y sus textos dentro con el nombre de siempre. Lo grabado antes **se queda donde está**.
- La carpeta de la reunión va dentro de la **carpeta principal** de su workstream o de su proyecto. Sin asignar, es la
  carpeta del proyecto (la que tiene su carpeta `Vocabulario`); la que ya recordaba la app para cada proyecto se queda
  como asignada (si era la carpeta de una reunión, «AAAA-MM-DD …», pasa a su carpeta padre, para no meter las nuevas
  dentro). Grabar en otra carpeta una vez ya no cambia la del proyecto. Con tema, la del proyecto va antes que la fija
  del perfil; sin tema, la fija del perfil o la última. Ctrl+Alt+R, el aviso de Teams y la tarjeta usan la misma regla,
  y el aviso de Ctrl+Alt+R dice dónde se guarda.
- Un proyecto cuyas listas vienen de una carpeta conectada no tiene carpeta en el árbol: su carpeta principal es la
  que le asignes en Ajustes → Proyectos o, al grabar, en la tarjeta con «Cambiar» y «Todas las de <proyecto>» (con
  «Solo esta reunión» es solo para esa). Mientras no tenga, la tarjeta lo avisa y dice cómo. Antes, Ctrl+Alt+R y el
  aviso de Teams con su tema grababan en la última carpeta usada, que podía ser la de otro proyecto, sin decir nada. Si el proyecto también está en el árbol, sus listas conectadas graban en
  la carpeta del proyecto, como las demás. La app ya no toma una carpeta de listas conectada por la carpeta de su
  proyecto, tampoco si queda dentro de una carpeta vinculada (antes las reuniones caían dentro solas y luego no se
  podía volver a conectar); una carpeta vinculada dentro de una de listas sí es del árbol. Al
  quitar una carpeta de listas, sus workstreams pierden la carpeta y el idioma asignados (revisión de la integración
  del 08/11/2026).
- Al empezar a grabar, la app espera hasta 10 s (eran 3) a que la grabadora cree el archivo. Con el PC cargado tardó
  más y la reunión se quedaba sin cabecera (tema, contexto, participantes) y sin directo; se vio en el E2E de carpetas
  (revisión de la integración del 08/11/2026). En cuanto existe, sigue.
- Si `settings.json` se rompe, se rescatan también las carpetas principales asignadas y la carpeta por reunión
  apagada (antes volvían a «sin asignar» y a encendida sin avisar), y el aviso lo cuenta.
- En Reuniones › Tabla, «Agrupar por carpeta» y la columna Carpeta usan la carpeta que contiene la de la reunión, como
  el árbol y las migas: antes cada reunión nueva salía como su propio grupo.
- En el árbol, la carpeta de una reunión se ve como la reunión (no se repite cada entrada) y no sale entre las carpetas
  de destino. Mover o renombrar la reunión mueve o renombra su carpeta con todo lo que tenga dentro, solo si la creó la
  app y no hay otra grabación dentro: una carpeta tuya que se llame igual se queda como está. Nunca se pisa un archivo
  y, si uno está en uso, no se mueve nada (antes podía quedarse a medias). Borrarla quita su carpeta si se queda vacía;
  llevarla a la carpeta que contiene la suya la saca de ella.
- Se puede apagar en Ajustes → Al grabar («Una carpeta por reunión») o en `settings.json` (`"carpeta_por_reunion":
  false`).

### Vocabulario desde una carpeta conectada (07/11/2026)
Lo que pidió: «me gustaría poder conectar a una carpeta, no tener que pegarlo o escribirlo ahí mismo» (msg 1148).
- **«Usar las listas de una carpeta…»** (Ajustes → Proyectos; en ● Grabar, al final del selector de proyecto y, sin
  listas, en el panel del vocabulario): eliges la carpeta donde ya tienes tus listas y la app las lee de allí **en vivo**, sin copiarlas.
  Lo que cambies en esos `.txt` vale desde la siguiente grabación (en directo, desde el siguiente trozo). Ajustes
  enseña cada carpeta con cuántas listas da, «Abrir» y «quitar» (no borra nada). Si el tema del perfil salía de la
  carpeta que quitas, vuelve a «Automático» también en Ajustes, y «Guardar» funciona (antes daba «Ese tema de
  vocabulario ya no existe»; revisión de la integración del 09/11/2026).
- Valen tres formas: `.txt` sueltos en la carpeta (el proyecto es el nombre de la carpeta; si se llama `Vocabulario`,
  el de la de encima), `<carpeta>\Vocabulario\*.txt` y `<carpeta>\<Proyecto>\Vocabulario\*.txt`. Cada `.txt` es un
  tema, con los términos separados por comas, punto y coma o **uno por línea**.
- Se saltan, con aviso en Ajustes: un `.txt` «solo en línea» de OneDrive (leerlo lo descargaría y, sin conexión, podía
  dejar esperando) y uno de más de 64 KB (no es una lista). Sin aviso: los ocultos (`._x.txt` de un USB de Mac) y las
  transcripciones de una grabación de al lado.
- No se puede conectar la carpeta de la app ni una de dentro de una vinculada (sus listas ya se leen solas), una que
  contenga la de la app o tu usuario, una de destinos de salida ni una con grabaciones; tampoco se puede elegir una
  carpeta conectada como destino de salida (los documentos se tomarían por listas).
- La carpeta conectada no sale en el árbol de reuniones ni en «Carpeta de destino»: ahí no se graba ni se mueve nada.
  Editar o crear un tema de un proyecto conectado escribe en esa carpeta (es tuya); el editor lo dice. Guardar sin
  cambios no reescribe el archivo, y una lista con un término por línea se guarda igual. El proyecto de una lista
  conectada no se cambia desde la app (sale del nombre de su carpeta): antes eso la sacaba de tu carpeta.
- Tampoco cuentan como listas los documentos de los destinos de salida (llevan la marca `generado_por`), ni un tema
  repetido dentro de la misma carpeta conectada (`Infra.txt` suelta y `Vocabulario\Infra.txt`: vale la primera, con
  aviso). Un destino de salida no puede contener una carpeta conectada.
- Si una lista conectada se llama igual que una de la app (las que importaste de esa carpeta, por ejemplo), las
  reuniones que ya usaban la de la app la siguen usando.
- Si vinculas (o desvinculas) la carpeta que contiene una conectada, el tema fijo del perfil, el último elegido y el de
  cada reunión siguen a sus listas: antes Ctrl+Alt+R se quedaba sin vocabulario.
- Las listas se leen también si las guardó el Bloc de notas antiguo o Excel (`cp1252`, «Texto Unicode» en UTF-16,
  columnas separadas por tabuladores): antes «José» llegaba a Whisper como «Jos�». Vale para todas las listas, y línea
  a línea: un carácter suelto en otra codificación solo cambia su línea. En el paquete para otro PC van en UTF-8 y sin
  temas repetidos.
- Al guardar un tema de un proyecto que se llama igual que otro, se queda en su carpeta (antes se mudaba a la del
  primero).
- «Importar listas de otra carpeta…» se llama ahora **«Copiar listas de otra carpeta…»** y hace lo mismo (copia a la
  carpeta de la app, sin pisar).
- API: `POST /api/vocab/link/add {path, name?}` y `/api/vocab/link/remove {rel}`; `/api/vocab` lleva `carpetas` (cada
  una con `listas` y `avisos`) y cada lista conectada, `conectada`; `/api/config` lleva `vocab_links`. Las listas
  conectadas tienen ids `:v<id>/…`, que `safe()` no resuelve. Ajuste nuevo `vocab_links`, que se rescata de un
  `settings.json` roto como las carpetas vinculadas.

### «Copiar para correo»: el resumen con formato (07/11/2026)
Lo que pasaba: «Copiar» dejaba en el portapapeles el Markdown del resumen (`## Resumen`, `- [ ] Luis: …`, `[1:12]`), y
así llegaba al correo (auditoría de UX del rediseño, A5; mock 08).
- «Copiar» pasa a **«Copiar para correo»** (pestaña Resumen): copia un acta con el título, la fecha («viernes 6 nov,
  10:00») y los asistentes confirmados, y el resumen con sus secciones, listas y negritas; el responsable de cada tarea,
  en negrita. Sin `##`, sin `[ ]` y sin los minutos (en un correo no saltan al audio).
- Al portapapeles van dos versiones a la vez: **HTML** (Outlook, Teams y Word lo pegan con formato, con letra Aptos o
  Calibri; sin letra, Outlook pegaba en Times) y **texto limpio** de reserva (Bloc de notas, chats sin formato). Si el
  navegador no deja escribir HTML, solo el texto, y el aviso lo dice («sin formato»).
- Los participantes que sugirió Outlook o Teams sin confirmar no salen en la cabecera (como en el resumen). La sección
  «Participantes» del resumen va tal cual, con sus roles.
- Fiel a lo que se ve en la pantalla: tablas como tablas (en el texto, «a · b»), listas numeradas, tareas hechas con
  «✓», líneas de continuación dentro de su punto, negritas dentro de una tarea, enlaces como «texto (url)». Solo se
  quitan los minutos entre corchetes, también en grupo, en rango y de más de 100 minutos (`[1:12, 3:40]`,
  `[12:34–13:10]`, `[105:20]`); «(10:30)» se queda, porque puede ser una hora. Un audio sin fecha lleva su nombre, una
  fecha imposible en el nombre del fichero ya no rompe la copia, y con el resumen en inglés la fecha va en inglés.
- Sin firma de IA: es tu correo y ya lo revisas (decisión D10). La página sigue diciendo que el resumen es de la IA.
- Desarrollo y CI: `test_copiar_correo.py` (el acta, con node), un ui-smoke que lee el portapapeles en Chromium, y
  `test_portapapeles_webview2.py`, que en `windows-real` abre WebView2, pulsa el botón con el ratón de Windows y lee los
  formatos «HTML Format» y CF_UNICODETEXT del portapapeles de Windows.

### «Quién habla»: una persona ya no sale como varias (08/11/2026)
Lo que vio: «a una persona la cataloga como 10 personas distintas a lo largo de la reunión» (msg 1242).
- Medido con reuniones reales de 4 personas con los hablantes anotados (AMI Meeting Corpus, CC BY 4.0) y
  `scripts/eval_voces.py`: la versión anterior partía a cada persona en 4 grupos de media (hasta 11) y enseñaba 36
  hablantes para 12 personas (48 para 16 en las de prueba).
- Al terminar de separar las voces, la app **une los grupos con la misma voz** (huellas parecidas), pasa los trozos
  cortos al hablante que más se les parece (solo si se le parecen mucho: si no, siguen como «(breve)») y separa con
  un corte más alto. Ajustado con 3 reuniones y comprobado con otras 4: 15 hablantes para 12 personas y 18 para 16, con
  la tasa de error de quién habla de 0,52 a 0,23 y de 0,49 a 0,25. Con 4 reuniones montadas con voces chilenas, 16 para
  16 (antes 18). Medido solo en reuniones de 4 personas.
- Si la reunión trae participantes (de Outlook o Teams, o escritos antes de identificar las voces), su número sirve de
  pista: con más hablantes que participantes + 1 se unen los dos que más se parecen, pero nunca dos voces que no se
  parecen lo bastante.
- **«Es el mismo que…»** en la etiqueta de un hablante: si aun así la app parte a alguien en dos, se juntan en uno.
  Se niega si los dos ya son personas distintas (o tú y otra persona): eso es corregir el nombre de uno de ellos.
  Si después se vuelve a identificar la reunión, la unión no se guarda.
- Las reuniones ya identificadas no cambian hasta «Volver a identificar».
- Los participantes de Outlook con el nombre del directorio de la empresa («Apellido, Nombre») se separan con «; »,
  como en Outlook: antes, con «, », cada uno contaba como dos personas en la pista y en las notas no se sabía dónde
  acababa cada nombre. La pista cuenta así solo el texto que escribió la app (también si lo confirmas sin cambiarlo);
  editado a mano, o si la app se ha reiniciado entretanto, lo cuenta como texto libre, del lado que une menos voces
  (revisión de la integración del 08/11/2026).
- «Fusionar con…» (el cuadro de un hablante) ya no se queda a medias si una reunión está abierta en OneDrive o en otro
  programa: lo dice, las dos personas siguen ahí y se puede repetir (antes la reunión se quedaba con una persona
  borrada).

### «Con tu cuenta de Claude» en Windows: «Comprobar» sin esperas ocultas (07/11/2026)
Lo que vio: «Comprobar» tardó unos minutos en su PC antes de responder (msg 1187).
- La app espera a que Claude Code **termine**, no a que se cierre su salida: en Windows un proceso que Claude Code deja
  en marcha (el actualizador en segundo plano, por ejemplo) heredaba esa salida y la llamada esperaba hasta que acabara.
  Si pasa, queda una línea en `server.log`.
- Las llamadas de la app a Claude Code van **sin tráfico no esencial** (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, de su
  documentación): ni actualización automática, ni telemetría, ni comprobaciones de disponibilidad, que en una red de
  empresa pueden frenarse. Solo para esas llamadas: el Claude Code del usuario no cambia.
- Con Claude Code instalado por npm (`claude.cmd`), la app lanza lo mismo que el `.cmd` sin pasar por cmd.exe: una carpeta
  con «&» o «%» rompía la orden («'100%' is not recognized…», medido en Windows).
- «Comprobar» enseña los segundos mientras espera (y avisa de que la primera vez puede tardar uno o dos minutos) y deja en
  `server.log` lo que tardó cada paso: `--version`, la petición mínima y el total.
- Cuando Claude Code está pero falla, la frase dice qué es: la red o el proxy de la empresa (un proxy que pedía
  credenciales, «407 Proxy Authentication Required», se tomaba por «sin sesión iniciada» y volver a iniciar sesión no lo
  arreglaba), una organización que no deja usarlo con esa cuenta, o que falta Git para Windows. Sin sesión, como antes.
- Desarrollo y CI: `claude-windows.yml` instala Claude Code de verdad en Windows (instalador nativo y npm en una carpeta
  con espacios, «&» y «%»), sin sesión iniciada, con la app abierta desde antes, y mide cada llamada en frío y en
  caliente.

### «Con tu cuenta de Claude» con la cuenta de la empresa (SSO) y Claude Code de VS Code (08/11/2026)
Lo que pidió: que funcione con su cuenta de Claude de empresa, con SSO, y él usa Claude Code en VS Code (msgs 1224-1228).
- El camino principal es **Claude Code instalado aparte** y su inicio de sesión **en el navegador**, con el de la empresa
  si la cuenta es de empresa (msg 1261). «Abrir PowerShell» abre la ventana con Claude Code ya en su PATH (el
  instalador oficial no lo pone en el PATH de Windows: «…\.local\bin is not in your PATH», medido) y, sin sesión, la
  frase dice que se escriba `claude` y se entre en el navegador que se abre. La app no inicia sesión por nadie.
- Sin Claude Code instalado aparte, la app usa (como respaldo) la copia que trae la **extensión de VS Code** (también Cursor, Windsurf y
  VSCodium; la versión más nueva): la extensión no pone `claude` en el PATH y antes la app decía «No encuentro Claude
  Code». «Comprobar» y `server.log` dicen de dónde salió («Claude Code de VS Code»). La sesión es la misma que la de
  VS Code (se guarda en la carpeta `.claude` del usuario).
- Lo que el usuario o su IT pongan en las variables de Windows con la app ya abierta (proxy, certificados de la empresa,
  `CLAUDE_CONFIG_DIR`, una pasarela…) llega a Claude Code, como a una PowerShell nueva; antes solo el PATH.
- Si el modelo elegido no está para esa cuenta (o para su plan), se repite una vez con el predeterminado de su Claude
  Code; queda en `server.log` y en la respuesta de «Comprobar» (la interfaz aún no lo enseña). Si la organización
  restringe modelos, Claude Code ya cambia a uno permitido por su cuenta.
- Frases nuevas, con los textos de Claude Code 2.1.319 (los de la terminal y los de `-p`, que son otros): sesión
  caducada o revocada (la empresa limita lo que dura), otra ventana renovando la sesión (VS Code abierto), disco lleno,
  el modelo, y la organización que no deja (ya no sugiere la cuenta personal: son reuniones del trabajo). Si no se han
  podido cargar los ajustes que exige la empresa, es la red, no que no deje. Sin sesión en la copia de VS Code, dice
  que se entre en VS Code.
- Con una pasarela de la empresa (`ANTHROPIC_BASE_URL` propia en el entorno o en los ajustes de Claude Code), la app ya
  no le quita su credencial del entorno.
- Desarrollo y CI: `claude-windows.yml` suma dos pasadas: «vscode» (solo la copia de la extensión, del paquete de Open
  VSX) y «pasarela» (una pasarela falsa en el runner, puesta en el registro con la app abierta: Claude Code real
  contesta a través de ella, sin Anthropic ni credenciales).

### Voces dobles en la grabación (07/11/2026, msgs 1154 y 1156)
- La grabadora pone cada fuente (micro y salidas) en una línea de tiempo común según cuándo llega su audio. Antes cada
  fuente se tomaba por la cabeza de su cola: tras un corte (reabrir el dispositivo al cambiar de cascos o de salida,
  o audio perdido con el PC cargado) esa fuente quedaba adelantada para siempre, y con altavoces el eco de la sala
  (~30 ms) pasaba a ser una repetición de cientos de ms. Medido con la grabadora real y dispositivos simulados: un
  corte de 300 ms dejaba el eco a +330 ms; ahora se queda a +30 ms. Un bloqueo al leer (con el audio atrasado
  llegando luego de golpe) no se toma por un corte, tampoco con el primer paquete tarde; la deriva entre relojes se
  corrige dentro de los silencios, en pasos de 15 ms que no se oyen. Lo que un corte de más de ~0,7 s deja sin
  colocar a tiempo cuenta como audio perdido. Los cortes recolocados van a `recorder.log`.
- «Todas las salidas»: una salida que es SOLO una copia retrasada de otra (un dispositivo virtual, el «espejo» de un
  driver) deja de sumarse en cuanto se detecta (unos 4 s): correlación cruzada a 8 kHz y, al quitarle la mejor copia
  de la otra (un filtro corto, que admite que cada dispositivo tenga su reloj o algo de ecualización), le queda menos
  del 2 % propio. Si lleva además otra cosa (una voz, un aviso), se sigue sumando. Se queda la más fuerte. Vuelve a
  sumarse en cuanto deja de ser solo una copia o suena ella sola (en las pausas no se decide nada), con un fundido de
  10 ms. El medidor lo dice: «X (igual que Y: no se suma)».
- Eco de los altavoces en el micro y salida que lleva tu micro («Escuchar este dispositivo», tono lateral): se
  detectan (correlación cruzada en su sentido, con el retardo y la atenuación medidos) y se avisan en el medidor y en
  `recorder.log` con qué hacer («con auriculares no pasa», «quita “Escuchar este dispositivo”»; si tu voz vuelve con
  más de ~120 ms, es el eco de otro participante y así se dice), pero no se toca el
  audio: bajarlos por niveles se comía tu voz cuando hablabais a la vez o sonaba un vídeo (revisión independiente:
  el 73 % de la voz bajaba más de 10 dB). Con las fuentes alineadas, el eco de la sala queda a ~30 ms y no se oye
  doble.
- Para medir una grabación: `python3 scripts/medir_eco.py GRABACION.wav` dice si hay voces dobles y con qué retardo
  (fijo: copia digital o eco estable; cambiante: eco que se mueve o los cortes de antes).

### Configuración en tres pasos: dónde se procesa, a la vista y con las mismas palabras (07/11/2026)
Bernardo, 06/11/2026: «Lo de config es un desastre. El UX es muy difícil y malo de seguir.»
- **La línea del modo.** El diálogo Grabar, la cabecera del directo, la Cola y el chip «EN VIVO» dicen lo mismo y con
  las mismas palabras: «Se transcribe en este PC (Normal) y al terminar la resume Claude con tu cuenta», «Se transcribe
  en este PC (Normal). Sin resumen con IA» o «Se transcribe con Groq y al terminar la resume Groq». Cada modelo tiene
  un solo nombre (Rápido, Normal, Preciso, Máxima). Los modelos del directo y «Rehacer con este modelo» quedan detrás
  de «Cambiar».
- **«Preparando la transcripción».** Mientras se descarga o se carga el modelo del directo, la cabecera lo dice con
  barra, %, MB y tiempo, y que la grabación sigue sin perder nada. El hueco del texto ya no dice «Escuchando…» ni la
  Cola «al día» con 0:00 transcrito. En Grabar, si el modelo aún no está en el PC, lo avisa con su tamaño y
  «Descargar ahora».
- **Asistente «Configurar Chuleta», en tres pasos.** 1, elegir dónde se procesan las reuniones (cada opción dice qué
  transcribe y qué resume; el aviso de privacidad, el de siempre, solo bajo la elegida). 2, prepararlo: la descarga de
  los dos modelos de Whisper del PC (el del directo y el de la cola) con una sola barra, los pasos de Claude Code
  (línea para copiar, winget, «Abrir PowerShell», y se marca solo cuando Chuleta lo encuentra) o la clave de la API.
  3, «Probar»: graba 3 s de tu voz («Di: hola, esto es una prueba»), los transcribe con el modo elegido y, si toca, los
  resume; sin voz o sin micro, prueba la cadena con 1 s de silencio y lo dice. Se para en el primer fallo con una
  frase y su botón. No guarda nada hasta «Terminar» (como «Aplicar ahora»). Se abre desde la tarjeta de bienvenida,
  desde Ajustes («Configurar paso a paso») y, tras actualizar, desde un aviso único en Inicio si la configuración
  nunca se ha probado. `/api/probar` corre en su hilo y la última prueba queda en Ajustes.
- **Ajustes por secciones, con «Guardar» siempre a la vista.** Antes, un solo diálogo de 6 a 7,5 pantallas con
  «Guardar» al final; ahora siete secciones a la izquierda (Procesado, Grabar, Vocabulario, Voces, Carpetas, Avanzado,
  Ayuda) y un pie fijo con «Cancelar · Guardar» que avisa de «Hay cambios sin guardar». Arriba de Procesado, una
  tarjeta dice lo que hay ahora (dónde se transcribe, quién resume y cuándo se probó por última vez) con «Probar» y
  «Cambiar…» (el asistente). Debajo sigue el cambio rápido con «Aplicar ahora». Lo técnico de la nube (modelo del
  directo, otras claves, trozos, paralelismo) pasa a Avanzado y solo sale con la API; «repasar la grabación entera» y
  el cupo de Groq, en Grabar, también solo con la API. Voces queda tal cual. Ajustes se abre en la última sección vista
  (o en la que toca: falta la clave → Procesado).
- **Errores en una frase con qué hacer** (`explicar.py`): Claude Code sin sesión, sin instalar, sin respuesta o con el
  plan en su límite de uso; una pieza de la instalación que falta («No module named…»); un proxy que contesta en lugar
  de la API. Los de la nube y la descarga de modelos ya venían en una frase (`connection_error`, `cloud_http_error`): se
  dejan igual y ganan su botón. La Cola, los avisos, «Comprobar» y «Probar conexión» usan la misma frase; el texto
  técnico sigue en `server.log`.

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

### Icono en la bandeja del sistema (07/11/2026)
Lo que pidió Bernardo el 02/11/2026: «que Chuleta quedase abierta como una app aquí corriendo aunque le ponga salir,
solo se pueda cerrar si la cierro desde ahí al salir».
- **Chuleta tiene su icono en la bandeja** (la zona de notificación, junto al reloj) mientras está abierta, con la
  ventana a la vista u oculta. Un clic la abre. El clic derecho abre un menú con «Abrir Chuleta», «Grabar» (o
  «Parar la grabación», según toque; lo mismo que Ctrl+Alt+R), «Arrancar con Windows» y «Salir». El texto del icono
  dice si está grabando (y cuánto lleva) y cuántos trabajos hay en marcha.
- **Cerrar la ventana (la X, Alt+F4) la deja en la bandeja.** Ya no sale el aviso de «sigue en segundo plano» de la
  página: la primera vez, un globo en el icono dice que sigue ahí y cómo salir (un clic en el globo abre la ventana;
  si Windows no lo enseña, se vuelve a intentar la próxima vez).
- **Salir** es el mismo apagado que ⚙ Ajustes › «Apagar Chuleta», que se mantiene (decisión por defecto del backlog).
  Si está grabando no sale, y lo dice un aviso de Windows; con trabajos en marcha pregunta antes (Ajustes pregunta
  siempre). El icono se quita al apagar, también cuando Windows cierra la sesión o si se apaga mientras arranca, para
  que no quede un icono fantasma.
- **Arrancar con Windows** (en el menú del icono; apagado por defecto) copia el acceso directo del menú Inicio en la
  carpeta Inicio de Windows: sin registro ni permisos de administrador, con el mismo icono y AppUserModelID. Si
  INSTALAR.bat rehace el acceso directo, la copia se pone al día la siguiente vez que se abre Chuleta; el
  desinstalador la quita. Las carpetas son las que da Windows (también si la empresa las redirige). Al iniciar sesión
  se abre con su ventana, como al pulsar el acceso directo. Si se desactiva desde el Administrador de tareas
  (Aplicaciones de arranque), Windows no la abre aunque el menú siga marcado. `/api/inicio-windows` (GET y POST
  `{"activado": true|false}`) queda para la casilla de Ajustes.
- Windows pone los iconos nuevos en los iconos ocultos (la flecha ^): no se puede cambiar por programa; se arrastra
  una vez junto al reloj (README y LEEME lo dicen).
- Sin dependencias nuevas: `System.Windows.Forms.NotifyIcon` por pythonnet, que ya carga pywebview (`_app/bandeja.py`).
  Si el icono no se puede poner, la app sigue como antes (con el aviso de la página al cerrar) y el log lo dice. Sin
  ventana propia (sin WebView2, en Edge) no hay icono: se apaga desde Ajustes. Tras una revisión independiente: Apagar
  durante el arranque ya no deja el icono puesto, lo que llegue al hilo de la ventana con el icono ya quitado no saca el
  diálogo de error de WinForms y dos X seguidas dan un solo globo.
- Tests: `tests/test_bandeja.py` (el menú, el clic, el texto, el globo, el Dispose, la caída limpia, cerrar sin el
  aviso de la página, Salir y Arrancar con Windows) y, en windows-real, con WinForms y WebView2 de verdad: la X oculta
  la ventana, el clic en el icono (el mensaje que Windows le manda) la vuelve a enseñar y al apagar el icono se quita
  antes de terminar. lookfeel-e2e comprueba en el log que el icono se pone y se quita.

### Doble apertura al arrancar (07/11/2026)
Lo que se veía como «se me abrían como 2 veces la app»: al pulsar el acceso directo otra vez mientras la app
arrancaba (en el portátil de la prueba del 02/11/2026, 31 s entre el arranque y la ventana), salían la ventana de
Chuleta y otra de Edge con la misma interfaz, o un aviso de «No se puede usar el puerto 8765: lo tiene otro programa».
- **Instancia única de verdad.** Al arrancar, la app bloquea `instancia-8765.lock` en su carpeta de datos (el sistema
  lo suelta al terminar el proceso, también si se cuelga o se mata; dentro van su pid y la hora). Un segundo
  lanzamiento lo ve al instante y espera como mucho 8 s, sin ventana: si la primera copia contesta, le pide su
  ventana; si suelta la marca (pulsar «Apagar» y volver a abrir enseguida), arranca él; si no, termina sin más,
  porque la ventana saldrá sola. Ya no acaba en el aviso de «puerto ocupado». Si la otra copia lleva más de 3 minutos
  sin contestar, lo dice, con su pid, para poder cerrarla en el Administrador de tareas. Un aviso de error al
  arrancar suelta la marca, para que el siguiente lanzamiento no espere a esa copia.
- **Actualización con la versión anterior abierta.** Sigue retirándose como antes, pero nunca se apaga ni se mata una
  copia de la versión nueva: con doble clic, la segunda copia deja que la retire la primera y le pide la ventana.
- **La ventana «en camino».** Desde que la app empieza a abrir su ventana hasta que se ve, `/api/mostrar` (lo que pide
  el acceso directo) ya no abre Edge: contesta que está en camino y, cuando aparece, se pone delante. Antes, en ese
  rato, la ventana aún no existía (y se abría Edge) o existía sin enseñarse: pywebview esperaba hasta 20 s y el
  acceso directo, cansado a los 5, abría Edge él mismo. Si en 2 minutos no llega a verse, se da por perdida y entonces
  sí se abre Edge.
- **Edge una sola vez.** Sin ventana propia (sin WebView2), el acceso directo pulsado en los 15 s del arranque ya no
  abre otro Edge encima del que se está abriendo. Pasado ese rato, cada vez que se pulsa abre el suyo, como antes.
- **Al frente, cuando Windows lo deja.** El acceso directo cede a la app que ya corre el permiso de Windows para
  ponerse delante (`AllowSetForegroundWindow`), y la ventana que se estaba abriendo lo intenta al aparecer si alguien
  la pidió mientras tanto. Windows retira ese permiso en cuanto el usuario toca otra cosa: tras una espera larga, lo
  normal es que parpadee en la barra de tareas.
- El log dice qué copia es cada una (`pid` en «arranque», en `/api/ping` y en las líneas de la segunda copia), y
  «Copiar diagnóstico» enseña las líneas nuevas y «ventana abierta (WebView2; hwnd …)».
- Tests: `tests/test_doble_apertura.py` (dos `server.py` de verdad con un pywebview falso que espera a «shown» como
  el 6.2.1 y los tiempos de un PC lento; la marca de instancia entre procesos, también matando al primero; la ventana
  en camino; Edge una vez; lo que hace `main()` en cada caso) y `VentanaRealTests` en windows-real con WebView2 de
  verdad (la segunda copia antes de crear la ventana y con la ventana creada sin enseñar). Sin el arreglo, 4 de 5
  escenarios y los 2 de windows-real fallaban con una ventana de Edge de más. lookfeel-e2e pulsa además el acceso
  directo justo cuando el instalador abre la app: una ventana, ningún Edge, la segunda copia termina sola. Tras una
  revisión independiente: la app ya abierta, oculta y lenta en contestar (la segunda copia se iba sin enseñarla),
  Apagar y volver a abrir, la copia colgada, la actualización con doble clic y el Edge del arranque en los dos
  órdenes.

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
- `ui-smoke` en Windows (08/11/2026): el 400 de `/api/rec/devices` se permite cuando no está instalado `soundcard`
  (el job no lo instala en ningún sistema), no según el sistema operativo. Hasta ahora pasaba en Windows solo porque
  el diálogo de grabar se cerraba antes de que llegara esa respuesta; Ajustes › Al grabar la pide al abrirse y la
  espera. Los fallos de esos tests dicen ya la URL que contestó con error.
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
