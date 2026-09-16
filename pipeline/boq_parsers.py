"""Type-aware BOQ parsers (recognize data format, apply format-specific rules)."""
import re

import fitz
import pandas as pd
import pdfplumber

from .stone_attrs import extract_stone_attrs

STONE_POS = re.compile(
    r"granite|garnite|marble|kota|sadarahalli|sadarhalli|sandstone|slate|quartz|"
    r"terracotta|kadappa",
    re.I,
)

CPWD_FALSE = re.compile(
    r"cutter|grinder|chips|polishing machine|cement concrete|aggregate|"
    r"crushed|trap|basalt|sheet|water proofing|waterproofing",
    re.I,
)

METRO_FALSE = re.compile(
    r"cement concrete|reinforced|rcc|pcc|steel fabrication|fabrication|"
    r"terraz+o|vitrified|ceramic tile|glass|waterproofing|"
    r"bidding procedure|corrigendum|revised boq|expansion hold|"
    r"hold fastener|concrete floor|granolithic|stone masonry|size stone|masonry|"
    r"trap|quartzite|gneiss|removing|dismantling",
    re.I,
)

UNIT_MAP = {
    "SQM": "SQM", "SQM.": "SQM", "SQ.M": "SQM", "SQ M": "SQM", "SQMT": "SQM",
    "M2": "SQM", "SQFT": "SQFT", "SQF": "SQFT", "SQ.FT": "SQFT", "SFT": "SQFT",
    "CUM": "CUM", "M3": "CUM", "RFT": "RFT", "RMT": "RMT", "MTR": "RMT", "M": "RMT",
    "NOS": "NOS", "NO": "NOS", "NUMBER": "NOS", "EACH": "NOS", "KG": "KG", "MT": "MT",
}

NUM = re.compile(r"^[\d,]+(?:\.\d+)?$")
ITEM_NO = re.compile(r"^\d+(?:\.\d+)+$")


def canonical_unit(u):
    return UNIT_MAP.get((u or "").strip().upper().rstrip("."))


def _is_stone(desc, false_re):
    if not desc or not STONE_POS.search(desc):
        return False
    if false_re and false_re.search(desc):
        return False
    if re.search(r"^Note\s*:", desc, re.I):
        return False
    return True


def _to_float(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _validate_qty_rate_amount(qty, rate, amount):
    if None in (qty, rate, amount) or qty <= 0 or rate <= 0:
        return False
    expected = qty * rate
    if amount <= 0:
        return False
    return abs(expected - amount) / amount <= 0.02  # 2% tolerance


def detect_format(path, sample_text=""):
    ext = (path or "").lower().rsplit(".", 1)[-1]
    if ext in ("xls", "xlsx"):
        return "cpwd_excel"
    if ext == "pdf":
        t = (sample_text or "").lower()
        if re.search(r"bill of quantities|schedule|boq", t):
            return "metro_pdf"
    return None


class CpwdExcelParser:
    def parse(self, path, false_re=CPWD_FALSE):
        items = []
        try:
            sheets = pd.read_excel(path, sheet_name=None, header=None)
        except Exception:
            return items
        for _sheet, df in sheets.items():
            # find header row (contains 'Description' and 'Quantity'/'Units')
            header_row = None
            for r in range(min(15, len(df))):
                cells = [str(x).strip().lower() for x in df.iloc[r] if pd.notna(x)]
                joined = " ".join(cells)
                if "description" in joined and ("quantity" in joined or "units" in joined or "qty" in joined):
                    header_row = r
                    break
            if header_row is None:
                continue
            # map columns by header text
            col = {}
            for c in range(df.shape[1]):
                h = str(df.iloc[header_row, c]).strip().lower()
                if "description" in h and "desc" not in col:
                    col["desc"] = c
                elif "quantity" in h or h == "qty":
                    col["qty"] = c
                elif "unit" in h:
                    col["unit"] = c
                elif "cost" in h or "amount" in h or "rate" in h:
                    col.setdefault("amount", c)
            if "desc" not in col or "qty" not in col or "unit" not in col:
                continue
            for r in range(header_row + 1, len(df)):
                desc = str(df.iloc[r, col["desc"]]).strip() if pd.notna(df.iloc[r, col["desc"]]) else ""
                if not desc or desc.lower().startswith("total") or desc.lower().startswith("note"):
                    continue
                if not _is_stone(desc, false_re):
                    continue
                qty = _to_float(df.iloc[r, col["qty"]])
                unit = canonical_unit(str(df.iloc[r, col["unit"]]).strip() if pd.notna(df.iloc[r, col["unit"]]) else "")
                amount = _to_float(df.iloc[r, col["amount"]]) if "amount" in col else None
                if qty is None or unit is None or qty <= 0:
                    continue
                items.append(_build(desc, qty, unit, amount, None))
        return items


class MetroPdfParser:
    def parse(self, path, false_re=METRO_FALSE, validate=True):
        items = []
        d = fitz.open(path)
        text = "\n".join(pg.get_text() for pg in d)
        d.close()
        lines = [l.strip() for l in text.split("\n")]
        buf = []
        for i, line in enumerate(lines):
            if not line:
                continue
            if re.match(r"^\([ivx]+\)", line, re.I):
                continue  # skip sub-item markers like (i)/(ii)/(iii)
            if NUM.match(line):
                continue  # pure number = qty/rate/amount, not description
            if ITEM_NO.match(line):
                continue  # item number like 2.4.1 / 2.1.1
            unit = canonical_unit(line)
            if unit:
                desc = " ".join(buf)
                if not validate and re.search(r"extra\s+for", desc, re.I):
                    buf = []
                    continue  # Pune "Extra for" items have no own quantity (inherit parent)
                if not _is_stone(desc, false_re):
                    buf = []
                    continue
                # numbers AFTER the unit (rate, amount, or qty+rate+amount)
                after = []
                for j in range(i + 1, min(i + 6, len(lines))):
                    if NUM.match(lines[j]):
                        after.append(_to_float(lines[j]))
                    elif lines[j] and not re.match(r"^[\d,.\s]+$", lines[j]):
                        break
                # numbers BEFORE the unit (quantity might be before, e.g. D2C-08)
                before = []
                for j in range(i - 1, max(-1, i - 4), -1):
                    if NUM.match(lines[j]):
                        before.append(_to_float(lines[j]))
                    elif lines[j] and not re.match(r"^[\d,.\s]+$", lines[j]):
                        break
                before.reverse()
                qty = rate = amount = None
                if validate:
                    # format A: qty BEFORE unit, rate+amount AFTER
                    if before and len(after) >= 2 and _validate_qty_rate_amount(before[-1], after[0], after[1]):
                        qty, rate, amount = before[-1], after[0], after[1]
                    # format B: qty+rate+amount all AFTER unit
                    if qty is None and len(after) >= 3 and _validate_qty_rate_amount(after[0], after[1], after[2]):
                        qty, rate, amount = after[0], after[1], after[2]
                    if qty is None:
                        buf = []
                        continue
                else:
                    qty = before[-1] if before else (after[0] if after else None)
                    if qty is None:
                        buf = []
                        continue
                items.append(_build(desc, qty, unit, amount, rate))
                buf = []
            else:
                buf.append(line)
                if len(buf) > 40:
                    buf = buf[-40:]
        return items


class PuneScheduleTableParser:
    """Parse Pune Metro SCHEDULE-* BOQ PDFs using their native table layout.

    Layout columns: item_no, code, description, unit, qty, rate, amount.
    Handles parent items without qty whose sub-items carry the qty, and
    keeps parent thickness/finish/variety context on sub-item rows.
    """

    def __init__(self, false_re=METRO_FALSE):
        self.false_re = false_re

    @staticmethod
    def _parse_qty(v):
        return _to_float(v)

    @staticmethod
    def _is_item_no(v):
        if not v:
            return None
        v = str(v).strip()
        if re.fullmatch(r"\d+", v):
            return "main"
        if re.fullmatch(r"\d+(?:\.\d+)+", v):
            return "sub"
        return None

    @staticmethod
    def _detect_cols(table):
        """Return {item,desc,unit,qty} column indexes from the first header rows."""
        max_cols = max((len(r) for r in table), default=0)
        sig = []
        for c in range(max_cols):
            parts = []
            for r in table[:3]:
                if c < len(r) and r[c]:
                    parts.append(str(r[c]).strip().lower())
            joined = " ".join(parts)
            sig.append(joined)
        cols = {"item": None, "desc": None, "unit": None, "qty": None}
        for c, joined in enumerate(sig):
            if "description" in joined:
                cols["desc"] = c
            if "unit" in joined and cols["unit"] is None:
                cols["unit"] = c
            if ("qty" in joined or "quantity" in joined) and cols["qty"] is None:
                cols["qty"] = c
            if ("item" in joined or "sr" in joined or "s.no" in joined) and cols["item"] is None:
                cols["item"] = c
        if cols["desc"] is None or cols["unit"] is None or cols["qty"] is None:
            return None
        if cols["item"] is None:
            for c in range(cols["desc"]):
                if c not in (cols["unit"], cols["qty"]):
                    cols["item"] = c
                    break
        return cols

    def parse(self, path):
        items = []
        parent = None
        last_cols = None
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table:
                        continue
                    cols = self._detect_cols(table)
                    if cols:
                        last_cols = cols
                    elif last_cols:
                        cols = last_cols
                    else:
                        continue
                    # header rows are the first 2-3 rows; start data after the row containing qty header.
                    start = 0
                    for ri, raw in enumerate(table[:4]):
                        joined = " ".join("" if c is None else str(c).lower() for c in raw)
                        if "qty" in joined or "quantity" in joined:
                            start = ri + 1
                            break
                    for raw in table[start:]:
                        row = [("" if c is None else str(c).strip()) for c in raw]
                        if not any(row):
                            continue
                        def cell(idx):
                            return row[idx] if idx is not None and idx < len(row) else ""
                        item_no = cell(cols["item"])
                        desc = re.sub(r"\s+", " ", cell(cols["desc"])).strip()
                        unit = canonical_unit(cell(cols["unit"]))
                        qty = self._parse_qty(cell(cols["qty"]))
                        kind = self._is_item_no(item_no)

                        if item_no and kind is None:
                            # section header like 1.0, A, B, C, TOTAL...
                            continue

                        if kind == "main":
                            parent = {
                                "item_no": item_no,
                                "desc": desc,
                                "attrs": extract_stone_attrs(desc),
                                "stone": _is_stone(desc, self.false_re),
                            }
                            if parent["stone"] and qty is not None and unit:
                                items.append(self._emit(parent["desc"], qty, unit, parent["attrs"]))
                            continue

                        # Decimal item that is a full BOQ item becomes the new parent.
                        if kind == "sub" and re.search(r"providing|supplying|fixing", desc, re.I):
                            parent = {
                                "item_no": item_no,
                                "desc": desc,
                                "attrs": extract_stone_attrs(desc),
                                "stone": _is_stone(desc, self.false_re),
                            }
                            if parent["stone"] and qty is not None and unit:
                                items.append(self._emit(parent["desc"], qty, unit, parent["attrs"]))
                            continue

                        # decimal or empty sub-item/extra: inherit parent context
                        if not desc or qty is None or not unit:
                            continue
                        combined = desc
                        attrs = extract_stone_attrs(desc)
                        if parent and parent.get("stone"):
                            combined = (parent["desc"] + " | " + desc).strip()
                            attrs = extract_stone_attrs(combined)
                        if not _is_stone(combined, self.false_re):
                            continue
                        items.append(self._emit(combined, qty, unit, attrs))
        return items

    @staticmethod
    def _emit(desc, qty, unit, attrs):
        return {
            "description": re.sub(r"\s+", " ", desc)[:1000],
            "quantity": qty,
            "unit": unit,
            "rate": None,
            "amount": None,
            "thickness_min_mm": attrs["thickness_min_mm"],
            "thickness_max_mm": attrs["thickness_max_mm"],
            "finish": attrs["finish"],
            "variety": attrs.get("variety"),
            "material_name": attrs.get("material_name") or attrs.get("material") or "Stone",
            "product_name": attrs.get("product_name") or attrs.get("material_name") or "Stone",
            "size_length": attrs.get("size_length"),
            "size_width": attrs.get("size_width"),
            "size_thickness": attrs.get("size_thickness"),
            "size_unit": attrs.get("size_unit"),
            "alternative_materials": attrs.get("alternative_materials", []),
            "alternative_varieties": attrs.get("alternative_varieties", []),
        }


def _build(desc, qty, unit, amount, rate):
    a = extract_stone_attrs(desc)
    is_extra = bool(re.search(r"extra\s+for", desc, re.I))
    mat = a.get("material_name") or (a.get("material") or "Stone")
    if is_extra:
        mat = "Premium " + mat if mat and mat.lower() != "premium" else "Premium Stone"
    pn = a.get("product_name") or mat
    if is_extra and not pn.lower().startswith("premium"):
        pn = "Premium " + pn
    return {
        "description": re.sub(r"\s+", " ", desc)[:500],
        "quantity": qty,
        "unit": unit,
        "rate": rate,
        "amount": amount,
        "thickness_min_mm": a["thickness_min_mm"],
        "thickness_max_mm": a["thickness_max_mm"],
        "finish": a["finish"],
        "variety": "Multiple" if is_extra and not a.get("variety") else a.get("variety"),
        "material_name": mat,
        "product_name": pn,
        "size_length": a.get("size_length"),
        "size_width": a.get("size_width"),
        "size_thickness": a.get("size_thickness"),
        "size_unit": a.get("size_unit"),
        "alternative_materials": a.get("alternative_materials", []),
        "alternative_varieties": a.get("alternative_varieties", []),
    }


def parse_boq(path, source_id=None):
    ext = (path or "").lower().rsplit(".", 1)[-1]
    sample = ""
    if ext == "pdf":
        try:
            d = fitz.open(path)
            sample = "\n".join(pg.get_text() for pg in d)
            d.close()
        except Exception:
            pass
    fmt = detect_format(path, sample)
    if fmt == "cpwd_excel":
        return CpwdExcelParser().parse(path)
    if fmt == "metro_pdf":
        if source_id and source_id.upper().startswith("PUNE"):
            return PuneScheduleTableParser().parse(path)
        # DMRC BOQ has qty+rate+amount together; Pune has qty only (rate in separate section)
        validate = (source_id == "DMRC")
        return MetroPdfParser().parse(path, validate=validate)
    return []
