#!/usr/bin/env python3
"""Vergi teknolojisi / muhasebe otomasyonu sirketlerini havuza ekler.

Bu firmalarda "muhasebe bilen + Python/VBA ile otomasyon yazan" profil
dogrudan urunun kendisi. Adaylar elle secildi; e-postalari scrape.py
tarafindan sitelerinden CIKARILACAK (tahmin yok).
"""
from __future__ import annotations

from scrape import add_lead, db

# (domain, firma adi, ulke) - e-fatura, vergi uyum, muhasebe otomasyonu
TARGETS = [
    # Almanya
    ("taxdoo.com", "Taxdoo", "DE"), ("candis.io", "Candis", "DE"),
    ("lucanet.com", "LucaNet", "DE"), ("sevdesk.de", "sevdesk", "DE"),
    ("lexware.de", "Lexware", "DE"), ("kontist.com", "Kontist", "DE"),
    ("getmyinvoices.com", "GetMyInvoices", "DE"), ("circula.com", "Circula", "DE"),
    ("moss.com", "Moss", "DE"), ("finway.de", "finway", "DE"),
    ("tacto.ai", "Tacto", "DE"), ("countx.com", "Countx", "DE"),
    ("hellotax.com", "hellotax", "DE"), ("norman.finance", "Norman", "DE"),
    ("papierkram.de", "Papierkram", "DE"), ("xentral.com", "Xentral", "DE"),
    ("agicap.com", "Agicap", "DE"), ("pliant.io", "Pliant", "DE"),
    # Hollanda
    ("klippa.com", "Klippa", "NL"), ("yukiworks.nl", "Yuki", "NL"),
    ("storecove.com", "Storecove", "NL"), ("staxxer.com", "Staxxer", "NL"),
    ("recommand.eu", "Recommand", "NL"), ("taxmarc.com", "Taxmarc", "NL"),
    ("vatinstitute.com", "VAT Institute", "NL"), ("visionplanner.com", "Visionplanner", "NL"),
    ("jortt.nl", "Jortt", "NL"), ("moneybird.nl", "Moneybird", "NL"),
    ("e-boekhouden.nl", "e-Boekhouden", "NL"), ("snelstart.nl", "SnelStart", "NL"),
    # Irlanda
    ("fonoa.com", "Fonoa", "IE"), ("accountsiq.com", "AccountsIQ", "IE"),
    ("surf-accounts.com", "Surf Accounts", "IE"), ("bigredbook.com", "Big Red Book", "IE"),
    # Belcika
    ("silverfin.com", "Silverfin", "BE"), ("unifiedpost.com", "Unifiedpost", "BE"),
    ("accountable.eu", "Accountable", "BE"), ("teamleader.eu", "Teamleader", "BE"),
    # Polonya
    ("comarch.com", "Comarch", "PL"), ("infakt.pl", "inFakt", "PL"),
    ("fakturownia.pl", "Fakturownia", "PL"), ("symfonia.pl", "Symfonia", "PL"),
    ("taxxo.pl", "Taxxo", "PL"), ("wfirma.pl", "wFirma", "PL"),
    # Avusturya / Isvicre
    ("bmd.com", "BMD Systemhaus", "AT"), ("rzlsoftware.at", "RZL Software", "AT"),
    ("abacus.ch", "Abacus Research", "CH"), ("bexio.com", "bexio", "CH"),
    ("accounto.ch", "Accounto", "CH"), ("runmyaccounts.ch", "Run my Accounts", "CH"),
    ("klara.ch", "KLARA", "CH"),
    # Ispanya / Portekiz / Italya
    ("holded.com", "Holded", "ES"), ("getquipu.com", "Quipu", "ES"),
    ("marosavat.com", "Marosa VAT", "ES"), ("declarando.es", "Declarando", "ES"),
    ("invoicexpress.com", "InvoiceXpress", "PT"), ("vendus.pt", "Vendus", "PT"),
    ("cegid.com", "Cegid", "PT"), ("fattureincloud.it", "Fatture in Cloud", "IT"),
    # Kuzey / Baltik / Orta Avrupa
    ("fortnox.se", "Fortnox", "SE"), ("pagero.com", "Pagero", "SE"),
    ("basware.com", "Basware", "FI"), ("procountor.fi", "Procountor", "FI"),
    ("e-conomic.dk", "e-conomic", "DK"), ("dinero.dk", "Dinero", "DK"),
    ("merit.ee", "Merit Software", "EE"), ("directo.ee", "Directo", "EE"),
    ("taxually.com", "Taxually", "HU"), ("billingo.hu", "Billingo", "HU"),
    ("fakturoid.cz", "Fakturoid", "CZ"), ("solitea.com", "Solitea", "CZ"),
    ("superfaktura.sk", "SuperFaktura", "SK"), ("1stopvat.com", "1stopVAT", "LT"),
]


def main() -> None:
    conn = db()
    added = 0
    for domain, name, country in TARGETS:
        if add_lead(conn, domain, name, "", country, "taxtech-secili"):
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"{added} yeni vergi-teknoloji firmasi eklendi "
          f"({len(TARGETS) - added} zaten havuzdaydi) | havuz: {total}")


if __name__ == "__main__":
    main()
