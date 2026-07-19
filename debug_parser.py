"""
Run this directly to debug extraction on your DOCX.
Usage:  python debug_parser.py path/to/your/protocol.docx
"""
import re
import sys
from docx import Document

# ── Step 1: Read DOCX ─────────────────────────────────────────────────────────
def read_docx(path):
    doc = Document(path)
    text = []
    for para in doc.paragraphs:
        if para.text.strip():
            text.append(para.text.strip())
    return "\n".join(text)

# ── Step 2: Narrow to study section ──────────────────────────────────────────
def extract_study_selection(text):
    text_lower = text.lower()
    start_keywords = ["study design", "study population", "inclusion criteria"]
    end_keywords = ["exposure variable", "primary independent variable",
                    "covariates", "study outcomes", "product codes"]
    start_idx = None
    for kw in start_keywords:
        match = re.search(kw, text_lower)
        if match:
            start_idx = match.start()
            print(f"  [study section start] found '{kw}' at char {start_idx}")
            break
    if start_idx is None:
        print("  [study section] NOT FOUND — using full text")
        return text
    end_idx = len(text)
    for kw in end_keywords:
        match = re.search(kw, text_lower[start_idx:])
        if match:
            end_idx = start_idx + match.start()
            print(f"  [study section end] found '{kw}' at char {end_idx}")
            break
    return text[start_idx:end_idx]

# ── Step 3: Split into inc/exc sections ──────────────────────────────────────
def split_criteria_sections(text):
    text_lower = text.lower()
    if "table of contents" in text_lower:
        text = text.split("table of contents")[-1]
        text_lower = text.lower()
        print("  [toc] removed table of contents")

    inc_matches = list(re.finditer(r"inclusion criteria", text_lower))
    exc_matches = list(re.finditer(r"exclusion criteria", text_lower))

    print(f"  [markers] 'inclusion criteria' found {len(inc_matches)} times")
    print(f"  [markers] 'exclusion criteria' found {len(exc_matches)} times")

    inc_start = inc_matches[-1].start() if inc_matches else -1
    exc_start = exc_matches[-1].start() if exc_matches else -1

    inc_text = text[inc_start: exc_start if exc_start != -1 else len(text)] if inc_start != -1 else ""
    exc_text = text[exc_start:] if exc_start != -1 else ""

    return inc_text, exc_text

# ── Step 4: Extract steps ─────────────────────────────────────────────────────
def extract_steps(section_text):
    raw_steps = section_text.split("\n")
    steps = []
    skipped = 0
    for step in raw_steps:
        step = step.strip()
        step_lower = step.lower()
        if len(step) < 15:
            skipped += 1
            continue
        if "inclusion criteria" in step_lower: continue
        if "exclusion criteria" in step_lower: continue
        if "patients will be included" in step_lower: continue
        if "patients will be excluded" in step_lower: continue
        if "table of contents" in step_lower: continue
        if step_lower.startswith("individuals") or step_lower.startswith("see ") or step_lower.startswith("product codes"): continue
        if "must meet all the following" in step_lower: continue
        if "meeting any of the following" in step_lower: continue
        step = re.sub(r'^\d+\.\s*', '', step)
        steps.append(step)
    print(f"  [steps] {skipped} lines skipped (< 15 chars or heading)")
    return steps

# ── Step 5: Data source ───────────────────────────────────────────────────────
DATA_SOURCE_MASTER = {
    "premier healthcare database": "Premier Healthcare Database",
    "pinc ai healthcare database": "Premier Healthcare Database",
    "pinc ai phd": "Premier Healthcare Database",
    "premier phd": "Premier Healthcare Database",
    "pinc ai": "Premier Healthcare Database",
    "premier": "Premier Healthcare Database",
    "phd v2": "Premier Healthcare Database",
    "phd": "Premier Healthcare Database",
    "optum clinformatics date of death": "Optum DOD/SES",
    "optum clinformatics": "Optum Clinformatics",
    "optum dod": "Optum DOD/SES",
    "optum ses": "Optum DOD/SES",
    "optum": "Optum",
    "ibm marketscan": "IBM MarketScan",
    "marketscan": "IBM MarketScan",
    "ccae": "IBM MarketScan",
    "mdcr": "IBM MarketScan",
    "mdcd": "IBM MarketScan",
    "healthverity": "HealthVerity",
    "concert ai": "Concert AI",
    "flatiron": "Flatiron Health",
    "truveta": "Truveta",
    "komodo": "Komodo",
    "mercy": "Mercy",
    "cprd": "CPRD",
    "hes": "HES",
    "jmdc": "JMDC",
}

def detect_data_source(text):
    text_lower = text.lower()
    start = re.search(r"data sources?", text_lower)
    if start:
        section = text[start.start():]
        print(f"  [data source section] found at char {start.start()}")
        print(f"  [data source section] first 200 chars: {section[:200]!r}")
    else:
        section = text
        print("  [data source section] NOT FOUND — searching full text")

    section_lower = section.lower()
    sorted_keys = sorted(DATA_SOURCE_MASTER.keys(), key=len, reverse=True)
    detected = []
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, section_lower):
            detected.append(DATA_SOURCE_MASTER[key])
            section_lower = re.sub(pattern, " " * len(key), section_lower)
    return list(set(detected))


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_parser.py path/to/protocol.docx")
        sys.exit(1)

    path = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"Parsing: {path}")
    print(f"{'='*60}\n")

    print("STEP 1: Reading DOCX...")
    text = read_docx(path)
    print(f"  Total chars: {len(text)}")
    print(f"  Total lines: {len(text.splitlines())}")
    print(f"  First 300 chars:\n  {text[:300]!r}\n")

    print("STEP 2: Extracting study section...")
    study = extract_study_selection(text)
    print(f"  Study section chars: {len(study)}\n")

    print("STEP 3: Splitting inclusion / exclusion...")
    inc_text, exc_text = split_criteria_sections(study)
    print(f"  Inclusion section chars: {len(inc_text)}")
    print(f"  Exclusion section chars: {len(exc_text)}\n")

    print("STEP 4: Extracting steps...")
    inc_steps = extract_steps(inc_text)
    exc_steps = extract_steps(exc_text)
    print(f"\n  INCLUSION STEPS ({len(inc_steps)}):")
    for i, s in enumerate(inc_steps, 1):
        print(f"    {i}. {s}")
    print(f"\n  EXCLUSION STEPS ({len(exc_steps)}):")
    for i, s in enumerate(exc_steps, 1):
        print(f"    {i}. {s}")

    print("\nSTEP 5: Detecting data sources...")
    sources = detect_data_source(text)
    print(f"  Detected: {sources}")

    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")
