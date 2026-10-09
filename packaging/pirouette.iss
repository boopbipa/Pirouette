; Installateur Windows de Pirouette (Inno Setup) — lancé par packaging/build_windows.ps1
; Installation pour l'utilisateur seul (pas besoin d'être administrateur) : les mises à jour s'installent
; ensuite toutes seules, sans rien demander. Les données (cours, cartes) sont ailleurs, dans %APPDATA%\Pirouette,
; et ne sont jamais touchées (ni par une mise à jour, ni par la désinstallation).

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{8C3F2B1E-6A1D-4E7B-9F3C-2D5A7B9E1C40}
AppName=Pirouette
AppVersion={#Version}
AppVerName=Pirouette {#Version}
AppPublisher=Pirouette
DefaultDirName={localappdata}\Programs\Pirouette
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Pirouette-{#Version}-Windows-Setup
SetupIconFile=Pirouette.ico
UninstallDisplayIcon={app}\Pirouette.exe
UninstallDisplayName=Pirouette
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Une mise à jour repart d'un dossier propre (pas de vieux fichiers d'une version précédente)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Pirouette\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Pirouette"; Filename: "{app}\Pirouette.exe"
Name: "{autodesktop}\Pirouette"; Filename: "{app}\Pirouette.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Pirouette.exe"; Description: "{cm:LaunchProgram,Pirouette}"; Flags: nowait postinstall skipifsilent
