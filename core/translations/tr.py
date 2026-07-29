"""Türkçe. The English sentence is the key — see core/i18n.py for why.

Nothing here is a machine translation of a shape: a sentence that explains *what to do* has
to still explain it, and the Turkish for "the hub is moving" has to leave somebody as sure of
what just happened as the English does.
"""

WORDS = {
    # ── Hub ▸ Serving ────────────────────────────────────────────────────
    "SERVING": "SUNUM",
    "This computer serves clients on port": "Bu bilgisayar istemcilere şu portta hizmet veriyor",
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
    "Group your services under headings — SAP, SQL, printing — so the dashboard and the tray "
    "panel can fold them away.":
        "Servislerinizi başlıklar altında toplayın — SAP, SQL, yazdırma — böylece pano ve "
        "tray paneli onları katlayabilir.",
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
