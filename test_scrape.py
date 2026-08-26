from __future__ import annotations

import sqlite3
import unittest

import scrape


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


class IdentityTests(unittest.TestCase):
    def test_duplicate_insert_is_not_counted_as_new(self):
        conn = memory_db()
        self.assertTrue(scrape.add_lead(conn, "acme.de", "Acme", "Berlin", "DE"))
        self.assertFalse(scrape.add_lead(conn, "acme.de", "Acme", "Berlin", "DE"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 1)

    def test_freemail_leads_are_keyed_by_full_address(self):
        first = scrape.lead_key("", "first.office@gmail.com")
        second = scrape.lead_key("", "second.office@gmail.com")
        self.assertEqual(first, "first.office@gmail.com")
        self.assertEqual(second, "second.office@gmail.com")
        self.assertNotEqual(first, second)


class EmailQualityTests(unittest.TestCase):
    def test_career_address_is_preferred_over_general_address(self):
        emails = scrape.clean_emails(
            {"info@acme.de", "jobs@acme.de", "owner@acme.de"},
            "acme.de",
        )
        self.assertEqual(emails[0], "jobs@acme.de")

    def test_support_and_sales_addresses_are_rejected(self):
        emails = scrape.clean_emails(
            {"support@acme.de", "sales@acme.de", "info@acme.de"},
            "acme.de",
        )
        self.assertEqual(emails, ["info@acme.de"])

    def test_lookalike_mail_domain_is_rejected(self):
        emails = scrape.clean_emails(
            {"jobs@acme.de.evil.test", "jobs@acme.de"},
            "acme.de",
        )
        self.assertEqual(emails, ["jobs@acme.de"])

    def test_direct_email_key_is_accepted(self):
        emails = scrape.clean_emails(
            {"office@small-firm.de"},
            "office@small-firm.de",
        )
        self.assertEqual(emails, ["office@small-firm.de"])

    def test_same_company_on_another_tld_is_accepted(self):
        emails = scrape.clean_emails(
            {"jobs@samplecompany.com"},
            "samplecompany.de",
        )
        self.assertEqual(emails, ["jobs@samplecompany.com"])


if __name__ == "__main__":
    unittest.main()
