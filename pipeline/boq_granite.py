"""Reusable granite/stone BOQ extraction (pure Python, zero DeepSeek tokens)."""
import hashlib
import json
import os
import re

import fitz

from .stone_attrs import extract_stone_attrs

STONE = re.compile(
    r"granite|marble|kota|sadarahalli|sadarhalli|sandstone|jet black|cherry|"
    r"tan brown|raw silk|udaipur",
    re.I,
)
NUM = re.compile(r"^[\d,]+(?:\.\d+)?$")
UNITS = {"SQM", "CUM", "RMT", "RFT", "NOS", "KG", "MT", "LS"}


def parse_text_boq(path, min_qty=1.0):
    """Extract stone items from a TEXT-based BOQ PDF (description -> UOM -> qty layout)."""
    d = fitz.open(path)
    items = []
    for pno in range(d.page_count):
        lines = [l.strip() for l in d[pno].get_text().split("\n")]
        buf = []
        for i, line in enumerate(lines):
            if not line:
                continue
            if line.upper() in UNITS:
                qty = None
                for j in range(i + 1, min(i + 4, len(lines))):
                    if NUM.match(lines[j]):
                        qty = float(lines[j].replace(",", ""))
                        break
                desc = re.sub(r"\s+", " ", " ".join(buf))
                if STONE.search(desc) and qty is not None and qty >= min_qty:
                    items.append({"description": desc, "quantity": qty, "unit": line.upper()})
                buf = []
            else:
                buf.append(line)
                if len(buf) > 40:
                    buf = buf[-40:]
    d.close()
    return items


def _dedupe_key(project_id, source_url, item_no, desc):
    return hashlib.sha256(f"{project_id}:{source_url}:{item_no}:{desc}".encode()).hexdigest()


def get_or_create_boq_document(cur, project_id, source_url, doc_type, local_path, file_name):
    cur.execute("SELECT boq_document_id FROM ekmi_zero.project_boq_document "
                "WHERE project_id=%s AND source_url=%s LIMIT 1", (project_id, source_url))
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute("""
        INSERT INTO ekmi_zero.project_boq_document
          (project_id, source_url, document_type, file_name, local_path, fetch_status)
        VALUES (%s,%s,%s,%s,%s,'fetched') RETURNING boq_document_id
    """, (project_id, source_url, doc_type, file_name, local_path))
    return cur.fetchone()[0]


def store_items(cur, project_id, source_url, items, doc_type="boq", local_path=None,
                file_name=None, extra_evidence=None, level=3):
    """Store stone items idempotently (dedupe by project+url+item_no+desc)."""
    did = get_or_create_boq_document(cur, project_id, source_url, doc_type, local_path,
                                     file_name or os.path.basename(source_url))
    n = 0
    for idx, it in enumerate(items):
        desc = (it.get("description") or "").strip()[:500]
        qty = it.get("quantity")
        unit = it.get("unit")
        if not desc or qty is None or not unit:
            continue
        a = extract_stone_attrs(desc)
        item_no = it.get("item_no") or str(idx)
        dedupe = _dedupe_key(project_id, source_url, item_no, desc)
        ev = {"source_type": "official_boq", "source_url": source_url, "actor": "codex_fresh_v2"}
        if extra_evidence:
            ev.update(extra_evidence)
        cur.execute("""
            INSERT INTO ekmi_zero.project_boq_item
              (project_id, boq_document_id, item_no, item_description, material_family,
               material_name, quantity, unit, currency, thickness_min_mm, thickness_max_mm,
               finish, color, source_url, dedupe_key, is_synthetic, verified_by_raj,
               verification_remarks, evidence_bundle_json, source_fingerprint,
               approved_by_deepseek, approved_by_deepseek_level)
            VALUES (%s,%s,%s,%s,'Natural Stone',%s,%s,%s,'INR',%s,%s,%s,%s,%s,%s,
                    false,'FRESH','boq_verified',%s::jsonb,%s,true,%s)
            ON CONFLICT (dedupe_key) DO NOTHING
        """, (project_id, did, item_no, desc,
              it.get("material") or "Stone",
              qty, unit, a["thickness_min_mm"], a["thickness_max_mm"],
              a["finish"], a["color"], source_url, dedupe,
              json.dumps(ev), source_url, level))
        if cur.rowcount:
            n += 1
    return n
