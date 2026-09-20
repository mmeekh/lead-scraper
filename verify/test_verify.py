"""Cekirdek kural testleri: uzlasma, alinti dogrulama, tuzel ad, sayfa turu.

  python verify/test_verify.py        (bagimlilik yok, stdlib unittest)
Ag ve model gerektirmez; saf mantik test edilir.
"""
from __future__ import annotations

import sys
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify.altin_kume import KOR_BASLIKLAR, _oku_etiketler
from verify.consensus import job_ads_cikar, kanit_gecidi, uzlas
from verify.impressum import ad_celisiyor_mu, display_name, legal_name_bul
from verify.judge import alintilari_dogrula
from verify import candidates, cli, consensus, db as veri


class MeslekKatalogu(unittest.TestCase):
    def test_tum_kayitlar_iki_hakeme_de_verilebilir(self):
        from verify.istemler import kullanici_a, kullanici_b
        katalog = candidates.meslekler()
        self.assertEqual(len({m['id'] for m in katalog.values()}), 54)
        for m in katalog.values():
            for istem in (kullanici_a, kullanici_b):
                self.assertIn(m['definition_de'], istem(m, [], 'Beleg'))
        for eski, yeni in [('berufskraftfahrer', 'fahrer'),
                           ('elektroingenieur', 'elektro_ing'),
                           ('marketing_manager', 'marketing')]:
            self.assertIs(katalog[eski], katalog[yeni])

    def test_gecersiz_katalog_kosuya_girmez(self):
        kaynak = json.loads(candidates.MESLEKLER_PATH.read_text(encoding='utf-8'))
        for hata in ('definition_de', 'beleg_anahtarlar', 'takma_ad', 'tekrar', 'oncelik'):
            with self.subTest(hata=hata), tempfile.TemporaryDirectory() as d:
                data = json.loads(json.dumps(kaynak))
                if hata in ('definition_de', 'beleg_anahtarlar'):
                    data['meslekler'][0][hata] = ''
                elif hata == 'takma_ad':
                    data['takma_adlar']['eski'] = 'tanim_yok'
                elif hata == 'tekrar':
                    data['meslekler'].append(data['meslekler'][0])
                else:
                    data['oncelik'].pop()
                yol = Path(d) / 'meslekler.json'
                yol.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    candidates.meslekler(yol)


class KatalogEntegrasyonu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(veri, 'DB_PATH', Path(self.tmp.name) / 'verify.sqlite3'))
        self.stack.enter_context(patch.object(veri, '_SEMA_KURULDU', False))
        self.stack.enter_context(patch.object(consensus, 'read_page_text', return_value=''))
        conn = veri.db()
        conn.close()

    def aday(self, meslek, domain='test.de', alinti='Our research and development department'):
        conn = veri.db()
        veri.upsert_domain(conn, domain, meslek=meslek)
        for model in (consensus.MODEL_A, consensus.MODEL_B):
            veri.save_judgment(conn, domain, meslek, model,
                              {'decision': 'yes', 'quotes': [alinti], 'quotes_verified': True})
        veri.set_status(conn, domain, 'yargilandi')
        conn.commit()
        conn.close()

    def test_eski_kimlik_kanit_gecidini_atlayamaz(self):
        self.aday('elektroingenieur')
        sonuc = consensus.isle()
        self.assertEqual(sonuc['evet'], 0)
        self.assertEqual(sonuc['belirsiz'], 1)

    def test_eski_kimlik_olcum_icin_korunur(self):
        self.aday('elektroingenieur', alinti='Unsere eigene Elektronikentwicklung')
        self.assertEqual(consensus.isle()['evet'], 1)
        conn = veri.db()
        try:
            rec = json.loads(conn.execute('SELECT professions_json FROM verified').fetchone()[0])
            self.assertEqual(rec[0]['meslek_kaydi'], 'elektroingenieur')
        finally:
            conn.close()

    def test_bilinmeyen_meslek_uzlasma_ve_yargilamaya_giremez(self):
        self.aday('tanim_yok')
        with self.assertRaisesRegex(ValueError, 'tanimi olmayan'):
            consensus.isle()
        with patch.object(cli.hakem, 'yargila') as yargila:
            with self.assertRaisesRegex(ValueError, 'tanimi olmayan'):
                cli.cmd_judge(SimpleNamespace(meslek='', limit=10, model='', yeniden=False))
            yargila.assert_not_called()
        conn = veri.db()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM verified').fetchone()[0], 0)
        finally:
            conn.close()

    def test_yeni_ve_eski_filtre_ayni_kuyrugu_secer(self):
        self.aday('elektroingenieur', domain='eski.de')
        self.aday('elektro_ing', domain='yeni.de')
        self.aday('fahrer', domain='baska.de')
        for filtre in ('elektroingenieur', 'elektro_ing'):
            with self.subTest(filtre=filtre), patch.object(cli.hakem, 'bosalt'), \
                    patch.object(cli.hakem, 'yargila') as yargila:
                cikti = io.StringIO()
                with contextlib.redirect_stdout(cikti):
                    cli.cmd_judge(SimpleNamespace(meslek=filtre, limit=10, model='', yeniden=False))
                sonuc = json.loads(cikti.getvalue())
                for model in sonuc['modeller'].values():
                    self.assertEqual(model['atlanan'], 2)
                yargila.assert_not_called()

    def test_baska_meslegin_yargisi_kullanilmaz(self):
        self.aday('elektroingenieur', alinti='Unsere eigene Elektronikentwicklung')
        conn = veri.db()
        veri.save_judgment(conn, 'test.de', 'fahrer', consensus.MODEL_A, {'decision': 'no'})
        conn.commit()
        conn.close()
        self.assertEqual(consensus.isle()['evet'], 1)


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


class KorCsv(unittest.TestCase):
    def test_kor_basliklar_karar_sizdirmaz(self):
        yasak = ("hat_karari", "agreement", "model_a", "model_b", "alinti_1", "alinti_2", "etiket")
        for k in yasak:
            self.assertNotIn(k, KOR_BASLIKLAR, f"kor CSV'de {k} olmamali")
        self.assertEqual(KOR_BASLIKLAR[:3], ["domain", "legal_name", "meslek"])

    def test_etiket_okuma_iki_bicimi_de_kabul_eder(self):
        import csv as _csv
        import tempfile
        for sutun in ("insan_karari", "etiket"):
            with tempfile.TemporaryDirectory() as d:
                yol = Path(d) / "x.csv"
                with yol.open("w", encoding="utf-8-sig", newline="") as f:
                    w = _csv.DictWriter(f, fieldnames=["domain", "meslek", sutun, "not"])
                    w.writeheader()
                    w.writerow({"domain": "A.DE", "meslek": "berufskraftfahrer", sutun: "Evet", "not": "n"})
                    w.writerow({"domain": "b.de", "meslek": "elektroingenieur", sutun: "hayır", "not": ""})
                    w.writerow({"domain": "c.de", "meslek": "marketing_manager", sutun: "", "not": ""})
                okunan = _oku_etiketler(yol)
                self.assertEqual(len(okunan), 2, f"{sutun}: bos satir olcume girmemeli")
                self.assertEqual(okunan[("a.de", "berufskraftfahrer")]["etiket"], "evet")
                self.assertEqual(okunan[("b.de", "elektroingenieur")]["etiket"], "hayir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
