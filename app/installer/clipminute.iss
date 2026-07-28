; ===== ClipMinute — installeur Windows per-user (sans admin), Inno Setup 6 =====
; Compiler :  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" clipminute.iss
; Prérequis : installer\build\payload\ produit par build_installer.py

#define MyAppName "ClipMinute"
; version lue depuis app\version.txt (source de vérité unique du système de MAJ)
#define VerFile FileOpen("..\version.txt")
#define MyAppVersion Trim(FileRead(VerFile))
#expr FileClose(VerFile)
#define MyAppPublisher "Alex Truchy"
#define MyAppURL "https://clipminute.app"
#define MyLauncher "launcher.pyw"
#define MyPythonW "python\ClipMinute.exe"

[Setup]
; GUID FIXE — généré une seule fois, NE JAMAIS LE CHANGER (sinon doublons/désinstallation cassée)
AppId={{C51A7ED3-9F44-4B21-A18E-2B7D0C3F9A61}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; ---- PER-USER : aucun droit admin, pas d'UAC ----
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\ClipMinute
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; ---- Icônes & désinstalleur ----
SetupIconFile=build\payload\clipminute.ico
UninstallDisplayIcon={app}\clipminute.ico
UninstallDisplayName={#MyAppName}
; ---- Métadonnées exe (limite les faux positifs AV / SmartScreen) ----
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=ClipMinute-Setup-{#MyAppVersion}
WizardStyle=modern
; ferme pythonw avant mise à jour/désinstallation (libère python312.dll)
CloseApplications=yes

[Languages]
Name: "french";  MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "build\payload\app\*";          DestDir: "{app}\app";    Flags: recursesubdirs ignoreversion
Source: "build\payload\python\*";       DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "build\payload\ffmpeg\*";       DestDir: "{app}\ffmpeg"; Flags: recursesubdirs ignoreversion
Source: "build\payload\launcher.pyw";   DestDir: "{app}";        Flags: ignoreversion
Source: "build\payload\clipminute.ico"; DestDir: "{app}";        Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";               Filename: "{app}\{#MyPythonW}"; Parameters: """{app}\{#MyLauncher}"""; WorkingDir: "{app}"; IconFilename: "{app}\clipminute.ico"
Name: "{group}\Désinstaller {#MyAppName}";  Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}";         Filename: "{app}\{#MyPythonW}"; Parameters: """{app}\{#MyLauncher}"""; WorkingDir: "{app}"; IconFilename: "{app}\clipminute.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyPythonW}"; Parameters: """{app}\{#MyLauncher}"""; WorkingDir: "{app}"; Description: "Lancer {#MyAppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; uniquement les fichiers techniques — les DONNÉES utilisateur sont traitées dans [Code]
Type: filesandordirs; Name: "{app}\app\jobs"
Type: files; Name: "{app}\clipminute.log"

[Code]
// À la désinstallation : demander avant de toucher aux données (vidéos, comptes, réglages).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('Supprimer aussi vos données ClipMinute (comptes, vidéos produites, réglages) ?'#13#10
              + 'Choisissez Non pour les conserver en vue d''une réinstallation.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\app\data'), True, True, True);
      DeleteFile(ExpandConstant('{app}\app\comptes.json'));
      DeleteFile(ExpandConstant('{app}\app\.secret_key'));
      RemoveDir(ExpandConstant('{app}\app'));
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;
