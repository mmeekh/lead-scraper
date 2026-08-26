#!/usr/bin/env python3
"""Uluslararasi muteahhitlik ve muhendislik sirketleri (BL Harbert profili).

Adayin BL Harbert International'daki deneyimi (yurt disi projede muhasebe,
cari hesaplar, GAAP kapanislari, doviz hesaplamalari) dogrudan bu sektore
oturuyor. Turk muteahhitler ayrica yurt disi projelerinde Turk finans
personeli calistiriyor ve vize surecini kendileri yurutuyor.
"""
from __future__ import annotations

from scrape import add_lead, db

# Turk uluslararasi muteahhitler - yurt disi proje ofisleri var
TURK_MUTEAHHIT = [
    ("enka.com", "ENKA Insaat", "Istanbul"),
    ("ronesans.com", "Ronesans Holding", "Ankara"),
    ("limak.com.tr", "Limak Insaat", "Ankara"),
    ("tekfen.com.tr", "Tekfen Insaat", "Istanbul"),
    ("tavconstruction.com", "TAV Construction", "Istanbul"),
    ("yapimerkezi.com.tr", "Yapi Merkezi", "Istanbul"),
    ("gama.com.tr", "GAMA Holding", "Ankara"),
    ("nurolinsaat.com.tr", "Nurol Insaat", "Ankara"),
    ("polimeks.com", "Polimeks Insaat", "Istanbul"),
    ("cengizholding.com.tr", "Cengiz Insaat", "Istanbul"),
    ("kalyon.com", "Kalyon Insaat", "Istanbul"),
    ("icictas.com", "IC Ictas Insaat", "Istanbul"),
    ("kolin.com.tr", "Kolin Insaat", "Ankara"),
    ("makyol.com.tr", "Makyol Insaat", "Istanbul"),
    ("stfa.com", "STFA Group", "Istanbul"),
    ("alarko.com.tr", "Alarko Holding", "Istanbul"),
    ("dogusinsaat.com.tr", "Dogus Insaat", "Istanbul"),
    ("onurgroup.com", "Onur Taahhut", "Ankara"),
    ("mapa.com.tr", "MAPA Insaat", "Ankara"),
    ("summa.com.tr", "Summa Turizm Yatirimciligi", "Ankara"),
    ("ozaltin.com.tr", "Ozaltin Insaat", "Ankara"),
    ("gulermak.com", "Gulermak", "Ankara"),
    ("calik.com", "Calik Holding", "Istanbul"),
    ("tekar.com.tr", "Tekar Insaat", "Ankara"),
]

# uluslararasi muhendislik/muteahhitlik (Avrupa ve Korfez'de proje ofisi olan)
ULUSLARARASI = [
    ("blharbert.com", "BL Harbert International", "US/uluslararasi"),
    ("hill-intl.com", "Hill International", "uluslararasi"),
    ("dar.com", "Dar Al-Handasah", "uluslararasi"),
    ("khatibalami.com", "Khatib & Alami", "Beyrut"),
    ("acwapower.com", "ACWA Power", "Riyad"),
    ("archirodon.net", "Archirodon Group", "Atina/Dubai"),
    ("consolidatedcontractors.com", "Consolidated Contractors Company", "Atina"),
    ("orascom.com", "Orascom Construction", "Kahire/Dubai"),
    ("depa.com", "Depa Group", "Dubai"),
    ("alec.ae", "ALEC Engineering", "Dubai"),
    ("ssh-international.com", "SSH International", "Kuveyt/Dubai"),
    ("parsons.com", "Parsons Middle East", "Dubai"),
]


def main() -> None:
    conn = db()
    tr = intl = 0
    for domain, name, city in TURK_MUTEAHHIT:
        if add_lead(conn, domain, name, city, "TR", "contractor-tr"):
            tr += 1
    for domain, name, city in ULUSLARARASI:
        if add_lead(conn, domain, name, city, "AE", "contractor-intl"):
            intl += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"Turk muteahhit: {tr} yeni | uluslararasi: {intl} yeni | havuz: {total}")


if __name__ == "__main__":
    main()
