"""Parse the DMRC LIST_OF_CONTRACTS_AWARDED PDF into structured records.

The official award list is a public, authoritative source for awarded contracts
(winner + value + award date + LOA URL + full bidder list). This module turns its
raw text into structured dicts, and classifies which contracts are granite-selling
relevant (architectural finishing / building construction with finishing).

No synthetic data: every field is read from the PDF text.
"""
import re

CODE_RE = re.compile(
    r"^(?:Contract\s+)?(?P<code>(?:DC-\d{2}[A-Z]?|CPD-\d+[A-Z]?\d?|PT-\d+[A-Z]?|"
    r"DT-\d+[A-Z]?|PC-\d+[A-Z]?|CFS-\d+[A-Z]?|MCD-[A-Z0-9-]+|SOC-\d+[A-Z]?))",
    re.I,
)

# contracts that matter for granite selling
GRANITE_CODE = re.compile(r"^DC-", re.I)
GRANITE_TEXT = re.compile(
    r"architectural|finishing|façade|facade|building|station|depot|"
    r"accommodation|barrack|interior|floor|cladding|granite|marble|stone|tile|"
    r"entry/exit|platform|residence|quarters|hostel|college",
    re.I,
)

GRANITE_NEG = re.compile(
    r"ballastless|track|tunnel|signalling|telecom|rolling stock|overhaul|"
    r"pantograph|escalator|lift|hvac|fire|led|bms|battery|failure diagnosis|"
    r"facility management|customer facilitation|security service|property development|"
    r"lease|license fee|property",
    re.I,
)

DATE_RE = re.compile(r"\d{2}[-.]\d{2}[-.]\d{4}")


def _is_header(line, prev_line):
    m = CODE_RE.match(line)
    if not m:
        return None
    if line.lower().endswith(".pdf"):
        return None
    if line.lower().startswith("https"):
        return None
    if prev_line and prev_line.lower().startswith("https"):
        return None
    return m.group("code").upper()


def split_blocks(lines):
    """Return list of (code, block_lines)."""
    blocks = []
    cur_code = None
    cur = []
    prev = None
    for line in lines:
        code = _is_header(line, prev)
        if code:
            if cur_code is not None:
                blocks.append((cur_code, cur))
            cur_code = code
            cur = [line]
        else:
            cur.append(line)
        prev = line
    if cur_code is not None:
        blocks.append((cur_code, cur))
    return blocks


def _clean(s):
    return re.sub(r"\s+", " ", s).strip()


def _work_description(block):
    # header remainder + following lines, before tender-mode markers
    desc = []
    header = block[0]
    rest = re.sub(r"^(?:Contract\s+)?[A-Z]{2,4}-\d+[A-Z]?\d?\s*[:：]?\s*", "", header, flags=re.I)
    if rest.strip():
        desc.append(rest)
    for ln in block[1:]:
        if re.search(r"^(Open|Limited|Single Bid|Two ?Bid|E-?Tender)", ln, re.I):
            break
        if ln.strip():
            desc.append(ln)
    return _clean(" ".join(desc))


def _award_section(block):
    # everything after the standalone "Yes" award marker
    for i, ln in enumerate(block):
        if ln.strip() == "Yes":
            return block[i + 1:]
    return []


def _parse_value(award_lines):
    """Join the value line(s): 'INR 68.19 Cr.' / 'INR61.29Crores' / 'Rs. 115.26'."""
    for i, ln in enumerate(award_lines):
        if re.search(r"(?:INR|Rs\.)\s*\d", ln, re.I):
            parts = [ln]
            j = i + 1
            while j < len(award_lines) and j < i + 5:
                nxt = award_lines[j].strip()
                if not nxt:
                    break
                if DATE_RE.search(nxt):
                    break  # completion date — stop
                if re.search(r"^(Crores?|Cr\.?|per|sqm|USD|\d|,)", nxt, re.I):
                    parts.append(nxt)
                    j += 1
                else:
                    break
            return _clean(" ".join(parts))
    return None


def _value_in_inr(value):
    """Return numeric INR value for 'Crores/Cr.' strings, else None."""
    if not value:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:Crores?|Cr\.?)", value, re.I)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    return round(num * 10_000_000, 2)


def _parse_date_after_value(award_lines, value_start_idx):
    # completion date is a date token after the value (skip lease '20years' etc.)
    for ln in award_lines[value_start_idx + 1:]:
        m = DATE_RE.search(ln)
        if m:
            return m.group(0)
    return None


def _parse_loa_url(block):
    # LOA URL is the last https://...pdf sequence in the block
    parts = []
    capturing = False
    for ln in block:
        if ln.startswith("https://"):
            capturing = True
            parts = [ln]
            continue
        if capturing:
            if ".pdf" in ln:
                parts.append(ln)
                break
            parts.append(ln)
    if not parts:
        return None
    return _clean("".join(parts))


def _parse_winner(award_lines):
    # winner name = non-empty lines between award date and value line
    name = []
    seen_date = False
    for ln in award_lines:
        s = ln.strip()
        if not s:
            continue
        if not seen_date:
            if DATE_RE.search(s):
                seen_date = True
            continue
        if re.search(r"(?:INR|Rs\.)\s*\d|per sqm|per month|years?$", s, re.I):
            break
        name.append(s)
    return _clean(" ".join(name))


def _parse_bidders(block):
    """Bidder list appears before the 'Yes' marker; lines like '1. Name (rank/n)'."""
    bidders = []
    in_list = False
    for ln in block:
        s = ln.strip()
        if s == "Yes":
            break
        m = re.match(r"^\d{1,2}\.\s+(.+?)(?:\s*\((\d+)/(\d+)\))?\s*$", s)
        if m:
            bidders.append(m.group(1))
            in_list = True
        elif in_list and s and not re.match(r"^\d{1,2}\.\s", s):
            # continuation of a wrapped name
            if bidders:
                bidders[-1] = bidders[-1] + " " + s
    return bidders


def parse_award_text(text):
    lines = [l.strip() for l in text.split("\n")]
    records = []
    for code, block in split_blocks(lines):
        award_lines = _award_section(block)
        if not award_lines:
            continue
        winner = _parse_winner(award_lines)
        value = _parse_value(award_lines)
        award_date = None
        for ln in award_lines:
            m = DATE_RE.search(ln)
            if m:
                award_date = m.group(0)
                break
        loa = _parse_loa_url(block)
        desc = _work_description(block)
        bidders = _parse_bidders(block)
        granite_relevant = bool(
            GRANITE_CODE.search(code)
            or (GRANITE_TEXT.search(desc) and not GRANITE_NEG.search(desc))
        )
        records.append({
            "code": code,
            "work_description": desc,
            "winner": winner or None,
            "value": value or None,
            "value_inr": _value_in_inr(value),
            "award_date": award_date,
            "loa_url": loa,
            "bidders": bidders,
            "granite_relevant": granite_relevant,
        })
    return records
