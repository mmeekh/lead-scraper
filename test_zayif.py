"""zayif.py birim testleri: alan adi eslesme, e-posta secimi (rol/kisisel), sehir, park."""
import unittest

import zayif


class AlanAdi(unittest.TestCase):
    def test_bus_eslesir(self):
        self.assertIn("bus", zayif.meslek_esle_alan("mueller-busreisen.de"))
        self.assertIn("bus", zayif.meslek_esle_alan("omnibus-schmidt.de"))

    def test_soyadi_busse_eslesmez(self):
        self.assertNotIn("bus", zayif.meslek_esle_alan("architekt-busse.de"))
        self.assertNotIn("bus", zayif.meslek_esle_alan("business-coach.de"))

    def test_elektro_elektronik_ayrimi(self):
        self.assertIn("elektro", zayif.meslek_esle_alan("elektro-maier.de"))
        self.assertNotIn("elektro", zayif.meslek_esle_alan("elektronik-shop.de"))
        self.assertIn("elektro_ing", zayif.meslek_esle_alan("elektronik-shop.de"))

    def test_alt_alan_ve_kisa_atlanir(self):
        self.assertEqual(zayif.meslek_esle_alan("www.de"), [])
        self.assertEqual(zayif.meslek_esle_alan("bus.de"), [])


class Eposta(unittest.TestCase):
    def test_rol_adresi_oncelikli(self):
        e = zayif.eposta_sec({"hans.mueller@firma.de", "info@firma.de"}, ["firma.de"])
        self.assertEqual(e, "info@firma.de")

    def test_kisisel_adres_toplanmaz(self):
        self.assertEqual(zayif.eposta_sec({"hans.mueller@firma.de", "h.mueller@firma.de"}, ["firma.de"]), "")

    def test_yabanci_alan_reddedilir(self):
        self.assertEqual(zayif.eposta_sec({"info@agentur.de", "info@nicsell.com"}, ["firma.de"]), "")

    def test_yonlendirme_koku_kabul(self):
        self.assertEqual(zayif.eposta_sec({"info@neu-firma.de"}, ["alt-firma.de", "www.neu-firma.de"]), "info@neu-firma.de")

    def test_gizlenmis_at(self):
        html_ = "<p>Schreiben Sie uns: info (at) firma (punkt) de</p><a href='mailto:kontakt&#64;firma.de'>Mail</a>"
        self.assertEqual(zayif.epostalari_cikar(html_), {"info@firma.de", "kontakt@firma.de"})


class Impressum(unittest.TestCase):
    def test_sehir_plz_haritasindan(self):
        zayif._PLZ = {"10115": "Berlin"}
        self.assertEqual(zayif.sehir_bul("Musterfirma GmbH\nMusterstr. 1\n10115 Berlin\nTel. 030"), "Berlin")

    def test_sehir_haritada_yoksa_metinden(self):
        zayif._PLZ = {}
        self.assertEqual(zayif.sehir_bul("Firma\n99999 Musterstadt Telefon 0123"), "Musterstadt")

    def test_park_sayfasi(self):
        self.assertTrue(zayif.PARK.search("Diese Domain steht zum Verkauf bei nicsell"))
        self.assertFalse(zayif.PARK.search("Bäckerei Abels – Traditionelle Familienbäckerei"))

    def test_baslik_alan_adi_degil(self):
        self.assertEqual(zayif.baslik_adi("<title>www.bus-bahnwerbung.de</title>", "bus-bahnwerbung.de"), "Bus Bahnwerbung")
        self.assertEqual(zayif.baslik_adi("<title>Startseite | Bierl Bus GmbH</title>", "bierl-bus.de"), "Bierl Bus GmbH")


if __name__ == "__main__":
    unittest.main()
