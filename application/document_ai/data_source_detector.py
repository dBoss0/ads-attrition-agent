"""
Data Source Detector — identifies which Premier data sources a protocol uses.

Preserves parser.py business rules EXACTLY:
  Rule 2 — Longest-key-first matching (avoids "PHD" matching before "PHD V2.2")
  Rule 3 — Erase matched key before next search (prevents double-matching)
  Rule 6 — Run on FULL document text (not per-section)
  Rule 7 — DATA_SOURCE_MASTER canonical name dictionary

DATA_SOURCE_MASTER maps search strings → canonical Premier data source names.
Keys are lower-cased for matching; canonical names are Title Case.
"""
from __future__ import annotations

import re

# ── DATA_SOURCE_MASTER ─────────────────────────────────────────────────────────
# Rule 7: canonical dictionary. Keys sorted longest-first at runtime (Rule 2).
DATA_SOURCE_MASTER: dict[str, str] = {
    # Premier specific
    "premier healthcare database": "Premier Healthcare Database",
    "pinc ai healthcare database": "Premier Healthcare Database",
    "pinc ai phd":                 "Premier Healthcare Database",
    "premier phd":                 "Premier Healthcare Database",
    "premier":                     "Premier Healthcare Database",
    "phd v2":                      "Premier Healthcare Database",
    "phd":                         "Premier Healthcare Database",

    # Optum
    "optum clinformatics":         "Optum Clinformatics Data Mart",
    "clinformatics":               "Optum Clinformatics Data Mart",
    "optum ehr":                   "Optum EHR",
    "optum":                       "Optum",

    # Truven / IBM MarketScan
    "marketscan":                  "IBM MarketScan",
    "truven":                      "IBM MarketScan",
    "ibm marketscan":              "IBM MarketScan",

    # Medicare / CMS
    "medicare claims":             "Medicare Claims (CMS)",
    "cms":                         "Medicare Claims (CMS)",
    "medicare":                    "Medicare Claims (CMS)",
    "medicaid":                    "Medicaid",

    # EHR / EMR
    "electronic health record":    "EHR",
    "electronic medical record":   "EHR",
    "ehr":                         "EHR",
    "emr":                         "EHR",

    # Others
    "flatiron":                    "Flatiron Health",
    "symphony health":             "Symphony Health",
    "iqvia":                       "IQVIA",
    "trinetx":                     "TriNetX",
    "allscripts":                  "Allscripts",
    "epic":                        "Epic EHR",
}


class DataSourceDetector:
    """
    Detects data sources from full protocol text using the DATA_SOURCE_MASTER.

    Follows parser.py rules precisely:
      - Run on FULL document text (Rule 6)
      - Longest key first (Rule 2)
      - Erase matched substring before searching for the next key (Rule 3)
    """

    def __init__(self) -> None:
        # Pre-sort keys longest-first so longer phrases match before substrings
        # (e.g. "premier healthcare database" before "premier")
        self._sorted_keys: list[str] = sorted(
            DATA_SOURCE_MASTER.keys(), key=len, reverse=True
        )

    def detect(self, full_text: str) -> list[str]:
        """
        Detect all data sources in the full document text.
        Returns a list of canonical data source names (deduplicated, order of discovery).

        Rule 6: must receive the FULL document text, not a section.
        Rules 2 & 3: longest-key-first, erase after each match.
        """
        if not full_text:
            return []

        search_text = full_text.lower()
        found_canonical: list[str] = []
        seen_canonical: set[str] = set()

        for key in self._sorted_keys:
            if key in search_text:
                canonical = DATA_SOURCE_MASTER[key]
                # Rule 3: erase the matched key so shorter aliases don't re-match it
                search_text = search_text.replace(key, " " * len(key), 1)
                if canonical not in seen_canonical:
                    found_canonical.append(canonical)
                    seen_canonical.add(canonical)

        return found_canonical
