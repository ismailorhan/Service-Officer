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
#define MyAppVersion     "2.2.2"
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
english.MethodCaption=Installation method
english.MethodBody=How should this computer get its service information?
english.MethodNew=Install a new Service Officer Hub on this computer
english.MethodNewHint=This computer does the work: it watches the services, restarts them and keeps the history. Choose this for the first installation, and to upgrade a hub that is already here.
english.MethodJoin=Connect to an existing hub
english.MethodJoinHint=Another computer already does the work. This one reads it and asks it to act.
english.HubCaption=The hub to connect to
english.HubBody=Which hub should this computer read? The address is checked before the installation continues.
english.HubAddress=Address (host name or IP, and :port if it is not 8797)
english.HubToken=Token (from "ServiceOfficerHub.exe client add" on the hub)
english.HubNeedAddress=Enter the hub's host name or IP address.
english.HubNeedToken=Enter the token the hub printed for this computer. Without it this computer can reach the hub but not read anything.
english.HubIsThisPC=That address is this computer.%n%nThe hub installed here will be upgraded to version %1. The services it watches keep running; it is stopped and started again during the upgrade.%n%nContinue?
english.HubVersionClash=That hub is version %1 and this installer is version %2.%n%nA client and its hub have to be the same version. Upgrade the hub first, or install the matching version here.
english.HubSilent=%1 did not answer.%n%nNothing about it can be checked — its version, or whether it is this computer. The address and the token will be stored and used on the first launch.%n%nInstall anyway?
turkish.SecuringData=Veri klasörü izinleri ayarlanıyor...
turkish.MethodCaption=Kurulum yöntemi
turkish.MethodBody=Bu bilgisayar servis bilgilerini nasıl alsın?
turkish.MethodNew=Bu bilgisayara yeni bir Service Officer Hub kur
turkish.MethodNewHint=İşi bu bilgisayar yapar: servisleri izler, yeniden başlatır ve geçmişi tutar. İlk kurulum için ve buradaki hub'ı yükseltmek için bunu seçin.
turkish.MethodJoin=Mevcut bir hub'a bağlan
turkish.MethodJoinHint=İşi başka bir bilgisayar yapıyor. Bu bilgisayar onu okur ve ondan işlem yapmasını ister.
turkish.HubCaption=Bağlanılacak hub
turkish.HubBody=Bu bilgisayar hangi hub'ı okusun? Adres, kuruluma devam edilmeden önce denetlenir.
turkish.HubAddress=Adres (makine adı veya IP, 8797 değilse :port ile)
turkish.HubToken=Token (hub üzerinde "ServiceOfficerHub.exe client add" komutundan)
turkish.HubNeedAddress=Hub'ın makine adını veya IP adresini girin.
turkish.HubNeedToken=Hub'ın bu bilgisayar için yazdırdığı token'ı girin. O olmadan bu bilgisayar hub'a ulaşır ama hiçbir şey okuyamaz.
turkish.HubIsThisPC=Bu adres bu bilgisayarı gösteriyor.%n%nBuradaki hub %1 sürümüne yükseltilecek. İzlediği servisler çalışmaya devam eder; yükseltme sırasında hub durdurulup yeniden başlatılır.%n%nDevam edilsin mi?
turkish.HubVersionClash=O hub %1 sürümünde, bu kurulum ise %2 sürümünde.%n%nİstemci ile hub aynı sürümde olmak zorunda. Önce hub'ı yükseltin ya da buraya eşleşen sürümü kurun.
turkish.HubSilent=%1 yanıt vermedi.%n%nHakkında hiçbir şey denetlenemiyor — sürümü de, bu bilgisayar olup olmadığı da. Adres ve token kaydedilip ilk açılışta kullanılacak.%n%nYine de kurulsun mu?
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
// The wizard asks one question before anything else: does this computer do the work, or
// read a computer that does? Everything else follows from the answer — which components
// are installed, whether an address is asked for, and whether the hub here is upgraded.
//
// The address is *checked* rather than trusted, because the two ways of getting it wrong
// are both quiet. An address that turns out to be this computer means the person wants a
// hub here after all, and installing only a client would leave them with a panel reading
// a hub that nobody upgraded. An address on another computer running a different version
// means a client that connects and then refuses everything — see wire.PROTOCOL.
const
  MethodNewHub = 0;
  MethodExistingHub = 1;

var
  MethodPage: TInputOptionWizardPage;
  HubPage: TInputQueryWizardPage;
  //: The address given, normalised, once it has passed the checks.
  CheckedHubUrl: String;
  //: The address given points at this computer, so the hub component is installed too
  //: and the hub here is upgraded rather than left behind.
  UpgradingLocalHub: Boolean;
  ComponentsPreset: Boolean;

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
// `client pair --local`, which needs no token to be carried anywhere.
function PairingWithARemoteHub(): Boolean;
begin
  Result := (GetHubUrl('') <> '') and (not UpgradingLocalHub);
end;

// ---------------------------------------------------------------------------
// the pages
// ---------------------------------------------------------------------------
procedure InitializeWizard();
var
  Remembered: String;
begin
  MethodPage := CreateInputOptionPage(
    wpSelectDir, ExpandConstant('{cm:MethodCaption}'),
    ExpandConstant('{cm:MethodBody}'), '', True, False);
  MethodPage.Add(ExpandConstant('{cm:MethodNew}'));
  MethodPage.Add(ExpandConstant('{cm:MethodJoin}'));

  HubPage := CreateInputQueryPage(
    MethodPage.ID, ExpandConstant('{cm:HubCaption}'),
    ExpandConstant('{cm:HubBody}'), '');
  HubPage.Add(ExpandConstant('{cm:HubAddress}'), False);
  HubPage.Add(ExpandConstant('{cm:HubToken}'), True);

  // Where the wizard starts from. A machine that already has the hub service is being
  // upgraded, whatever else is true; otherwise a remembered address means this was a
  // client, and it is filled in but still editable.
  Remembered := StoredHubUrl();
  if HubInstalledHere() then
    MethodPage.SelectedValueIndex := MethodNewHub
  else if Remembered <> '' then
  begin
    MethodPage.SelectedValueIndex := MethodExistingHub;
    HubPage.Values[0] := Remembered;
  end
  else
    MethodPage.SelectedValueIndex := MethodNewHub;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (HubPage <> nil) and (PageID = HubPage.ID)
            and (MethodPage.SelectedValueIndex <> MethodExistingHub);
end;

// The components follow from the method, and are preset once rather than on every visit:
// somebody who unticks the tray application on a server should not have it ticked again
// by stepping back and forward.
procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectComponents) and (not ComponentsPreset) then
  begin
    ComponentsPreset := True;
    if (MethodPage.SelectedValueIndex = MethodNewHub) or UpgradingLocalHub then
      WizardSelectComponents('hub,client')
    else
      WizardSelectComponents('client');
  end;
end;

// Everything that has to be true before the address is accepted. Returns False to keep
// the person on the page; `Complaint` is shown if it is set, and is left empty when the
// function has already said something itself.
function AddressIsUsable(var Complaint: String): Boolean;
var
  Url, Host, HubName, HubVersion: String;
begin
  Result := False;
  Complaint := '';
  UpgradingLocalHub := False;
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
    // Not answering is allowed, with the uncertainty said out loud: a workstation is
    // often imaged before the server it will read exists. Nothing is checked, so
    // nothing is claimed.
    if MsgBox(FmtMessage(ExpandConstant('{cm:HubSilent}'), [Host]),
              mbConfirmation, MB_YESNO) <> IDYES then
      Exit;
    UpgradingLocalHub := LooksLikeThisComputer(Host);
    if (not UpgradingLocalHub) and (Trim(HubPage.Values[1]) = '') then
    begin
      Complaint := ExpandConstant('{cm:HubNeedToken}');
      Exit;
    end;
    CheckedHubUrl := Url;
    Result := True;
    Exit;
  end;

  // It answered, and its answer says which computer it is on — better than comparing
  // names, because a hub can be reached by an address that resolves in ways this
  // installer cannot see.
  if (CompareText(HubName, GetComputerNameString) = 0)
     or LooksLikeThisComputer(Host) then
  begin
    if MsgBox(FmtMessage(ExpandConstant('{cm:HubIsThisPC}'), ['{#MyAppVersion}']),
              mbConfirmation, MB_YESNO) <> IDYES then
      Exit;
    UpgradingLocalHub := True;
    CheckedHubUrl := Url;
    Result := True;
    Exit;
  end;

  if CompareText(ReleasePart(HubVersion), '{#MyAppVersion}') <> 0 then
  begin
    // The array stays on this line: Inno reads a line that *starts* with "[" as a
    // section tag, wherever it is, and says only "Invalid section tag".
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
begin
  Result := True;
  if (HubPage <> nil) and (CurPageID = HubPage.ID) then
  begin
    Result := AddressIsUsable(Complaint);
    if (not Result) and (Complaint <> '') then
      MsgBox(Complaint, mbError, MB_OK);
    Exit;
  end;
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
