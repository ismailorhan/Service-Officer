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
}
