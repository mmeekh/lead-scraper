from __future__ import annotations

import sqlite3
import unittest
from io import StringIO

import harvest_uk_sponsors as huk


def memory_db() -> sqlite3.Connection:
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


CSV_HEADER = "Organisation Name,Town/City,County,Type & Rating,Route\n"


class RowFilterTests(unittest.TestCase):
    def test_skilled_worker_a_rating_passes_with_padding(self):
        row = {"Route": " Skilled Worker ",
               "Type & Rating": "  Worker (A rating)  "}
        self.assertTrue(huk.row_ok(row))

    def test_other_route_is_rejected(self):
        row = {"Route": "Temporary Worker (Creative Worker)",
               "Type & Rating": "Worker (A rating)"}
        self.assertFalse(huk.row_ok(row))

    def test_b_rating_is_rejected(self):
        row = {"Route": "Skilled Worker",
               "Type & Rating": "Worker (B rating)"}
        self.assertFalse(huk.row_ok(row))

    def test_missing_fields_are_rejected(self):
        self.assertFalse(huk.row_ok({}))

    def test_iter_eligible_filters_and_dedupes(self):
        handle = StringIO(
            CSV_HEADER
            + " Mercia Tax Advisory Ltd , Birmingham ,,Worker (A rating),Skilled Worker\n"
            + "Mercia Tax Advisory Limited,Birmingham,,Worker (A rating),Skilled Worker\n"
            + "Golden Halal Foods Ltd,London,,Worker (A rating),Skilled Worker\n"
            + "Northbridge Data Systems Ltd,Leeds,,Worker (B rating),Skilled Worker\n"
            + "Northbridge Data Systems Ltd,Leeds,,Worker (A rating),Temporary Worker\n"
            + "Quantum Payroll Solutions Ltd,York,,Worker (A rating),Skilled Worker\n"
        )
        firms = list(huk.iter_eligible(handle))
        self.assertEqual(firms, [
            ("Mercia Tax Advisory Ltd", "Birmingham"),
            ("Quantum Payroll Solutions Ltd", "York"),
        ])


class RelevanceTests(unittest.TestCase):
    def test_finance_and_data_names_are_accepted(self):
        for name in ("Mercia Tax Advisory Ltd",
                     "Northbridge Data Systems Limited",
                     "Alpha Payroll Solutions Ltd",
                     "Harborview Capital Partners LLP",
                     "Nimbus Software UK Ltd"):
            self.assertTrue(huk.name_relevant(name), name)

    def test_obviously_unrelated_names_are_rejected(self):
        for name in ("Sunrise Care Home Ltd",
                     "Golden Halal Foods Ltd",
                     "City Recruitment Agency Ltd",
                     "Fresh Farm Produce Limited",
                     "ALT"):
            self.assertFalse(huk.name_relevant(name), name)

    def test_taxi_is_not_matched_by_tax_keyword(self):
        # "tax" parcasi "taxi" icinde gecer; dislama once uygulanmali
        self.assertFalse(huk.name_relevant("Speedy Taxi Ltd"))
        self.assertTrue(huk.name_relevant("Speedy Tax Advisers Ltd"))


class SlugTests(unittest.TestCase):
    def test_candidates_for_three_word_name(self):
        self.assertEqual(
            huk.domain_candidates("Harborview Capital Partners LLP"),
            ["harborviewcapitalpartners.co.uk",
             "harborviewcapitalpartners.com",
             "harborviewcapitalpartners.uk",
             "harborviewcapital.co.uk",
             "harborviewcapital.com"])

    def test_candidates_for_two_word_name(self):
        self.assertEqual(
            huk.domain_candidates("Nimbus Analytics Ltd"),
            ["nimbusanalytics.co.uk", "nimbusanalytics.com",
             "nimbusanalytics.uk"])

    def test_candidate_count_never_exceeds_five(self):
        candidates = huk.domain_candidates(
            "Very Long Multi Word Consulting Advisory Partnership Ltd")
        self.assertLessEqual(len(candidates), 5)

    def test_short_slug_yields_no_candidates(self):
        self.assertEqual(huk.domain_candidates("AB Ltd"), [])


class MatchTests(unittest.TestCase):
    def test_extract_title(self):
        html = "<html><head><title>\n  Harborview Capital \n</title></head>"
        self.assertEqual(huk.extract_title(html), "Harborview Capital")

    def test_extract_title_missing(self):
        self.assertEqual(huk.extract_title("<html><body>hi</body>"), "")

    def test_verify_tokens_drop_short_words(self):
        self.assertEqual(huk.verify_tokens("A B C Ltd"), [])
        self.assertEqual(huk.verify_tokens("Harborview Capital Partners LLP"),
                         ["harborview", "capital", "partners"])

    def test_half_of_tokens_must_match(self):
        name = "Harborview Capital Advisory Ltd"  # 3 token
        probe = huk.probe_text("Harborview Capital", "welcome to our firm")
        self.assertIsNotNone(huk.match_evidence(name, probe))  # 2/3 yeter
        probe = huk.probe_text("Capital", "welcome")
        self.assertIsNone(huk.match_evidence(name, probe))     # 1/3 yetmez

    def test_single_long_token_must_match(self):
        probe = huk.probe_text("Welcome", "harborview accountants est 1990")
        self.assertIsNotNone(huk.match_evidence("Harborview Ltd", probe))
        self.assertIsNone(huk.match_evidence("Harborview Ltd",
                                             huk.probe_text("Other", "site")))

    def test_no_verifiable_token_never_matches(self):
        self.assertIsNone(huk.match_evidence("A B C Ltd",
                                             huk.probe_text("a b c", "a b c")))

    def test_domain_echo_is_not_evidence(self):
        # sayfa yalnizca kendi alan adini basiyorsa eslesme sayilmaz
        name = "Multiplier Technologies UK Ltd"
        host = "multipliertechnologies.com"
        probe = huk.probe_text("multipliertechnologies.com", "welcome buy now")
        self.assertIsNone(huk.match_evidence(
            name, huk.strip_domain_echo(probe, host, name)))
        probe = huk.probe_text("Multiplier Technologies",
                               "multiplier technologies payroll platformu")
        self.assertIsNotNone(huk.match_evidence(
            name, huk.strip_domain_echo(probe, host, name)))

    def test_generic_only_hits_are_not_evidence(self):
        # isimde ayirt edici token varken yalnizca jenerik kelime eslesmesi yetmez
        name = "Multiplier Technologies UK Ltd"
        probe = huk.probe_text("Starfield Technologies", "tech solutions")
        self.assertIsNone(huk.match_evidence(name, probe))
        probe = huk.probe_text("Multiplier", "payroll ekibi icin multiplier")
        self.assertIsNotNone(huk.match_evidence(name, probe))

    def test_placeholder_builder_page_is_parked(self):
        probe = huk.probe_text("mysite.com",
                               "Go Daddy Website Builder coming soon")
        self.assertTrue(huk.looks_parked(probe))

    def test_single_token_name_survives_echo_strip(self):
        # tek kelimeli firmada gercek metindeki ad kanit olmaya devam eder
        name = "Harborview Ltd"
        probe = huk.probe_text("Harborview Chartered Accountants", "est 1990")
        stripped = huk.strip_domain_echo(probe, "harborview.co.uk", name)
        self.assertIsNotNone(huk.match_evidence(name, stripped))

    def test_parked_page_is_rejected(self):
        self.assertTrue(huk.looks_parked(
            huk.probe_text("mysite.co.uk", "This domain is for sale at Sedo")))
        self.assertFalse(huk.looks_parked(
            huk.probe_text("Harborview Capital", "chartered accountants")))


class DbTests(unittest.TestCase):
    def test_record_lead_inserts_with_source_and_note(self):
        conn = memory_db()
        added = huk.record_lead(conn, "merciatax.co.uk", "Mercia Tax Advisory Ltd",
                                "Birmingham", "visa-priority-20260831")
        self.assertTrue(added)
        row = conn.execute(
            "SELECT name, city, country, source, status, note FROM leads "
            "WHERE domain='merciatax.co.uk'").fetchone()
        self.assertEqual(row, ("Mercia Tax Advisory Ltd", "Birmingham", "GB",
                               "visa-priority-20260831:uksr", "pending",
                               huk.NOTE_TEXT))

    def test_record_lead_does_not_touch_existing_row(self):
        conn = memory_db()
        conn.execute(
            "INSERT INTO leads(domain,name,city,country,source,note) "
            "VALUES('merciatax.co.uk','Eski Kayit','Londra','GB','osm:x','eski not')")
        added = huk.record_lead(conn, "merciatax.co.uk", "Mercia Tax Advisory Ltd",
                                "Birmingham", "visa-priority-20260831")
        self.assertFalse(added)
        row = conn.execute(
            "SELECT name, source, note FROM leads WHERE domain='merciatax.co.uk'"
        ).fetchone()
        self.assertEqual(row, ("Eski Kayit", "osm:x", "eski not"))

    def test_already_known_checks_candidate_list(self):
        conn = memory_db()
        conn.execute("INSERT INTO leads(domain) VALUES('nimbusanalytics.com')")
        self.assertTrue(huk.already_known(
            conn, ["nimbusanalytics.co.uk", "nimbusanalytics.com"]))
        self.assertFalse(huk.already_known(conn, ["baskafirma.co.uk"]))
        self.assertTrue(huk.already_known(conn, []))


if __name__ == "__main__":
    unittest.main()
