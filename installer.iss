; -----------------------------------------------------------------------------
; Service Officer - Inno Setup script
;
; Build:
;   1. Run build.bat to produce ServiceOfficer.exe
;   2. Open this file in Inno Setup Compiler (or run iscc.exe installer.iss)
;
; Output: dist\ServiceOfficerSetup.exe
; -----------------------------------------------------------------------------

#define MyAppName        "Service Officer"
#define MyAppVersion     "2.0.0"
#define MyAppPublisher   "ismailorhan"
#define MyAppExeName     "ServiceOfficer.exe"
#define MyAppId          "{{A4F1F8C2-3D7B-4A8D-9E5F-1B2C3D4E5F60}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ServiceOfficerSetup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
english.AutoStartTask=Start &Service Officer automatically when Windows starts
english.DesktopIconTask=Create a &desktop shortcut
turkish.AutoStartTask=Windows ba&şladığında Service Officer'ı otomatik başlat
turkish.DesktopIconTask=&Masaüstü kısayolu oluştur

[Tasks]
Name: "autostart"; Description: "{cm:AutoStartTask}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; Config, history and the app log live here rather than in one user's profile:
; the services being watched belong to the machine, and a second administrator
; must see the same setup and the same history. Administrators get write access
; explicitly — the default ACL on a ProgramData subfolder only lets the creating
; user modify it, and the app can be started by any admin on the box.
Name: "{commonappdata}\{#MyAppName}"; Permissions: admins-modify

[Files]
; One-dir build: a Qt app re-extracted from a one-file exe on every launch is
; slow to start, so PyInstaller emits a folder and we package all of it.
Source: "dist\ServiceOfficer\ServiceOfficer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\ServiceOfficer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon.ico";          DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";         DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}";            Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";  Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}";    Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: shellexec nowait postinstall skipifsilent

[UninstallDelete]
; Remove the per-user Startup shortcut even if it was created later via the
; in-app settings checkbox.
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"
Type: files; Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\Startup\Service Officer.lnk"

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Kill a running instance so we can overwrite the exe.
  Exec('taskkill.exe', '/F /IM ServiceOfficer.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec('taskkill.exe', '/F /IM ServiceOfficer.exe', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
end;
