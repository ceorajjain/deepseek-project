"""Stage 11: enrich CMRL projects with publish_date + project_value from NIT PDF text."""
import os
import re

import fitz

from ..logger import get_logger
from .base import connect

log = get_logger("11_cmrl_meta")


def pdf_text(path):
    d = fitz.open(path)
    t = "\n".join(pg.get_text() for pg in d)
    d.close()
    return t


def parse_meta(text):
    out = {}
    m = re.search(r"dated\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if m:
        d, mo, y = re.split(r"[/-]", m.group(1))
        out["publish_date"] = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    m = re.search(r"(?:approximate\s*)?value\s*(?:of\s*work)?[\s\S]{0,120}?(?:INR|Rs\.?|₹)\s*([\d.,]+)\s*(crores?|lakhs?|cr)\b", text, re.I)
    if m:
        num = float(m.group(1).replace(",", ""))
        mult = 10_000_000 if "crore" in m.group(2).lower() or m.group(2).lower() == "cr" else 100_000
        out["project_value"] = num * mult
    return out


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT d.project_id, d.local_path
        FROM ekmi_zero.project_boq_document d
        JOIN ekmi_zero.project p ON p.project_id=d.project_id
        WHERE p.source_id='CMRL' AND d.local_path IS NOT NULL
    """)
    rows = cur.fetchall()
    upd = 0
    for pid, lp in rows:
        if not lp or not os.path.isfile(lp) or not str(lp).lower().endswith(".pdf"):
            continue
        try:
            meta = parse_meta(pdf_text(lp))
        except Exception as e:
            log.warning("parse fail proj=%s: %s", pid, e)
            continue
        if not meta:
            continue
        sets = []
        args = []
        for k in ("publish_date", "project_value"):
            if meta.get(k) is not None:
                sets.append(f"{k}=COALESCE(%s, {k})")
                args.append(meta[k])
        if sets:
            args.append(pid)
            cur.execute(f"UPDATE ekmi_zero.project SET {', '.join(sets)}, updated_at=now() WHERE project_id=%s", args)
            upd += 1
    conn.commit()
    conn.close()
    log.info("CMRL metadata enriched: %d projects", upd)


if __name__ == "__main__":
    main()
