"""
Data Source Detector — identifies which data sources a protocol uses.

Exact replication of parser.py detect_data_source() + extract_data_source_section().

parser.py rules preserved:
  Rule 2 — Longest-key-first matching
  Rule 3 — Erase matched key before next search (no double-match)
  Rule 6 — Run detect on the Data Sources section of the document
  Rule 7 — DATA_SOURCE_MASTER canonical name dictionary
"""
from __future__ import annotations

import re

# ── DATA_SOURCE_MASTER ─────────────────────────────────────────────────────────
# Merged from original parser.py + expanded for Premier project.
# Keys are lower-cased; sorted longest-first at runtime (Rule 2).
DATA_SOURCE_MASTER: dict[str, str] = {
    # Premier
    "premier healthcare database":      "Premier Healthcare Database",
    "pinc ai healthcare database":      "Premier Healthcare Database",
    "pinc ai phd":                      "Premier Healthcare Database",
    "premier phd":                      "Premier Healthcare Database",
    "pinc ai":                          "Premier Healthcare Database",
    "premier":                          "Premier Healthcare Database",
    "phd v2":                           "Premier Healthcare Database",
    "phd":                              "Premier Healthcare Database",

    # Optum
    "optum clinformatics date of death": "Optum DOD/SES",
    "optum clinformatics":              "Optum Clinformatics Data Mart",
    "clinformatics":                    "Optum Clinformatics Data Mart",
    "optum ehr":                        "Optum EHR",
    "optum dod":                        "Optum DOD/SES",
    "optum ses":                        "Optum DOD/SES",
    "optum":                            "Optum",

    # IBM MarketScan / Truven
    "ibm marketscan":                   "IBM MarketScan",
    "marketscan":                       "IBM MarketScan",
    "truven":                           "IBM MarketScan",
    "ccae":                             "IBM MarketScan",
    "mdcr":                             "IBM MarketScan",
    "mdcd":                             "IBM MarketScan",

    # Medicare / CMS
    "medicare claims":                  "Medicare Claims (CMS)",
    "medicare":                         "Medicare Claims (CMS)",
    "medicaid":                         "Medicaid",
    "cms":                              "Medicare Claims (CMS)",

    # EHR
    "electronic health record":         "EHR",
    "electronic medical record":        "EHR",
    "ehr":                              "EHR",
    "emr":                              "EHR",

    # Other databases (from original parser.py)
    "healthverity":                     "HealthVerity",
    "concert ai":                       "Concert AI",
    "flatiron":                         "Flatiron Health",
    "truveta":                          "Truveta",
    "komodo":                           "Komodo",
    "symphony health":                  "Symphony Health",
    "iqvia":                            "IQVIA",
    "trinetx":                          "TriNetX",
    "allscripts":                       "Allscripts",
    "epic":                             "Epic EHR",
    "mercy":                            "Mercy",
    "cprd":                             "CPRD",
    "hes":                              "HES",
    "salford":                          "Salford Royal",
    "jmdc":                             "JMDC",
    "loopback":                         "Loopback",
    "integra":                          "Integra",
    "connect":                          "Connect",
}


class DataSourceDetector:
    """
    Detects data sources from protocol text.

    Mirrors parser.py exactly:
      1. extract_data_source_section() — narrow to "Data Sources" section
      2. Sort keys longest-first (Rule 2)
      3. Word-boundary regex match per key
      4. Erase matched text before next search (Rule 3)
      5. Return unique canonical names
    """

    def __init__(self) -> None:
        self._sorted_keys: list[str] = sorted(
            DATA_SOURCE_MASTER.keys(), key=len, reverse=True
        )

    def detect(self, full_text: str) -> list[str]:
        """
        Detect all data sources. Mirrors parser.py detect_data_source(text).
        Rule 6: receives full document text; narrows to Data Sources section internally.
        """
        if not full_text:
            return []

        # Extract the Data Sources section (original: extract_data_source_section)
        section = self._extract_data_source_section(full_text)
        # If no dedicated section found, fall back to searching full text
        search_text = (section if section else full_text).lower()

        found_canonical: list[str] = []
        seen_canonical: set[str] = set()

        for key in self._sorted_keys:
            # Rule 2+3: word-boundary search, erase on match (original uses \b)
            pattern = r"\b" + re.escape(key) + r"\b"
            if re.search(pattern, search_text):
                canonical = DATA_SOURCE_MASTER[key]
                # Rule 3: erase matched key so shorter aliases don't re-match
                search_text = re.sub(pattern, " " * len(key), search_text)
                if canonical not in seen_canonical:
                    found_canonical.append(canonical)
                    seen_canonical.add(canonical)

        return found_canonical

    @staticmethod
    def _extract_data_source_section(text: str) -> str:
        """
        parser.py extract_data_source_section() — finds the 'Data Sources'
        section and returns just that slice of text.
        """
        text_lower = text.lower()
        start = re.search(r"data sources?", text_lower)
        if not start:
            return ""

        start_idx = start.start()
        end_idx = len(text)

        for pattern in ["study design", "study population", "endpoints", "data analyses"]:
            match = re.search(pattern, text_lower[start_idx:])
            if match:
                end_idx = start_idx + match.start()
                break

        return text[start_idx:end_idx]
