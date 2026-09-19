"""Cekirdek kural testleri: uzlasma, alinti dogrulama, tuzel ad, sayfa turu.

  python verify/test_verify.py        (bagimlilik yok, stdlib unittest)
Ag ve model gerektirmez; saf mantik test edilir.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify.consensus import job_ads_cikar, kanit_gecidi, uzlas
from verify.impressum import ad_celisiyor_mu, display_name, legal_name_bul
from verify.judge import alintilari_dogrula


def y(decision: str, quotes_verified: bool = True) -> dict:
    return {"decision": decision, "quotes_verified": quotes_verified}


class Uzlasma(unittest.TestCase):
    def test_iki_evet_ve_alintilar_dogru_ise_evet(self):
        self.assertEqual(uzlas(y("yes"), y("yes")), ("evet", "both"))

    def test_alinti_dogrulanmazsa_listelenmez(self):
        self.assertEqual(uzlas(y("yes"), y("yes", False)), ("belirsiz", "one"))
        self.assertEqual(uzlas(y("yes", False), y("yes")), ("belirsiz", "one"))

    def test_bir_taraf_hayir_ise_hayir(self):
        self.assertEqual(uzlas(y("yes"), y("no"))[0], "hayir")
        self.assertEqual(uzlas(y("no"), y("unclear"))[0], "hayir")

    def test_tek_evet_listelenmez(self):
        self.assertEqual(uzlas(y("yes"), y("unclear")), ("belirsiz", "one"))

    def test_eksik_yargi_belirsiz(self):
        self.assertEqual(uzlas(None, y("yes")), ("belirsiz", "none"))
        self.assertEqual(uzlas(y("yes"), None), ("belirsiz", "none"))

    def test_ikisi_de_belirsiz(self):
        self.assertEqual(uzlas(y("unclear"), y("unclear")), ("belirsiz", "both"))


class AlintiDogrulama(unittest.TestCase):
    sayfa = {"home": "Wir sind ein Umzugsunternehmen.\nErfahrene  Umzugshelfer und Fahrer stehen bereit."}

    def test_birebir_gecen_alinti(self):
        tam, _ = alintilari_dogrula(["Erfahrene Umzugshelfer und Fahrer"], self.sayfa)
        self.assertTrue(tam)   # fazla bosluk normalize edilir

    def test_uydurma_alinti_reddedilir(self):
        tam, tekil = alintilari_dogrula(["Wir beschaeftigen 20 Berufskraftfahrer"], self.sayfa)
        self.assertFalse(tam)
        self.assertEqual(tekil, [False])

    def test_bos_alinti_listesi_gecersiz(self):
        tam, _ = alintilari_dogrula([], self.sayfa)
        self.assertFalse(tam)

    def test_cok_kisa_alinti_sayilmaz(self):
        tam, _ = alintilari_dogrula(["Wir"], self.sayfa)
        self.assertFalse(tam)

    def test_biri_uymazsa_hepsi_dusor(self):
        tam, tekil = alintilari_dogrula(
            ["Erfahrene Umzugshelfer und Fahrer", "Wir haben 50 LKW"], self.sayfa)
        self.assertFalse(tam)
        self.assertEqual(tekil, [True, False])


class TuzelAd(unittest.TestCase):
    def test_hukuki_bicimde_biter_adres_kesilir(self):
        metin = "Impressum\nAngaben gemäß § 5 TMG\nAutoService-Helmer e.K Osterrade 29\n22559 Hamburg"
        self.assertEqual(legal_name_bul(metin), "AutoService-Helmer e.K")

    def test_gmbh_co_kg(self):
        metin = "Impressum\nJohannes J. Matthies GmbH & Co. KG\nTelefon: 040 123"
        self.assertEqual(legal_name_bul(metin), "Johannes J. Matthies GmbH & Co. KG")

    def test_yumusak_tire_temizlenir(self):
        self.assertEqual(legal_name_bul("Impressum\nHans Schmitz Karos­serie­bau GmbH"),
                         "Hans Schmitz Karosseriebau GmbH")

    def test_bulunamazsa_bos(self):
        self.assertEqual(legal_name_bul("Startseite\nWillkommen bei uns"), "")

    def test_display_name_hukuki_eki_atar(self):
        self.assertEqual(display_name("Johann Wunder GmbH"), "Johann Wunder")

    def test_havuz_adi_celiskisi(self):
        self.assertTrue(ad_celisiyor_mu("dubb AG", "Motorradvermietung Hamburg"))
        self.assertFalse(ad_celisiyor_mu("Johann Wunder GmbH", "Johann Wunder"))
        self.assertFalse(ad_celisiyor_mu("", "herhangi"))


class KanitGecidi(unittest.TestCase):
    anahtar = ["elektr", "steuerung", "sensor", "antrieb"]

    def test_anahtar_yoksa_gecer(self):
        self.assertTrue(kanit_gecidi(["beliebiger Text"], []))

    def test_elektrik_kaniti_varsa_gecer(self):
        self.assertTrue(kanit_gecidi(["Software, Konstruktion, Elektronik oder Laseroptik"], self.anahtar))

    def test_genel_muhendislik_sozcugu_gecmez(self):
        self.assertFalse(kanit_gecidi(
            ["Our research and development department with its own in-house laboratory"], self.anahtar))

    def test_buyuk_kucuk_harf_duyarsiz(self):
        self.assertTrue(kanit_gecidi(["Zuverlaessige STEUERUNG"], self.anahtar))


class Ilanlar(unittest.TestCase):
    def test_mwd_basligi_bulunur(self):
        metin = "Karriere\nJobs\nPflegefachkraft (m/w/d) in Vollzeit\nJetzt bewerben"
        ilan = job_ads_cikar(metin, "https://x.de/karriere")
        self.assertEqual([i["baslik"] for i in ilan], ["Pflegefachkraft (m/w/d) in Vollzeit"])

    def test_gurultu_satirlari_elenir(self):
        self.assertEqual(job_ads_cikar("Jobs\nKarriere\nImpressum", "u"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
