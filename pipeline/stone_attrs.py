"""Deterministic (regex) extraction of stone material / thickness / finish / variety from a
BOQ item description. No LLM/API — pure Python, zero tokens.

Granite VARIETY names (Tan Brown, Jet Black, Sadarahalli Grey, Cheema Pink) are commercial
granite type/quality names (from a specific quarry), NOT "colors".
"""
import re

THICKNESS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*mm\s*(?:thick|thickness|(?:mirror\s+)?polish|"
    r"(?:pre\s*)?polish|flam|hon|leather|finish|sand\s*blast|lappato)",
    re.I,
)
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(?:x\s*(\d+(?:\.\d+)?))?\s*(mm|cm)",
    re.I,
)

FINISHES = [
    (r"mirror\s*polish", "Mirror Polished"),
    (r"semi\s*polish", "Semi Polished"),
    (r"lappato", "Lappato"),
    (r"sand\s*blast", "Sand Blasted"),
    (r"flamed|flame\s*finish|flamming", "Flamed"),
    (r"honed", "Honed"),
    (r"leather", "Leather"),
    (r"bush\s*hammer", "Bush Hammered"),
    (r"pre\s*polish", "Prepolished"),
    (r"gang\s*saw", "Gang Saw Cut"),
    (r"rough", "Rough"),
    (r"polish", "Polished"),
]

# granite/marble/kota VARIETY names (commercial granite types, source-verified)
VARIETIES = [
    "Jet Black", "Cherry Red", "Tan Brown", "Raw Silk", "Ikon Brown",
    "Kashmir White", "Kashmir Gold", "Sadarahalli Grey", "Sadarhalli Grey",
    "Sadarahalli", "Sadarhalli",
    "Cheema Pink", "Jhalore Beige", "Udaipur Green", "Makrana White", "Perlato",
    "Italian", "Ruby Red", "Blue Pearl", "Multi Red", "Kashmir Yellow",
]

MATERIALS = [
    ("Granite", "Granite"),
    ("Garnite", "Granite"),
    ("Marble", "Marble"),
    ("Kota", "Kota Stone"),
    ("Kotah", "Kotah Stone"),
    ("Sandstone", "Sandstone"),
    ("Slate", "Slate"),
    ("Quartz", "Quartz"),
    ("Kadappa", "Kadappa Stone"),
    ("Stone", "Stone"),
]

MATERIAL_TOKENS = [
    "sadarahalli", "sadarhalli", "granite", "marble", "kota", "kotah",
    "garnite", "sandstone", "sand stone", "slate", "quartz", "terracotta",
    "kadappa",
]


def extract_thickness(desc):
    d = desc or ""
    m = SIZE_RE.search(d)
    if m:
        t = float(m.group(3)) if m.group(3) else None
        if t is not None:
            if m.group(4).lower() == "cm":
                t *= 10.0
            return t, t
    m = THICKNESS_RE.search(d)
    if m:
        t = float(m.group(1))
        return t, t
    return None, None


def extract_size(desc):
    d = desc or ""
    m = SIZE_RE.search(d)
    if not m:
        return {}
    return {
        "size_length": float(m.group(1)),
        "size_width": float(m.group(2)),
        "size_thickness": float(m.group(3)) if m.group(3) else None,
        "size_unit": (m.group(4) or "").upper(),
    }


def extract_finish(desc):
    d = (desc or "").lower()
    for pat, label in FINISHES:
        if re.search(pat, d):
            return label
    return None


def extract_variety(desc):
    d = desc or ""
    if re.search(r"extra\s+for|extra\s+rate", d, re.I):
        return None
    for v in VARIETIES:
        if re.search(re.escape(v), d, re.I):
            return v
    return None


def extract_material(desc):
    d = desc or ""
    for pat, name in MATERIALS:
        if re.search(re.escape(pat), d, re.I):
            return name
    return None


def extract_alternatives(desc):
    """Alternative stone MATERIALS and granite VARIETIES mentioned as 'A/B/C'."""
    d = re.sub(r"extra\s+for", "", desc or "", flags=re.I)
    materials = []
    varieties = []
    for m in re.finditer(r"[A-Za-z][A-Za-z\s&.\-]*(?:\s*/\s*[A-Za-z][A-Za-z\s&.\-]*)+", d):
        parts = [p.strip() for p in m.group(0).split("/") if p.strip()]
        for p in parts:
            pl = p.lower()
            for mt in MATERIAL_TOKENS:
                if mt in pl:
                    materials.append("Sandstone" if mt == "sand stone" else mt.title())
                    break
            for v in VARIETIES:
                if re.search(re.escape(v), p, re.I):
                    varieties.append(v)
                    break
    return {
        "alternative_materials": list(dict.fromkeys(materials)),
        "alternative_varieties": list(dict.fromkeys(varieties)),
    }


def build_material_name(desc):
    """Specific granite type: 'Jet Black Granite' / 'Kota Stone' / 'Udaipur Green Marble'."""
    mat = extract_material(desc)
    variety = extract_variety(desc)
    is_extra = bool(re.search(r"extra\s+for", desc or "", re.I))
    parts = []
    if variety and mat:
        parts.append(variety)
    if mat:
        parts.append(mat)
    name = " ".join(parts) or mat or None
    if is_extra and name:
        name = "Premium " + name
    return name


def build_product_name(desc):
    """Full spec: 'Jet Black Granite 1200x600mm 20mm Mirror Polished'."""
    mn = build_material_name(desc)
    sz = extract_size(desc)
    tmin, _ = extract_thickness(desc)
    finish = extract_finish(desc)
    parts = [mn] if mn else []
    if sz.get("size_length") and sz.get("size_width"):
        parts.append(f"{sz['size_length']:g}x{sz['size_width']:g}{sz['size_unit'].lower()}")
    if tmin:
        parts.append(f"{int(tmin) if tmin == int(tmin) else tmin}mm")
    if finish:
        parts.append(finish)
    return " ".join(parts) or (desc or "")[:80]


def extract_stone_attrs(desc):
    tmin, tmax = extract_thickness(desc)
    sz = extract_size(desc)
    alt = extract_alternatives(desc)
    return {
        "thickness_min_mm": tmin,
        "thickness_max_mm": tmax,
        "finish": extract_finish(desc),
        "variety": extract_variety(desc),
        "color": extract_variety(desc),
        "material": extract_material(desc),
        "material_name": build_material_name(desc),
        "product_name": build_product_name(desc),
        **sz,
        "alternative_materials": alt["alternative_materials"],
        "alternative_varieties": alt["alternative_varieties"],
    }
