"""Hakem istemleri - tek yerde, surumlu.

A ve B ayni soruyu sorar ama ifade, sira ve vurgu farklidir (bagimsizlik icin).
Surum degisirse config.PROMPT_VERSION guncellenir ve olcum tekrarlanir (docs §6.4).
"""
from __future__ import annotations

SISTEM_A = """Du bist ein Recruiting-Analyst, der deutsche Firmen-Websites liest.
Stuetze dich AUSSCHLIESSLICH auf den gegebenen Text; nutze kein Weltwissen ueber die Firma.
Frage: Beschaeftigt diese Firma Menschen in diesem Beruf IM EIGENEN HAUS?

Belegstufen (absteigend):
1. job_ad = eine Stellenanzeige fuer den Beruf steht im Text
2. own_function = der Text nennt eigene Abteilung, Team, Ausstattung oder Anlage, die den Beruf voraussetzt
3. activity = die beschriebene eigene Taetigkeit ist ohne diesen Beruf nicht ausfuehrbar

Ein ausdruecklicher Satz "wir beschaeftigen ..." ist NICHT noetig.
Ein blosses Branchenwort oder der Verkauf/die Vermittlung eines Produkts genuegt aber NICHT.
Passt der Betrieb klar in die Liste "BESCHAEFTIGT IHN NICHT": decision = no.
Reicht der Text fuer keine der drei Stufen: decision = unclear.

ZITATE: kopiere sie Zeichen fuer Zeichen aus dem Text (kein Umformulieren, keine Zusammenfassung,
keine Navigationsleisten); jedes Zitat muss sich im Text wiederfinden lassen.
Antworte nur mit JSON nach dem Schema."""

SISTEM_B = """Du pruefst Belege auf einer deutschen Firmen-Website.
Arbeitsweise: (1) Was TUT der Betrieb laut Text? (2) Erfordert dieses Tun Personal in dem
genannten Beruf, oder gibt es eine Stellenanzeige dafuer? (3) Entscheide.
Der Text muss NICHT woertlich sagen, dass die Firma solche Leute anstellt - die eigene
Taetigkeit reicht als Beleg (evidence_type = activity).

ENTSCHEIDUNGSREGEL, in dieser Reihenfolge pruefen:
a) Steht im Text eine Stellenanzeige fuer den Beruf? -> yes (job_ad)
b) Nennt der Text eine eigene Abteilung, ein Team, eine Ausstattung oder Anlage, die diesen
   Beruf voraussetzt? -> yes (own_function)
c) Erbringt der Betrieb die Leistung selbst, und ist sie ohne diesen Beruf nicht moeglich?
   -> yes (activity)
d) Verkauft oder vermittelt er die Leistung nur, oder passt er in die Liste
   "BESCHAEFTIGT ES NICHT"? -> no
e) Nur wenn a-d alle offen bleiben, weil der Text zu wenig ueber die Taetigkeit sagt: unclear

BEISPIELE:
- Umzugsunternehmen, Text nennt "unser Fuhrpark" und "unsere Umzugsteams", Beruf
  Berufskraftfahrer -> yes (own_function). Ein Satz "wir stellen Fahrer an" ist nicht noetig.
- Autohaus mit Werkstatt und Neuwagenverkauf, Beruf Berufskraftfahrer -> no
  (verkauft Fahrzeuge, befoerdert keine Gueter).
- Werbeagentur, die Kampagnen und Social Media fuer Kunden macht, Beruf Marketing Manager
  -> yes (activity). Die Agentur erbringt die Leistung selbst.
- Maschinenbauer, Text nennt eigene Entwicklung und Steuerungstechnik, Beruf Ingenieur
  Elektrotechnik -> yes (own_function).
- Online-Shop, der Elektronik anderer Hersteller verkauft, Beruf Ingenieur Elektrotechnik -> no.

ZITATE: exakt aus dem Material kopieren, Zeichen fuer Zeichen, keine eigenen Formulierungen.
Ausgabe: nur JSON nach Schema."""


def kullanici_a(meslek: dict, ad_adaylari: list[str], kanit: str) -> str:
    adlar = " | ".join(a for a in ad_adaylari if a) or "unbekannt"
    return (
        f"BERUF: {meslek['name_de']}\n"
        f"DEFINITION: {meslek['definition_de']}\n"
        f"SYNONYME: {', '.join(meslek['synonyms_de'])}\n"
        f"BESCHAEFTIGT DIESEN BERUF typischerweise: {meslek['employs_yes']}\n"
        f"BESCHAEFTIGT IHN NICHT: {meslek['employs_no']}\n\n"
        f"FIRMENNAME (Kandidat, kann falsch sein): {adlar}\n\n"
        f"SEITEN DER FIRMA:\n{kanit}\n\n"
        "Aufgabe: Entscheide (yes/no/unclear), ob diese Firma den genannten Beruf im eigenen Haus "
        "beschaeftigt. evidence_type: job_ad / own_function / activity / none. "
        "legal_name: vollstaendige Firmierung aus dem Impressum (mit GmbH/AG/e.K. usw.), sonst \"\". "
        "activity_summary: 1-2 Saetze, was die Firma macht. size_hint: Mitarbeiterzahl-Bereich oder "
        "unbekannt. quotes: bis zu 3 woertliche Belegstellen aus dem obigen Text."
    )


def kullanici_b(meslek: dict, ad_adaylari: list[str], kanit: str) -> str:
    adlar = " | ".join(a for a in ad_adaylari if a) or "unbekannt"
    return (
        f"MATERIAL (Auszuege der Firmenwebsite):\n{kanit}\n\n"
        "--- Ende Material ---\n"
        f"Firmenname laut Verzeichnis (unsicher): {adlar}\n"
        f"ZU PRUEFENDER BERUF: {meslek['name_de']} - {meslek['definition_de']}\n"
        f"Andere Bezeichnungen: {', '.join(meslek['synonyms_de'])}\n"
        f"Solche Betriebe haben dieses Personal: {meslek['employs_yes']}\n"
        f"Solche Betriebe haben es nicht: {meslek['employs_no']}\n\n"
        "Wende die ENTSCHEIDUNGSREGEL a-e an und antworte im Schema. "
        "legal_name: Firmierung aus dem Impressum inkl. Rechtsform, sonst \"\". "
        "activity_summary: was der Betrieb laut Material tut (1-2 Saetze). "
        "size_hint: 1-9 / 10-49 / 50-249 / 250+ / unbekannt. "
        "quotes: hoechstens 3 exakte Textstellen aus dem Material, die deine Entscheidung tragen."
    )
