; Setup.exe de Tenca, fase 1 (ítem 8 del backlog; aprobado por Bernardo en el msg 1296 del 08/11/2026).
;
; Un asistente de Inno Setup que envuelve el instalador de siempre: copia a su carpeta temporal lo mismo que lleva el
; zip (Tenca\INSTALAR.bat, Tenca\_app\...) y ejecuta Tenca\_app\instalar.ps1 con -DesdeSetup. instalar.ps1 hace todo
; lo demás igual que INSTALAR.bat: Python, librerías, carpeta propia (%USERPROFILE%\Tenca, o la de antes si ya estaba
; instalada, también la de Chuleta), accesos directos, «Aplicaciones instaladas» con su desinstalador
; (_app\desinstalar.ps1) y la migración desde Chuleta. Inno no instala nada por su cuenta: sin carpeta de programa
; (CreateAppDir=no), sin desinstalador ni entrada propia en el registro (Uninstallable=no). Al terminar no enseña su
; página final (DisableFinishedPage): la ventana de instalar.ps1 ya dice cómo ha ido y tiene el botón «Abrir».
;
; Sin firma: Windows enseña SmartScreen («Windows protegió tu PC» → «Más información» → «Ejecutar de todas formas»),
; como con INSTALAR.bat. No se esquiva (README, «Instalar con Setup.exe»).
;
; Lo compila scripts/build_setup.py, que prepara la carpeta con el contenido del zip (server.build_package) y llama a
; ISCC con /D: Fuente (la carpeta que contiene Tenca\), Version («2026-11-09 · abc1234»), VersionNum (2026.11.9.0),
; Salida y Nombre. Instalado en silencio (/VERYSILENT o /SILENT), instalar.ps1 va por consola, escondido y sin abrir la
; app al final (-NoLaunch). El código de salida de Setup.exe es 1 si instalar.ps1 no terminó bien.

#ifndef Fuente
  #error Falta /DFuente=<carpeta con Tenca\INSTALAR.bat y Tenca\_app\...> (lo pasa scripts/build_setup.py)
#endif
#ifndef Version
  #define Version "sin versión"
#endif
#ifndef VersionNum
  #define VersionNum "0.0.0.0"
#endif
#ifndef Salida
  #define Salida "."
#endif
#ifndef Nombre
  #define Nombre "Tenca - Setup"
#endif

[Setup]
AppId=Tenca.Setup
AppName=Tenca
AppVersion={#Version}
AppVerName=Tenca {#Version}
AppPublisher=Bernardo Stegmaier
AppPublisherURL=https://github.com/bastegman/tenca
AppSupportURL=https://github.com/bastegman/tenca
VersionInfoVersion={#VersionNum}
VersionInfoProductName=Tenca
VersionInfoProductTextVersion={#Version}
VersionInfoDescription=Instalador de Tenca
VersionInfoCompany=Bernardo Stegmaier
VersionInfoCopyright=Bernardo Stegmaier (licencia MIT)
; por usuario y sin UAC, como instalar.ps1 (y como VS Code)
PrivilegesRequired=lowest
MinVersion=10.0
; Inno solo deja los ficheros en {tmp}: la carpeta de la app, la entrada de «Aplicaciones instaladas» y el
; desinstalador los pone instalar.ps1 (los de siempre)
CreateAppDir=no
Uninstallable=no
DisableWelcomePage=no
DisableReadyPage=yes
DisableProgramGroupPage=yes
DisableFinishedPage=yes
CloseApplications=no
RestartIfNeededByRun=no
ShowLanguageDialog=no
WizardStyle=modern dynamic
WizardImageFile=asistente.png,asistente-2x.png
WizardSmallImageFile={#Fuente}\Tenca\_app\static\tenca.png
SetupIconFile={#Fuente}\Tenca\_app\static\tenca.ico
SetupLogging=yes
OutputDir={#Salida}
OutputBaseFilename={#Nombre}
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
SetupWindowTitle=Instalar %1
WelcomeLabel1=Instalar Tenca
WelcomeLabel2=Tenca graba, transcribe y resume tus reuniones en este PC.%n%nSe instala solo para tu usuario, sin permisos de administrador. Si ya tenías Tenca o Chuleta, se actualiza en su carpeta: tus reuniones, voces y ajustes no se tocan.%n%nAl pulsar «Instalar» se abre la ventana del instalador con los pasos. La primera vez tarda unos minutos: descarga Python y las librerías.%n%nVersión {#Version}

[Files]
Source: "{#Fuente}\Tenca\*"; DestDir: "{tmp}\Tenca"; Flags: recursesubdirs createallsubdirs ignoreversion

[Code]
var
  Resultado: Integer;

// sin la página «Listo para instalar» (DisableReadyPage), el botón de la bienvenida tiene que decir «Instalar»: Inno
// no lo cambia solo
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpWelcome then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
end;

// PowerShell de 64 bits, como el de INSTALAR.bat (cmd lo lanza desde el Explorador): este Setup es de 32 bits y la
// constante sys le daría el de SysWOW64, que ve otra rama del registro (los Python instalados para todos los usuarios).
// En los comentarios del código, nada entre llaves: en Pascal la primera llave de cierre acaba el comentario
function PowerShellExe: String;
begin
  Result := ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(Result) then
    Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  Mostrar: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\Tenca\_app\instalar.ps1') +
    '" -DesdeSetup';
  Mostrar := SW_SHOWNORMAL;
  if WizardSilent then
  begin
    // en silencio (TI, CI): por consola, escondida, y sin abrir la app al final
    Params := Params + ' -NoLaunch';
    Mostrar := SW_HIDE;
  end
  else
    WizardForm.StatusLabel.Caption := 'Instalando Tenca: los pasos van en la ventana del instalador...';
  Log('instalar.ps1: ' + PowerShellExe + ' ' + Params);
  if not Exec(PowerShellExe, Params, ExpandConstant('{tmp}\Tenca'), Mostrar, ewWaitUntilTerminated, Resultado) then
  begin
    Log('instalar.ps1 no se ha podido ejecutar: ' + SysErrorMessage(Resultado));
    Resultado := -1;
  end
  else
    Log('instalar.ps1 ha terminado con el código ' + IntToStr(Resultado));
  if (Resultado <> 0) and not WizardSilent then
    SuppressibleMsgBox('No se ha podido instalar Tenca. El motivo está en la ventana del instalador y en ' +
      ExpandConstant('{localappdata}') + '\AudiosApp\instalar.log.', mbError, MB_OK, IDOK);
end;

// Setup.exe sale con 1 si instalar.ps1 no terminó bien (para quien lo instale en silencio y mire el código)
function GetCustomSetupExitCode: Integer;
begin
  if Resultado <> 0 then
    Result := 1
  else
    Result := 0;
end;
