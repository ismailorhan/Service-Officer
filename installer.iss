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
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=hub [/HUBPORT=9100]
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=client ^
;       /HUBURL=https://ctl052:8797 /HUBTOKEN=xxxxxxxx
;   ServiceOfficerSetup.exe /SILENT /NORESTART /TYPE=full
; An upgrade needs no /TYPE: Inno preselects whatever is already installed.
; -----------------------------------------------------------------------------

#define MyAppName        "Service Officer"
#define MyAppVersion     "2.2.3"
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
english.TypeCaption=Setup type
english.TypeBody=What should this computer do?
english.TypeBoth=Hub (service) and Client (tray) — this computer does the work
english.TypeHubOnly=Hub (service) only — a server nobody logs into
english.TypeClientOnly=Client (tray) only — read a hub on another computer
english.PortCaption=Hub port
english.PortBody=The port the Hub listens on for its clients. Leave it unless something else is using it.
english.PortField=Port
english.PortBad=A port has to be a number between 1 and 65535.
english.PortKept=The Hub here already serves on port %1, and that is kept — clients have it stored. Change it with "ServiceOfficerHub.exe port <n>" and restart the service.
english.HubCaption=Hub address
english.HubBody=Which hub should the Client read? A hub on this computer counts — the address is checked either way.
english.HubAddress=Address (host name or IP, and :port if it is not the default)
english.HubToken=Token (from "ServiceOfficerHub.exe client add" on the hub)
english.HubTokenLocal=Not needed for a hub on this computer — one is issued during the installation.
english.HubNeedAddress=Enter the hub's host name or IP address.
english.HubNeedToken=Enter the token the hub printed for this computer. Without it the Client can reach the hub but not read anything.
english.HubIsThisPC=That address is this computer, and the Hub here will be upgraded to version %1.%n%nThe services it watches keep running; the Hub is stopped and started again during the installation.%n%nContinue?
english.HubNotHere=That address is this computer, but no Hub is installed here.%n%nThe Client will be installed and will have nothing to read until a Hub exists at that address. You can change the address later in Settings %+ General.%n%nInstall anyway?
english.HubVersionClash=That hub is version %1 and this installer is version %2.%n%nA client and its hub have to be the same version — the connection would succeed and then refuse everything. Upgrade that hub first, or install the matching version here.
english.HubSilent=%1 did not answer.%n%nNothing about it can be checked — its version, or whether it is this computer. The address and the token will be stored and used on the first launch, and can be changed in Settings %+ General.%n%nInstall anyway?
english.ReadyHubUpgrade=Hub (service): %1 will be upgraded to %2
english.ReadyHubNew=Hub (service): a new hub on port %1
english.ReadyClientLocal=Client (tray): reads the hub on this computer
english.ReadyClientRemote=Client (tray): reads %1%n%nInstall anyway?
turkish.SecuringData=Veri klasörü izinleri ayarlanıyor...
turkish.TypeCaption=Kurulum türü
turkish.TypeBody=Bu bilgisayar ne yapsın?
turkish.TypeBoth=Hub (servis) ve Client (tepsi) — işi bu bilgisayar yapar
turkish.TypeHubOnly=Yalnızca Hub (servis) — kimsenin oturum açmadığı bir sunucu
turkish.TypeClientOnly=Yalnızca Client (tepsi) — başka bir bilgisayardaki hub'ı okur
turkish.PortCaption=Hub portu
turkish.PortBody=Hub'ın istemcileri için dinlediği port. Başka bir şey kullanmıyorsa olduğu gibi bırakın.
turkish.PortField=Port
turkish.PortBad=Port 1 ile 65535 arasında bir sayı olmalı.
turkish.PortKept=Buradaki hub zaten %1 portunda hizmet veriyor ve bu korunuyor — istemcilerde kayıtlı. Değiştirmek için "ServiceOfficerHub.exe port <n>" komutunu kullanıp servisi yeniden başlatın.
turkish.HubCaption=Hub adresi
turkish.HubBody=Client hangi hub'ı okusun? Bu bilgisayardaki bir hub da sayılır — adres her durumda denetlenir.
turkish.HubAddress=Adres (makine adı veya IP, varsayılan değilse :port ile)
turkish.HubToken=Token (hub üzerinde "ServiceOfficerHub.exe client add" komutundan)
turkish.HubTokenLocal=Bu bilgisayardaki bir hub için gerekmiyor — kurulum sırasında bir tane üretilir.
turkish.HubNeedAddress=Hub'ın makine adını veya IP adresini girin.
turkish.HubNeedToken=Hub'ın bu bilgisayar için yazdırdığı token'ı girin. O olmadan Client hub'a ulaşır ama hiçbir şey okuyamaz.
turkish.HubIsThisPC=Bu adres bu bilgisayarı gösteriyor ve buradaki Hub %1 sürümüne yükseltilecek.%n%nİzlediği servisler çalışmaya devam eder; kurulum sırasında Hub durdurulup yeniden başlatılır.%n%nDevam edilsin mi?
turkish.HubNotHere=Bu adres bu bilgisayarı gösteriyor ama burada kurulu bir Hub yok.%n%nClient kurulacak ve o adreste bir Hub olana kadar okuyacak bir şeyi olmayacak. Adresi sonradan Ayarlar %+ Genel bölümünden değiştirebilirsiniz.%n%nYine de kurulsun mu?
turkish.HubVersionClash=O hub %1 sürümünde, bu kurulum ise %2 sürümünde.%n%nİstemci ile hub aynı sürümde olmak zorunda — bağlantı kurulur ama her isteği reddeder. Önce o hub'ı yükseltin ya da buraya eşleşen sürümü kurun.
turkish.HubSilent=%1 yanıt vermedi.%n%nHakkında hiçbir şey denetlenemiyor — sürümü de, bu bilgisayar olup olmadığı da. Adres ve token kaydedilip ilk açılışta kullanılacak, Ayarlar %+ Genel bölümünden değiştirilebilir.%n%nYine de kurulsun mu?
turkish.ReadyHubUpgrade=Hub (servis): %1 sürümü %2 sürümüne yükseltilecek
turkish.ReadyHubNew=Hub (servis): %1 portunda yeni bir hub
turkish.ReadyClientLocal=Client (tepsi): bu bilgisayardaki hub'ı okur
turkish.ReadyClientRemote=Client (tepsi): %1 okur
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
//   Hub address   which hub the Client reads. A hub on *this* computer is still a hub it
//                 connects to: there is no mode anywhere in this product, there is an
//                 address, and localhost is an address.
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
  TypeHubOnly = 1;
  TypeClientOnly = 2;

var
  TypePage: TInputOptionWizardPage;
  PortPage: TInputQueryWizardPage;
  HubPage: TInputQueryWizardPage;
  //: The address given, normalised, once it has passed its checks.
  CheckedHubUrl: String;
  //: That address is this computer — so no token is needed and `client pair --local`
  //: does the pairing.
  HubIsLocal: Boolean;
  //: The port already in use here, read at startup. Empty on a first install.
  ExistingPort: String;
  ExistingHubVersion: String;

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

// ---------------------------------------------------------------------------
// reading and normalising an address
// ---------------------------------------------------------------------------
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
  while (Start <= Length(Body))
        and ((Body[Start] = ' ') or (Body[Start] = '"')) do
    Start := Start + 1;
  Finish := Start;
  while (Finish <= Length(Body)) and (Body[Finish] <> '"')
        and (Body[Finish] <> ',') and (Body[Finish] <> '}') do
    Finish := Finish + 1;
  Result := Trim(Copy(Body, Start, Finish - Start));
end;

// "ctl052" and "https://ctl052:8797/" and "10.77.3.50" all mean the same thing, and a
// person typing an address should not have to know which one this wants.
function NormalisedUrl(Given: String): String;
var
  Host: String;
  Scheme: Integer;
  Colon: Integer;
begin
  Result := '';
  Host := Trim(Given);
  Scheme := Pos('://', Host);
  if Scheme > 0 then
    Host := Copy(Host, Scheme + 3, Length(Host));
  if Pos('/', Host) > 0 then
    Host := Copy(Host, 1, Pos('/', Host) - 1);
  Host := Trim(Host);
  if Host = '' then
    Exit;
  // An IPv6 address is full of colons, so the port is only looked for after the
  // brackets it has to be written in.
  if Copy(Host, 1, 1) = '[' then
    Colon := Pos(']:', Host)
  else
    Colon := Pos(':', Host);
  if Colon = 0 then
    Host := Host + ':{#MyHubPort}';
  Result := 'https://' + Host;
end;

function HostOfUrl(Url: String): String;
var
  Host: String;
  Colon: Integer;
begin
  Host := Url;
  if Pos('://', Host) > 0 then
    Host := Copy(Host, Pos('://', Host) + 3, Length(Host));
  if Copy(Host, 1, 1) = '[' then
    Colon := Pos(']:', Host)
  else
    Colon := Pos(':', Host);
  if Colon > 0 then
    Host := Copy(Host, 1, Colon - 1);
  Result := Host;
end;

// Is that address this computer? Asked because "connect to an existing hub" pointed at
// this one means the person wants a hub here, and a client-only install would leave them
// reading a hub nobody upgraded. The hub's own ping answers this better than any name
// comparison can — it says which computer it is running on — and this is the fallback for
// when it does not answer at all.
function LooksLikeThisComputer(Host: String): Boolean;
var
  Mine: String;
begin
  Host := LowerCase(Trim(Host));
  Mine := LowerCase(GetComputerNameString);
  Result := (Host = 'localhost') or (Host = '127.0.0.1') or (Host = '::1')
            or (Host = '[::1]') or (Host = Mine) or (Pos(Mine + '.', Host) = 1);
end;

// The hub's name and version, from the one endpoint that needs no token. Certificate
// errors are ignored here on purpose: the hub is self-signed and it is the *client* that
// pins it, on every connection. Nothing secret crosses this request — it is a version
// number and a computer name — and refusing to read it would only mean asking the person
// to check by hand.
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

// A running hub reports 2.2.2.7 — release, then the build number, which counts builds on
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

// What this computer was paired with last time, so somebody reinstalling does not have to
// remember. This user's own copy first, then the machine's — the same order the client
// itself reads them in.
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

function HubInstalledHere(): Boolean;
begin
  Result := RegKeyExists(HKEY_LOCAL_MACHINE,
                         'SYSTEM\CurrentControlSet\Services\{#MyHubService}');
end;

function InstallingHub(): Boolean;
begin
  Result := TypePage.SelectedValueIndex <> TypeClientOnly;
end;

function InstallingClient(): Boolean;
begin
  Result := TypePage.SelectedValueIndex <> TypeHubOnly;
end;

// The port the hub here already serves on, asked of the hub itself rather than guessed
// from a file: `ServiceOfficerHub.exe port` prints it, and it is the only thing that knows.
function PortAlreadyHere(): String;
var
  Exe, Output: String;
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  Exe := ExpandConstant('{app}\{#MyHubService}\{#MyHubExeName}');
  if not FileExists(Exe) then
    Exit;
  Output := ExpandConstant('{tmp}\port.txt');
  if not Exec(ExpandConstant('{cmd}'), '/C ""' + Exe + '" port > "' + Output + '""',
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Exit;
  if LoadStringsFromFile(Output, Lines) and (GetArrayLength(Lines) > 0) then
    Result := Trim(Lines[0]);
end;

// ---------------------------------------------------------------------------
// what the [Run] entries ask
// ---------------------------------------------------------------------------
function GetHubUrl(Param: String): String;
begin
  Result := ParamValue('HUBURL');
  if Result <> '' then
    Result := NormalisedUrl(Result)
  else
    Result := CheckedHubUrl;
end;

function GetHubToken(Param: String): String;
begin
  Result := ParamValue('HUBTOKEN');
  if (Result = '') and (HubPage <> nil) then
    Result := Trim(HubPage.Values[1]);
end;

// Only when the hub is somewhere else. A hub on this computer pairs its own client with
// `client pair --local`, which needs no token carried anywhere.
function PairingWithARemoteHub(): Boolean;
begin
  Result := (GetHubUrl('') <> '') and (not HubIsLocal);
end;

// The port for the firewall rule and for `hub.exe port`. A port already in use here wins:
// clients have it stored, and moving it would leave every one of them looking at nothing.
function GetHubPort(Param: String): String;
begin
  if ParamValue('HUBPORT') <> '' then
    Result := ParamValue('HUBPORT')
  else if ExistingPort <> '' then
    Result := ExistingPort
  else if PortPage <> nil then
    Result := Trim(PortPage.Values[0])
  else
    Result := '{#MyHubPort}';
  if Result = '' then
    Result := '{#MyHubPort}';
end;

// Writing the port is skipped when it is already what the hub uses — one less thing done
// to a machine for no reason, and one less way to fail.
function PortWasChosen(): Boolean;
begin
  Result := (ExistingPort = '') and (GetHubPort('') <> '{#MyHubPort}');
end;

// ---------------------------------------------------------------------------
// the pages
// ---------------------------------------------------------------------------
procedure InitializeWizard();
var
  Remembered: String;
  HubName: String;
begin
  ExistingPort := PortAlreadyHere();
  ExistingHubVersion := '';

  TypePage := CreateInputOptionPage(
    wpSelectDir, ExpandConstant('{cm:TypeCaption}'),
    ExpandConstant('{cm:TypeBody}'), '', True, False);
  TypePage.Add(ExpandConstant('{cm:TypeBoth}'));
  TypePage.Add(ExpandConstant('{cm:TypeHubOnly}'));
  TypePage.Add(ExpandConstant('{cm:TypeClientOnly}'));

  PortPage := CreateInputQueryPage(
    TypePage.ID, ExpandConstant('{cm:PortCaption}'),
    ExpandConstant('{cm:PortBody}'), '');
  PortPage.Add(ExpandConstant('{cm:PortField}'), False);
  PortPage.Values[0] := '{#MyHubPort}';

  HubPage := CreateInputQueryPage(
    PortPage.ID, ExpandConstant('{cm:HubCaption}'),
    ExpandConstant('{cm:HubBody}'), '');
  HubPage.Add(ExpandConstant('{cm:HubAddress}'), False);
  HubPage.Add(ExpandConstant('{cm:HubToken}'), True);

  // Where the wizard starts from, taken from what is already true here rather than from a
  // default nobody chose: a machine with the hub service is doing both, a machine with a
  // remembered address is a client of it, and anything else is a first install.
  Remembered := StoredHubUrl();
  if HubInstalledHere() then
  begin
    TypePage.SelectedValueIndex := TypeBoth;
    if ExistingPort <> '' then
      PortPage.Values[0] := ExistingPort;
    HubPage.Values[0] := GetComputerNameString;
  end
  else if Remembered <> '' then
  begin
    TypePage.SelectedValueIndex := TypeClientOnly;
    HubPage.Values[0] := Remembered;
  end
  else
  begin
    TypePage.SelectedValueIndex := TypeBoth;
    HubPage.Values[0] := GetComputerNameString;
  end;
  HubName := '';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // The components page is gone: the setup type says which components, and a list of two
  // parts is a worse question than "what should this computer do".
  if PageID = wpSelectComponents then
    Result := True
  // A port is only asked for when a hub is being installed here *and* there is not
  // already one serving on a port its clients have stored.
  else if (PortPage <> nil) and (PageID = PortPage.ID) then
    Result := (not InstallingHub()) or (ExistingPort <> '')
  else if (HubPage <> nil) and (PageID = HubPage.ID) then
    Result := not InstallingClient();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (HubPage <> nil) and (CurPageID = HubPage.ID) then
  begin
    // The token is not needed for a hub on this computer, and saying so is better than
    // leaving an empty box that looks required.
    if InstallingHub() then
      HubPage.SubCaptionLabel.Caption := ExpandConstant('{cm:HubTokenLocal}')
    else
      HubPage.SubCaptionLabel.Caption := ExpandConstant('{cm:HubBody}');
  end;
end;

// Everything that has to be true before an address is accepted. False keeps the person on
// the page; `Complaint` is shown when it is set, and left empty when this has already said
// something itself.
function AddressIsUsable(var Complaint: String): Boolean;
var
  Url, Host, HubName, HubVersion: String;
  Local: Boolean;
begin
  Result := False;
  Complaint := '';
  HubIsLocal := False;
  CheckedHubUrl := '';

  Url := NormalisedUrl(HubPage.Values[0]);
  if Url = '' then
  begin
    Complaint := ExpandConstant('{cm:HubNeedAddress}');
    Exit;
  end;
  Host := HostOfUrl(Url);

  if not AskHub(Url, HubName, HubVersion) then
  begin
    Local := LooksLikeThisComputer(Host);
    // Nothing answered. Two different silences: a hub that is about to be installed here
    // has not started yet and that is expected, otherwise it is genuinely unknown.
    if Local and InstallingHub() then
    begin
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    if Local then
    begin
      if MsgBox(ExpandConstant('{cm:HubNotHere}'), mbConfirmation, MB_YESNO) <> IDYES then
        Exit;
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    if MsgBox(FmtMessage(ExpandConstant('{cm:HubSilent}'), [Host]), mbConfirmation, MB_YESNO) <> IDYES then
      Exit;
    if Trim(HubPage.Values[1]) = '' then
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
    ExistingHubVersion := ReleasePart(HubVersion);
    if not InstallingHub() then
    begin
      // A hub is here and this is a client-only install: perfectly normal, and no token
      // is needed because `client pair --local` cannot run without the hub component.
      // The pairing already on this machine is what the client will use.
      HubIsLocal := True;
      CheckedHubUrl := Url;
      Result := True;
      Exit;
    end;
    if MsgBox(FmtMessage(ExpandConstant('{cm:HubIsThisPC}'), ['{#MyAppVersion}']), mbConfirmation, MB_YESNO) <> IDYES then
      Exit;
    HubIsLocal := True;
    CheckedHubUrl := Url;
    Result := True;
    Exit;
  end;

  if CompareText(ReleasePart(HubVersion), '{#MyAppVersion}') <> 0 then
  begin
    Complaint := FmtMessage(ExpandConstant('{cm:HubVersionClash}'), [ReleasePart(HubVersion), '{#MyAppVersion}']);
    Exit;
  end;

  if Trim(HubPage.Values[1]) = '' then
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
    if ExistingHubVersion <> '' then
      Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubUpgrade}'), [ExistingHubVersion, '{#MyAppVersion}']) + NewLine
    else
      Lines := Lines + Space + FmtMessage(ExpandConstant('{cm:ReadyHubNew}'), [GetHubPort('')]) + NewLine;
  end;
  if InstallingClient() then
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
var
  Complaint: String;
  Port: Integer;
begin
  Result := True;

  if CurPageID = TypePage.ID then
  begin
    // The components are decided here, once, from the answer — there is no components
    // page left to tick.
    if TypePage.SelectedValueIndex = TypeBoth then
      WizardSelectComponents('hub,client')
    else if TypePage.SelectedValueIndex = TypeHubOnly then
      WizardSelectComponents('hub')
    else
      WizardSelectComponents('client');
    Exit;
  end;

  if (PortPage <> nil) and (CurPageID = PortPage.ID) then
  begin
    Port := StrToIntDef(Trim(PortPage.Values[0]), -1);
    if (Port < 1) or (Port > 65535) then
    begin
      MsgBox(ExpandConstant('{cm:PortBad}'), mbError, MB_OK);
      Result := False;
      Exit;
    end;
    // The client on this machine reads the hub through its host name, so the port it is
    // told about has to be the one just chosen.
    if (HubPage <> nil) and LooksLikeThisComputer(HostOfUrl(NormalisedUrl(HubPage.Values[0]))) then
      HubPage.Values[0] := GetComputerNameString + ':' + Trim(PortPage.Values[0]);
    Exit;
  end;

  if (HubPage <> nil) and (CurPageID = HubPage.ID) then
  begin
    Result := AddressIsUsable(Complaint);
    if (not Result) and (Complaint <> '') then
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
