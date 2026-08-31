#!/usr/bin/env python3
"""harvest_overture icin birim testler.

Sahte places.sqlite3 tempfile'da kurulur, hedef leads DB'si :memory: olur;
canli leads.sqlite3 ve gercek Overture dosyasina ASLA dokunulmaz.
Calistirma: python3 -m unittest test_overture -v
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import harvest_overture


def memory_leads_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE leads (
            domain TEXT PRIMARY KEY,
            name TEXT,
            city TEXT,
            country TEXT,
            source TEXT,
            status TEXT DEFAULT 'pending',
            email TEXT,
            all_mails TEXT,
            note TEXT,
            checked_at TEXT
        )
        """
    )
    return conn


FIXTURE_ROWS = [
    # id, name, email, website, domain, category, city, country, confidence, city_key, sector
    # 1) alakali kategori, yuksek confidence -> girer
    ("p1", "Acme Steuerberatung", None, "https://www.acme-tax.de", "www.acme-tax.de",
     "accountant", "Berlin", "DE", 0.97, "berlin", "Muhasebe & Vergi"),
    # 2) alakasiz kategori (restoran), sektoru bile alakali olsa girmez
    ("p2", "Doner Palast", None, "https://doener-palast.de", "doener-palast.de",
     "restaurant", "Berlin", "DE", 0.99, "berlin", "Danışmanlık"),
    # 3) dusuk confidence -> elenir
    ("p3", "Low Conf GmbH", None, "https://lowconf.de", "lowconf.de",
     "software_development", "Hamburg", "DE", 0.30, "hamburg", "Yazılım & SaaS"),
    # 4) freemail domain + e-posta -> anahtar e-postanin kendisi olur
    ("p4", "Kucuk Ofis Muhasebe", "kucuk.ofis@gmail.com", "", "gmail.com",
     "tax_services", "Koeln", "DE", 0.90, "koeln", "Muhasebe & Vergi"),
    # 5) 1 numaranin www'suz dupesi -> dedupe, ikinci kez sayilmaz
    ("p5", "Acme Tax Dup", None, "acme-tax.de", "acme-tax.de",
     "tax_services", "Berlin", "DE", 0.88, "berlin", "Muhasebe & Vergi"),
    # 6) kategori BOS ama sektor alakali -> kurtarilir, girer
    ("p6", "ERP Haus", None, "https://erp-haus.de", "erp-haus.de",
     None, "Muenchen", "DE", 0.85, "muenchen", "ERP & Raporlama"),
    # 7) ciplak freemail hostu (e-postasiz) -> anahtar uretilemez, atlanir
    ("p7", "Mail Host Bozuk", None, "https://web.de", "web.de",
     "accountant", "Berlin", "DE", 0.80, "berlin", "Muhasebe & Vergi"),
    # 8) sitesi platform sayfasi (youtube) -> lead domaini olamaz, atlanir
    ("p8", "Platform Sayfali Firma", None, "https://www.youtube.com/@firma",
     "youtube.com", "credit_union", "Dublin", "DE", 0.95, "dublin",
     "Finansal Teknoloji"),
]


class OvertureHarvestTests(unittest.TestCase):
    def setUp(self):
        handle, self.places_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        conn = sqlite3.connect(self.places_path)
        conn.execute(
            "CREATE TABLE places(id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
            "email VARCHAR, website VARCHAR, domain VARCHAR, category VARCHAR, "
            "city VARCHAR, country VARCHAR, confidence DOUBLE, city_key TEXT, "
            "sector TEXT)")
        conn.executemany(
            "INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?)", FIXTURE_ROWS)
        conn.commit()
        conn.close()
        # fixture da salt okunur acilir; uretim kodundaki yol birebir
        self.places = harvest_overture.places_ro(self.places_path)
        self.leads = memory_leads_db()

    def tearDown(self):
        self.places.close()
        self.leads.close()
        os.unlink(self.places_path)

    def test_dry_run_filters_and_counts_without_writing(self):
        stats = harvest_overture.harvest_country(
            self.places, None, "DE", target=100, source_label="test",
            min_confidence=0.5, dry_run=True)
        # girenler: acme-tax.de, e-posta anahtarli freemail, erp-haus.de
        # elenenler: restoran, dusuk confidence, dupe, ciplak freemail hostu
        self.assertEqual(stats["eligible"], 3)
        self.assertEqual(stats["added"], 0)
        keys = {p[0] for p in stats["preview"]}
        self.assertEqual(
            keys, {"acme-tax.de", "kucuk.ofis@gmail.com", "erp-haus.de"})
        self.assertNotIn("doener-palast.de", keys)
        self.assertNotIn("lowconf.de", keys)
        self.assertNotIn("web.de", keys)
        self.assertNotIn("youtube.com", keys)

    def test_insert_dedupe_and_source_label(self):
        stats = harvest_overture.harvest_country(
            self.places, self.leads, "DE", target=100,
            source_label="test-etiket", min_confidence=0.5, dry_run=False)
        self.assertEqual(stats["added"], 3)
        rows = dict(self.leads.execute(
            "SELECT domain, source FROM leads").fetchall())
        self.assertEqual(len(rows), 3)
        # www'lu/bozuk domain norm_domain'den gecti, dupe tek satir kaldi
        self.assertIn("acme-tax.de", rows)
        # freemail kurali: firma alan adi yok, e-postanin kendisi anahtar
        self.assertIn("kucuk.ofis@gmail.com", rows)
        self.assertEqual(rows["acme-tax.de"], "test-etiket:ovt-de")
        # Overture email'i status kisayolu yapmaz: herkes pending kalir
        statuses = {row[0] for row in self.leads.execute(
            "SELECT status FROM leads")}
        self.assertEqual(statuses, {"pending"})

    def test_only_real_inserts_count_toward_target(self):
        # acme-tax.de zaten havuzda: insert olmaz, hedefe sayilmaz
        self.leads.execute(
            "INSERT INTO leads(domain, country, source) "
            "VALUES ('acme-tax.de', 'DE', 'eski:kaynak')")
        stats = harvest_overture.harvest_country(
            self.places, self.leads, "DE", target=100,
            source_label="test-etiket", min_confidence=0.5, dry_run=False)
        self.assertEqual(stats["added"], 2)
        # eski kaydin kaynagi ezilmedi (INSERT OR IGNORE)
        source = self.leads.execute(
            "SELECT source FROM leads WHERE domain='acme-tax.de'").fetchone()[0]
        self.assertEqual(source, "eski:kaynak")

    def test_stops_when_target_reached(self):
        stats = harvest_overture.harvest_country(
            self.places, self.leads, "DE", target=1,
            source_label="test-etiket", min_confidence=0.5, dry_run=False)
        self.assertEqual(stats["added"], 1)
        count = self.leads.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        self.assertEqual(count, 1)
        # confidence DESC: en yuksek guvenli uygun aday ilk girer
        domain = self.leads.execute("SELECT domain FROM leads").fetchone()[0]
        self.assertEqual(domain, "acme-tax.de")

    def test_countries_parsed_defensively(self):
        self.assertEqual(
            harvest_overture.parse_countries(" de, NL ,ie,, "),
            ["DE", "NL", "IE"])

    def test_places_connection_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.places.execute("INSERT INTO places(id, name) VALUES ('x', 'y')")


if __name__ == "__main__":
    unittest.main()
