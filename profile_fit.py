#!/usr/bin/env python3
"""Emin'in CV'si icin aciklanabilir sirket/rol uygunluk puanlamasi.

LLM gerektirmez: ayni girdi her zaman ayni puani verir. Puan yalnizca
yayimlanmis sirket ve ilan metnindeki kanitlardan uretilir; eksik deneyim ya da
vize destegi tahmin edilmez.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


TRACKS: dict[str, tuple[int, int, tuple[str, ...]]] = {
    "finance_ops": (4, 28, (
        "accounting", "accounts payable", "accounts receivable", "bookkeeping",
        "financial reporting", "finance operations", "month-end", "month end",
        "reconciliation", "tax", "audit", "payroll", "treasury", "controller",
        "erp", "billing", "invoicing", "payments", "fintech", "regtech",
        "financial services", "economics", "compliance", "gaap", "ifrs",
    )),
    "automation_ai": (5, 30, (
        "automation", "workflow", "process automation", "rpa", "artificial intelligence",
        "machine learning", "generative ai", "llm", "agentic", "n8n", "zapier",
        "integration platform", "orchestration", "document processing",
        "operational efficiency", "digital transformation", "process improvement",
    )),
    "backend_platform": (4, 24, (
        "python", "golang", " go developer", "fastapi", "backend", "back-end",
        "rest api", "api platform", "microservices", "docker", "kubernetes",
        "github actions", "devops", "cloud platform", "node.js", "mongodb",
        "postgresql", "sqlite", "supabase", "software as a service", "saas",
    )),
    "data_bi": (5, 28, (
        "data analytics", "data analysis", "business intelligence", "power bi",
        "data platform", "data engineering", "analytics engineering", "dashboard",
        "reporting automation", "sql", "pandas", "data modeling", "data model",
        "decision support", "kpi", "etl", "data pipeline", "insights",
    )),
    "finance_software": (5, 25, (
        "accounting software", "tax software", "finance platform", "payment platform",
        "billing platform", "expense management", "spend management", "open banking",
        "banking platform", "erp software", "financial automation", "paytech",
        "insurtech", "wealthtech", "invoice automation", "procure-to-pay",
    )),
}

ROLE_TERMS = (
    "finance analyst", "financial analyst", "finance operations", "accounting analyst",
    "accounts payable", "reporting analyst", "business intelligence analyst",
    "bi analyst", "data analyst", "junior data engineer", "analytics engineer",
    "automation engineer", "automation specialist", "process automation",
    "python developer", "backend developer", "backend engineer", "api engineer",
    "integration engineer", "solutions engineer", "implementation specialist",
    "technical operations", "business systems", "revenue operations", "finops",
    "tax technology", "finance systems", "erp specialist", "product analyst",
    "operations analyst", "graduate developer", "junior developer",
)

CAREER_SIGNALS = (
    "careers", "open positions", "open roles", "join our team", "vacancies",
    "we are hiring", "work with us", "job openings", "graduate programme",
    "graduate program", "early careers", "open application",
)

INTERNATIONAL_SIGNALS = (
    "international team", "global team", "multicultural", "english speaking",
    "working language is english", "offices worldwide", "global company",
    "distributed team", "remote-first", "remote first", "relocation support",
    "visa sponsorship", "work permit", "immigration support",
)

# Only explicit first-party corporate wording counts. A founder's/person's
# name, surname or visual appearance is never used to infer national origin.
TURKISH_COMPANY_SIGNALS = (
    "turkish company", "turkish-founded", "turkish founded",
    "turkish-owned", "turkish owned", "founded in turkey", "founded in türkiye",
    "founded in istanbul", "established in turkey", "established in türkiye",
    "headquartered in turkey", "headquartered in türkiye",
    "headquartered in istanbul", "turkey-based company", "türkiye-based company",
    "istanbul-based company", "turkish roots", "turkish origin",
    "originated in turkey", "originated in türkiye", "türkiye merkezli",
    "türkiye'de kuruldu", "türkiye'de kurulmuş", "istanbul'da kuruldu",
    "türk şirketi", "türk teknoloji şirketi",
    # 9 Eyl 2026: kampanyanin uc gorusmesi de Turk sahipli muhasebe/vergi
    # burolarindan geldi (Berlin Steuerberater, Amsterdam administratie, BG
    # muhasebe). Yurt disindaki Turk sahipli finans burolarinin sitelerinde
    # Turkce hizmet dili gecer; bunlar da ayni +15 sinyalini alsin.
    "türkçe konuşan", "türkçe hizmet", "türkçe danışmanlık", "türkçe destek",
    "muhasebe hizmet", "muhasebe ve vergi", "vergi danışmanlığı", "mali müşavir",
    "türk toplumu", "türk müşteri", "türk girişimci",
    "turks sprekend", "turkssprekend", "turkse ondernemers", "turkse gemeenschap",
    "türkischsprachig", "türkisch sprechend", "türkische unternehmer",
    "turkish-speaking", "turkish speaking", "turkish community", "turkish entrepreneurs",
)

SENIOR_TERMS = (
    "senior", "staff", "principal", "lead ", "manager", "director", "head of",
    "vice president", "vp ", "chief ", "architect",
)

NOISE_TERMS = (
    "restaurant", "hair salon", "beauty salon", "massage", "psychotherapy",
    "real estate agent", "travel agency", "car repair", "construction contractor",
    "dentist", "medical clinic", "veterinary", "funeral", "driving school",
    "jobcenter", "government office",
)

MAJOR_CITIES: dict[str, tuple[str, ...]] = {
    "IE": ("dublin", "cork", "galway", "limerick"),
    "PL": ("warszawa", "warsaw", "kraków", "krakow", "wrocław", "wroclaw",
           "poznań", "poznan", "gdańsk", "gdansk", "katowice", "łódź", "lodz"),
    "NL": ("amsterdam", "rotterdam", "utrecht", "eindhoven", "den haag",
           "the hague", "groningen", "tilburg", "breda", "arnhem"),
    "SE": ("stockholm", "göteborg", "gothenburg", "malmö", "malmo"),
    "DK": ("københavn", "copenhagen", "aarhus", "århus"),
    "NO": ("oslo", "bergen"),
    "MT": ("valletta", "sliema", "birkirkara", "san ġiljan", "st julian", "saint julian"),
    "LU": ("luxembourg city", "esch-sur-alzette", "kirchberg"),
    # İngiltere için ISO kodu GB; yalnızca kullanıcının kabul ettiği büyük şehirler.
    "GB": ("london", "manchester", "birmingham", "edinburgh", "glasgow", "bristol"),
    "CA": ("toronto", "vancouver", "calgary", "ottawa"),
    "NZ": ("auckland", "wellington", "christchurch"),
}

COUNTRY_LOCATION_TERMS: dict[str, tuple[str, ...]] = {
    "IE": ("ireland",), "GB": ("united kingdom", "england", "scotland", "wales"),
    "CA": ("canada",), "AU": ("australia",), "SG": ("singapore",),
    "AE": ("united arab emirates", "uae", "dubai", "abu dhabi"),
    "NZ": ("new zealand",), "QA": ("qatar", "doha"), "MT": ("malta",),
    "NL": ("netherlands", "nederland"), "DE": ("germany", "deutschland"),
    "CH": ("switzerland", "schweiz", "suisse"), "SE": ("sweden", "sverige"),
    "DK": ("denmark", "danmark"), "LU": ("luxembourg",),
    "NO": ("norway", "norge"), "FI": ("finland", "suomi"),
    "PT": ("portugal",), "PL": ("poland", "polska"),
}


# 6 Eyl 2026: uygunluk esigi tek kaynaktan gelir. Onceden 35 degeri
# profile_fit, deep_enrich, incremental_publish_worker ve publish_verified_batch
# icinde ayri ayri sabit kodluydu; deep_enrich'e --min-score verilse bile
# buradaki 35 kazandigi icin baraj gercekte hic inmiyordu.
QUALIFY_MIN_SCORE = 25


@dataclass(frozen=True)
class FitResult:
    score: int
    tracks: tuple[str, ...]
    reasons: tuple[str, ...]
    keywords: tuple[str, ...]
    english_signal: bool
    qualified: bool


def normalize_text(value: str) -> str:
    value = (value or "").casefold().replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term.strip() for term in terms if term.strip() in text]


def role_fit(title: str) -> int:
    title = normalize_text(title)
    if not title:
        return 0
    hits = _matched_terms(title, ROLE_TERMS)
    if not hits:
        return 0
    score = min(32, 18 + 5 * (len(hits) - 1))
    if any(term in title for term in SENIOR_TERMS):
        score = max(4, score - 15)
    if any(term in title for term in ("junior", "graduate", "associate", "analyst", "specialist")):
        score = min(35, score + 7)
    return score


def target_country_from_location(location: str,
                                 countrywide: set[str] | None = None) -> tuple[str, str] | None:
    """Match major cities, plus explicitly enabled country-wide campaigns."""
    text = normalize_text(location)
    for country, cities in MAJOR_CITIES.items():
        for city in cities:
            if normalize_text(city) in text:
                return country, city
    for country in sorted(countrywide or set()):
        for term in COUNTRY_LOCATION_TERMS.get(country, ()):
            if re.search(rf"(?<!\w){re.escape(normalize_text(term))}(?!\w)", text):
                return country, "countrywide"
    return None


def score_profile(text: str, *, name: str = "", domain: str = "",
                  job_titles: tuple[str, ...] = (),
                  min_score: int = QUALIFY_MIN_SCORE) -> FitResult:
    haystack = normalize_text(" ".join((name, domain, text, " ".join(job_titles))))
    score = 0
    matched_tracks: list[str] = []
    reasons: list[str] = []
    keywords: list[str] = []
    track_contributions: list[int] = []

    for track, (weight, cap, terms) in TRACKS.items():
        hits = _matched_terms(haystack, terms)
        if not hits:
            continue
        contribution = min(cap, len(set(hits)) * weight)
        score += contribution
        track_contributions.append(contribution)
        matched_tracks.append(track)
        keywords.extend(hits)
        reasons.append(f"{track}:{contribution}")

    role_scores = [(title, role_fit(title)) for title in job_titles]
    role_scores = [(title, value) for title, value in role_scores if value]
    if role_scores:
        best_title, best_score = max(role_scores, key=lambda item: item[1])
        score += best_score
        reasons.append(f"role:{best_title[:70]}:+{best_score}")

    career_hits = _matched_terms(haystack, CAREER_SIGNALS)
    if career_hits:
        bonus = min(8, len(career_hits) * 2)
        score += bonus
        reasons.append(f"career_signal:+{bonus}")
        keywords.extend(career_hits)

    international_hits = _matched_terms(haystack, INTERNATIONAL_SIGNALS)
    if international_hits:
        bonus = min(12, len(international_hits) * 4)
        score += bonus
        reasons.append(f"international:+{bonus}")
        keywords.extend(international_hits)

    turkish_company_hits = _matched_terms(haystack, TURKISH_COMPANY_SIGNALS)
    if turkish_company_hits:
        score += 15
        reasons.append("turkish_company:+15")
        keywords.append("turkish-company")

    english_markers = _matched_terms(haystack, (
        " our ", " we ", " with ", " solutions", " careers", "platform",
        "services", "technology", "business", "team",
    ))
    english_signal = len(english_markers) >= 4
    if english_signal:
        score += 5
        reasons.append("english_site:+5")

    if len(matched_tracks) >= 2:
        bonus = min(12, 4 * (len(matched_tracks) - 1))
        score += bonus
        reasons.append(f"hybrid_profile:+{bonus}")
    elif track_contributions and max(track_contributions) >= 24:
        score += 6
        reasons.append("strong_single_track:+6")

    noise_hits = _matched_terms(haystack, NOISE_TERMS)
    if noise_hits:
        penalty = min(50, 25 + 5 * (len(noise_hits) - 1))
        score -= penalty
        reasons.append(f"unrelated:-{penalty}")

    score = max(0, min(100, score))
    qualified = bool(matched_tracks) and score >= min_score
    return FitResult(
        score=score,
        tracks=tuple(matched_tracks),
        reasons=tuple(reasons),
        keywords=tuple(dict.fromkeys(keywords)),
        english_signal=english_signal,
        qualified=qualified,
    )
