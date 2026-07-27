; AutoRender Studio - Instalador com senha
;
; Como definir a senha:
; 1. Recomendado: rode BUILD_INSTALLER_COM_SENHA.bat e digite a senha quando pedir.
; 2. Manual: troque TROQUE_A_SENHA_AQUI abaixo antes de compilar no Inno Setup.

#define MyAppName "AutoRender Studio - CRIAMIGOS"
#define MyAppVersion "1.2.4"
#define MyAppPublisher "Clicks da Serra"
#define MyAppExeName "AutoRenderPreset.exe"
#define MyAppURL "https://criamigos.com"
#define EnvSetupPassword GetEnv("AUTORENDER_SETUP_PASSWORD")

#if EnvSetupPassword != ""
#define SetupPassword EnvSetupPassword
#else
#define SetupPassword "TROQUE_A_SENHA_AQUI"
#endif

[Setup]
AppId={{A3F5E8D2-9C4B-4A1E-8F7D-2B6C9E5A4D3F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\AutoRender Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=AutoRenderStudio_Setup_{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
Password={#SetupPassword}
Encryption=yes
#ifexist "autorendericon.ico"
SetupIconFile=autorendericon.ico
#endif

[Languages]
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"

[Files]
; O build completo e montado por BUILD_INSTALLER_COM_SENHA.bat em dist\AutoRenderPreset.
; Dados de trabalho nao entram no instalador. O settings.json e instalado apenas se ainda nao existir.
Source: "dist\AutoRenderPreset\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "entrada\*,saida\*,processados\*,erros\*,logs\*,update\packages\*,update\pending\*,config\settings.json"
Source: "dist\AutoRenderPreset\config\settings.json"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist
#ifexist "autorendericon.ico"
Source: "autorendericon.ico"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Dirs]
Name: "{app}\config"
Name: "{app}\entrada"
Name: "{app}\saida"
Name: "{app}\processados"
Name: "{app}\erros"
Name: "{app}\logs"
Name: "{app}\update"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\autorendericon.ico"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\autorendericon.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "AutoRenderStudio"; Flags: deletevalue uninsdeletevalue

[Code]
procedure InitializeWizard;
begin
  WizardForm.Caption := '{#MyAppName} - Instalacao';
end;
