from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sponsor_registry


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    sponsor_registry.ensure_table(conn)
    return conn


def seed(conn: sqlite3.Connection, country: str, *names: str) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sponsors(country,name,name_norm,extra,fetched_at) "
        "VALUES(?,?,?,?,?)",
        [(country, name, sponsor_registry.normalize_name(name), "", "test")
         for name in names])
    conn.commit()


class NormalizeTests(unittest.TestCase):
    def test_accents_are_folded(self):
        self.assertEqual(
            sponsor_registry.normalize_name("Café Müller GmbH"), "cafe muller")

    def test_trailing_legal_suffixes_are_stripped(self):
        self.assertEqual(
            sponsor_registry.normalize_name("ASML Holding N.V."), "asml")
        self.assertEqual(
            sponsor_registry.normalize_name("Google UK Limited"), "google")
        self.assertEqual(
            sponsor_registry.normalize_name(" Asian African Foods Ltd "),
            "asian african foods")

    def test_punctuation_becomes_space(self):
        self.assertEqual(
            sponsor_registry.normalize_name('""AAE"" Advanced Automated Equipment B.V.'),
            "aae advanced automated equipment")

    def test_suffix_in_the_middle_is_kept(self):
        # ek yalnizca SONDAN atilir
        self.assertEqual(
            sponsor_registry.normalize_name("Group Therapy Kitchen"),
            "group therapy kitchen")

    def test_short_names_are_not_emptied(self):
        # isim tamamen ekten olussa bile en az bir token kalir
        self.assertEqual(sponsor_registry.normalize_name("Ltd"), "ltd")
        self.assertEqual(sponsor_registry.normalize_name("B.V."), "b v")
        self.assertEqual(sponsor_registry.normalize_name("Lp Group"), "lp")


class IsSponsorTests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        seed(self.conn, "GB", "Google UK Limited",
             "Rippling People Center Ltd", " ALT")
        seed(self.conn, "NL", "ASML Holding N.V.")

    def test_exact_match_after_normalization(self):
        self.assertTrue(
            sponsor_registry.is_sponsor(self.conn, "NL", "asml holding n.v."))
        self.assertTrue(
            sponsor_registry.is_sponsor(self.conn, "GB", "GOOGLE UK LTD"))

    def test_containment_both_directions(self):
        # sponsor adi lead adini icerir (lead-in-sponsor, kelime sinirli)
        self.assertTrue(
            sponsor_registry.is_sponsor(self.conn, "GB", "Rippling"))
        # lead adi COK KELIMELI sponsor normunu icerir (sponsor-in-lead)
        self.assertTrue(sponsor_registry.is_sponsor(
            self.conn, "GB", "Rippling People Center Europe BV"))
        # tek kelimeye cokusen sponsor normu ('Google UK Ltd' -> 'google')
        # icerme yonunde kanit OLAMAZ: 'Bridge UK, Limited' -> 'bridge' gibi
        # kayitlar her lead'i yakalardi (31 Agu duzeltmesi)
        self.assertFalse(
            sponsor_registry.is_sponsor(self.conn, "GB", "Google Deutschland"))

    def test_single_common_word_sponsor_norm_is_not_containment_evidence(self):
        seed(self.conn, "GB", "Bridge UK, Limited")  # norm: 'bridge'
        self.assertFalse(sponsor_registry.is_sponsor(
            self.conn, "GB", "London Bridge Consulting"))

    def test_generic_phrase_register_rows_never_gate(self):
        # IND'de belediye kaydi var: 'Den Haag' (kvk). Baslik parcasi ya da
        # icerme kaniti olarak kullanilamaz.
        seed(self.conn, "NL", "Den Haag")
        self.assertFalse(sponsor_registry.is_sponsor_strict(self.conn, "NL", "Den Haag"))
        self.assertFalse(sponsor_registry.is_sponsor(
            self.conn, "NL", "Bakkerij Den Haag Catering"))

    def test_trade_name_matches_registered_name_by_word_boundary(self):
        # ticari ad -> tescilli ad: 'Kayak' 'Kayak Software (UK) Limited'i
        # KELIME SINIRINDA yakalar (31 Agu duzeltmesi: on-crawl sahte negatif)
        seed(self.conn, "GB", "Kayak Software (UK) Limited")
        self.assertTrue(sponsor_registry.is_sponsor(self.conn, "GB", "Kayak"))

    def test_subword_containment_never_matches(self):
        # kelime siniri sart: 'abcde', 'abcdefgh' icinde eslesmez
        seed(self.conn, "GB", "Abcdefgh Ltd")
        self.assertFalse(sponsor_registry.is_sponsor(self.conn, "GB", "Abcde"))

    def test_generic_single_token_lead_never_matches(self):
        # tek jenerik token lead adi kanit olamaz ('data' -> 'Data Systems...')
        seed(self.conn, "GB", "Data Systems International Ltd")
        self.assertFalse(sponsor_registry.is_sponsor(self.conn, "GB", "Data"))

    def test_generic_single_token_sponsor_never_matches(self):
        # sicilde normu tek jenerik kelimeye dusen kayit ('London & Co UK Ltd'
        # -> 'london') baska lead'leri yakalamamali (31 Agu sahte pozitif)
        seed(self.conn, "GB", "London & Co UK Ltd")
        self.assertFalse(sponsor_registry.is_sponsor(
            self.conn, "GB", "London Bridge Consulting"))

    def test_short_sponsor_name_never_matches_by_containment(self):
        # sponsor "alt" (3 harf) jenerik: "Alternative..." lead'ini yakalamamali
        self.assertFalse(
            sponsor_registry.is_sponsor(self.conn, "GB", "Alternative Solutions"))

    def test_strict_only_exact_and_non_generic(self):
        # baslik parcalari icin: icerme yok, jenerik aday reddedilir.
        # Dikkat: normalize 'group'/'holding' son ekini atar; 'Home Group Ltd'
        # sicil normu 'home' olur ama jenerik oldugu icin baslik 'Home'
        # yine de kapiyi acamaz.
        seed(self.conn, "GB", "Home Group Ltd", "London & Co UK Ltd")
        self.assertFalse(sponsor_registry.is_sponsor_strict(self.conn, "GB", "Home"))
        self.assertFalse(sponsor_registry.is_sponsor_strict(self.conn, "GB", "London"))
        self.assertFalse(sponsor_registry.is_sponsor_strict(self.conn, "GB", "Home Group"))
        # icerme yok: 'Google Deutschland' tam eslesme degil
        self.assertFalse(sponsor_registry.is_sponsor_strict(
            self.conn, "GB", "Google Deutschland GmbH"))
        # jenerik olmayan tam eslesme (kisa marka dahil) gecer
        self.assertTrue(sponsor_registry.is_sponsor_strict(self.conn, "GB", "Google"))
        self.assertTrue(sponsor_registry.is_sponsor_strict(
            self.conn, "NL", "ASML Holding N.V."))

    def test_wrong_country_and_empty_name(self):
        self.assertFalse(
            sponsor_registry.is_sponsor(self.conn, "GB", "ASML Holding N.V."))
        self.assertFalse(sponsor_registry.is_sponsor(self.conn, "NL", ""))


UK_CSV_FIXTURE = """Organisation Name,Town/City,County,Type & Rating,Route
 Acme Robotics Ltd , London  ,,Worker (A rating),Skilled Worker
 Acme Robotics Ltd ,London,,Temporary Worker (A rating),Creative Worker
Beta Farms Limited,York,,Worker (A rating),Seasonal Worker
 Gamma Health Plc,Leeds,Yorkshire,Worker (A rating),Skilled Worker
,London,,Worker (A rating),Skilled Worker
"""


class UkCsvTests(unittest.TestCase):
    def test_only_skilled_worker_rows_pass(self):
        rows = list(sponsor_registry.iter_uk_rows(io.StringIO(UK_CSV_FIXTURE)))
        self.assertEqual(
            [name for name, _ in rows],
            ["Acme Robotics Ltd", "Gamma Health Plc"])
        self.assertEqual(rows[0][1], "London | Worker (A rating)")
        self.assertEqual(rows[1][1], "Leeds | Worker (A rating)")

    def test_store_dedupes_on_normalized_name(self):
        conn = memory_db()
        inserted = sponsor_registry._store(
            conn, "GB",
            [("Acme Robotics Ltd", "x"), ("ACME ROBOTICS LIMITED", "y"),
             ("Gamma Health Plc", "z")])
        self.assertEqual(inserted, 2)
        self.assertEqual(sponsor_registry.sponsor_count(conn, "GB"), 2)


NL_HTML_FIXTURE = """
<table><thead><tr>
  <th scope="col">Organisation</th>
  <th scope="col">KVK (Chamber of Commerce) number</th>
</tr></thead><tbody>
<tr>
  <th scope="row"> Alexia Kliniek Echt B.V.</th>
  <td>17214152</td>
</tr>
<tr>
  <th scope="row">&quot;&quot;AAE&quot;&quot; Advanced Automated Equipment B.V.</th>
  <td>17037842</td>
</tr>
</tbody></table>
"""


class NlHtmlTests(unittest.TestCase):
    def test_rows_are_parsed_with_kvk(self):
        rows = sponsor_registry.parse_nl_html(NL_HTML_FIXTURE)
        self.assertEqual(rows, [
            ("Alexia Kliniek Echt B.V.", "kvk:17214152"),
            ('""AAE"" Advanced Automated Equipment B.V.', "kvk:17037842"),
        ])

    def test_fetch_nl_fails_loudly_when_too_few_rows(self):
        # 1000'den az satir = parse bozulmus; sessizce gecmemeli
        conn = memory_db()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ind-work-test.html"
            path.write_text(NL_HTML_FIXTURE, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                sponsor_registry.fetch_nl(conn, path=path)


if __name__ == "__main__":
    unittest.main()
