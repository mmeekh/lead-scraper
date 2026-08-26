#!/usr/bin/env python3
"""Tersaneler ve denizcilik/kruvaziyer sirketlerini havuza ekler.

Iki ayri hedef grubu:
  TR-YARD  : Turkiye'deki tersaneler ve denizcilik sanayi (ihracatci, doviz
             kazanan sirketler; finans ekibi Ingilizce yaziyor)
  CRUISE   : kruvaziyer isletmeleri ve gemi personeli saglayan acenteler
             (gemide finans pozisyonlari: purser, financial controller)

E-postalar scrape.py tarafindan sitelerinden CIKARILIR, tahmin edilmez.
"""
from __future__ import annotations

from scrape import add_lead, db

TERSANELER = [
    # Tuzla / Istanbul
    ("rmkmarine.com.tr", "RMK Marine", "Tuzla"),
    ("sedefshipyard.com", "Sedef Shipyard", "Tuzla"),
    ("dearsan.com", "Dearsan Shipyard", "Tuzla"),
    ("anadolutersanesi.com.tr", "Anadolu Tersanesi", "Tuzla"),
    ("gemak.com", "Gemak Group", "Tuzla"),
    ("desan.com.tr", "Desan Shipyard", "Tuzla"),
    ("istanbulshipyard.com", "Istanbul Shipyard", "Tuzla"),
    ("kuzeystar.com", "Kuzey Star Shipyard", "Tuzla"),
    ("ceksan.com", "Ceksan Shipyard", "Tuzla"),
    ("selahmaritime.com", "Selah Maritime", "Tuzla"),
    ("torlak.com.tr", "Torlak Shipyard", "Tuzla"),
    # Yalova / Altinova
    ("tersan.com.tr", "Tersan Shipyard", "Yalova"),
    ("sefineshipyard.com", "Sefine Shipyard", "Yalova"),
    ("cemreshipyard.com", "Cemre Shipyard", "Yalova"),
    ("ozatashipyard.com", "Ozata Shipyard", "Yalova"),
    ("besiktasshipyard.com", "Besiktas Shipyard", "Yalova"),
    ("madencigemi.com", "Madenci Shipyard", "Yalova"),
    # Kocaeli / diger
    ("uzmar.com", "Uzmar Shipyard", "Kocaeli"),
    ("sanmar.com.tr", "Sanmar Shipyard", "Kocaeli"),
    ("medmarine.com.tr", "Med Marine", "Istanbul"),
    ("cicekgroup.com.tr", "Cicek Shipyard", "Kocaeli"),
    ("hatsan.com.tr", "Hat-San Shipyard", "Kocaeli"),
    ("tais.org.tr", "TAIS - Gemi Insa Sanayicileri Birligi", "Istanbul"),
    # denizcilik / armator
    ("arkasline.com.tr", "Arkas Line", "Izmir"),
    ("yasamaritime.com", "Yasa Holding Denizcilik", "Istanbul"),
    ("dituras.com.tr", "Ditas Deniz", "Istanbul"),
    ("statuemarine.com", "Statu Marine", "Istanbul"),
    ("gsdmarin.com.tr", "GSD Marin", "Istanbul"),
    ("cinerdenizcilik.com", "Ciner Denizcilik", "Istanbul"),
    ("beyazgemi.com.tr", "Beyaz Gemi", "Istanbul"),
]

KRUVAZIYER = [
    # kruvaziyer isletmecileri (kurumsal iletisim)
    ("msccruises.com", "MSC Cruises", "Cenevre"),
    ("costacruises.com", "Costa Cruises", "Cenova"),
    ("tuicruises.com", "TUI Cruises", "Hamburg"),
    ("aida.de", "AIDA Cruises", "Rostock"),
    ("hurtigruten.com", "Hurtigruten", "Oslo"),
    ("vikingcruises.com", "Viking Cruises", "Basel"),
    ("celestyalcruises.com", "Celestyal Cruises", "Limasol"),
    ("ponant.com", "Ponant", "Marsilya"),
    ("swanhellenic.com", "Swan Hellenic", "Valletta"),
    # gemi personeli / crewing acenteleri
    ("marlowturkey.com", "Marlow Navigation Turkey", "Istanbul"),
    ("vshipsturkey.com", "V.Ships Turkey", "Istanbul"),
    ("columbia-shipmanagement.com", "Columbia Shipmanagement", "Limasol"),
    ("anglo-eastern.com", "Anglo-Eastern Ship Management", "Hong Kong"),
    ("bernhard-schulte.com", "Bernhard Schulte Shipmanagement", "Limasol"),
    ("wallem.com", "Wallem Group", "Hong Kong"),
    ("oceanwidecrew.com", "Oceanwide Crew", "Rotterdam"),
    ("cti-crewing.com", "CTI Group Crewing", "Istanbul"),
]


def main() -> None:
    conn = db()
    yard = cruise = 0
    for domain, name, city in TERSANELER:
        if add_lead(conn, domain, name, city, "TR", "maritime-yard"):
            yard += 1
    for domain, name, city in KRUVAZIYER:
        if add_lead(conn, domain, name, city, "CRUISE", "maritime-cruise"):
            cruise += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    print(f"tersane/denizcilik: {yard} yeni | kruvaziyer: {cruise} yeni | havuz: {total}")


if __name__ == "__main__":
    main()
