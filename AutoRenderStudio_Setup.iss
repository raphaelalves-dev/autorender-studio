; AutoRender Studio - instalador base da versão 1.2.8, sem preset.
; Atualizações futuras são aplicadas pelo próprio app a partir de arquivos ZIP.

#define MyAppName "AutoRender Studio"
#define MyAppVersion "1.2.8"
#define MyAppPublisher "Clicks da Serra"
#define MyAppExeName "AutoRenderPreset.exe"
#define MyAppURL "https://criamigos.com"

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
DiskSpanning=no
WizardStyle=modern
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
#ifexist "autorendericon.ico"
SetupIconFile=autorendericon.ico
#endif

[Languages]
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "dist\AutoRenderPreset\AutoRenderPreset.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\AutoRenderPreset\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "bin\ffmpeg.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "bin\ffprobe.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "installer\settings.default.json"; DestDir: "{app}\config"; DestName: "settings.json"; Flags: onlyifdoesntexist
Source: "installer\LEIA-ME_INSTALACAO.md"; DestDir: "{app}"; Flags: ignoreversion
#ifexist "autorendericon.ico"
Source: "autorendericon.ico"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Dirs]
Name: "{app}\config"
Name: "{app}\bin"
Name: "{app}\preset"
Name: "{app}\entrada"
Name: "{app}\saida"
Name: "{app}\processados"
Name: "{app}\erros"
Name: "{app}\quarentena"
Name: "{app}\logs"
Name: "{app}\update"
Name: "{app}\render_staging"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\autorendericon.ico"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\autorendericon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  WizardForm.Caption := '{#MyAppName} - Instalacao';
end;
