#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "ManuCodeAI"
#define AppPublisher "ManuCodeAI"
#define AppExeName "ManuCodeAI.exe"

[Setup]
AppId={{C12D00E4-5C1A-4F45-9D76-62C7B8F2A961}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ManuCodeAI
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\..\dist\installer
OutputBaseFilename=ManuCodeAI-Setup
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes

[Files]
Source: "..\..\dist\ManuCodeAI\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Icons]
Name: "{group}\ManuCodeAI"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\ManuCodeAI"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch ManuCodeAI"; Flags: postinstall nowait skipifsilent
