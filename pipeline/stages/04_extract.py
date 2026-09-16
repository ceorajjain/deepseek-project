"""Stage 4: extract stone items.
XLS/XLSX -> deterministic adapter.parse_boq (per-source column rules).
PDF -> regex pre-filter + DeepSeek judge.
"""
import hashlib
import json
import os
import re
import sys

import fitz

from .. import config
from ..adapters import SOURCES
from ..logger import get_logger
from ..boq_parsers import parse_boq
from .base import connect

log = get_logger("04_extract")

STONE_RE = re.compile(
    r"granite|marble|kota|sandstone|slate|quartz|terracotta|sadarahalli|"
    r"jet black|tan brown|red granite", re.I)


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def pdf_text(path):
    d = fitz.open(path)
    text = "\n".join(pg.get_text() for pg in d)
    d.close()
    return text


def prefilter(text, context=4):
    lines = text.split("\n")
    keep = set()
    for i, line in enumerate(lines):
        if STONE_RE.search(line):
            for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                keep.add(j)
    return "\n".join(lines[j] for j in sorted(keep))


def chunk(text, size=None):
    size = size or config.DEEPSEEK_CHUNK_SIZE
    lines = text.split("\n")
    out, cur, n = [], [], 0
    for l in lines:
        cur.append(l)
        n += len(l) + 1
        if n >= size:
            out.append("\n".join(cur))
            cur, n = [], 0
    if cur:
        out.append("\n".join(cur))
    return out


def main():
    conn = connect()
    cur = conn.cursor()
    for adapter in SOURCES:
        src = adapter.source_id
        cur.execute("""
            SELECT d.boq_document_id, d.project_id, d.sha256_hash, d.local_path, d.source_url, d.raw_metadata_json->>'label' AS label
            FROM ekmi_zero.project_boq_document d
            JOIN ekmi_zero.project p ON p.project_id=d.project_id
            WHERE p.status='downloaded' AND p.source_id=%s AND d.local_path IS NOT NULL
            ORDER BY d.project_id, d.boq_document_id
        """, (src,))
        rows = cur.fetchall()
        if not rows:
            continue
        log.info("%s: extracting from %d downloaded docs", src, len(rows))
        total = 0
        for did, pid, sha, lp, url, label in rows:
            if not lp or not os.path.isfile(lp):
                continue
            items = parse_boq(lp, src)
            seen = set()
            for it in items:
                desc = (it.get("description") or "").strip()[:255]
                mat = it.get("material_name")
                qty = it.get("quantity")
                unit = it.get("unit")
                if not desc or qty is None or unit is None:
                    continue
                key = (round(float(qty), 4), str(unit).upper(), desc[:80])
                if key in seen:
                    continue
                seen.add(key)
                dedupe = sha256(f"{pid}:{sha}:{label}:{desc}")
                cur.execute("""
                    INSERT INTO ekmi_zero.project_boq_item
                      (project_id, boq_document_id, item_no, item_description, material_family, material_name,
                       quantity, unit, dedupe_key, source_url, is_synthetic, verified_by_raj, verification_remarks,
                       evidence_bundle_json, source_fingerprint, approved_by_deepseek, approved_by_deepseek_level,
                       thickness_min_mm, thickness_max_mm, finish, color, size_length, size_width, size_unit,
                       alternative_materials)
                    VALUES (%s,%s,%s,%s,'Natural Stone',%s,%s,%s,%s,%s,false,'FRESH','codex_fresh_v2',
                            %s::jsonb,%s,false,2,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (dedupe_key) DO NOTHING
                """, (pid, did, label, desc, mat, qty, unit, dedupe, url,
                      json.dumps({"sha256": sha, "local_path": lp, "actor": config.ACTOR}), sha,
                      it.get("thickness_min_mm"), it.get("thickness_max_mm"), it.get("finish"),
                      it.get("variety"), it.get("size_length"), it.get("size_width"), it.get("size_unit"),
                      json.dumps({"materials": it.get("alternative_materials", []),
                                  "varieties": it.get("alternative_varieties", [])})))
                total += cur.rowcount
        cur.execute("""
            UPDATE ekmi_zero.project SET status='extracted'
            WHERE status='downloaded' AND source_id=%s
              AND EXISTS (SELECT 1 FROM ekmi_zero.project_boq_item i WHERE i.project_id=project_id AND i.verified_by_raj='FRESH')
        """, (src,))
        log.info("%s extract done: items inserted=%d", src, total)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
