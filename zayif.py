#!/usr/bin/env python3
"""Zayif mesleklerde yeni sirket tarama (21 Eyl 2026).

Havuzda az e-postali sirketi olan 14 meslek grubu icin Almanya genelinde yeni sirket bulur:
  1) aday   : Common Crawl alan adi listesi (cc-main domain vertices, ucretsiz) + OSM onbellegi
              (../scraper/data/osm) -> alan adinda / adinda meslek anahtar kelimesi gecen .de alan adlari
  2) ele    : havuz-gonder.py --bilinen mantigi (API /api/havuz/bilinen) -> yalniz havuzda OLMAYANLAR kalir
  3) tara   : site (ana sayfa + Impressum/Kontakt, en cok 4 sayfa): robots.txt, alan adi basina ardisik istek,
              >= 1 sn aralik, 20 sn zaman asimi; YALNIZ sitede yazili kurumsal (rol) e-posta; kisisel adres yok;
              Impressum'dan tuzel ad ve PLZ->sehir (OSM'den turetilmis zayif/plz-sehir.json)
  4) yukle  : havuz CSV'si (company, city, sector, website, email, source_url) -> havuz-gonder.py --etiket pc-<meslek>
  5) verify-ekle : bulunan canli sirketleri verify/verify.sqlite3 sirketler+ciftler'e ekler (ilan cikarimi isler)
  6) rapor  : meslek basina taranan / e-postali / yuklenen (eklenen, guncellenen, red)

  python zayif.py calis            # hepsi sirayla, meslek oncelik sirasinda; surekli isci
  python zayif.py aday|ele|tara|yukle|verify-ekle|rapor [--meslek m] [--limit n]
Veri: zayif/ (git disi). Anahtar yalniz JOBFIND_HAVUZ_ANAHTAR ortam degiskeninden.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import html
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from verify.impressum import legal_name_bul  # noqa: E402

ZAYIF = BASE / "zayif"
DB_PATH = ZAYIF / "zayif.sqlite3"
CC_GZ = ZAYIF / "cc-domain-vertices.txt.gz"
PLZ_JSON = ZAYIF / "plz-sehir.json"
OSM_DIR = BASE.parent / "scraper" / "data" / "osm"
OUT = ZAYIF / "out"
LOG = ZAYIF / "zayif.log"
DURUM_MD = ZAYIF / "DURUM-zayif.md"
GONDER = BASE / "verify" / "havuz-gonder.py"
VERIFY_DB = BASE / "verify" / "verify.sqlite3"

CONTACT = os.environ.get("SCRAPER_CONTACT", "").strip()
UA = f"lead-scraper-zayif/0.1 (+{CONTACT})" if CONTACT else "lead-scraper-zayif/0.1"
TIMEOUT_S = 20
DELAY_S = 1.0
MAX_SAYFA = 5                 # ana sayfa + en cok 4 alt sayfa
ISCI = 24
BYTE_TAVAN = 1_500_000

# --- meslek tanimlari: alan adi deseni (ascii etiket), metin deseni (uyum), sektor, verify meslek id'leri
# Sira = oncelik (kullanicinin listesi: en zayif once)
MESLEKLER: dict[str, dict] = {
    "bus": dict(ad="Busunternehmen / Verkehrsbetriebe / Busreisen", sektor="Otomotiv & Lojistik", verify=["bus"],
                alan=r"omnibus|(^|-)(busreisen|busbetrieb|busunternehmen|bustouristik|busverkehr|busservice|reisebus|busfahrt|busfahrten|verkehrsbetrieb|verkehrsbetriebe|verkehrsgesellschaft|nahverkehr|stadtverkehr|bus)(-|$)",
                metin=r"omnibus|autobus|busreise|busunternehm|busbetrieb|reisebus|linienverkehr|busverkehr|busfahrer|bustour|fernbus|schulbus|linienbus|busvermietung|buscharter|verkehrsbetrieb|nahverkehr"),
    "fahrzeug": dict(ad="Fahrzeugbau / Nutzfahrzeuge / Karosseriebau", sektor="Otomotiv & Lojistik", verify=["fahrzeug_ing", "karosserie"],
                     alan=r"karosserie|fahrzeugbau|nutzfahrzeug|anhaengerbau|fahrzeugaufbau|aufbauten|(^|-)(nfz|lkw|truck|trucks)(-|$)",
                     metin=r"karosserie|fahrzeugbau|nutzfahrzeug|anhänger|aufbauten|lkw|fahrzeugaufbau|unfallinstandsetzung|nfz"),
    "kinder": dict(ad="Kinderklinik / Kinderarztpraxis / Kinderkrankenpflege", sektor="Sağlık & Bakım", verify=["kinderkrankenpflege"],
                   alan=r"kinderarzt|kinderaerzt|kinderklinik|kinderkrankenpfl|kinderpraxis|kinderarztpraxis|kinder-und-jugend|kinderdoc|paediatr|kinderintensiv|kinderhospiz",
                   metin=r"kinderarzt|kinderärzt|kinderklinik|kinderkrankenpfl|kinder-? ?und ?jugendmedizin|pädiatr|kinderheilkunde|kinderintensiv|kinderhospiz"),
    "ergo": dict(ad="Ergotherapie-Praxen", sektor="Sağlık & Bakım", verify=["ergo"],
                 alan=r"ergotherap|(^|-)ergo(-|$)|praxis-ergo|ergopraxis",
                 metin=r"ergotherap"),
    "rettung": dict(ad="Rettungsdienst / Krankentransport", sektor="Sağlık & Bakım", verify=["rettung"],
                    alan=r"rettungsdienst|krankentransport|rettungswache|sanitaetsdienst|krankenfahrt|notfallrettung|(^|-)(rettung|ambulanz|rettungsdienste)(-|$)",
                    metin=r"rettungsdienst|krankentransport|rettungswache|sanitätsdienst|notfallrettung|rettungssanitäter|notfallsanitäter|krankenfahrt|rettungswagen"),
    "backer": dict(ad="Bäckerei", sektor="Gastronomi & Turizm", verify=["backer"],
                   alan=r"baeckerei|backhaus|backstube|bakery|landbaeckerei|brotmanufaktur|backwaren|xn--bckerei|(^|-)baecker(-|$)|baeckerei",
                   metin=r"bäckerei|baeckerei|backhaus|backstube|bäcker\b|backwaren|brot|brötchen|konditorei"),
    "fleischer": dict(ad="Fleischerei / Metzgerei", sektor="Perakende & Mağazacılık", verify=["fleischer"],
                      alan=r"fleischerei|metzgerei|schlachterei|wurstwaren|landmetzgerei|fleischwaren|(^|-)(fleischer|metzger)(-|$)",
                      metin=r"fleischerei|metzgerei|schlachterei|wurstwaren|fleischwaren|wurst\b|fleischerfachgeschäft|metzgermeister|fleischermeister"),
    "fliesen": dict(ad="Fliesenleger", sektor="İnşaat, Zanaat & Tesis", verify=["fliesen"],
                    alan=r"fliesen",
                    metin=r"fliesen"),
    "kalte": dict(ad="Kälte- und Klimatechnik", sektor="İnşaat, Zanaat & Tesis", verify=["kalte"],
                  alan=r"kaelte|klimatechnik|klimaanlage|kuehltechnik|klimaservice|klimasysteme|xn--klte|(^|-)klima(-|$)",
                  metin=r"kälte|kaelte|klimatechnik|klimaanlage|kühltechnik|klimaservice|kälteanlage|wärmepumpe"),
    "elektro_ing": dict(ad="Elektrotechnik / Automatisierung / Schaltschrankbau", sektor="Sanayi & Mühendislik", verify=["elektro_ing"],
                        alan=r"automatisierung|automation|schaltschrank|steuerungstechnik|steuerungsbau|antriebstechnik|regelungstechnik|(^|-)msr(-|$)|elektronik",
                        metin=r"automatisierung|automation|schaltschrank|steuerungstechnik|steuerungsbau|antriebstechnik|regelungstechnik|\bmsr\b|elektronik|sps\b"),
    "gartner": dict(ad="Garten- und Landschaftsbau", sektor="İnşaat, Zanaat & Tesis", verify=["gartner"],
                    alan=r"galabau|landschaftsbau|gartenbau|gartengestaltung|gartenpflege|baumschule|gaertnerei|gartenservice|landschaftsgaertner|gartenbetrieb|garten-und-landschaft|gartenlandschaft",
                    metin=r"garten-? ?und ?landschaftsbau|landschaftsbau|galabau|gartenbau|gartengestaltung|gartenpflege|baumschule|gärtnerei|landschaftsgärtner"),
    "elektro": dict(ad="Elektroinstallation / Elektrotechnik-Handwerk", sektor="İnşaat, Zanaat & Tesis", verify=["elektro"],
                    alan=r"elektro(?!nik|auto|mobil|rad|fahr|roller|scooter|markt|handel|geraet|bike|shop|welt)|elektrotechnik|elektroinstallation|elektromeister|elektroanlagen|elektroservice|(^|-)elektrik(-|$)",
                    metin=r"elektroinstallation|elektrotechnik|elektromeister|elektroanlagen|elektriker|elektrofachbetrieb|elektro-?installation|elektrobetrieb|schaltanlagen|gebäudetechnik|photovoltaik|smart ?home|elektrohandwerk"),
    "buchhaltung": dict(ad="Steuerberater / Buchhaltungsbüro", sektor="Hukuk, Muhasebe & Vergi", verify=["buchhaltung"],
                        alan=r"steuerberat|steuerkanzlei|steuerbuero|buchhaltung|buchfuehrung|lohnbuero|wirtschaftspruef|(^|-)(stb|steuern)(-|$)",
                        metin=r"steuerberat|steuerkanzlei|steuerbüro|buchhaltung|buchführung|lohnbüro|wirtschaftsprüf|jahresabschl|steuererklärung"),
    "it": dict(ad="Softwarehaus / IT-Beratung / SAP / Data-Analytics", sektor="Yazılım & Teknoloji", verify=["it", "dev"],
               alan=r"software|systemhaus|informatik|(^|-)(it|edv|sap|erp|data|daten|analytics|cloud|ki|ai)(-|$)|it-service|it-solutions|it-beratung|it-consulting|datenanalyse|machine-learning",
               metin=r"software|it-service|it-dienstleist|systemhaus|informatik|\bsap\b|\berp\b|datenanalyse|data analytics|\bki\b|künstliche intelligenz|machine learning|cloud|it-beratung|it-consulting|digitalisierung|entwickl"),
}
for _m in MESLEKLER.values():
    _m["alan_re"] = re.compile(_m["alan"], re.I)
    _m["metin_re"] = re.compile(_m["metin"], re.I)
ONCELIK = {m: i for i, m in enumerate(MESLEKLER)}

PLATFORM = ("facebook.", "instagram.", "linkedin.", "google.", "wixsite.", "jimdo", "business.site", "xing.",
            "youtube.", "twitter.", "tiktok.", "wordpress.com", "blogspot.", "webnode.", "jimdosite.", "site123.",
            "weebly.", "yelp.", "gelbeseiten.", "dasoertliche.", "11880.", "meinestadt.", "cylex.", "kununu.")
# Gecersiz .de etiketleri (parked, spam vs.) icin kaba eleme
ALAN_ATLA = re.compile(r"^(www|mail|ftp|test|shop|blog|forum|news|jobs|job|stellen|karriere|wiki|app|api|cdn|static|"
                       r"de|com|net|org|info|online|web|home|xn--[a-z0-9]{1,3})$")

# Basta lookbehind: uzun harf/rakam dizilerinde (base64, data: URI) her konumdan yeniden tarama = O(n^2) donmasini onler
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UZUN_DIZI = re.compile(r"data:[^\s\"')]{100,}|[A-Za-z0-9+/=_-]{200,}")
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
OBF_AT = re.compile(r"\s*[\(\[\{]\s*(?:at|ät|aet)\s*[\)\]\}]\s*|\s+at\s+(?=[a-z0-9.-]+\s*(?:[\(\[\{]\s*(?:dot|punkt)\s*[\)\]\}]|\.))", re.I)
OBF_DOT = re.compile(r"\s*[\(\[\{]\s*(?:dot|punkt)\s*[\)\]\}]\s*", re.I)
OBF_WP = re.compile(r"\s*dontospamme\s*@\s*gowaway\.\s*", re.I)
OBF_BOSLUK = re.compile(r"([A-Za-z0-9._%+-]{2,})\s+@\s+([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
ROL = ("info", "kontakt", "contact", "mail", "email", "e-mail", "post", "office", "buero", "büro", "zentrale", "service",
       "anfrage", "anfragen", "hallo", "hello", "willkommen", "team", "sekretariat", "empfang", "rezeption", "vertrieb",
       "sales", "hr", "personal", "bewerbung", "bewerbungen", "jobs", "karriere", "career", "kanzlei", "praxis",
       "verwaltung", "kontakt", "dispo", "disposition", "reisen", "leitstelle", "geschaeftsstelle", "geschäftsstelle",
       "zentrale", "firma", "company", "webmaster", "admin", "support", "kundenservice", "kundendienst", "auftrag",
       "bestellung", "shop", "verkauf", "einkauf", "buchhaltung", "rechnung", "steuerberater", "bäckerei", "baeckerei",
       "metzgerei", "fleischerei", "apotheke", "labor", "werkstatt", "technik", "planung", "projekt", "marketing",
       "presse", "pr", "management", "direktion", "gf", "geschaeftsfuehrung", "geschäftsführung", "sekretariat", "welcome")
JUNK_LOCAL = {"noreply", "no-reply", "donotreply", "postmaster", "abuse", "hostmaster", "privacy", "datenschutz",
              "dpo", "gdpr", "dsb", "datenschutzbeauftragter"}
JUNK_SUBSTR = ("sentry.io", "wixpress", "example.", "domain.com", "yourdomain", "@2x.", ".png", ".jpg", ".jpeg",
               ".gif", ".svg", ".webp", ".css", ".js", "u003e", "email@", "your@", "name@", "test@", "abc@", "xxx@",
               "muster", "beispiel", "mustermann")
KISISEL = re.compile(r"^[a-z]{2,}[._-][a-z]{2,}$|^[a-z]\.[a-z]{2,}$|^[a-z]{2,}\.[a-z]$")   # vorname.nachname / v.nachname
PARK = re.compile(r"domain (is )?for sale|domain kaufen|diese domain (steht|ist)|parked|sedo\.com|domain-?parking|nicsell|domainprofi|"
                  r"website is under construction|baustelle|coming soon|this domain|hier entsteht|domain wird|domain (zu verkaufen|erwerben)|"
                  r"afternic|dan\.com|spaceship\.com|united-domains|domain steht zum verkauf", re.I)
IMPRESSUM_LINK = re.compile(r"impressum|imprint|anbieterkennzeichnung|legal-?notice", re.I)
KONTAKT_LINK = re.compile(r"kontakt|contact|anfahrt|ansprechpartner", re.I)
PLZ_RE = re.compile(r"(?<!\d)(?:D-?\s?)?(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-]+(?:\s(?:am|an|der|im|in|bei|ob|a\.|i\.|v\.|d\.)\s?[A-ZÄÖÜ][\w.\-]+|\s[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-]+)?)")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
GENEL_BASLIK = re.compile(r"^(home|startseite|willkommen|herzlich willkommen|start|index|aktuelles|impressum|kontakt|über uns|ueber uns)$", re.I)

_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}
_ROBOTS_KILIT = threading.Lock()
_LOG_KILIT = threading.Lock()
_PLZ: dict[str, str] | None = None


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(m: str) -> None:
    s = f"[{now()}] zayif: {m}"
    with _LOG_KILIT:
        print(s, flush=True)
        ZAYIF.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(s + "\n")


# ------------------------------------------------------------------ veritabani
SEMA = """
CREATE TABLE IF NOT EXISTS adaylar (
    domain TEXT PRIMARY KEY, meslekler TEXT NOT NULL, kaynak TEXT, osm_ad TEXT DEFAULT '', osm_sehir TEXT DEFAULT '',
    havuz TEXT, durum TEXT DEFAULT 'yeni', tries INTEGER DEFAULT 0,
    company TEXT DEFAULT '', city TEXT DEFAULT '', website TEXT DEFAULT '', email TEXT DEFAULT '', source_url TEXT DEFAULT '',
    uyum INTEGER DEFAULT 0, sayfa INTEGER DEFAULT 0, note TEXT DEFAULT '', scanned_at TEXT,
    yuklendi TEXT, yuklendi_at TEXT, verify_eklendi INTEGER DEFAULT 0, created_at TEXT, oncelik INTEGER DEFAULT 99
);
CREATE INDEX IF NOT EXISTS ix_aday_durum ON adaylar(havuz, durum);
CREATE INDEX IF NOT EXISTS ix_aday_oncelik ON adaylar(oncelik);
CREATE TABLE IF NOT EXISTS yuklemeler (
    meslek TEXT, dosya TEXT, satir INTEGER, eklenen INTEGER, guncellenen INTEGER, reddedilen INTEGER, at TEXT
);
"""


def db() -> sqlite3.Connection:
    ZAYIF.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SEMA)
    return conn


def yaz(fn, deneme: int = 8):
    for i in range(deneme):
        conn = db()
        try:
            r = fn(conn); conn.commit(); return r
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == deneme - 1:
                raise
            time.sleep(0.5 + i)
        finally:
            conn.close()


# ------------------------------------------------------------------ 1) aday
def alan_etiketi(domain: str) -> str:
    return domain[:-3] if domain.endswith(".de") else domain.rsplit(".", 1)[0]


def meslek_esle_alan(domain: str) -> list[str]:
    etiket = alan_etiketi(domain)
    if "." in etiket or ALAN_ATLA.match(etiket) or len(etiket) < 4:
        return []
    return [m for m, t in MESLEKLER.items() if t["alan_re"].search(etiket)]


def meslek_esle_ad(ad: str, tags: dict) -> list[str]:
    tv = {f"{k}={tags[k]}" for k in ("shop", "craft", "office", "healthcare", "amenity", "emergency") if k in tags}
    ETIKET = {"fahrzeug": {"shop=truck", "shop=trailer", "shop=truck_repair"}, "ergo": {"healthcare=occupational_therapist"},
              "rettung": {"emergency=ambulance_station"}, "backer": {"shop=bakery", "craft=bakery"},
              "fleischer": {"shop=butcher", "craft=butcher"}, "fliesen": {"craft=tiler"}, "kalte": {"craft=hvac"},
              "gartner": {"craft=gardener"}, "elektro": {"craft=electrician"},
              "buchhaltung": {"office=tax_advisor", "office=accountant", "office=tax"},
              "it": {"office=it", "office=software", "office=web_design", "office=data_processing", "office=computer", "craft=it"}}
    sonuc = []
    for m, t in MESLEKLER.items():
        if (tv & ETIKET.get(m, set())) or t["metin_re"].search(ad) or (m == "kinder" and "paediatric" in tags.get("healthcare:speciality", "")):
            sonuc.append(m)
    return sonuc


def host_of(url: str) -> str:
    if not url:
        return ""
    h = (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower().strip(".")
    return h[4:] if h.startswith("www.") else h


def aday_topla(sadece: set[str] | None = None) -> None:
    """Common Crawl alan adi listesi + OSM onbellegi -> adaylar tablosu."""
    conn = db()
    var = {r[0] for r in conn.execute("SELECT domain FROM adaylar")}
    yeni: dict[str, dict] = {}
    # a) Common Crawl domain vertices: "<id>\t<ters alan adi>\t<host sayisi>" (ters: de.baeckerei-mueller)
    if CC_GZ.exists():
        t0 = time.time(); n = 0
        with gzip.open(CC_GZ, "rt", encoding="utf-8", errors="replace") as f:
            for satir in f:
                p = satir.rstrip("\n").split("\t")
                if len(p) < 2 or not p[1].startswith("de."):
                    continue
                n += 1
                parcalar = p[1].split(".")
                if len(parcalar) != 2:              # yalniz ikinci seviye .de alan adlari
                    continue
                domain = parcalar[1] + ".de"
                mes = meslek_esle_alan(domain)
                if sadece:
                    mes = [m for m in mes if m in sadece]
                if mes and domain not in var and domain not in yeni:
                    yeni[domain] = {"meslekler": mes, "kaynak": "cc", "ad": "", "sehir": ""}
        log(f"aday: Common Crawl .de alan adi {n}, eslesen yeni {len(yeni)} ({time.time() - t0:.0f} sn)")
    else:
        log(f"aday: {CC_GZ.name} yok, Common Crawl atlandi")
    # b) OSM onbellegi (scraper/data/osm): ad/etiket eslesmesi; sitesi olanlar
    n_osm = 0
    if OSM_DIR.exists():
        for dosya in OSM_DIR.glob("*.json"):
            try:
                els = json.loads(dosya.read_text(encoding="utf-8"))
            except Exception:
                continue
            for e in els:
                t = e.get("tags", {}); ad = t.get("name", "")
                if not ad:
                    continue
                domain = host_of(t.get("website") or t.get("contact:website") or "")
                if not domain or any(p in domain for p in PLATFORM) or domain in var:
                    continue
                mes = meslek_esle_ad(ad, t)
                if sadece:
                    mes = [m for m in mes if m in sadece]
                if not mes:
                    continue
                if domain in yeni:
                    y = yeni[domain]; y["meslekler"] = sorted(set(y["meslekler"]) | set(mes), key=ONCELIK.get)
                    if not y["ad"]:
                        y["ad"], y["sehir"], y["kaynak"] = ad, t.get("addr:city", ""), y["kaynak"] + "+osm"
                else:
                    yeni[domain] = {"meslekler": mes, "kaynak": "osm", "ad": ad, "sehir": t.get("addr:city", "")}; n_osm += 1
        log(f"aday: OSM onbelleginden yeni {n_osm}")
    conn.executemany("INSERT OR IGNORE INTO adaylar(domain, meslekler, kaynak, osm_ad, osm_sehir, created_at, oncelik) VALUES (?,?,?,?,?,?,?)",
                     [(d, ",".join(y["meslekler"]), y["kaynak"], y["ad"], y["sehir"], now(), min(ONCELIK[m] for m in y["meslekler"])) for d, y in yeni.items()])
    conn.commit()
    sayim = {m: 0 for m in MESLEKLER}
    for (ms,) in conn.execute("SELECT meslekler FROM adaylar"):
        for m in ms.split(","):
            sayim[m] += 1
    conn.close()
    log("aday: toplam " + ", ".join(f"{m} {n}" for m, n in sayim.items()))


# ------------------------------------------------------------------ 2) ele (havuzda olanlari cikar)
def havuz_modul():
    spec = importlib.util.spec_from_file_location("havuz_gonder", GONDER)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def ele(limit: int | None = None) -> None:
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("ele: JOBFIND_HAVUZ_ANAHTAR tanimsiz; havuz sorgusu yapilamadi"); return
    hg = havuz_modul()
    conn = db()
    domainler = [r[0] for r in conn.execute("SELECT domain FROM adaylar WHERE havuz IS NULL ORDER BY rowid" + (f" LIMIT {int(limit)}" if limit else ""))]
    conn.close()
    if not domainler:
        return
    bilinen_n = bilinmeyen_n = 0
    for i in range(0, len(domainler), 2000):
        parca = domainler[i:i + 2000]
        r = hg.istek("/api/havuz/bilinen", {"domainler": parca})
        bilinen = r.get("bilinen", {}); bilinmeyen = set(r.get("bilinmeyen", []))
        guncel = [(bilinen.get(d) or ("bilinmeyen" if d in bilinmeyen else "bilinmeyen"), d) for d in parca]
        yaz(lambda c, g=guncel: c.executemany("UPDATE adaylar SET havuz=? WHERE domain=?", g))
        bilinen_n += len(bilinen); bilinmeyen_n += len(parca) - len(bilinen)
        time.sleep(1.0)
    log(f"ele: {len(domainler)} sorgulandi -> havuzda {bilinen_n}, bilinmeyen {bilinmeyen_n}")


# ------------------------------------------------------------------ 3) tara
def robots(domain: str) -> urllib.robotparser.RobotFileParser:
    with _ROBOTS_KILIT:
        rp = _ROBOTS.get(domain)
    if rp:
        return rp
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = requests.get(f"https://{domain}/robots.txt", headers={"User-Agent": UA}, timeout=TIMEOUT_S, allow_redirects=True)
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])
    with _ROBOTS_KILIT:
        _ROBOTS[domain] = rp
    return rp


def izinli(rp, url: str) -> bool:
    try:
        return rp.can_fetch(UA, url) or rp.can_fetch("*", url)
    except Exception:
        return True


def metin(html_: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", html_)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article|header|footer|td|th)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def cf_decode(hexstr: str) -> str:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except Exception:
        return ""


def epostalari_cikar(html_: str) -> set[str]:
    html_ = UZUN_DIZI.sub(" ", html_)          # gomulu base64/data URI: e-posta icermez, regexleri bogar
    ham = html.unescape(html_)
    bul = set(EMAIL_RE.findall(ham))
    for m in re.finditer(r'href=["\']mailto:([^"\'?]+)', ham, re.I):
        bul.update(EMAIL_RE.findall(m.group(1)))
    for hx in CFEMAIL_RE.findall(html_):
        d = cf_decode(hx)
        if "@" in d:
            bul.add(d)
    # gorunur metindeki (at)/[at]/(punkt) gizlemeleri
    duz = metin(html_)
    cozuk = OBF_DOT.sub(".", OBF_AT.sub("@", duz))
    cozuk = OBF_WP.sub("@", cozuk)                      # WordPress "dontospamme @ gowaway." gizlemesi
    cozuk = OBF_BOSLUK.sub(r"@", cozuk)            # "info @ firma.de"
    if cozuk != duz:
        bul.update(EMAIL_RE.findall(cozuk))
    return {b.strip().strip(".,;:'\"()<>").lower() for b in bul}


def eposta_sec(adaylar: set[str], hostlar: list[str]) -> str:
    """Yalniz sirketin kendi alan adindaki, rol (kisisel olmayan) adres. Yoksa ''."""
    kokler = {".".join(h.split(".")[-2:]) for h in hostlar if h}
    uygun = []
    for e in adaylar:
        if not EMAIL_RE.fullmatch(e) or len(e) > 90 or any(j in e for j in JUNK_SUBSTR):
            continue
        local, _, host = e.partition("@")
        if local in JUNK_LOCAL or ".".join(host.split(".")[-2:]) not in kokler:
            continue
        rol = local in ROL or local.split("-")[0] in ROL or local.split(".")[0] in ROL
        if not rol and KISISEL.match(local):
            continue                       # kisisel adres (vorname.nachname) toplanmaz
        uygun.append((0 if rol else 1, len(e), e))
    return sorted(uygun)[0][2] if uygun else ""


def plz_haritasi() -> dict[str, str]:
    global _PLZ
    if _PLZ is None:
        _PLZ = json.loads(PLZ_JSON.read_text(encoding="utf-8")) if PLZ_JSON.exists() else {}
    return _PLZ


def sehir_bul(impressum: str) -> str:
    plz = plz_haritasi()
    for m in PLZ_RE.finditer(impressum):
        if m.group(1) in plz:
            return plz[m.group(1)]
    for m in PLZ_RE.finditer(impressum):
        aday = re.sub(r"\s+(Deutschland|Germany|Tel|Telefon|Fon|Fax|Mobil|E-Mail|Mail|Web|Internet|Email|USt|Ust|Umsatzsteuer|Steuernummer|Handelsregister|Registergericht|Geschäftsführer|Vertreten|Inhaber)\b.*$", "", m.group(2)).strip(" .,-")
        if 2 <= len(aday) <= 40:
            return aday
    return ""


def site_kisalt(url: str) -> str:
    """Havuz siniri 500 karakter; sorgu dizili/uzun yonlendirmelerde (login vb.) yalniz kok adres."""
    p = urlsplit(url)
    if len(url) > 200 or p.query or "login" in p.path.lower() or "auth" in p.path.lower():
        return f"{p.scheme}://{p.netloc}/"
    return url[:500]


def baslik_adi(html_: str, domain: str) -> str:
    m = TITLE_RE.search(html_)
    if m:
        parcalar = [p.strip() for p in re.split(r"\s+[|–—\-:»«·]\s+|\s*::\s*", html.unescape(m.group(1))) if p.strip()]
        parcalar = [p for p in parcalar if not GENEL_BASLIK.match(p) and 3 <= len(p) <= 80
                    and not re.search(r"^(www\.)?[a-z0-9-]+\.[a-z]{2,}$|https?://", p, re.I)]
        if parcalar:
            return re.sub(r"\s+", " ", parcalar[0])
    etiket = alan_etiketi(domain)
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", etiket) if w)


def site_tara(domain: str, meslekler: list[str]) -> dict:
    """Ana sayfa + Impressum/Kontakt; kurumsal e-posta, tuzel ad, sehir, uyum."""
    sonuc = {"domain": domain, "durum": "hata", "company": "", "city": "", "website": "", "email": "", "source_url": "",
             "uyum": 0, "sayfa": 0, "note": ""}
    rp = robots(domain)
    s = requests.Session(); s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})
    son_istek = 0.0

    def getir(url: str):
        nonlocal son_istek
        if not izinli(rp, url):
            return None, url, "robots"
        gecen = time.monotonic() - son_istek
        if gecen < DELAY_S:
            time.sleep(DELAY_S - gecen)
        son_istek = time.monotonic()
        try:
            r = s.get(url, timeout=TIMEOUT_S, allow_redirects=True, stream=True)
            if r.status_code >= 400:
                return None, r.url, f"http-{r.status_code}"
            if "html" not in (r.headers.get("content-type") or "text/html").lower():
                return None, r.url, "html-degil"
            govde = r.raw.read(BYTE_TAVAN, decode_content=True)
            enc = r.encoding or "utf-8"
            try:
                return govde.decode(enc, "replace"), r.url, ""
            except LookupError:
                return govde.decode("utf-8", "replace"), r.url, ""
        except requests.exceptions.SSLError:
            return None, url, "ssl"
        except Exception as e:
            return None, url, type(e).__name__
    try:
        ana = ana_url = None; hata = ""
        for u in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
            ana, ana_url, hata = getir(u); sonuc["sayfa"] += 1
            if ana is not None:
                break
            if hata == "robots":
                sonuc["durum"] = "robots"; sonuc["note"] = "robots.txt izin vermiyor"; return sonuc
        if ana is None:
            sonuc["durum"] = "ulasilamadi"; sonuc["note"] = hata; return sonuc
        ana_host = host_of(ana_url)
        hostlar = [domain, ana_host]
        ana_metin = metin(ana)
        if ".".join(ana_host.split(".")[-2:]) != ".".join(domain.split(".")[-2:]):
            sonuc["note"] = f"yonlendirme: {ana_host}"
        if len(ana_metin) < 200 or PARK.search(ana_metin[:3000]) or PARK.search(ana_url):
            sonuc["durum"] = "park"; sonuc["note"] = "bos/park sayfa"; sonuc["website"] = ana_url; return sonuc
        sonuc["website"] = site_kisalt(ana_url)
        adaylar = epostalari_cikar(ana)
        kaynak = {e: ana_url for e in adaylar}
        # Impressum / Kontakt baglantilari
        linkler: dict[str, str] = {}
        for href, txt in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', ana, re.I | re.S):
            try:
                mutlak = urljoin(ana_url, html.unescape(href))
            except ValueError:
                continue
            p = urlsplit(mutlak)
            if p.scheme not in ("http", "https") or host_of(mutlak) not in hostlar or re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?)$", p.path, re.I):
                continue
            imza = f"{p.path} {metin(txt)[:60]}"
            if "impressum" not in linkler and IMPRESSUM_LINK.search(imza):
                linkler["impressum"] = mutlak
            elif "kontakt" not in linkler and KONTAKT_LINK.search(imza):
                linkler["kontakt"] = mutlak
        taban = f"{urlsplit(ana_url).scheme}://{urlsplit(ana_url).netloc}"
        # baglanti yoksa yaygin yollar (JS menulu sitelerde Impressum baglantisi HTML'de olmayabilir)
        sira = ([("impressum", linkler["impressum"])] if "impressum" in linkler else
                [("impressum", f"{taban}/impressum"), ("impressum", f"{taban}/impressum.html")])
        sira += ([("kontakt", linkler["kontakt"])] if "kontakt" in linkler else
                 [("kontakt", f"{taban}/kontakt"), ("kontakt", f"{taban}/kontakt.html")])
        impressum_metin = ""
        for tur, url in sira:
            if sonuc["sayfa"] >= MAX_SAYFA or (tur == "impressum" and impressum_metin):
                continue
            h, u, _ = getir(url); sonuc["sayfa"] += 1
            if h is None:
                continue
            m = metin(h)
            if tur == "impressum" and len(m) > 80 and IMPRESSUM_LINK.search(m[:2000]):
                impressum_metin = m
            for e in epostalari_cikar(h):
                kaynak.setdefault(e, u)
            if eposta_sec(set(kaynak), hostlar) and impressum_metin:
                break
        sonuc["email"] = eposta_sec(set(kaynak), hostlar)
        sonuc["source_url"] = kaynak.get(sonuc["email"], "") if sonuc["email"] else ""
        sonuc["company"] = legal_name_bul(impressum_metin) or baslik_adi(ana, domain)
        sonuc["city"] = sehir_bul(impressum_metin) if impressum_metin else ""
        # uyum: Impressum bulundu (gercek isletme) VE meslek metin deseni en az 2 kez geciyor
        butun = (ana_metin + "\n" + impressum_metin)
        sonuc["uyum"] = int(bool(impressum_metin) and any(len(MESLEKLER[m]["metin_re"].findall(butun)) >= 2 for m in meslekler))
        sonuc["durum"] = "tarandi"
        if not impressum_metin:
            sonuc["note"] = (sonuc["note"] + "; " if sonuc["note"] else "") + "impressum yok"
        return sonuc
    finally:
        s.close()


def tara(meslek: str | None = None, limit: int | None = None, isci: int = ISCI, surekli: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        conn = db()
        kosul = "havuz='bilinmeyen' AND durum IN ('yeni','hata') AND tries < 2 AND domain NOT LIKE '%.%.%'"
        if meslek:
            kosul += f" AND (','||meslekler||',') LIKE '%,{meslek},%'"
        satirlar = conn.execute(f"SELECT domain, meslekler FROM adaylar WHERE {kosul} ORDER BY oncelik, rowid LIMIT ?", (min(limit or 10**9, 2000),)).fetchall()
        conn.close()
        if not satirlar:
            if surekli:
                log("tara: kuyruk bos, 10 dk"); time.sleep(600); continue
            return
        t0 = time.time(); n = ok = 0
        with ThreadPoolExecutor(max_workers=isci) as pool:
            isler = {pool.submit(site_tara, r["domain"], r["meslekler"].split(",")): r["domain"] for r in satirlar}
            for f in as_completed(isler):
                d = isler[f]
                try:
                    r = f.result()
                except Exception as e:
                    r = {"domain": d, "durum": "hata", "note": f"{type(e).__name__}: {e}"[:200], "company": "", "city": "",
                         "website": "", "email": "", "source_url": "", "uyum": 0, "sayfa": 0}
                yaz(lambda c, r=r: c.execute(
                    "UPDATE adaylar SET durum=?, tries=tries+1, company=?, city=?, website=?, email=?, source_url=?, uyum=?, sayfa=?, note=?, scanned_at=? WHERE domain=?",
                    (r["durum"], r["company"], r["city"], r["website"], r["email"], r["source_url"], r["uyum"], r["sayfa"], r["note"], now(), r["domain"])))
                n += 1; ok += bool(r["email"] and r["uyum"])
                if n % 200 == 0:
                    log(f"tara: {n}/{len(satirlar)} site, {ok} uyumlu e-postali, {time.time() - t0:.0f} sn")
        log(f"tara: parti bitti {n} site, {ok} uyumlu e-postali, {time.time() - t0:.0f} sn")
        if limit:
            limit -= n
            if limit <= 0:
                return
        if not surekli and len(satirlar) < 2000:
            return


# ------------------------------------------------------------------ 4) yukle
def yukle(meslek: str) -> dict | None:
    if not os.environ.get("JOBFIND_HAVUZ_ANAHTAR", "").strip():
        log("yukle: JOBFIND_HAVUZ_ANAHTAR tanimsiz"); return None
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db()
    satirlar = conn.execute(
        "SELECT * FROM adaylar WHERE durum='tarandi' AND email<>'' AND uyum=1 AND yuklendi IS NULL AND (','||meslekler||',') LIKE ?",
        (f"%,{meslek},%",)).fetchall()
    conn.close()
    if not satirlar:
        return None
    damga = time.strftime("%Y%m%d-%H%M%S")
    dosya = OUT / f"yeni-{meslek}-{damga}.csv"
    with dosya.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "city", "sector", "website", "email", "source_url"])
        w.writeheader()
        for r in satirlar:
            w.writerow({"company": r["company"] or r["osm_ad"], "city": r["city"] or r["osm_sehir"], "sector": MESLEKLER[meslek]["sektor"],
                        "website": site_kisalt(r["website"]), "email": r["email"], "source_url": site_kisalt(r["source_url"])[:500]})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(GONDER), str(dosya), "--etiket", f"pc-{meslek}"],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=BASE)
    for satir in (p.stdout + p.stderr).splitlines():
        log(f"  yukle {meslek}: {satir}")
    m = re.search(r"TOPLAM (\{.*\})", p.stdout)
    if p.returncode != 0 or not m:
        log(f"yukle {meslek}: basarisiz (kod {p.returncode})"); return None
    toplam = json.loads(m.group(1))
    yaz(lambda c: (c.executemany("UPDATE adaylar SET yuklendi=?, yuklendi_at=? WHERE domain=?", [(f"pc-{meslek}", now(), r["domain"]) for r in satirlar]),
                   c.execute("INSERT INTO yuklemeler VALUES (?,?,?,?,?,?,?)", (meslek, dosya.name, len(satirlar), toplam.get("eklenen", 0),
                                                                             toplam.get("guncellenen", 0), toplam.get("reddedilen", 0), now()))))
    log(f"yukle {meslek}: {len(satirlar)} satir -> TOPLAM {json.dumps(toplam, ensure_ascii=False)}")
    return toplam


# ------------------------------------------------------------------ 5) verify-ekle (ilan cikarimi kuyruguna)
def verify_ekle() -> None:
    if not VERIFY_DB.exists():
        log("verify-ekle: verify.sqlite3 yok"); return
    conn = db()
    satirlar = conn.execute("SELECT * FROM adaylar WHERE durum='tarandi' AND uyum=1 AND verify_eklendi=0").fetchall()
    conn.close()
    if not satirlar:
        return
    sys.path.insert(0, str(BASE / "verify"))
    from verify.candidates import meslekler as katalog
    kat = katalog(); sira = {m: i for i, m in enumerate(json.loads((BASE / "verify" / "meslekler.json").read_text(encoding="utf-8"))["oncelik"])}
    v = sqlite3.connect(VERIFY_DB, timeout=60)
    n_s = n_c = 0
    for r in satirlar:
        ids = sorted({vid for m in r["meslekler"].split(",") for vid in MESLEKLER[m]["verify"] if vid in kat and vid in sira}, key=sira.get)
        if not ids:
            continue
        v.execute("INSERT OR IGNORE INTO sirketler(domain,company,city,category,website,oncelik) VALUES (?,?,?,?,?,?)",
                  (r["domain"], r["company"], r["city"], "zayif:" + r["meslekler"], r["website"] or f"https://{r['domain']}/", sira[ids[0]]))
        n_s += v.total_changes and 1
        for vid in ids:
            v.execute("INSERT OR IGNORE INTO ciftler(domain,meslek,oncelik) VALUES (?,?,?)", (r["domain"], vid, sira[vid])); n_c += 1
    v.commit(); v.close()
    yaz(lambda c: c.executemany("UPDATE adaylar SET verify_eklendi=1 WHERE domain=?", [(r["domain"],) for r in satirlar]))
    log(f"verify-ekle: {len(satirlar)} sirket, {n_c} cift verify kuyruguna eklendi")


# ------------------------------------------------------------------ 6) rapor
def rapor(yazdir: bool = True) -> str:
    conn = db()
    satirlar = ["# DURUM — zayıf meslek taraması", f"Güncelleme: {now()}", "",
                "| meslek | aday | havuzda yok | taranan | canlı+uyumlu | e-postalı | yüklenen | eklenen | güncellenen | red |",
                "|---|---|---|---|---|---|---|---|---|---|"]
    for m, t in MESLEKLER.items():
        k = f"%,{m},%"
        r = conn.execute("""SELECT COUNT(1), SUM(havuz='bilinmeyen'), SUM(durum<>'yeni' AND havuz='bilinmeyen'),
                            SUM(durum='tarandi' AND uyum=1), SUM(durum='tarandi' AND uyum=1 AND email<>''), SUM(yuklendi IS NOT NULL)
                            FROM adaylar WHERE (','||meslekler||',') LIKE ?""", (k,)).fetchone()
        y = conn.execute("SELECT COALESCE(SUM(eklenen),0), COALESCE(SUM(guncellenen),0), COALESCE(SUM(reddedilen),0) FROM yuklemeler WHERE meslek=?", (m,)).fetchone()
        satirlar.append(f"| {m} ({t['ad']}) | {r[0]} | {r[1] or 0} | {r[2] or 0} | {r[3] or 0} | {r[4] or 0} | {r[5] or 0} | {y[0]} | {y[1]} | {y[2]} |")
    d = conn.execute("SELECT durum, COUNT(1) FROM adaylar WHERE havuz='bilinmeyen' GROUP BY durum").fetchall()
    conn.close()
    satirlar += ["", "Tarama durumları (havuzda olmayanlar): " + ", ".join(f"{a} {b}" for a, b in d), "",
                 "Kurallar: robots.txt, alan adı başına ardışık istek ≥1 sn, 20 sn zaman aşımı, ≤4 sayfa/site; yalnız sitede yazılı rol e-postası (kişisel adres yok); ücretli API yok."]
    s = "\n".join(satirlar)
    DURUM_MD.write_text(s + "\n", encoding="utf-8")
    if yazdir:
        print(s)
    return s


# ------------------------------------------------------------------ calis: tam dongu
def calis() -> None:
    log("calis: basladi")
    conn = db(); n = conn.execute("SELECT COUNT(1) FROM adaylar").fetchone()[0]; conn.close()
    if n == 0:
        aday_topla()
    ele()
    son_rapor = 0.0
    while not (ZAYIF / "DUR").exists():
        conn = db()
        bekleyen = conn.execute("SELECT COUNT(1) FROM adaylar WHERE havuz='bilinmeyen' AND durum IN ('yeni','hata') AND tries<2").fetchone()[0]
        conn.close()
        if bekleyen:
            tara(limit=2000)
        for m in MESLEKLER:
            yukle(m)
        verify_ekle()
        if time.time() - son_rapor > 1800:
            rapor(yazdir=False); son_rapor = time.time()
        if not bekleyen:
            log("calis: kuyruk bos, 30 dk"); time.sleep(1800)
    log("calis: durdu (DUR)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["calis", "aday", "ele", "tara", "yukle", "verify-ekle", "rapor", "dene"])
    p.add_argument("--meslek"); p.add_argument("--limit", type=int); p.add_argument("--isci", type=int, default=ISCI)
    p.add_argument("--domain")
    a = p.parse_args()
    if a.komut == "calis":
        calis()
    elif a.komut == "aday":
        aday_topla({a.meslek} if a.meslek else None)
    elif a.komut == "ele":
        ele(a.limit)
    elif a.komut == "tara":
        tara(a.meslek, a.limit, a.isci)
    elif a.komut == "yukle":
        for m in ([a.meslek] if a.meslek else list(MESLEKLER)):
            yukle(m)
    elif a.komut == "verify-ekle":
        verify_ekle()
    elif a.komut == "rapor":
        rapor()
    elif a.komut == "dene":
        print(json.dumps(site_tara(a.domain, [a.meslek] if a.meslek else list(MESLEKLER)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
