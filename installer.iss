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
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full [/HUBPORT=9100]
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=client ^
;       /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxxxxxx
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full
; An upgrade needs no /TYPE: Inno preselects whatever is already installed.
; -----------------------------------------------------------------------------

#define MyAppName        "Service Officer"
; The release, and then the full version if a build has stamped one. The .iss cannot work
; the build number out for itself: it is counted during the build and version.py is restored
; before ISCC runs, so stamp_version.py leaves it in a file. Without this the installer said
; "2.2.7 will be upgraded to 2.2.7" — true about the release and useless about the build,
; which is the only thing that differed.
#define MyRelease        "2.2.13"
#if FileExists("installer-version.txt")
  #define VersionFile    FileOpen("installer-version.txt")
  #define MyAppVersion   Trim(FileRead(VersionFile))
  #expr FileClose(VersionFile)
#else
  ; A clean checkout, compiled without building first. Honest rather than fatal: the release
  ; is right and only the build number is missing.
  #define MyAppVersion   MyRelease
#endif
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
; The setup type page below replaces it: three answers instead of a list of parts, and
; nothing anybody has to work out from the names of two components.
DisableReadyPage=no
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
; One language, so Setup asks nothing before it starts. The *application's* language is its
; own setting — English until somebody changes it under Settings ▸ General — and asking twice
; about the same thing, once in a wizard and once in the app, is how they end up disagreeing:
; a machine installed by an administrator in one language and used by somebody in another.
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.AutoStartTask=Start the &panel when I sign in to this computer
english.DesktopIconTask=Put a shortcut to the panel on the &desktop
english.TypeClient=Client (tray) only
english.TypeFull=Hub and Client
english.CompClient=Service Officer (tray application)
english.CompHub=Service Officer Hub (Windows service)
english.RegisteringHub=Registering the hub service...
english.PairingLocal=Pairing this computer with its hub...
english.SecuringData=Setting permissions on the data folder...
english.PanelTasks=The panel (Client) — the Hub is a Windows service and starts with the computer on its own
english.TypeCaption=Setup type
english.TypeBody=What is this computer's part?
english.TypeBothWhy=The services are watched from here: this computer asks them how they are, restarts them when they fail, runs the schedule and keeps the history — whether or not anybody is logged in, because that part is a Windows service. Other computers connect to it and read what it knows. The panel is installed here as well.
english.TypeClientWhy=Another computer already does that work. This one shows what that hub knows and asks it to act — it watches nothing itself, so closing it stops nothing. You will need that computer's address, and a token issued on it.
english.TypeBoth=This is the hub computer  (Hub + Client)
english.TypeClientOnly=This computer reads a hub somewhere else  (Client)
english.HubCaption=The hub
english.HubBody=Which hub should the Client read? A hub on this computer counts — the address is checked either way.
english.HubHostField=Host name or IP
english.HubPortField=Port
english.HubToken=Token
english.HubTokenWhy=Issued on the hub with "ServiceOfficerHub.exe client add <a name for this computer>", and printed once.
english.HubPortNew=The port the Hub will listen on, and the one its clients will connect to. Leave it unless something else is using it.
english.HubPortLocked=This is the port the Hub here listens on, and every client of it has that port stored — so moving it is a change to the Hub, not to this installation. Do that on this computer, afterwards: "ServiceOfficerHub.exe port <n>", then restart the service.
english.HubLocalNote=A hub on this computer: no token is needed, one is issued during the installation.
english.PortBad=A port has to be a number between 1 and 65535.
english.HubNeedAddress=Enter the hub's host name or IP address.
english.HubNeedToken=Enter the token the hub printed for this computer. Without it the Client can reach the hub but not read anything.
english.HubIsThisPC=That address is this computer. The Hub here is version %1 and this installer carries %2.%n%nThe services it watches keep running; the Hub is stopped and started again during the installation.%n%nContinue?
english.HubNotHere=That address is this computer, but no Hub is installed here.%n%nThe Client will be installed and will have nothing to read until a Hub exists at that address. You can change the address later in Settings %+ General.%n%nInstall anyway?
english.HubVersionClash=That hub is version %1 and this installer is version %2.%n%nA client and its hub have to be the same version — the connection would succeed and then refuse everything. Upgrade that hub first, or install the matching version here.
english.HubSilent=%1 did not answer.%n%nNothing about it can be checked — its version, or whether it is this computer. The address and the token will be stored and used on the first launch, and can be changed in Settings %+ General.%n%nInstall anyway?
english.ReadyHubUpgrade=Hub (service): %1 will be upgraded to %2, keeping port %3
english.ReadyHubSame=Hub (service): %1 reinstalled, keeping port %2
english.ReadyHubKept=Hub (service): upgraded to %1, keeping port %2
english.ReadyHubNew=Hub (service): a new hub on port %1
english.ReadyClientLocal=Client (tray): reads the hub on this computer
english.ReadyClientRemote=Client (tray): reads %1

[Types]
; Two, matching the two answers on the first page. There is no "hub only": every
; installation has the panel, because a server somebody logs into wants it and a server
; nobody logs into never runs it.
Name: "client"; Description: "{cm:TypeClient}"
Name: "full";   Description: "{cm:TypeFull}"

[Components]
; The client is in both types and the hub only in `full`: every installation has a panel,
; and the setup type decides whether a hub comes with it. The components page is never
; shown — see ShouldSkipPage — so this section is what the two answers mean, not a list
; anybody picks from.
Name: "client"; Description: "{cm:CompClient}"; Types: client full
Name: "hub";    Description: "{cm:CompHub}";    Types: full

[Tasks]
; Both are about the *panel*. The Hub needs neither: it is a Windows service, it starts
; with the computer whether anybody signs in or not, and a shortcut to it would do nothing.
; That was worth saying on screen, because "start automatically when Windows starts" next to
; a product that installs a service reads as the service.
;
; And the first one is a shortcut in a Startup folder, so it happens when somebody *signs
; in*, not when Windows boots. It is also that person's own — anybody else who uses this
; computer sets it for themselves, on the panel's Settings page.
Name: "autostart"; Description: "{cm:AutoStartTask}"; GroupDescription: "{cm:PanelTasks}"; Components: client
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:PanelTasks}"; Flags: unchecked; Components: client

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
; No `isreadme`: that flag is what puts "View README.md" on the last page, ticked, and
; nobody finishing an install of a service manager wants a markdown file. It still ships.
Source: "README.md";         DestDir: "{app}"; Flags: ignoreversion
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
; Deleted first: `add rule` appends, so every installation left another rule with the same
; name behind it — two of them on the machine this was found on. Deleting one that is not
; there is not a failure worth minding, and the exit code is ignored either way.
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Service Officer Hub"""; \
  Components: hub; Flags: runhidden waituntilterminated
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Service Officer Hub"" dir=in action=allow protocol=TCP localport={code:GetHubPort} profile=domain"; \
  Components: hub; Flags: runhidden waituntilterminated
; 3. the port, if it was chosen. Written by the hub itself: the installer has no
;    business editing services.json, and the exe already validates the number.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "port {code:GetHubPort}"; \
  Components: hub; Check: PortWasChosen; Flags: runhidden waituntilterminated
; 4. the certificate, so there is something for a client to pin.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "--fingerprint"; \
  Components: hub; Flags: runhidden waituntilterminated
; 5. start it, so the next step has something to talk to.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "start"; \
  Components: hub; Flags: runhidden waituntilterminated
; 6. this machine's own client, paired without asking anything: a token issued and
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
  Components: client; Check: PairingWithARemoteHub; Flags: runhidden waituntilterminated

Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Components: client; Flags: shellexec nowait postinstall skipifsilent

; And the same thing when nobody is watching. The entry above is `postinstall skipifsilent`, so
; a silent install never runs it — and an unattended update *is* a silent install. Measured on
; 2.2.9, the first real one: the update worked, the service came back, and the tray icon was
; gone until somebody signed in again.
;
; Through the hub exe rather than by starting the app here: when the hub drives an update this
; installer is LocalSystem, and a window started from session 0 is one nobody can see. See
; core/session.py.
;
; Only when we closed one. `InitializeSetup` stops the tray application so its files can be
; replaced; putting back exactly what was taken is the rule, and it means a silent install on a
; machine where nobody had it open does not make a tray icon appear on somebody's desktop.
Filename: "{app}\{#MyHubService}\{#MyHubExeName}"; Parameters: "panel"; \
  Components: client and hub; Check: WeClosedThePanel; \
  Flags: runhidden waituntilterminated

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
// Two questions, kept apart, because one radio cannot answer three things:
//
//   Setup type    what is installed here — the Hub service, the Client tray app, or both
//   The hub       host, port, and a token when one is needed. A hub on *this* computer is
//                 still a hub the Client connects to: there is no mode anywhere in this
//                 product, there is an address, and localhost is an address.
//
// An upgrade is a state, not an answer. The installer knows what is already here, so it
// says so on the summary page instead of hiding it in the wording of a choice.
//
// The address is *checked* rather than trusted, because every way of getting it wrong is
// quiet: a version that will refuse every request after connecting (wire.PROTOCOL), or an
// address on this computer where no hub exists. Neither would be noticed until somebody
// opened the panel and found it empty.
const
  TypeBoth = 0;
  TypeClientOnly = 1;

var
  TypePage: TInputOptionWizardPage;
  //: Built by hand rather than with CreateInputQueryPage, for two reasons: that helper
  //: stacks full-width fields and the host and port belong side by side, and the token has
  //: to be able to disappear — a field shown with "leave this empty" under it is worse than
  //: no field at all.
  //: What the chosen answer means, under the two of them. The labels used to carry this
  //: themselves, and Inno's radio list gives each item one line — so the longer half of
  //: each sentence was being cut off.
  TypeNote: TNewStaticText;
  HubPage: TWizardPage;
  HostEdit, PortEdit: TNewEdit;
  //: TPasswordEdit, not TNewEdit with a flag: TNewEdit has no Password property, and this
  //: is the class Inno's own CreateInputQueryPage uses for a masked field.
  TokenEdit: TPasswordEdit;
  HostCaption, PortCaption, TokenCaption, PortNote, TokenNote: TNewStaticText;
  //: The address given, normalised, once it has passed its checks.
  CheckedHubUrl: String;
  //: That address is this computer — so no token is needed and `client pair --local` does
  //: the pairing.
  HubIsLocal: Boolean;
  //: The port a hub here already serves on. Empty on a first install, and unknown until
  //: {app} exists — which is why it is not read in InitializeWizard: that runs before the
  //: directory page, and expanding {app} there is a runtime error.
  ExistingPort: String;
  ExistingPortKnown: Boolean;
  ExistingHubVersion: String;

// ---------------------------------------------------------------------------
// reading what we were given
// ---------------------------------------------------------------------------
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

// One value out of a small flat JSON object. Not a parser: the two files this reads —
// client.json and the hub's own ping answer — are written by this product and are one
// level deep. Anything more would be a library, and there is none here.
function JsonValue(Body, Key: String): String;
var
  Start, Finish: Integer;
  Needle: String;
begin
  Result := '';
  Needle := '"' + Key + '":';
  Start := Pos(Needle, Body);
  if Start = 0 then
    Exit;
  Start := Start + Length(Needle);
  while (Start <= Length(Body)) and ((Body[Start] = ' ') or (Body[Start] = '"')) do
    Start := Start + 1;
  Finish := Start;
  while (Finish <= Length(Body)) and (Body[Finish] <> '"')
        and (Body[Finish] <> ',') and (Body[Finish] <> '}') do
    Finish := Finish + 1;
  Result := Trim(Copy(Body, Start, Finish - Start));
end;

// A port, or ''. Never anything else — which is the whole lesson of PortAlreadyHere.
function AsPort(Given: String): String;
var
  Number: Integer;
begin
  Number := StrToIntDef(Trim(Given), -1);
  if (Number >= 1) and (Number <= 65535) then
    Result := IntToStr(Number)
  else
    Result := '';
end;

function HostOfUrl(Url: String): String;
var
  Host: String;
  Colon: Integer;
begin
  Host := Url;
  if Pos('://', Host) > 0 then
    Host := Copy(Host, Pos('://', Host) + 3, Length(Host));
  if Pos('/', Host) > 0 then
    Host := Copy(Host, 1, Pos('/', Host) - 1);
  // An IPv6 address is full of colons, so a port is only looked for after the brackets it
  // has to be written in.
  if Copy(Host, 1, 1) = '[' then
    Colon := Pos(']:', Host)
  else
    Colon := Pos(':', Host);
  if Colon > 0 then
    Host := Copy(Host, 1, Colon - 1);
  Result := Trim(Host);
end;

// Is that address this computer? Asked because "connect to a hub" pointing at this one
// means a hub belongs here, and a client-only install would leave somebody reading a hub
// nobody upgraded. The hub's own ping answers it better — it says which computer it runs on
// — and this is what there is when nothing answers.
function LooksLikeThisComputer(Host: String): Boolean;
var
  Mine: String;
begin
  Host := LowerCase(Trim(Host));
  Mine := LowerCase(GetComputerNameString);
  Result := (Host = 'localhost') or (Host = '127.0.0.1') or (Host = '::1')
            or (Host = '[::1]') or (Host = Mine) or (Pos(Mine + '.', Host) = 1);
end;

// The hub's name and version, from the one endpoint that needs no token. Certificate errors
// are ignored here on purpose: the hub is self-signed and it is the *client* that pins it,
// on every connection. Nothing secret crosses this request — a version number and a
// computer name — and refusing to read it would only mean asking somebody to check by hand.
function AskHub(Url: String; var HubName, HubVersion: String): Boolean;
var
  Http: Variant;
  Body: String;
begin
  Result := False;
  HubName := '';
  HubVersion := '';
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(4000, 4000, 5000, 8000);
    Http.Open('GET', Url + '/api/v1/ping', False);
    Http.Option(4) := 13056;
    Http.Send('');
    if Http.Status = 200 then
    begin
      Body := Http.ResponseText;
      HubName := JsonValue(Body, 'name');
      HubVersion := JsonValue(Body, 'version');
      Result := HubVersion <> '';
    end;
  except
    Result := False;
  end;
end;

// A running hub reports 2.2.6.7 — release, then the build number, which counts builds on
// the machine that made it. Only the release part is a compatibility question.
function ReleasePart(Version: String): String;
var
  I, Dots: Integer;
begin
  Result := Version;
  Dots := 0;
  for I := 1 to Length(Version) do
    if Version[I] = '.' then
    begin
      Dots := Dots + 1;
      if Dots = 3 then
      begin
        Result := Copy(Version, 1, I - 1);
        Exit;
      end;
    end;
end;

// The port out of the config the hub reads. The "hub" section is located first: a health
// check has a "port" too, and the first match in the file would be somebody's TCP check.
function PortFromConfig(): String;
var
  Body: AnsiString;
  Path, Section: String;
  At: Integer;
begin
  Result := '';
  Path := ExpandConstant('{commonappdata}\{#MyDataDir}\services.json');
  if not (FileExists(Path) and LoadStringFromFile(Path, Body)) then
    Exit;
  Section := String(Body);
  At := Pos('"hub"', Section);
  if At = 0 then
  begin
    // Saved before that section existed. Still an answer: a hub reading this file gets the
    // default, because that is what core/config gives a file without one.
    Result := '{#MyHubPort}';
    Exit;
  end;
  Result := AsPort(JsonValue(Copy(Section, At, Length(Section)), 'port'));
end;

// The port a hub here already serves on.
//
// Asked of the hub itself first, because it is the thing that knows — but *validated*,
// because during an upgrade the exe still under {app} is the previous release. One that
// predates the `port` command answers "Unknown command - 'port'", and that went into the
// address field as `CTL052:Unknown command - 'port'` before this checked. An external
// command's output is input.
function PortAlreadyHere(): String;
var
  Exe, Output: String;
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  // `WizardDirValue`, not `{app}`. They name the same folder, and only one of them can be
  // asked for at any time: `{app}` is not initialized until the directory page has been left,
  // and expanding it before that is a runtime error rather than an empty string —
  //
  //     Runtime error (at 27:156): An attempt was made to expand the "app" constant
  //     before it was initialized.
  //
  // which is what a person double-clicking 2.2.10 got. `ShouldSkipPage` calls this, and it is
  // asked about the directory page itself, so the comment there — "every page this is asked
  // about comes after the directory page" — was an assumption and not a fact. Silent installs
  // never showed it, because a silent install has no pages to skip.
  //
  // WizardDirValue is the directory edit's current value: valid from the moment the wizard
  // exists, and pre-filled with the previous installation's folder on an upgrade.
  Exe := AddBackslash(WizardDirValue()) + '{#MyHubService}\{#MyHubExeName}';
  if FileExists(Exe) then
  begin
    Output := ExpandConstant('{tmp}\port.txt');
    if Exec(ExpandConstant('{cmd}'), '/C ""' + Exe + '" port > "' + Output + '" 2>&1"',
            '', SW_HIDE, ewWaitUntilTerminated, Code) then
      if LoadStringsFromFile(Output, Lines) and (GetArrayLength(Lines) > 0) then
        Result := AsPort(Lines[0]);
  end;
  if Result = '' then
    Result := PortFromConfig();
end;

// What this computer was paired with last time, so nobody has to remember. This user's own
// copy first, then the machine's — the order the client itself reads them in.
function StoredHubUrl(): String;
var
  Body: AnsiString;
  Path: String;
begin
  Result := '';
  Path := ExpandConstant('{localappdata}\Service Officer\client.json');
  if not FileExists(Path) then
    Path := ExpandConstant('{commonappdata}\{#MyDataDir}\client.json');
  if FileExists(Path) and LoadStringFromFile(Path, Body) then
    Result := JsonValue(String(Body), 'hub_url');
end;

// Whether a hub is installed here is a fact in Windows' own record, and the only one that
// cannot be out of date. What port it uses is a detail; not knowing that is not evidence
// of anything, and reading it as "nothing is installed" is what put a port page in front of
// a machine that had been running a hub for two days.
function HubInstalledHere(): Boolean;
begin
  Result := RegKeyExists(HKEY_LOCAL_MACHINE,
                         'SYSTEM\CurrentControlSet\Services\{#MyHubService}');
end;

function InstallingHub(): Boolean;
begin
  Result := TypePage.SelectedValueIndex = TypeBoth;
end;

// There is no InstallingClient. Every installation has one — see [Types] — so the question
// stopped being a question, and the branches that asked it are gone rather than left
// answering True for ever.

// ---------------------------------------------------------------------------
// what the [Run] entries ask
// ---------------------------------------------------------------------------
// The port for the firewall rule, for `hub.exe port`, and for the address the client is
// given. A port a hub here already serves on wins: its clients have it stored, and moving
// it would leave every one of them looking at nothing.
function GetHubPort(Param: String): String;
begin
  Result := AsPort(ParamValue('HUBPORT'));
  if Result = '' then
    Result := AsPort(ExistingPort);
  if (Result = '') and (PortEdit <> nil) then
    Result := AsPort(PortEdit.Text);
  if Result = '' then
    Result := '{#MyHubPort}';
end;

function GetHubUrl(Param: String): String;
begin
  Result := ParamValue('HUBURL');
  if Result <> '' then
  begin
    // A command line may carry the port inside the address; take it as given.
    if Pos('://', Result) = 0 then
      Result := 'https://' + Result;
    Exit;
  end;
  Result := CheckedHubUrl;
end;

function GetHubToken(Param: String): String;
begin
  Result := ParamValue('HUBTOKEN');
  if (Result = '') and (TokenEdit <> nil) then
    Result := Trim(TokenEdit.Text);
end;

// Only when the hub is somewhere else. A hub on this computer pairs its own client with
// `client pair --local`, which needs no token carried anywhere.
function PairingWithARemoteHub(): Boolean;
begin
  Result := (GetHubUrl('') <> '') and (not HubIsLocal);
end;

// Writing the port is skipped when it is already what the hub uses — one less thing done to
// a machine for no reason, and one less way to fail.
function PortWasChosen(): Boolean;
begin
  Result := (ExistingPort = '') and (GetHubPort('') <> '{#MyHubPort}');
end;

// ---------------------------------------------------------------------------
// the page
// ---------------------------------------------------------------------------
// The address as one URL, from the two fields.
function AddressFromFields(): String;
var
  Host, Port: String;
begin
  Result := '';
  Host := HostOfUrl(Trim(HostEdit.Text));
  if Host = '' then
    Exit;
  Port := AsPort(PortEdit.Text);
  if Port = '' then
    Port := '{#MyHubPort}';
  Result := 'https://' + Host + ':' + Port;
end;

// A token is for a hub somewhere else. Hidden the moment the address stops being one,
// because a field shown next to "you do not need this" is a question nobody should have to
// answer twice.
procedure ShowTokenOnlyIfNeeded();
var
  Local: Boolean;
begin
  Local := LooksLikeThisComputer(HostOfUrl(Trim(HostEdit.Text)));
  TokenCaption.Visible := not Local;
  TokenEdit.Visible := not Local;
  if Local then
    TokenNote.Caption := ExpandConstant('{cm:HubLocalNote}')
  else
    TokenNote.Caption := ExpandConstant('{cm:HubTokenWhy}');
end;

procedure AddressTyped(Sender: TObject);
begin
  ShowTokenOnlyIfNeeded();
end;

procedure DescribeChosenType();
begin
  if TypeNote = nil then
    Exit;
  if TypePage.SelectedValueIndex = TypeBoth then
    TypeNote.Caption := ExpandConstant('{cm:TypeBothWhy}')
  else
    TypeNote.Caption := ExpandConstant('{cm:TypeClientWhy}');
end;

procedure TypeChosen(Sender: TObject);
begin
  DescribeChosenType();
end;

// Laid out here so the arithmetic is in one place: a wide host, a narrow port beside it,
// and a token row underneath that is hidden more often than not.
procedure BuildHubPage();
var
  PortWidth, Gap, Row: Integer;
begin
  HubPage := CreateCustomPage(TypePage.ID, ExpandConstant('{cm:HubCaption}'),
                              ExpandConstant('{cm:HubBody}'));
  PortWidth := ScaleX(70);
  Gap := ScaleX(12);
  Row := 0;

  HostCaption := TNewStaticText.Create(HubPage);
  HostCaption.Parent := HubPage.Surface;
  HostCaption.Caption := ExpandConstant('{cm:HubHostField}');
  HostCaption.Top := Row;
  HostCaption.Left := 0;

  PortCaption := TNewStaticText.Create(HubPage);
  PortCaption.Parent := HubPage.Surface;
  PortCaption.Caption := ExpandConstant('{cm:HubPortField}');
  PortCaption.Top := Row;
  PortCaption.Left := HubPage.SurfaceWidth - PortWidth;

  Row := Row + HostCaption.Height + ScaleY(4);

  HostEdit := TNewEdit.Create(HubPage);
  HostEdit.Parent := HubPage.Surface;
  HostEdit.Top := Row;
  HostEdit.Left := 0;
  HostEdit.Width := HubPage.SurfaceWidth - PortWidth - Gap;
  HostEdit.OnChange := @AddressTyped;

  PortEdit := TNewEdit.Create(HubPage);
  PortEdit.Parent := HubPage.Surface;
  PortEdit.Top := Row;
  PortEdit.Left := HubPage.SurfaceWidth - PortWidth;
  PortEdit.Width := PortWidth;
  PortEdit.Text := '{#MyHubPort}';

  Row := Row + HostEdit.Height + ScaleY(6);

  PortNote := TNewStaticText.Create(HubPage);
  PortNote.Parent := HubPage.Surface;
  PortNote.WordWrap := True;
  PortNote.Width := HubPage.SurfaceWidth;
  PortNote.Top := Row;
  PortNote.Caption := ExpandConstant('{cm:HubPortNew}');
  Row := Row + PortNote.Height + ScaleY(22);

  TokenCaption := TNewStaticText.Create(HubPage);
  TokenCaption.Parent := HubPage.Surface;
  TokenCaption.Caption := ExpandConstant('{cm:HubToken}');
  TokenCaption.Top := Row;
  TokenCaption.Left := 0;
  Row := Row + TokenCaption.Height + ScaleY(4);

  TokenEdit := TPasswordEdit.Create(HubPage);
  TokenEdit.Parent := HubPage.Surface;
  TokenEdit.Top := Row;
  TokenEdit.Left := 0;
  TokenEdit.Width := HubPage.SurfaceWidth;
  Row := Row + TokenEdit.Height + ScaleY(6);

  TokenNote := TNewStaticText.Create(HubPage);
  TokenNote.Parent := HubPage.Surface;
  TokenNote.WordWrap := True;
  TokenNote.Width := HubPage.SurfaceWidth;
  TokenNote.Top := Row;
  TokenNote.Caption := ExpandConstant('{cm:HubTokenWhy}');
end;

// Called from the pages that come *after* the directory page, because {app} does not exist
// before it. Doing this in InitializeWizard raised "an attempt was made to expand the app
// constant before it was initialized" — a runtime error, so only running the installer
// finds it.
procedure EnsureExistingPort();
begin
  if ExistingPortKnown then
    Exit;
  ExistingPortKnown := True;
  ExistingPort := PortAlreadyHere();
  if (AsPort(ExistingPort) <> '') and (PortEdit <> nil) then
    PortEdit.Text := AsPort(ExistingPort);
end;

procedure InitializeWizard();
var
  Remembered, Host, Port: String;
begin
  ExistingPort := '';
  ExistingPortKnown := False;
  ExistingHubVersion := '';

  TypePage := CreateInputOptionPage(
    wpSelectDir, ExpandConstant('{cm:TypeCaption}'),
    ExpandConstant('{cm:TypeBody}'), '', True, False);
  TypePage.Add(ExpandConstant('{cm:TypeBoth}'));
  TypePage.Add(ExpandConstant('{cm:TypeClientOnly}'));
  TypePage.CheckListBox.OnClickCheck := @TypeChosen;

  TypeNote := TNewStaticText.Create(TypePage);
  TypeNote.Parent := TypePage.Surface;
  TypeNote.WordWrap := True;
  TypeNote.Width := TypePage.SurfaceWidth;
  // Under the list, with the list shortened to make room: it is two items and does not
  // need the whole page.
  TypePage.CheckListBox.Height := ScaleY(46);
  TypeNote.Top := TypePage.CheckListBox.Top + TypePage.CheckListBox.Height + ScaleY(14);

  BuildHubPage();

  // Where the wizard starts from, taken from what is already true here rather than from a
  // default nobody chose: a machine with the hub service is doing both, a machine with a
  // remembered address is a client of it, and anything else is a first install.
  Remembered := StoredHubUrl();
  if HubInstalledHere() then
  begin
    TypePage.SelectedValueIndex := TypeBoth;
    HostEdit.Text := GetComputerNameString;
  end
  else if Remembered <> '' then
  begin
    TypePage.SelectedValueIndex := TypeClientOnly;
    HostEdit.Text := HostOfUrl(Remembered);
    // The port travels with a remembered address, and it is not always the default.
    Host := Remembered;
    if Pos('://', Host) > 0 then
      Host := Copy(Host, Pos('://', Host) + 3, Length(Host));
    Port := '';
    if Pos(':', Host) > 0 then
      Port := AsPort(Copy(Host, Pos(':', Host) + 1, Length(Host)));
    if Port <> '' then
      PortEdit.Text := Port;
  end
  else
  begin
    TypePage.SelectedValueIndex := TypeBoth;
    HostEdit.Text := GetComputerNameString;
  end;
  ShowTokenOnlyIfNeeded();
  DescribeChosenType();
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // Not "safe here" — that is what the comment used to say, and it was wrong: Inno asks this
  // about the directory page itself, before `{app}` exists. What made it safe was changing
  // PortAlreadyHere to read WizardDirValue instead.
  EnsureExistingPort();
  // The components page is gone: the setup type says which components, and a list of two
  // parts is a worse question than "what should this computer do".
  if PageID = wpSelectComponents then
    Result := True
  // The hub page is always shown: there is always a Client, and it always needs an
  // address. Which hub, and on which port, is the whole question.
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = TypePage.ID then
    EnsureExistingPort();
  if (HubPage <> nil) and (CurPageID = HubPage.ID) then
  begin
    EnsureExistingPort();
    // A hub already serving here keeps its port: its clients have that one stored, so the
    // field says what it is and does not invite a change that would break them.
    if InstallingHub() and HubInstalledHere() then
    begin
      // ReadOnly on its own is invisible: a read-only TNewEdit still takes focus, still
      // lets its text be selected, and keeps a white background — so a field the note
      // above says cannot be changed looked exactly like one that could. The grey and the
      // missing tab stop are what make the sentence and the field agree.
      PortEdit.ReadOnly := True;
      PortEdit.Color := clBtnFace;
      PortEdit.TabStop := False;
      PortNote.Caption := ExpandConstant('{cm:HubPortLocked}');
    end
    else
    begin
      PortEdit.ReadOnly := False;
      PortEdit.Color := clWindow;
      PortEdit.TabStop := True;
      PortNote.Caption := ExpandConstant('{cm:HubPortNew}');
    end;
    ShowTokenOnlyIfNeeded();
  end;
end;

// Ask, unless there is nobody to ask.
//
// `/SILENT` hides Inno's *wizard*. It does not hide a MsgBox raised from [Code], and that cost
// us the first automatic update: the hub launched this installer as LocalSystem, so the
// question "the Hub here is 2.2.7.18 and this installer carries 2.2.8 — continue?" was drawn on
// session 0, a desktop nobody can see or click, and the installer waited there for ever. Its
// own log is what proved it: "Message box (Yes/No): … User chose Yes" at 01:10:28, from a run
// that was silent.
//
// Silent takes Yes, and that is not a shortcut: something asked for this install, and there is
// no third answer. A silent installer that stops to ask is an installer that never finishes.
function Confirmed(Question: String): Boolean;
begin
  if WizardSilent() then
  begin
    Log('Silent: assuming Yes for: ' + Question);
    Result := True;
    Exit;
  end;
  Result := MsgBox(Question, mbConfirmation, MB_YESNO) = IDYES;
end;

// Everything that has to be true before an address is accepted. False keeps the person on
// the page; `Complaint` is shown when it is set, and left empty when this has already said
// something itself.
function AddressIsUsable(var Complaint: String): Boolean;
var
  Url, Host, HubName, HubVersion, Question: String;
  Local: Boolean;
begin
  Result := False;
  Complaint := '';
  HubIsLocal := False;
  CheckedHubUrl := '';

  if AsPort(PortEdit.Text) = '' then
  begin
    Complaint := ExpandConstant('{cm:PortBad}');
    Exit;
  end;
  Url := AddressFromFields();
  if Url = '' then
  begin
    Complaint := ExpandConstant('{cm:HubNeedAddress}');
    Exit;
  end;
  Host := HostOfUrl(Url);

  if not AskHub(Url, HubName, HubVersion) then
  begin
    Local := LooksLikeThisComputer(Host);
    // Nothing answered. Two different silences: a hub about to be installed here has not
    // started yet and that is expected, otherwise it is genuinely unknown.
    if Local and InstallingHub() then
    begin
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    if Local then
    begin
      if not Confirmed(ExpandConstant('{cm:HubNotHere}')) then
        Exit;
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    if not Confirmed(FmtMessage(ExpandConstant('{cm:HubSilent}'), [Host])) then
      Exit;
    if Trim(TokenEdit.Text) = '' then
    begin
      Complaint := ExpandConstant('{cm:HubNeedToken}');
      Exit;
    end;
    CheckedHubUrl := Url;
    Result := True;
    Exit;
  end;

  // It answered, and it says which computer it is on — a better answer than any name
  // comparison, because an address can resolve in ways this installer cannot see.
  if (CompareText(HubName, GetComputerNameString) = 0) or LooksLikeThisComputer(Host) then
  begin
    // The whole thing, not the release part: the release is what has to *match*, and the
    // build number is the only thing that differs between two installs of one release. Cutting
    // it off is what made the wizard say "2.2.7 will be upgraded to 2.2.7".
    ExistingHubVersion := HubVersion;
    if not InstallingHub() then
    begin
      // A hub is here and this is a client-only install: normal, and no token is needed
      // because the pairing already on this machine is what the client will use.
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    // One line on purpose: a continuation starting with '[' is read by Inno as a section
    // tag inside [Code], and it reports only "Invalid section tag". A test guards this.
    Question := FmtMessage(ExpandConstant('{cm:HubIsThisPC}'), [HubVersion, '{#MyAppVersion}']);
    if not Confirmed(Question) then
      Exit;
    HubIsLocal := True;
    CheckedHubUrl := Url;
    Result := True;
    Exit;
  end;

  // Against the *release*, not against MyAppVersion — that now carries the build number, and
  // comparing with it would refuse every hub including the matching one. A client and its hub
  // have to agree on the release; the build number counts builds on one machine.
  if CompareText(ReleasePart(HubVersion), '{#MyRelease}') <> 0 then
  begin
    Complaint := FmtMessage(ExpandConstant('{cm:HubVersionClash}'), [HubVersion, '{#MyAppVersion}']);
    Exit;
  end;

  if Trim(TokenEdit.Text) = '' then
  begin
    Complaint := ExpandConstant('{cm:HubNeedToken}');
    Exit;
  end;

  CheckedHubUrl := Url;
  Result := True;
end;

// The summary, which is where an upgrade belongs: a fact about this machine, not a choice
// anybody had to make.
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
                         MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Lines: String;
begin
  Lines := '';
  if InstallingHub() then
  begin
    // Three different things, and the reader deserves to be told which: an upgrade of a hub
    // that answered a ping, an upgrade of one in the registry that did not, and a hub that
    // does not exist here yet.
    if ExistingHubVersion <> '' then
      if CompareText(ExistingHubVersion, '{#MyAppVersion}') = 0 then
        // The same build going on again. Saying "upgraded" about that is the wizard telling
        // somebody something happened that did not.
        Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubSame}'), ['{#MyAppVersion}', GetHubPort('')]) + NewLine
      else
        Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubUpgrade}'), [ExistingHubVersion, '{#MyAppVersion}', GetHubPort('')]) + NewLine
    else if HubInstalledHere() then
      Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubKept}'), ['{#MyAppVersion}', GetHubPort('')]) + NewLine
    else
      Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubNew}'), [GetHubPort('')]) + NewLine;
  end;
  begin
    if HubIsLocal then
      Lines := Lines + Space + ExpandConstant('{cm:ReadyClientLocal}') + NewLine
    else
      Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyClientRemote}'), [CheckedHubUrl]) + NewLine;
  end;
  Result := MemoDirInfo + NewLine + NewLine + Lines;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + MemoTasksInfo;
end;

// Whether this installation stopped a running tray application. taskkill answers 0 when it
// terminated something and 128 when there was nothing to terminate, so this is a fact rather
// than a guess — and it is what decides whether the panel is put back at the end.
var
  ClosedThePanel: Boolean;

function WeClosedThePanel(): Boolean;
begin
  Result := ClosedThePanel;
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Kill a running instance so we can overwrite the exe.
  Exec('taskkill.exe', '/F /IM ServiceOfficer.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  ClosedThePanel := (ResultCode = 0);
  Log('Closed a running panel? taskkill said ' + IntToStr(ResultCode));
  Result := True;
end;

// An installer that copies over a running service exe fails with "file in use", and this is
// the ordinary case: every upgrade of a hub.
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
var
  Complaint: String;
begin
  Result := True;

  if CurPageID = TypePage.ID then
  begin
    // The components are decided here, once, from the answer — there is no components page
    // left to tick.
    if TypePage.SelectedValueIndex = TypeBoth then
      WizardSelectComponents('hub,client')
    else
      WizardSelectComponents('client');
    Exit;
  end;

  if (HubPage <> nil) and (CurPageID = HubPage.ID) then
  begin
    Result := AddressIsUsable(Complaint);
    if (not Result) and (Complaint <> '') and (not WizardSilent()) then
      MsgBox(Complaint, mbError, MB_OK);
    Exit;
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
