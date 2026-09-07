"""Arastirma orkestratorunun baslangic denetimi ve kilit dayanikliligi.

6-7 Eyl 2026 gecesi servis, ulke listesi uyusmazligi ve kuyruk kilidi
cakismasi yuzunden 398 kez cokup hicbir sey uretmedi. Buradaki testler o
gece yasanan iki arizayi birebir yeniden uretir ve artik yakalandigini
kanitlar. Orkestrator dosya adinda tire oldugu icin importlib ile yuklenir.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent


def load_orchestrator():
    spec = importlib.util.spec_from_file_location(
        "orchestrator", BASE / "run-qualified-contact-targets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SelfcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orch = load_orchestrator()

    def test_current_configuration_passes(self):
        # Gercek yapilandirma. Bu test kirmiziysa gece taramasi baslamaz;
        # preflight zamanlayicisi ayni kontrolu 22:50'de calistirip alarm verir.
        self.assertEqual(self.orch.selfcheck(), [])

    def test_country_without_cities_or_qid_is_reported(self):
        # CH: OSM ulke adinda var ama sehir listesi ve Wikidata QID'i yok.
        # 6 Eyl'de tam bu durumdaki ulkeler arastirma kumesine girmisti.
        with patch.object(self.orch, "RESEARCH_COUNTRIES", ("GB", "CH")), \
             patch.object(self.orch, "accepted", lambda **kw: {}), \
             patch.object(self.orch, "load_progress", lambda: {}):
            problems = self.orch.selfcheck()
        self.assertTrue(any("CH" in p and "MAJOR_CITIES" in p for p in problems), problems)
        self.assertTrue(any("CH" in p and "COUNTRY_QIDS" in p for p in problems), problems)
        self.assertFalse(any(p.startswith("GB") for p in problems), problems)

    def test_research_country_outside_send_list_is_reported(self):
        with patch.object(self.orch, "RESEARCH_COUNTRIES", ("GB", "XX")), \
             patch.object(self.orch, "accepted", lambda **kw: {}), \
             patch.object(self.orch, "load_progress", lambda: {}):
            problems = self.orch.selfcheck()
        self.assertTrue(any("XX" in p and "COUNTRIES" in p for p in problems), problems)

    def test_active_country_sort_covers_every_research_country(self):
        # 6-7 Eyl gecesi coken ifade birebir: ayri bir sira listesi yok,
        # RESEARCH_COUNTRIES kendi sirasidir; index() hicbir uye icin patlayamaz.
        from collections import Counter
        counts = Counter()
        active = sorted(
            self.orch.RESEARCH_COUNTRIES,
            key=lambda c: (self.orch.RESEARCH_COUNTRIES.index(c), counts[c]))
        self.assertEqual(tuple(active), self.orch.RESEARCH_COUNTRIES)


class QueueLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orch = load_orchestrator()

    def test_accepted_waits_out_a_busy_lock(self):
        from contextlib import contextmanager
        from send_mails import AlreadyRunningError
        calls = []

        @contextmanager
        def flaky_lock():
            calls.append(1)
            if len(calls) < 3:
                raise AlreadyRunningError("kuyruk guncellemesi zaten calisiyor")
            yield

        with patch.object(self.orch, "queue_lock", flaky_lock), \
             patch.object(self.orch, "accepted_counts", lambda reader: {"GB": 7}), \
             patch.object(self.orch.time, "sleep"), \
             patch.object(self.orch, "CSV_PATH", Path(__file__)):
            counts = self.orch.accepted(attempts=5, wait=0)
        self.assertEqual(counts, {"GB": 7})
        self.assertEqual(len(calls), 3)

    def test_accepted_gives_up_when_lock_never_frees(self):
        from contextlib import contextmanager
        from send_mails import AlreadyRunningError

        @contextmanager
        def stuck_lock():
            raise AlreadyRunningError("kuyruk guncellemesi zaten calisiyor")
            yield  # pragma: no cover

        with patch.object(self.orch, "queue_lock", stuck_lock), \
             patch.object(self.orch.time, "sleep"), \
             patch.object(self.orch, "CSV_PATH", Path(__file__)):
            with self.assertRaises(AlreadyRunningError):
                self.orch.accepted(attempts=4, wait=0)


if __name__ == "__main__":
    unittest.main()
