; -----------------------------------------------------------------------------
; Service Officer - Inno Setup script
;
; Build:
;   1. Run build.bat to produce dist\ServiceOfficer and dist\ServiceOfficerHub
;   2. Open this file in Inno Setup Compiler (or run iscc.exe installer.iss)
;
; Output: dist\ServiceOfficerSetup.exe
;
; ONE installer, TWO components — not two installers. The reasoning, because this is
; the kind of decision that gets quietly reversed: one artifact to build, sign and
; publish; and "the hub and the client are the same build" becomes true by
; construction rather than by discipline. The protocol version check in core/wire.py
; exists because version skew between the two is a real failure mode. The price is a
; download that carries a payload most people will not install, and it is worth it.
;
; Silent installs:
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=hub
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=client ^
;       /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxxxxxx
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full
; An upgrade needs no /TYPE: Inno preselects whatever is already installed.
; -----------------------------------------------------------------------------

#define MyAppName        "Service Officer"
#define MyAppVersion     "2.2.1"
#define MyAppPublisher   "ismailorhan"
#define MyAppExeName     "ServiceOfficer.exe"
#define MyHubExeName     "ServiceOfficerHub.exe"
#define MyHubService     "ServiceOfficerHub"
#define MyHubPort        "8797"
#define MyAppId          "{{A4F1F8C2-3D7B-4A8D-9E5F-1B2C3D4E5F60}}"
; Must match core/config.APP_DIR exactly. It is the product name, spaces and all,
; so the folder sits alongside the other vendors' in ProgramData — but the two
; have to be kept in step by hand, and once were not: the installer permissioned
; "Service Officer" while the app wrote to "ServiceOfficer".
#define MyDataDir        "Service Officer"

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
english.TypeClient=Client only — the tray application
english.TypeHub=Hub only — the Windows service, for a server
english.TypeFull=Both, on this machine
english.TypeCustom=Choose
english.CompClient=Service Officer (tray application)
english.CompHub=Service Officer Hub (Windows service)
english.PickOne=Choose at least one: the tray application, the hub service, or both.
english.RegisteringHub=Registering the hub service...
english.PairingLocal=Pairing this computer with its hub...
english.SecuringData=Setting permissions on the data folder...
turkish.AutoStartTask=Windows ba&şladığında Service Officer'ı otomatik başlat
turkish.DesktopIconTask=&Masaüstü kısayolu oluştur
turkish.TypeClient=Yalnızca istemci — sistem tepsisi uygulaması
turkish.TypeHub=Yalnızca hub — sunucu için Windows hizmeti
turkish.TypeFull=İkisi de, bu makinede
turkish.TypeCustom=Seç
turkish.CompClient=Service Officer (tepsi uygulaması)
turkish.CompHub=Service Officer Hub (Windows hizmeti)
turkish.PickOne=En az birini seçin: tepsi uygulaması, hub hizmeti ya da ikisi.
turkish.RegisteringHub=Hub hizmeti kaydediliyor...
turkish.PairingLocal=Bu makine hub'ına eşleştiriliyor...
turkish.SecuringData=Veri klasörü izinleri ayarlanıyor...

[Types]
Name: "client"; Description: "{cm:TypeClient}"
Name: "hub";    Description: "{cm:TypeHub}"
Name: "full";   Description: "{cm:TypeFull}"
Name: "custom"; Description: "{cm:TypeCustom}"; Flags: iscustom

[Components]
; Neither is `fixed`: a server nobody logs into wants the hub without a tray icon, and
; a workstation wants the tray without a service. What must not happen is *neither* —
; see NextButtonClick.
Name: "client"; Description: "{cm:CompClient}"; Types: client full
Name: "hub";    Description: "{cm:CompHub}";    Types: hub full

[Tasks]
Name: "autostart"; Description: "{cm:AutoStartTask}"; GroupDescription: "{cm:AdditionalIcons}"; Components: client
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; Components: client

[Dirs]
; Config, history and the app log live here rather than in one user's profile:
; the services being watched belong to the machine, and a second administrator
; must see the same setup and the same history. Administrators get write access
; explicitly — the default ACL on a ProgramData subfolder only lets the creating
; user modify it, and the app can be started by any admin on the box.
;
; An existing installation upgrades into this untouched: the services.json already
; here becomes the hub's, in place, because it is exactly where the hub looks.
;
; `Permissions` only *adds*. ProgramData hands `Write` down to the built-in Users group,
; and that inherited right is not removed by anything here — see the [Run] entry that
; calls icacls, which is what actually closes it.
Name: "{commonappdata}\{#MyDataDir}"; Permissions: admins-modify

[Files]
; One-dir builds: a Qt app re-extracted from a one-file exe on every launch is
; slow to start, so PyInstaller emits a folder and we package all of it.
Source: "dist\ServiceOfficer\ServiceOfficer.exe"; DestDir: "{app}"; Components: client; Flags: ignoreversion
Source: "dist\ServiceOfficer\*"; DestDir: "{app}"; Components: client; Flags: ignoreversion recursesubdirs createallsubdirs
; The hub in its own folder: two PyInstaller one-dir trees cannot be merged, and
; keeping them apart means a client-only install carries nothing of the service.
Source: "dist\ServiceOfficerHub\*"; DestDir: "{app}\{#MyHubService}"; Components: hub; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon.ico";          DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";         DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "docs\HUB.md";       DestDir: "{app}\docs"; Components: hub; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";            Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Components: client
Name: "{group}\Uninstall {#MyAppName}";  Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}";    Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
; -- who may write the data directory -----------------------------------------
; This has to happen before the hub is registered, and it matters most on a machine that
; other people log into.
;
; ProgramData grants the built-in Users group `Write` by inheritance, and Inno's
; `Permissions` parameter adds rights without removing that. Before there was a hub it
; was untidy: the app ran as the administrator sitting in front of it, so a user who
; edited services.json gained nothing they did not already have. With a hub it is a
; privilege escalation — the hub runs as **LocalSystem** and a health check of kind
; `command` is a shell command line stored in that file, so anyone who could write it
; could run anything as SYSTEM.
;
; SIDs, not names: this installer runs on Turkish Windows, where the Users group is
; "Kullanıcılar" and an English name silently matches nothing.
;   *S-1-5-18     SYSTEM                  full
;   *S-1-5-32-544 Administrators          full
;   *S-1-5-11     Authenticated Users     read — the tray application must still read the
;                                         pairing left by `client pair --local`
Filename: "{sys}\icacls.exe";   Parameters: """{commonappdata}\{#MyDataDir}"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F /grant:r *S-1-5-32-544:(OI)(CI)F /grant:r *S-1-5-11:(OI)(CI)RX";   StatusMsg: "{cm:SecuringData}"; Flags: runhidden waituntilterminated

; -- the hub, in the order the next step depends on ---------------------------
; 1. register it. LocalSystem; the account is changed afterwards in services.msc,
;    because an installer that collected a password would hold it in memory, in its
;    own log and on an unattended command line — see docs/HUB.md.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "--startup auto install"; \
  StatusMsg: "{cm:RegisteringHub}"; Components: hub; Flags: runhidden waituntilterminated
; 2. the firewall rule. Domain profile only, deliberately: a management port has no
;    business being open on a network the machine merely finds itself on.
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Service Officer Hub"" dir=in action=allow protocol=TCP localport={#MyHubPort} profile=domain"; \
  Components: hub; Flags: runhidden waituntilterminated
; 3. the certificate, so there is something for a client to pin.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "--fingerprint"; \
  Components: hub; Flags: runhidden waituntilterminated
; 4. start it, so step 5 has something to talk to.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "start"; \
  Components: hub; Flags: runhidden waituntilterminated
; 5. this machine's own client, paired without asking anything: a token issued and
;    written straight into client.json, never shown, never leaving the machine. The
;    address is this computer's *name*, not localhost — the certificate is issued for
;    the host name, and a client that pinned localhost could not later be pointed at
;    the same hub by name without failing its own check.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "client pair --local"; \
  StatusMsg: "{cm:PairingLocal}"; Components: hub and client; Flags: runhidden waituntilterminated

; -- a remote client, already pointed at its hub ------------------------------
; /HUBURL=... /HUBTOKEN=... — five workstations is four too many to do by hand.
; --store-only writes the token and pins the certificate, then exits: no tray icon
; appears in the middle of an install, and the first real launch is already connected.
Filename: "{app}\{#MyAppExeName}"; \
  Parameters: "--connect ""{code:GetHubUrl}"" --token ""{code:GetHubToken}"" --store-only"; \
  Components: client; Check: HaveHubDetails; Flags: runhidden waituntilterminated

Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Components: client; Flags: shellexec nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "stophub"
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "removehub"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Service Officer Hub"""; Flags: runhidden waituntilterminated; RunOnceId: "closeport"

[UninstallDelete]
; Remove the per-user Startup shortcut even if it was created later via the
; in-app settings checkbox.
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"
Type: files; Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\Startup\Service Officer.lnk"

[Code]
function ParamValue(Name: String): String;
var
  I: Integer;
  Prefix, Arg: String;
begin
  Result := '';
  Prefix := '/' + Uppercase(Name) + '=';
  for I := 1 to ParamCount do
  begin
    Arg := ParamStr(I);
    if Pos(Prefix, Uppercase(Arg)) = 1 then
      Result := Copy(Arg, Length(Prefix) + 1, MaxInt);
  end;
end;

function GetHubUrl(Param: String): String;
begin
  Result := ParamValue('HUBURL');
end;

function GetHubToken(Param: String): String;
begin
  Result := ParamValue('HUBTOKEN');
end;

// Both, or neither: a URL with no token cannot pin a certificate unattended, and a
// token with no URL has nowhere to go. Half of the pair would fail quietly, which is
// the worst outcome for something nobody is watching.
function HaveHubDetails(): Boolean;
begin
  Result := (ParamValue('HUBURL') <> '') and (ParamValue('HUBTOKEN') <> '');
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Kill a running instance so we can overwrite the exe.
  Exec('taskkill.exe', '/F /IM ServiceOfficer.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

// An installer that copies over a running service exe fails with "file in use", and
// this is the ordinary case: every upgrade of a hub.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Hub: String;
begin
  Hub := ExpandConstant('{app}\{#MyHubService}\{#MyHubExeName}');
  if FileExists(Hub) then
    Exec(Hub, 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectComponents)
     and not (WizardIsComponentSelected('client')
              or WizardIsComponentSelected('hub')) then
  begin
    MsgBox(ExpandConstant('{cm:PickOne}'), mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec('taskkill.exe', '/F /IM ServiceOfficer.exe', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
end;
