"""Türkçe. The English sentence is the key — see core/i18n.py for why.

Nothing here is a machine translation of a shape: a sentence that explains *what to do* has
to still explain it, and the Turkish for "the hub is moving" has to leave somebody as sure of
what just happened as the English does.
"""

WORDS = {
    # ── Hub ▸ Address and Token ──────────────────────────────────────────
    "Host": "Sunucu",
    "Port": "Port",
    "Token": "Token",
    "A host name or IP. Leave it empty to watch this computer's own services instead — that "
    "is what a single-machine install is.":
        "Bir makine adı ya da IP. Bunun yerine bu bilgisayarın kendi servislerini izlemek "
        "için boş bırakın — tek makinelik kurulum bu demektir.",
    "Empty means 8797.\n\n"
    "This is not the port a hub on this computer listens on — that one is under SERVING "
    "below.":
        "Boş bırakılırsa 8797.\n\n"
        "Bu, bu bilgisayardaki bir hub'ın dinlediği port değil — o aşağıda SUNUM altında.",
    "What proves this computer may read that hub. Shown once, when the hub issues it, and "
    "stored on this computer afterwards.":
        "Bu bilgisayarın o hub'ı okuyabildiğini kanıtlayan şey. Hub onu üretirken bir kez "
        "gösterilir, sonrasında bu bilgisayarda saklanır.",

    # ── the version handshake ────────────────────────────────────────────
    # Composed on the *client* and shown in that person's panel, so it is translated. The
    # hub's own refusals are not: a hub words those in its language and a client may be
    # reading in another.
    "This computer is running {mine} and the hub is running {theirs}. A client and its hub "
    "have to be the same release.":
        "Bu bilgisayar {mine}, hub ise {theirs} çalıştırıyor. Bir client ve hub'ı aynı sürüm "
        "olmalı.",

    # ── Hub ▸ Update ─────────────────────────────────────────────────────
    "UPDATE": "GÜNCELLEME",
    "Install it": "Kur",
    "Check now": "Şimdi kontrol et",
    "Asking…": "Soruluyor…",
    "This computer watches its own services.":
        "Bu bilgisayar kendi servislerini izliyor.",
    "Running {version} — the newest there is.":
        "{version} çalışıyor — en yenisi bu.",
    # "Nothing new" and "could not ask" are different facts. A hub that has not reached the
    # feed for a week must not read as up to date.
    "Running {version}. The last check did not get through: {why}":
        "{version} çalışıyor. Son kontrol ulaşamadı: {why}",
    "{offered} is available — this hub is on {running}.":
        "{offered} çıkmış — bu hub {running} kullanıyor.",
    "Not now — {why}. It will wait.": "Şimdi olmaz — {why}. Bekleyecek.",
    "Could not ask the hub: {why}": "Hub'a sorulamadı: {why}",
    "Downloading and checking it…": "İndiriliyor ve doğrulanıyor…",
    "It was not installed: {why}": "Kurulmadı: {why}",
    "Installing {version}. This hub stops for a moment, so every panel shows disconnected "
    "until it is back — including this one.":
        "{version} kuruluyor. Bu hub bir anlık duruyor, yani geri gelene kadar bütün "
        "paneller bağlantı yok gösterir — bu panel dahil.",

    # ── the sidebar's foot ───────────────────────────────────────────────
    "{version} is available": "{version} çıkmış",
    "Update this computer to {version}": "Bu bilgisayarı {version} sürümüne güncelle",

    # ── Hub ▸ catching this computer up ──────────────────────────────────
    "Update this computer": "Bu bilgisayarı güncelle",
    "The hub can hand this computer the installer, so no internet is needed here.":
        "Hub kurulum dosyasını bu bilgisayara verebilir, yani burada internet gerekmiyor.",
    "The hub does not have the installer for its own release, so it has to be fetched from "
    "the release page and run here.":
        "Hub'ın kendi sürümünün kurulum dosyası elinde değil; sürüm sayfasından indirilip "
        "burada çalıştırılması gerekiyor.",
    "Downloading from the hub and checking it…":
        "Hub'dan indiriliyor ve doğrulanıyor…",
    "Installing. This app closes and reopens on the new release.":
        "Kuruluyor. Bu uygulama kapanıp yeni sürümle yeniden açılacak.",

    # ── Hub ▸ Serving ────────────────────────────────────────────────────
    "SERVING": "SUNUM",
    # "This computer serves clients on port" was the label until the fields were put in a
    # column: it is wider than the label column, so it pushed its own field out of line with
    # every other field on the page. The heading above it already says SUNUM.
    "Listens on": "Dinlediği port",
    "The port the Hub service listens on — not the one above, which is where this panel "
    "reads from. Applying it moves the socket and the firewall rule, and every client is "
    "told the new number first so they follow rather than losing the hub.\n"
    "ServiceOfficerHub.exe port <n> does the same from a command line, which is the way in "
    "when the hub cannot be reached at all.":
        "Hub servisinin dinlediği port — yukarıdaki değil, o bu panelin okuduğu adres. "
        "Uygulandığında soket ve güvenlik duvarı kuralı taşınır; her istemciye yeni numara "
        "önce bildirilir, böylece hub'ı kaybetmek yerine onu takip ederler.\n"
        "ServiceOfficerHub.exe port <n> aynı işi komut satırından yapar; hub'a hiç "
        "ulaşılamadığında giriş yolu budur.",
    "Move to this port": "Bu porta taşı",
    "A port is a number between 1 and 65535.": "Port, 1 ile 65535 arasında bir sayıdır.",
    "The hub refused that: {why}": "Hub bunu reddetti: {why}",
    "Moving to {port}…": "{port} portuna taşınıyor…",
    "The hub is moving from {was} to {port}. This panel follows it, and the other clients "
    "were told first.":
        "Hub {was} portundan {port} portuna taşınıyor. Bu panel onu takip ediyor, diğer "
        "istemcilere de önceden bildirildi.",
    # ── not connected ────────────────────────────────────────────────────
    "not connected": "bağlantı yok",
    "Cannot reach {where} — trying again. What these services are doing is unknown until it "
    "answers.":
        "{where} adresine ulaşılamıyor — yeniden denenecek. Cevap verene kadar bu servislerin "
        "ne durumda olduğu bilinmiyor.",
    # ── a click while the hub is down ────────────────────────────────────
    "the hub is not answering": "hub cevap vermiyor",
    "The hub is not answering, so nothing was run. It is being retried; the tray shows when "
    "it is back.":
        "Hub cevap vermiyor, bu yüzden hiçbir şey çalıştırılmadı. Yeniden denenecek; geri "
        "geldiğinde tray'de görünür.",

    # ── the navigation ───────────────────────────────────────────────────
    # Translated where they are drawn, so these are the keys and the `kind` beside each one
    # in panel.py is untouched — a translated key would find no page.
    "Overview": "Genel görünüm",
    # "Pano", not "Panosu": the possessive suffix would make it "its board".
    "Dashboard": "Pano",
    "Manage": "Yönetim",
    "Infrastructure": "Altyapı",
    "Settings": "Ayarlar",
    "Services": "Servisler",
    "Categories": "Kategoriler",
    "Stacks": "Gruplar",
    "Schedule": "Zamanlama",
    "History": "Geçmiş",
    "Hub": "Hub",
    "Machines": "Makineler",
    "Clients": "İstemciler",
    "General": "Genel",

    # ── the service detail ▸ Check now ───────────────────────────────────
    # "Check now" itself is already above, under Hub ▸ Update: the same two words, the same
    # button, and one entry for both.
    "Checking…": "Kontrol ediliyor…",
    "All checks pass.": "Bütün kontroller geçti.",
    "Something is not answering.": "Bir şey cevap vermiyor.",
    # Which computer ran them, because that was the bug: a workstation answering "this desk can
    # reach that port" to a question about the machine doing the watching.
    "Asked from {where}.": "{where} üzerinden soruldu.",
    "the hub": "hub",
    "this computer": "bu bilgisayar",
    "nothing to check": "kontrol edilecek bir şey yok",

    # ── the service detail's tabs ────────────────────────────────────────
    "Recovery": "Kurtarma",
    "Health": "Sağlık",

    # ── General ▸ Appearance ─────────────────────────────────────────────
    "Theme": "Tema",
    "Language": "Dil",
    "System follows the Windows setting and switches with it.":
        "Sistem, Windows ayarını izler ve onunla birlikte değişir.",
    "Windows opened after this read the new language. This one keeps the words it was built "
    "with — its labels are set when it opens, and rewriting them under somebody mid-sentence "
    "is worse than reopening a window.":
        "Bundan sonra açılan pencereler yeni dili okur. Bu pencere kendi kelimelerini korur — "
        "etiketleri açılırken belirlenir, ve birinin gözü önünde cümle ortasında yeniden "
        "yazmak pencereyi yeniden açmaktan kötüdür.",

    # ── the service detail ▸ General ─────────────────────────────────────
    "Shown as": "Görünen adı",
    "Category": "Kategori",
    "The name this service goes by in this app — the dashboard, the tray panel and the "
    "history. Windows keeps its own.":
        "Bu servisin bu uygulamadaki adı — pano, tray paneli ve geçmiş. Windows kendi adını "
        "korur.",
    "Groups this service under a heading in the dashboard and the tray panel. Define the "
    "headings on the Categories page.":
        "Bu servisi panoda ve tray panelinde bir başlık altında toplar. Başlıkları "
        "Kategoriler sayfasında tanımlayın.",

    # ── the service detail ▸ Recovery ────────────────────────────────────
    # Nouns, not sentence openings. The rows used to read as prose with the values inside them,
    # which cannot be translated: Turkish orders those pieces differently and a list of
    # fragments cannot be reordered. One of them needed an empty string to hide a word.
    "Attempts": "Deneme sayısı",
    "First wait": "İlk bekleme",
    "Multiply by": "Çarpan",
    "Give up after": "Vazgeçme eşiği",
    "stops": "duruş",
    "within": "şu süre içinde",
    "How many times to start it again before giving up. Zero means keep trying.":
        "Vazgeçmeden önce kaç kez yeniden başlatılacağı. Sıfır, denemeye devam et demektir.",
    "How long to wait before the first attempt.":
        "İlk denemeden önce ne kadar bekleneceği.",
    "Each attempt waits this much longer than the one before. A service that failed to start "
    "once will usually fail again immediately, so trying harder and harder is worse than "
    "trying later and later.":
        "Her deneme öncekinden bu kat kadar daha uzun bekler. Bir kez başlamayan servis "
        "genellikle hemen tekrar başlamaz; bu yüzden daha sık denemek, daha seyrek denemekten "
        "kötüdür.",
    "A service that keeps dying is not something restarting will fix, and an app restarting it "
    "every minute for ever hides that. Recovery stops until somebody looks.":
        "Sürekli ölen bir servisi yeniden başlatmak düzeltmez, ve onu sonsuza kadar her dakika "
        "başlatan bir uygulama bunu gizler. Biri bakana kadar kurtarma durur.",
    "The window those stops are counted in.":
        "O duruşların sayıldığı zaman aralığı.",

    # ── page headings ────────────────────────────────────────────────────
    "How the app itself behaves.": "Uygulamanın kendi davranışı.",
    "Where this panel gets its services from. A hub watches and controls; this window is a "
    "view of one.":
        "Bu panelin servislerini nereden aldığı. Hub izler ve yönetir; bu pencere onun bir "
        "görünümü.",
    "Who may read this hub. A token is shown once, when it is issued.":
        "Bu hub'ı kim okuyabilir. Token yalnızca bir kez, üretildiğinde gösterilir.",
    "Where your services live. This computer is always here; add another and its services "
    "appear in the same panel. Open one to set how it is reached — Windows through its "
    "service manager, Linux over SSH.":
        "Servislerinizin yaşadığı yer. Bu bilgisayar her zaman burada; başka bir makine "
        "ekleyin, servisleri aynı panelde görünür. Nasıl erişildiğini ayarlamak için birini "
        "açın — Windows kendi servis yöneticisiyle, Linux SSH üzerinden.",
    # The English was reworded on the page and this entry kept the old wording, so the heading
    # was shown in English in Turkish. Nothing caught it: the orphan check compared entries
    # against every literal in the source *including this file*, so each key matched itself.
    # With the catalogue excluded the check has teeth, and this was the first thing it found.
    "Group your services under headings — SAP, SQL, printing — so the dashboard and the tray "
    "panel can fold away the ones you aren't looking at. Drag to change the order the groups "
    "appear in.":
        "Servislerinizi başlıklar altında toplayın — SAP, SQL, yazdırma — böylece pano ve tray "
        "paneli bakmadıklarınızı katlayıp saklayabilir. Grupların görünme sırasını değiştirmek "
        "için sürükleyin.",
    "Make something happen without anyone watching — after Windows starts, or at a time of "
    "day.":
        "Kimse başında olmadan bir şeyin olmasını sağlayın — Windows açıldıktan sonra ya da "
        "günün belirli bir saatinde.",
    "An ordered group you can start, stop or restart in one go. Stopping walks the order "
    "backwards.":
        "Tek hamlede başlatabileceğiniz, durdurabileceğiniz ya da yeniden başlatabileceğiniz "
        "sıralı bir grup. Durdurma sırayı tersten yürür.",
    "Every state change, with its cause — evidence for a ticket, and the only way to see a "
    "service that keeps dying quietly.":
        "Her durum değişimi, sebebiyle birlikte — bir kayıt için kanıt, ve sessizce sürekli "
        "ölen bir servisi görmenin tek yolu.",

    # ── the Machines page ────────────────────────────────────────────────
    "Add machine…": "Makine ekle…",
    "Open": "Aç",
    "Remove": "Kaldır",
    # No separate key for the chip on a machine row. It says "Hub" in English and so does the
    # nav entry, and inventing "Hub chip" to tell them apart put *that* on screen — in English
    # `t()` returns the key verbatim, which is the whole point of the key being the sentence.
    # The chip test caught it in one run. If the two ever need different words, the moment to
    # split them is when the *English* differs.
    "This PC": "Bu bilgisayar",
    "not asked yet": "henüz sorulmadı",
    "waiting": "bekliyor",
    "connected": "bağlı",
    "answered {when}": "{when} cevap verdi",

    # ── a machine's own settings ─────────────────────────────────────────
    # "Host" is already above, from Hub ▸ Address: the same word for the same thing, and one
    # key is the point — see the note on "This PC".
    "Description": "Açıklama",
    "what this machine is": "bu makine ne",
    "Shown everywhere this machine appears.":
        "Bu makinenin göründüğü her yerde bu yazı görünür.",
    "Target type": "Makine türü",
    "Everything else here follows from this.":
        "Buradaki diğer her şey bu seçime bağlıdır.",
    "host name or IP address": "makine adı ya da IP adresi",
    "port": "port",
    "Port 0 means the usual one — 22 for SSH.":
        "Port 0, alışılmış portu kullanmak demektir — SSH için 22.",
    "Sign in method": "Oturum açma yöntemi",
    "A key is preferred: no secret to keep.":
        "Anahtar tercih edilir: saklanacak bir sır kalmaz.",
    "User": "Kullanıcı",
    "account on that machine": "o makinedeki hesap",
    "Who we log in as. Reading needs no privilege; acting needs sudo without a password.":
        "Hangi hesapla bağlandığımız. Okumak için yetki gerekmez; işlem yapmak şifre sormayan "
        "sudo ister.",
    "Password": "Şifre",
    "type to set a password": "şifre koymak için yazın",
    "Forget": "Unut",
    "Kept encrypted on this computer, not in services.json. Any administrator here can read "
    "it.":
        "services.json içinde değil, bu bilgisayarda şifreli tutulur. Bu bilgisayardaki her "
        "yönetici onu okuyabilir.",
    "Key file": "Anahtar dosyası",
    "Browse…": "Gözat…",
    "The private key here. Its public half goes in that account's ~/.ssh/authorized_keys.":
        "Özel anahtarın kendisi burada durur. Açık yarısı o hesabın ~/.ssh/authorized_keys "
        "dosyasına girer.",
    "Host key": "Makine anahtarı",
    "SHA256:… — confirm it on the machine": "SHA256:… — makinenin üzerinde doğrulayın",
    "Get it": "Getir",
    "Proves the machine is the one you meant. A different key later is refused.":
        "Makinenin kastettiğiniz makine olduğunu kanıtlar. Sonradan başka bir anahtar gelirse "
        "reddedilir.",
    "Check every": "Kontrol aralığı",
    "seconds": "saniye",
    "A remote machine cannot tell us on its own. With journal access this is only a safety "
    "net.":
        "Uzak bir makine durumunu kendiliğinden bildiremez. Journal erişimi varken bu yalnızca "
        "bir emniyet ağıdır.",
    "Extras": "Ek yetenekler",
    "Use WinRM on this machine": "Bu makinede WinRM kullan",
    "Lets this machine's process be terminated, its event log read and a command health check "
    "run on it. Without it those three are not offered.\n\n"
    "Every WinRM call signs in, so it leaves a logon record in that machine's Security log. "
    "Test connection sets this for you.":
        "Bu makinedeki bir işlemin sonlandırılmasını, olay kaydının okunmasını ve üzerinde "
        "komutla sağlık kontrolü yapılmasını sağlar. Bu olmadan bu üçü sunulmaz.\n\n"
        "Her WinRM çağrısı oturum açar, yani o makinenin Güvenlik kaydına bir oturum açma "
        "kaydı bırakır. Bağlantıyı test et bunu sizin yerinize ayarlar.",

    # ── a machine's row: what it is, and when it last answered ───────────
    # One service and several are two entries, not a stem plus "s": Turkish does not form a
    # plural that way, and neither do most languages the app may meet.
    "1 service": "1 servis",
    "{count} services": "{count} servis",
    "Windows service manager": "Windows servis yöneticisi",
    "Windows service manager · {who}": "Windows servis yöneticisi · {who}",
    "Windows service manager · this computer's account":
        "Windows servis yöneticisi · bu bilgisayarın hesabı",
    "systemd over SSH · {who}": "SSH üzerinden systemd · {who}",
    "no account set": "hesap belirtilmemiş",
    "host key not confirmed": "makine anahtarı doğrulanmadı",
    # Whole phrases with the number inside them. "s ago" cannot be translated on its own,
    # because where the number goes is part of the language.
    "just now": "az önce",
    "{seconds}s ago": "{seconds} sn önce",
    "{minutes} min ago": "{minutes} dk önce",
    "{hours} h ago": "{hours} sa önce",
    "no answer": "cevap yok",
    "no answer, last tried {when}": "cevap yok, son deneme {when}",
    "It did not answer, and said nothing about why.":
        "Cevap vermedi ve sebebine dair bir şey söylemedi.",
    "Nothing has asked this machine anything yet. It is asked every {seconds}s once the app "
    "is watching a service on it.":
        "Bu makineye henüz hiçbir şey sorulmadı. Uygulama üzerindeki bir servisi izlemeye "
        "başladığında her {seconds} sn'de bir sorulur.",

    # ── adding and removing one ──────────────────────────────────────────
    "Add machine": "Makine ekle",
    "Host name or IP of the machine.\n"
    "Its description, account and the rest are on the next page.":
        "Makinenin adı ya da IP'si.\n"
        "Açıklaması, hesabı ve gerisi bir sonraki sayfada.",
    "That machine is already listed.": "O makine zaten listede.",
    "This computer can't be removed.": "Bu bilgisayar kaldırılamaz.",
    "{machine} still has {count} service(s) here. Remove them first:":
        "{machine} üzerinde hâlâ {count} servis var. Önce onları kaldırın:",

    # ── the two choice lists ─────────────────────────────────────────────
    # Class constants, so they are translated where they are drawn rather than where they are
    # written — a t() in a class body would settle the language at import.
    "Windows — service manager": "Windows — servis yöneticisi",
    "Linux — systemd over SSH": "Linux — SSH üzerinden systemd",
    "Private key file": "Özel anahtar dosyası",
    "Password (stored on this machine)": "Şifre (bu makinede saklanır)",
    "This computer's signed-in account": "Bu bilgisayarda oturum açmış hesap",
    "User name and password": "Kullanıcı adı ve şifre",

    # ── what each kind of machine needs set up ───────────────────────────
    "This computer is reached by being it — the service manager is right here.":
        "Bu bilgisayara erişim onun kendisi olmakla olur — servis yöneticisi tam burada.",
    "Signing in as root needs nothing set up on that machine — no sudo rule, no group. The "
    "cost is that a root password is stored on this computer, where any administrator can "
    "read it.":
        "root olarak oturum açmak o makinede hiçbir şey ayarlamayı gerektirmez — ne sudo "
        "kuralı, ne grup. Bedeli, bir root şifresinin bu bilgisayarda saklanması: buradaki "
        "her yönetici onu okuyabilir.",
    "Reading a status needs no privilege. Starting and stopping needs sudo without a "
    "password, and instant updates need the account in the systemd-journal group — the "
    "button below writes out exactly what to run for the services you have chosen.":
        "Durum okumak yetki gerektirmez. Başlatmak ve durdurmak şifre sormayan sudo ister, "
        "anlık güncellemeler ise hesabın systemd-journal grubunda olmasını — aşağıdaki "
        "buton, seçtiğiniz servisler için tam olarak neyin çalıştırılacağını yazar.",
    "Windows allows one account per machine at a time: if this computer already has a "
    "connection to it — a mapped drive, an open Explorer window — as somebody else, that "
    "account is used and this one is refused. The account must administer services on that "
    "machine, which normally means its Administrators group.":
        "Windows bir makine için aynı anda tek hesaba izin verir: bu bilgisayarın o makineye "
        "başka biri olarak zaten bir bağlantısı varsa — eşlenmiş bir sürücü, açık bir "
        "Explorer penceresi — o hesap kullanılır ve bu reddedilir. Hesabın o makinede "
        "servisleri yönetmesi gerekir, ki bu normalde Administrators grubunda olmaktır.",
    "Reached as whoever is signed in to this computer. Nothing to set up, as long as that "
    "account administers services on the other machine — otherwise choose a user name and "
    "password above.":
        "Bu bilgisayarda oturum açmış olan kimse, onunla erişilir. O hesap diğer makinede "
        "servisleri yönetiyorsa ayarlanacak bir şey yok — yönetmiyorsa yukarıdan kullanıcı "
        "adı ve şifre seçin.",
    "DOMAIN\\account, or .\\account for one local to that machine.":
        "DOMAIN\\hesap, ya da o makineye yerel bir hesap için .\\hesap.",
    "The signed-in account needs nothing set up. A user name and password is for when it is "
    "not an administrator there.":
        "Oturum açmış hesap için ayarlanacak bir şey yoktur. Kullanıcı adı ve şifre, o hesap "
        "orada yönetici değilse gerekir.",

    # ── the password, and the host key ───────────────────────────────────
    "The password could not be stored. Is Service Officer running as administrator?":
        "Şifre saklanamadı. Service Officer yönetici olarak mı çalışıyor?",
    "Forgotten. That machine cannot be reached with a password until a new one is set.":
        "Unutuldu. Yenisi konulana kadar o makineye şifreyle erişilemez.",
    "{host} did not offer a key — {why}": "{host} bir anahtar sunmadı — {why}",
    "{host} did not offer a key.": "{host} bir anahtar sunmadı.",
    "The key changed. It was {was} and is now {now}. Either that machine was rebuilt, or it "
    "is not the same machine — check before you keep this.":
        "Anahtar değişti. Önce {was} idi, şimdi {now}. Ya o makine yeniden kuruldu ya da "
        "aynı makine değil — bunu saklamadan önce kontrol edin.",
    "{host} offers {key}. Read over the network, so it proves nothing on its own: confirm "
    "it on the machine with ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub if the network "
    "between here and there is not trusted.":
        "{host} {key} sunuyor. Ağ üzerinden okundu, yani tek başına bir şey kanıtlamaz: "
        "burayla orası arasındaki ağa güvenilmiyorsa makinenin üzerinde "
        "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub ile doğrulayın.",

    # ── Test connection ──────────────────────────────────────────────────
    "Test connection": "Bağlantıyı test et",
    "Asking {machine}…": "{machine} soruluyor…",
    "This computer answers — it is the one running Service Officer.":
        "Bu bilgisayar cevap veriyor — Service Officer'ı çalıştıran o.",
    "{machine} answered.": "{machine} cevap verdi.",
    "{machine} did not answer.": "{machine} cevap vermedi.",
    "Signed in as {who}.": "{who} olarak oturum açıldı.",
    "Services can be started and stopped.": "Servisler başlatılıp durdurulabilir.",
    "Watching only — no control.": "Yalnızca izleme — kontrol yok.",
    "Changes arrive as they happen.": "Değişiklikler olduğu anda geliyor.",
    "Status is asked for every {seconds}s.": "Durum her {seconds} sn'de bir soruluyor.",
    "WinRM answers, so it is switched on: its process can be terminated, its event log "
    "read, and a command run on it.":
        "WinRM cevap veriyor, o yüzden açıldı: işlemi sonlandırılabilir, olay kaydı "
        "okunabilir ve üzerinde komut çalıştırılabilir.",
    "WinRM is not usable, so it is switched off.":
        "WinRM kullanılamıyor, o yüzden kapatıldı.",

    # ── what to run on a Linux machine ───────────────────────────────────
    # The comments in that block are read by whoever pastes it, so they are prose. The
    # commands beside them are not, and are left alone.
    "Copy what to run on that machine": "O makinede çalıştırılacakları kopyala",
    "Signing in as root needs no setup on that machine — nothing to run.":
        "root olarak oturum açmak o makinede kurulum gerektirmez — çalıştırılacak bir şey "
        "yok.",
    "# Nothing to set up on {machine}: root already has\n"
    "# every privilege this app needs, and reads the journal.\n":
        "# {machine} üzerinde ayarlanacak bir şey yok: root bu uygulamanın\n"
        "# ihtiyaç duyduğu her yetkiye zaten sahip ve journal'ı okuyor.\n",
    "# Run this on {machine} as root.": "# Bunu {machine} üzerinde root olarak çalıştırın.",
    "# If you are not root: sudo -i   (or su -)":
        "# root değilseniz: sudo -i   (ya da su -)",
    "# Refuse a broken file rather than discovering sudo is gone:":
        "# sudo'nun gittiğini sonradan fark etmek yerine bozuk dosyayı reddet:",
    "# Choose this machine's services on the Services page and\n"
    "# copy this again — the sudo rule will then name them, and\n"
    "# grant nothing beyond them.":
        "# Bu makinenin servislerini Servisler sayfasından seçip\n"
        "# bunu tekrar kopyalayın — sudo kuralı o zaman onları adlandırır\n"
        "# ve ötesinde hiçbir şeye izin vermez.",
    "Copied — paste the whole block into a root shell on {machine}. {count} service(s) "
    "covered.":
        "Kopyalandı — bloğun tamamını {machine} üzerinde bir root kabuğuna yapıştırın. "
        "{count} servis kapsandı.",
    "Copied. It only adds the journal group so far: choose this machine's services first "
    "and copy again to include the sudo rule.":
        "Kopyalandı. Şimdilik yalnızca journal grubunu ekliyor: sudo kuralının da girmesi "
        "için önce bu makinenin servislerini seçip tekrar kopyalayın.",

    # ── a service's state, as a word ─────────────────────────────────────
    # Lower case, because they are read inside a sentence: "{service} is {state}." One entry
    # per state rather than one per sentence that mentions one.
    "running": "çalışıyor",
    "stopped": "durduruldu",
    "paused": "duraklatıldı",
    "not found": "bulunamadı",
    "unknown": "bilinmiyor",

    # ── a service ▸ Recovery ─────────────────────────────────────────────
    "Keep this service running": "Bu servisi çalışır tut",
    "If it stops on its own, start it again.":
        "Kendi kendine durursa yeniden başlat.",
    "Also restart after a clean stop": "Düzgün duruştan sonra da yeniden başlat",
    "Off by default, only crashes are recovered — a non-zero exit code. A service you "
    "stopped yourself in services.msc is left alone.":
        "Varsayılan olarak kapalı, yalnızca çökmeler kurtarılır — sıfır olmayan bir çıkış "
        "kodu. services.msc'den kendiniz durdurduğunuz bir servise dokunulmaz.",

    # ── a service ▸ Health ───────────────────────────────────────────────
    "Watch this service": "Bu servisi izle",
    "Off keeps the checks below but stops asking. Nothing is reported as unhealthy while it "
    "is off.":
        "Kapalıyken aşağıdaki kontroller korunur ama sorulmaz. Kapalı olduğu sürece hiçbir "
        "şey sağlıksız olarak bildirilmez.",
    "Windows reports Running as soon as a process exists. These say whether anyone can "
    "actually use it — the “running but dead” case that a service list cannot show. Every "
    "check has to pass.\n\n"
    "Off keeps the checks below but stops asking, and nothing is reported as unhealthy "
    "while it is off.":
        "Windows, bir işlem var olduğu anda Çalışıyor der. Buradakiler onu gerçekten "
        "kullanabilen biri var mı sorusunu cevaplar — bir servis listesinin gösteremediği "
        "“çalışıyor ama ölü” hali. Her kontrolün geçmesi gerekir.\n\n"
        "Kapalıyken aşağıdaki kontroller korunur ama sorulmaz, ve kapalı olduğu sürece "
        "hiçbir şey sağlıksız olarak bildirilmez.",
    "Ask every": "Sorma aralığı",
    "How often to run the checks below, while the service is running.":
        "Servis çalışırken aşağıdaki kontrollerin ne sıklıkla yapılacağı.",
    "Ignore the first": "Başta yoksay",
    "after it starts": "başladıktan sonra",
    "A service that has just come up hasn't opened its port yet, so asking straight away "
    "would report every restart as a failure.":
        "Yeni ayağa kalkmış bir servis portunu henüz açmamıştır; hemen sormak her yeniden "
        "başlatmayı bir hata olarak bildirirdi.",
    "Call it unhealthy after": "Sağlıksız say",
    "failures in a row": "üst üste hatadan sonra",
    "Consecutive failures before it is called unhealthy. One bad answer is usually load, "
    "not death.":
        "Sağlıksız denmesi için gereken üst üste hata sayısı. Tek kötü cevap genelde yüktür, "
        "ölüm değil.",
    "Then": "Sonra",
    "Just tell me": "Sadece haber ver",
    "Restart the service": "Servisi yeniden başlat",
    "What to do about a service that has stopped answering. Restarting is the obvious fix "
    "and also the one that hides a problem, so it is not the default.":
        "Cevap vermeyi bırakan bir servis için ne yapılacağı. Yeniden başlatmak akla ilk "
        "gelen çözüm ve aynı zamanda problemi gizleyen çözüm, o yüzden varsayılan değil.",
    "At most once every": "En çok şu sıklıkla",
    "A service a restart cannot fix must not be restarted every minute for ever. Zero "
    "restarts it on every verdict.\n\n"
    "Only applies while the action above is Restart the service.":
        "Yeniden başlatmanın çözemediği bir servis sonsuza kadar her dakika yeniden "
        "başlatılmamalı. Sıfır, her kararda yeniden başlatır.\n\n"
        "Yalnızca yukarıdaki eylem Servisi yeniden başlat iken geçerlidir.",

    # ── the checks themselves ────────────────────────────────────────────
    # The five kinds and their fields are class constants in service_detail.py, translated
    # where they are drawn.
    "Add check": "Kontrol ekle",
    "A port answers": "Bir port cevap veriyor",
    "A URL answers": "Bir URL cevap veriyor",
    "It has a process": "Bir işlemi var",
    "A file is being written": "Bir dosya yazılıyor",
    "A command succeeds": "Bir komut başarılı oluyor",
    "Host (blank = this service’s machine)": "Sunucu (boş = bu servisin makinesi)",
    "Expect status (0 = any 2xx/3xx)": "Beklenen durum (0 = herhangi bir 2xx/3xx)",
    "Must contain (optional)": "İçermeli (isteğe bağlı)",
    "Written within (seconds)": "Şu süre içinde yazılmış (saniye)",
    "Expect exit code": "Beklenen çıkış kodu",
    "File": "Dosya",
    "Command": "Komut",
    "cannot reach the file on this machine": "bu makinedeki dosyaya erişemiyor",
    "cannot run a command on another Windows machine — that needs something we do not have "
    "there yet":
        "başka bir Windows makinesinde komut çalıştıramıyor — bunun için orada henüz sahip "
        "olmadığımız bir şey gerekiyor",
    "No checks yet. Use Add check — until then this service is judged only by whether "
    "Windows says it is running.":
        "Henüz kontrol yok. Kontrol ekle'yi kullanın — o zamana kadar bu servis yalnızca "
        "Windows'un çalışıyor demesine göre değerlendirilir.",
    "1 CHECK": "1 KONTROL",
    "{count} CHECKS · ALL MUST PASS": "{count} KONTROL · HEPSİ GEÇMELİ",
    "Include this check": "Bu kontrolü dahil et",
    "gives up after {seconds}s": "{seconds} sn sonra vazgeçer",
    "Double-click to edit": "Düzenlemek için çift tıklayın",
    "Give up after (seconds)": "Vazgeçme süresi (saniye)",
    "Accept any certificate": "Herhangi bir sertifikayı kabul et",
    "For an internal server with its own certificate.":
        "Kendi sertifikası olan bir iç sunucu için.",
    "Health check": "Sağlık kontrolü",

    # ── and what the checks have found ───────────────────────────────────
    "Nothing is being checked yet.": "Henüz hiçbir şey kontrol edilmiyor.",
    "Watching is switched off — the checks below are kept but never run.":
        "İzleme kapalı — aşağıdaki kontroller korunuyor ama hiç çalıştırılmıyor.",
    "{service} is {state}.": "{service} {state}.",
    "Checks only run while it is running — a stopped service isn't unhealthy, it is "
    "stopped.":
        "Kontroller yalnızca çalışırken yapılır — durmuş bir servis sağlıksız değildir, "
        "durmuştur.",
    "Not checked yet — {why}": "Henüz kontrol edilmedi — {why}",
    "waiting for it to settle": "yerleşmesi bekleniyor",
    "passed": "geçti",
    "failed ({count} in a row)": "başarısız (üst üste {count})",
    "Last checked {when} — {outcome}": "Son kontrol {when} — {outcome}",
    "Next check {when}": "Sonraki kontrol {when}",
    "Next check {when}, in {seconds}s": "Sonraki kontrol {when}, {seconds} sn içinde",
    "Currently: not responding": "Şu an: cevap vermiyor",
    "Currently: responding": "Şu an: cevap veriyor",
    "Currently: no verdict yet": "Şu an: henüz karar yok",
    "Restarts are limited to one every {seconds}s.":
        "Yeniden başlatmalar {seconds} sn'de bir ile sınırlı.",

    # ── the History page ─────────────────────────────────────────────────
    # The columns, the ranges and the triggers are class constants in history.py, translated
    # where they are drawn. The *values* beside the trigger names are not translated: they are
    # what an engine wrote into the file, and the panel reading them may be reading in another
    # language — so the column looks the value up in this same table instead.
    "Time": "Zaman",
    "Service": "Servis",
    "Event": "Olay",
    "Detail": "Ayrıntı",
    "Source": "Kaynak",
    "Asked by": "İsteyen",
    "Last hour": "Son bir saat",
    "Last 8 hours": "Son 8 saat",
    "Last 24 hours": "Son 24 saat",
    "Last 7 days": "Son 7 gün",
    "Last 30 days": "Son 30 gün",
    "Everything": "Her şey",
    "Any trigger": "Her tetikleyici",
    "You, from the panel": "Siz, panelden",
    "Scheduled trigger": "Zamanlanmış tetikleyici",
    "Stack run": "Grup çalıştırma",
    "Watchdog": "Gözcü",
    "Outside this app": "Bu uygulamanın dışından",
    "Windows event log": "Windows olay kaydı",
    "Record state changes": "Durum değişimlerini kaydet",
    "All services": "Tüm servisler",
    "Clear filters": "Filtreleri temizle",
    "Back to all services, the last 24 hours, any trigger.":
        "Tüm servisler, son 24 saat, her tetikleyici haline dön.",
    "Full detail": "Tüm ayrıntı",
    "Every state the SCM reported, including the halfway ones. Off, a restart reads as "
    "“restart requested” then “Running” instead of four rows saying the same thing. Nothing "
    "is left out of the file either way.":
        "SCM'in bildirdiği her durum, aradaki adımlar dahil. Kapalıyken bir yeniden başlatma, "
        "aynı şeyi söyleyen dört satır yerine “yeniden başlatma istendi” ve ardından "
        "“Çalışıyor” olarak okunur. Her iki halde de dosyadan hiçbir şey çıkarılmaz.",
    "Merge what Windows recorded about these services — the SCM's \"terminated "
    "unexpectedly\", and errors the service itself logged. This is usually where the reason "
    "is.":
        "Windows'un bu servisler hakkında kaydettiklerini de katar — SCM'in \"beklenmedik "
        "şekilde sona erdi\" kaydı ve servisin kendi yazdığı hatalar. Sebep genelde buradadır.",
    "Refresh": "Yenile",
    "Export CSV…": "CSV olarak aktar…",
    "Open folder": "Klasörü aç",
    "Kept by the hub at  {url}": "Hub tarafından şurada tutuluyor:  {url}",
    "not written yet": "henüz yazılmadı",
    "Written to  {path}": "Şuraya yazılıyor:  {path}",
    "Cannot write the history — {why}. Is Service Officer running as administrator?":
        "Geçmiş yazılamıyor — {why}. Service Officer yönetici olarak mı çalışıyor?",
    "Could not open\n{folder}\n\n{why}": "Açılamadı\n{folder}\n\n{why}",
    "1 entry": "1 kayıt",
    "{count} entries": "{count} kayıt",
    "{count} from the Windows event log": "{count} tanesi Windows olay kaydından",
    "Nothing recorded yet.": "Henüz bir şey kaydedilmedi.",
    "Export history": "Geçmişi aktar",
    "For Excel (*.csv)": "Excel için (*.csv)",
    "Plain comma-separated (*.csv)": "Düz virgülle ayrılmış (*.csv)",
    "Exported {count} rows to\n{path}": "{count} satır şuraya aktarıldı:\n{path}",
    "Export failed:\n{why}": "Aktarma başarısız:\n{why}",

    # ── Stacks ───────────────────────────────────────────────────────────
    "New stack…": "Yeni grup…",
    "New stack": "Yeni grup",
    "Delete": "Sil",
    "Delete stack": "Grubu sil",
    "A stack with that name already exists.": "Bu adda bir grup zaten var.",
    'Delete "{name}"?': '"{name}" silinsin mi?',
    "Name": "Ad",
    "Name:": "Ad:",
    "no steps yet": "henüz adım yok",
    "No steps yet — add the first service to start.":
        "Henüz adım yok — başlatılacak ilk servisi ekleyin.",
    "Each step starts, then waits before the next one begins. Some services report Running "
    "before they can actually serve, so a fixed wait is sometimes the honest answer. Drag a "
    "step by its handle to change the order.":
        "Her adım servisi başlatır, sonra bir sonrakine geçmeden önce bekler. Bazı servisler "
        "gerçekten hizmet verebilmeden önce Çalışıyor der, o yüzden sabit bir bekleme bazen "
        "en dürüst cevaptır. Sırayı değiştirmek için bir adımı tutamacından sürükleyin.",
    "Show in the tray panel": "Tepsi panelinde göster",
    "Offer this stack with a Run button under the services, next to where you notice a "
    "problem.":
        "Bu grubu, problemi fark ettiğiniz yerin yanında, servislerin altında bir Çalıştır "
        "butonuyla sun.",
    "Add step…": "Adım ekle…",
    "Add step": "Adım ekle",
    "Which service?": "Hangi servis?",
    "Remove step": "Adımı kaldır",
    "Test run": "Deneme çalıştırması",
    "Add the services on the Services page first.":
        "Önce Servisler sayfasından servisleri ekleyin.",
    "What this step does to the service. The stack runs these in order, so it reads as a "
    "script.":
        "Bu adımın servise ne yaptığı. Grup bunları sırayla çalıştırır, yani bir betik gibi "
        "okunur.",
    "this service — verified {state}, up to {seconds}s":
        "bu servisi — {state} olduğu doğrulanır, en fazla {seconds} sn",
    "this service, then wait": "bu servisi, sonra bekle",
    "until applied": "uygulanana kadar",
    "a fixed": "sabit süre",
    "until applied — wait until this service is {state}.":
        "uygulanana kadar — bu servis {state} olana kadar bekle.",
    "a fixed — wait a set time, whatever it reports.":
        "sabit süre — ne derse desin belirli bir süre bekle.",
    "Extra pause once it reports Running, before the next step starts. Many services need a "
    "moment more.":
        "Çalışıyor dedikten sonra, bir sonraki adım başlamadan önceki ek bekleme. Çoğu servis "
        "biraz daha zamana ihtiyaç duyar.",
    "Wait exactly this long, whatever the service reports.":
        "Servis ne derse desin tam bu kadar bekle.",
    "give up after": "vazgeçme süresi",
    "If it hasn't reached its target state by then, stop the run here — later steps are not "
    "attempted.":
        "O zamana kadar hedef durumuna ulaşmadıysa çalıştırmayı burada bitir — sonraki "
        "adımlar denenmez.",

    # ── the Schedule page ────────────────────────────────────────────────
    "New trigger…": "Yeni tetikleyici…",
    "New trigger": "Yeni tetikleyici",
    "Delete trigger": "Tetikleyiciyi sil",
    "A trigger with that name already exists.": "Bu adda bir tetikleyici zaten var.",
    "off": "kapalı",
    "RECENT EXECUTIONS": "SON ÇALIŞTIRMALAR",
    "What": "Ne",
    "Outcome": "Sonuç",
    "Took": "Süre",
    "Enabled": "Etkin",
    "WHEN": "NE ZAMAN",
    "Windows starts": "Windows başlayınca",
    "At a time of day": "Günün bir saatinde",
    "Wait": "Bekle",
    "at": "saat",
    "· then every": "· sonra her",
    "(leave 0 for once a day)": "(günde bir için 0 bırakın)",
    "on": "şu günlerde",
    # The day names are a class constant, translated where they are drawn.
    "Mon": "Pzt", "Tue": "Sal", "Wed": "Çar", "Thu": "Per", "Fri": "Cum",
    "Sat": "Cmt", "Sun": "Paz",
    "Leave every day unticked to mean every day. A trigger missed while the machine was "
    "asleep runs when it wakes, if it is less than half an hour late.":
        "Her gün anlamına gelmesi için tüm günleri işaretsiz bırakın. Makine kapalıyken "
        "kaçırılan bir tetikleyici, yarım saatten az gecikmişse makine uyandığında çalışır.",
    "ACTION": "EYLEM",
    "Run a stack": "Bir grup çalıştır",
    "Act on one service": "Tek bir servise uygula",
    "TELL ME": "BANA HABER VER",
    "Never": "Asla",
    "On success": "Başarılı olunca",
    "On failure": "Hata olunca",
    "When skipped": "Atlanınca",
    "On failure or skipped": "Hata olunca ya da atlanınca",
    "On success or failure": "Başarılı ya da hatalı olunca",
    "Every run, even skipped": "Her çalıştırmada, atlananlar dahil",
    "Skipped means there was nothing to do — asking a service to start when it is already "
    "running.":
        "Atlandı, yapılacak bir şey olmadığı anlamına gelir — zaten çalışan bir servisten "
        "başlamasını istemek gibi.",
    "Run now": "Şimdi çalıştır",
    "In words: {summary}": "Kelimelerle: {summary}",
    # A strftime pattern, not a sentence: what is translated is the *order*, which differs.
    # The day and month names themselves come from the C library.
    "%a %d %b, %H:%M": "%d %b %a, %H:%M",
    "Next run: {when}": "Sonraki çalıştırma: {when}",
    "Next run: the next time Windows starts":
        "Sonraki çalıştırma: Windows'un bir sonraki açılışı",
    "Next run: never — this trigger is switched off":
        "Sonraki çalıştırma: asla — bu tetikleyici kapalı",

    # ── the tray and its hover card ──────────────────────────────────────
    "Service Management Panel": "Servis Yönetim Paneli",
    # services.msc, which Turkish Windows calls Hizmetler.
    "Open Services": "Hizmetler'i aç",
    "No services configured": "Yapılandırılmış servis yok",
    "{running}/{total} running": "{running}/{total} çalışıyor",
    # Follows the count above: "8/9 çalışıyor, 1 tanesine dikkat".
    ", {count} to watch": ", {count} tanesine dikkat",

    # ── the flyout ───────────────────────────────────────────────────────
    "No services configured.\nAdd some from Settings.":
        "Yapılandırılmış servis yok.\nAyarlar'dan ekleyin.",
    "Pin the panel open, so clicking elsewhere doesn't close it.":
        "Paneli açık sabitle; başka bir yere tıklamak onu kapatmasın.",
    "Pinned — stays open until you close it.\nClick to unpin.":
        "Sabitlendi — siz kapatana kadar açık kalır.\nSabitlemeyi kaldırmak için tıklayın.",

    # ── the dashboard ────────────────────────────────────────────────────
    "Nothing is being monitored yet — add services on the Services page.":
        "Henüz hiçbir şey izlenmiyor — Servisler sayfasından servis ekleyin.",
    "no services": "servis yok",
    "{running} of {total} running": "{running}/{total} çalışıyor",
    "{count} running": "{count} çalışıyor",
    "{count} stopped": "{count} durduruldu",
    "{count} other": "{count} diğer",
    "{count} disabled in Windows": "{count} Windows'ta devre dışı",
    "{count} not responding": "{count} cevap vermiyor",
    "{count} starting": "{count} başlıyor",

    # ── a service row and the bulk bar ───────────────────────────────────
    "Include in a bulk action": "Toplu işleme dahil et",
    "Kill the process — for when Stop doesn't work":
        "İşlemi sonlandır — Durdur işe yaramadığında",
    "This service is disabled in Windows — enable it in services.msc before it can start.":
        "Bu servis Windows'ta devre dışı — başlatılabilmesi için önce services.msc'den "
        "etkinleştirin.",
    "The service is running, but its health checks are failing:":
        "Servis çalışıyor ama sağlık kontrolleri başarısız:",
    "no detail": "ayrıntı yok",
    "It has started; its health checks have not passed yet.":
        "Başladı; sağlık kontrolleri henüz geçmedi.",
    "Health checks pass — {detail}": "Sağlık kontrolleri geçiyor — {detail}",
    "Add steps in Settings": "Ayarlar'dan adım ekleyin",
    "Click to show these services": "Bu servisleri göstermek için tıklayın",
    "Click to fold this group away": "Bu grubu daraltmak için tıklayın",
    # {action} is the button's own word, so the sentence is built around it.
    "{action} every selected service": "Seçili her servis için: {action}",
    "Unselect everything": "Tüm seçimi kaldır",
    "Drag to reorder": "Sıralamak için sürükleyin",

    # ── the service picker ───────────────────────────────────────────────
    "Add services": "Servis ekle",
    "Pick the services to monitor": "İzlenecek servisleri seçin",
    "Search by display name or service name. Ctrl/Shift-click to choose several.":
        "Görünen ada ya da servis adına göre arayın. Birden fazlasını seçmek için "
        "Ctrl/Shift ile tıklayın.",
    "Reading the services on {machine}…": "{machine} üzerindeki servisler okunuyor…",
    "Could not list the services on {machine}.":
        "{machine} üzerindeki servisler listelenemedi.",
    "This computer's service manager did not answer.":
        "Bu bilgisayarın servis yöneticisi cevap vermedi.",
    "{machine} did not answer over SSH. Check the account, the password or key, and that "
    "the host key matches.":
        "{machine} SSH üzerinden cevap vermedi. Hesabı, parolayı ya da anahtarı ve sunucu "
        "anahtarının eşleştiğini kontrol edin.",
    "{machine} did not answer. It may be switched off, or its firewall may be blocking "
    "Remote Service Management — the rule Windows needs for another computer to list "
    "services.":
        "{machine} cevap vermedi. Kapalı olabilir ya da güvenlik duvarı Remote Service "
        "Management kuralını engelliyor olabilir — Windows'un başka bir bilgisayarın "
        "servisleri listeleyebilmesi için gereken kural bu.",

    # ── Services ─────────────────────────────────────────────────────────
    "Grouped by category, in the order the tray flyout and the dashboard show them. Drag a "
    "service to move it, or onto another heading to file it there. Open one to set how it "
    "should recover when it stops on its own.":
        "Kategoriye göre gruplanır; tepsi paneli ve pano da aynı sırayı gösterir. Bir "
        "servisi taşımak için sürükleyin, başka bir başlığın üzerine bırakırsanız oraya "
        "konur. Kendi kendine durduğunda nasıl kurtarılacağını ayarlamak için birini açın.",
    "Add services…": "Servis ekle…",
    "Category…": "Kategori…",
    "empty — drag a service here": "boş — buraya bir servis sürükleyin",
    "no automatic recovery": "otomatik kurtarma yok",
    "recovers automatically, up to {count} attempts":
        "otomatik kurtarılır, en fazla {count} deneme",
    "recovers automatically, unlimited attempts": "otomatik kurtarılır, sınırsız deneme",
    "On which machine?": "Hangi makinede?",
    "Select a service in the list first.": "Önce listeden bir servis seçin.",
    "Remove service": "Servisi kaldır",
    'Stop monitoring "{name}"?': '"{name}" artık izlenmesin mi?',
    "Stop monitoring these {count} services?": "Bu {count} servis artık izlenmesin mi?",
    "Select one or more services in the list first.":
        "Önce listeden bir ya da daha fazla servis seçin.",
    "New category…": "Yeni kategori…",
    "Put this service under:": "Bu servisi şunun altına koy:",
    "Put these {count} services under:": "Bu {count} servisi şunun altına koy:",
    "New category": "Yeni kategori",
    "Select one service to open.": "Açmak için tek bir servis seçin.",

    # ── Categories ───────────────────────────────────────────────────────
    "Add category…": "Kategori ekle…",
    "Rename…": "Yeniden adlandır…",
    "Services you haven't filed anywhere are listed together under “{heading}”, so nothing "
    "goes missing.":
        "Hiçbir kategoriye koymadığınız servisler “{heading}” altında birlikte listelenir; "
        "böylece hiçbiri kaybolmaz.",
    ", and {count} more": " ve {count} tane daha",
    "nothing filed here yet": "henüz burada bir şey yok",
    "Category name:": "Kategori adı:",
    "Add category": "Kategori ekle",
    "That category already exists.": "Bu kategori zaten var.",
    "Rename category": "Kategoriyi yeniden adlandır",
    'Remove the category "{name}"?': '"{name}" kategorisi kaldırılsın mı?',
    "Its {count} service(s) stay, listed under “{heading}”.":
        "İçindeki {count} servis kalır ve “{heading}” altında listelenir.",
    "Remove category": "Kategoriyi kaldır",

    # ── Hub ▸ this panel's hub ───────────────────────────────────────────
    "empty — this computer does the work itself": "boş — işi bu bilgisayar kendisi yapar",
    "Apply and restart": "Uygula ve yeniden başlat",
    "No address: this computer will do the work itself.":
        "Adres yok: işi bu bilgisayar kendisi yapacak.",
    "{url} did not answer: {why}": "{url} cevap vermedi: {why}",
    "{name} answered — version {version}": "{name} cevap verdi — sürüm {version}",
    # Follows the sentence above.
    ", which is not this panel's {mine}. A client and its hub have to be the same release; "
    "update one of them.":
        ", ama bu panelin sürümü {mine}. Bir istemci ve hub'ı aynı sürüm olmalı; "
        "birini güncelleyin.",
    "Could not save this client's settings.": "Bu istemcinin ayarları kaydedilemedi.",
    "not pinned yet": "henüz sabitlenmedi",
    "Reading {url}": "Okunan: {url}",
    "Certificate  {fingerprint}": "Sertifika  {fingerprint}",
    "Stored already — type a new one only to replace it.":
        "Zaten kayıtlı — yalnızca değiştirmek için yenisini yazın.",
    "Needed. On the hub: ServiceOfficerHub.exe client add <a name for this computer>":
        "Gerekli. Hub üzerinde: ServiceOfficerHub.exe client add <bu bilgisayar için bir ad>",
    "No hub: this computer watches its own services, and stops watching when this app is "
    "closed.":
        "Hub yok: bu bilgisayar kendi servislerini izler ve bu uygulama kapanınca izlemeyi "
        "bırakır.",

    # ── Clients ──────────────────────────────────────────────────────────
    "Client added": "İstemci eklendi",
    "{name} — token issued": "{name} — token üretildi",
    "This is the only time it is shown. The hub keeps a hash of it, not the token, so it "
    "cannot be looked up again — issue another if it is lost.":
        "Yalnızca bu kez gösterilir. Hub token'ın kendisini değil, özetini (hash) saklar; "
        "bu yüzden sonradan bakılamaz — kaybolursa yenisini üretin.",
    "Run this on that computer": "Bunu o bilgisayarda çalıştırın",
    "It should see this certificate:  {fingerprint}": "Şu sertifikayı görmeli:  {fingerprint}",
    "That name already had a token. The old one has stopped working.":
        "Bu adın zaten bir token'ı vardı. Eskisi artık çalışmıyor.",
    "Copy token": "Token'ı kopyala",
    "Copy command": "Komutu kopyala",
    "Done": "Tamam",
    "Add a client": "İstemci ekle",
    "whose it is, or why it exists": "kimin olduğu ya da neden var olduğu",
    "The name is how this client appears here and in the hub's log. It is not checked "
    "against anything — the machine reports its own host name when it connects, and that "
    "appears beside it.":
        "Ad, bu istemcinin burada ve hub'ın günlüğünde nasıl görüneceğidir. Hiçbir şeyle "
        "karşılaştırılmaz — makine bağlandığında kendi adını bildirir ve o da yanında "
        "görünür.",
    "Cancel": "İptal",
    "Issue token": "Token üret",
    "Add client…": "İstemci ekle…",
    "Revoke": "Erişimi kaldır",
    "No hub: there is nobody to pair with this computer.":
        "Hub yok: bu bilgisayarla eşleştirilecek kimse yok.",
    "Asking the hub…": "Hub'a soruluyor…",
    "Could not read the hub's client list: {why}": "Hub'ın istemci listesi okunamadı: {why}",
    "never used": "hiç kullanılmadı",
    "this hub": "bu hub",
    # {where} is an address or "bu hub", so it goes last where no suffix is needed.
    "1 client may read {where}.": "1 istemci okuyabilir: {where}.",
    "{count} clients may read {where}.": "{count} istemci okuyabilir: {where}.",
    "Nothing is paired with this hub yet.": "Bu hub ile henüz hiçbir şey eşleştirilmedi.",
    "The hub would not issue a token: {why}": "Hub token üretmedi: {why}",
    "Stop {name} from reading this hub?\n\nIt takes effect immediately. Issuing another "
    "token is the only way back.":
        "{name} bu hub'ı artık okuyamasın mı?\n\nHemen geçerli olur. Geri dönmenin tek yolu "
        "yeni bir token üretmektir.",
    "Could not revoke {name}: {why}": "{name} erişimi kaldırılamadı: {why}",
    "{name} was not paired with this hub.": "{name} bu hub ile eşleştirilmemişti.",

    # ── General ──────────────────────────────────────────────────────────
    "Start automatically when Windows starts": "Windows açılınca otomatik başlat",
    "A service stopped unexpectedly": "Bir servis beklenmedik şekilde durdu",
    "Recovery succeeded": "Kurtarma başarılı oldu",
    "Recovery gave up": "Kurtarma vazgeçti",
    "Installed in  {path}": "Kurulum yeri  {path}",
    "Copy build details": "Sürüm ayrıntılarını kopyala",
    "Installed in: {path}": "Kurulum yeri: {path}",
    "Data in: {path}": "Veri yeri: {path}",
    "Machine: {host} ({address})": "Makine: {host} ({address})",

    # ── a service's state, translated where it becomes words ─────────────
    # The values themselves stay English: they are compared, stored in history and sent
    # between a hub and its clients. Only the chip and the hover card translate them.
    "Running": "Çalışıyor",
    "Stopped": "Durduruldu",
    "Paused": "Duraklatıldı",
    "Not Found": "Bulunamadı",
    "Unknown": "Bilinmiyor",
    "Starting": "Başlıyor",
    "Stopping": "Duruyor",
    "Resuming": "Sürdürülüyor",
    "Pausing": "Duraklatılıyor",
    "Starting…": "Başlıyor…",
    "starting…": "başlıyor…",
    "Not responding": "Cevap vermiyor",
    "not responding": "cevap vermiyor",
    "Disabled": "Devre dışı",
    "Stopping…": "Durduruluyor…",
    "Restarting…": "Yeniden başlatılıyor…",
    "Killing…": "Sonlandırılıyor…",
    # Stored in English in services.json and translated on the way to the screen.
    "This computer": "Bu bilgisayar",
    "No category": "Kategorisiz",

    # ── notifications and confirmations ──────────────────────────────────
    "Service Officer — Service Management Panel": "Service Officer — Servis Yönetim Paneli",
    "Lost the connection to the hub. Trying again.":
        "Hub ile bağlantı koptu. Yeniden deneniyor.",
    "{name} is running but not responding.": "{name} çalışıyor ama cevap vermiyor.",
    "{name} is responding again.": "{name} yeniden cevap veriyor.",
    "{name} is running again.": "{name} yeniden çalışıyor.",
    "this computer's own services": "bu bilgisayarın kendi servisleri",
    "Saved. Service Officer will read {where} after a restart.\n\nRestart it now?":
        "Kaydedildi. Service Officer yeniden başladıktan sonra şunu okuyacak: {where}."
        "\n\nŞimdi yeniden başlatılsın mı?",
    "disabled in Windows": "Windows'ta devre dışı",
    "on {machine}, not this computer": "{machine} üzerinde, bu bilgisayarda değil",
    "already running": "zaten çalışıyor",
    "already stopped": "zaten durmuş",
    "Start this service?": "Bu servis başlatılsın mı?",
    "Start 1 service": "1 servisi başlat",
    "Start these {count} services?": "Bu {count} servis başlatılsın mı?",
    "Start {count} services": "{count} servisi başlat",
    "Stop this service?": "Bu servis durdurulsun mu?",
    "Stop 1 service": "1 servisi durdur",
    "Stop these {count} services?": "Bu {count} servis durdurulsun mu?",
    "Stop {count} services": "{count} servisi durdur",
    "Restart this service?": "Bu servis yeniden başlatılsın mı?",
    "Restart 1 service": "1 servisi yeniden başlat",
    "Restart these {count} services?": "Bu {count} servis yeniden başlatılsın mı?",
    "Restart {count} services": "{count} servisi yeniden başlat",
    "Kill the process of this service?": "Bu servisin işlemi sonlandırılsın mı?",
    "Kill the process of 1 service": "1 servisin işlemini sonlandır",
    "Kill the processes of these {count} services?":
        "Bu {count} servisin işlemleri sonlandırılsın mı?",
    "Kill the processes of {count} services": "{count} servisin işlemlerini sonlandır",
    "… and {count} more": "… ve {count} tane daha",
    "Each is killed outright, with no chance to shut down cleanly.":
        "Her biri, düzgünce kapanma fırsatı verilmeden anında sonlandırılır.",
    "Skipping:": "Atlananlar:",
    "{name} has no running process to kill.":
        "{name} servisinin sonlandırılacak çalışan bir işlemi yok.",
    "Kill process": "İşlemi sonlandır",
    "Terminate {name} (process {pid})?\n\nThe service is killed outright — it gets no "
    "chance to shut down cleanly. Use this when Stop has no effect.":
        "{name} (işlem {pid}) sonlandırılsın mı?\n\nServis anında sonlandırılır — düzgünce "
        "kapanma fırsatı olmaz. Durdur işe yaramadığında kullanın.",
    "A stack run is already in progress.": "Bir grup zaten çalışıyor.",

    # ── what this client says about its hub ──────────────────────────────
    # Worded on the client, so translated; whatever the hub itself sent stays as it came.
    "the hub's certificate has changed. It was {was} and is now {now}. Either that machine "
    "was rebuilt, or this is not the same hub — check before accepting it.":
        "hub'ın sertifikası değişti. Önceden {was} idi, şimdi {now}. Ya o makine yeniden "
        "kuruldu ya da bu aynı hub değil — kabul etmeden önce kontrol edin.",
    "the hub refused this token": "hub bu token'ı reddetti",
    "the hub has no such thing": "hub'da böyle bir şey yok",
    "somebody else saved first": "başka biri önce kaydetti",
    "the hub said {code}": "hub {code} döndürdü",
    "the hub sent an installer with no hash to check it against. Not running it.":
        "hub, doğrulanacak bir özet (hash) olmadan kurulum dosyası gönderdi. "
        "Çalıştırılmıyor.",
    "the installer does not match what the hub said it would be. Expected {wanted}…, got "
    "{found}…. Not running it.":
        "kurulum dosyası hub'ın bildirdiğiyle eşleşmiyor. Beklenen {wanted}…, gelen "
        "{found}…. Çalıştırılmıyor.",
    "the hub answered {code}": "hub {code} ile cevap verdi",
    "the hub refused the event stream: {code}": "hub olay akışını reddetti: {code}",

    # ── a health check, a stack and a trigger in words ───────────────────
    "No port set yet": "Henüz port belirtilmedi",
    "No URL set yet": "Henüz URL belirtilmedi",
    "No file set yet": "Henüz dosya belirtilmedi",
    "No command set yet": "Henüz komut belirtilmedi",
    "Not set up yet": "Henüz ayarlanmadı",
    "the service’s machine": "servisin makinesi",
    "something answers on {where}": "{where} üzerinde bir şey cevap veriyor",
    "{url} returns HTTP {status} containing “{body}”":
        "{url}, içinde “{body}” geçen HTTP {status} döndürüyor",
    "{url} returns HTTP {status}": "{url}, HTTP {status} döndürüyor",
    "{url} returns a success response containing “{body}”":
        "{url}, içinde “{body}” geçen başarılı bir cevap döndürüyor",
    "{url} returns a success response": "{url}, başarılı bir cevap döndürüyor",
    "it actually has a process": "gerçekten çalışan bir işlemi var",
    "{path} was written in the last {seconds}s": "{path} son {seconds} sn içinde yazılmış",
    "“{command}” succeeds": "“{command}” başarılı oluyor",
    "“{command}” succeeds (exit {code})": "“{command}” başarılı oluyor (çıkış kodu {code})",
    # Passive, so the service comes first and the verb last, as Turkish wants it.
    "start {name}": "{name} başlatılır",
    "stop {name}": "{name} durdurulur",
    "restart {name}": "{name} yeniden başlatılır",
    "verified running (up to {seconds}s)": "çalıştığı doğrulanır (en fazla {seconds} sn)",
    "verified stopped (up to {seconds}s)": "durduğu doğrulanır (en fazla {seconds} sn)",
    "then wait until applied + {grace}s, timeout {seconds}s":
        "sonra uygulanana kadar beklenir + {grace} sn, zaman aşımı {seconds} sn",
    "then wait until applied, timeout {seconds}s":
        "sonra uygulanana kadar beklenir, zaman aşımı {seconds} sn",
    "then wait {seconds}s, always": "sonra her durumda {seconds} sn beklenir",
    ", then every {every}": ", sonra her {every}",
    "when Windows starts, after {seconds}s": "Windows açıldığında, {seconds} sn sonra",
    "when Windows starts": "Windows açıldığında",
    "{days} at {time}": "{days}, saat {time}",
    "every day at {time}": "her gün saat {time}",
    "run “{stack}”": "“{stack}” çalıştırılır",
    "run a stack (none chosen)": "bir grup çalıştırılır (seçilmedi)",
    "no service chosen": "servis seçilmedi",

    # ── lists and tables ─────────────────────────────────────────────────
    "Search services…": "Servis ara…",
    "Select every service shown": "Gösterilen tüm servisleri seç",
    "Move this category up": "Bu kategoriyi yukarı taşı",
    "Move this category down": "Bu kategoriyi aşağı taşı",
    "Issued": "Üretilme",
    "Last used": "Son kullanım",

    # ── closing the panel ────────────────────────────────────────────────
    "Unsaved changes": "Kaydedilmemiş değişiklikler",
    "You have unsaved changes. Save them before closing?":
        "Kaydedilmemiş değişiklikleriniz var. Kapatmadan önce kaydedilsin mi?",
}
