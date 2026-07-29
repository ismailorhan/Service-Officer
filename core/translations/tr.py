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
}
