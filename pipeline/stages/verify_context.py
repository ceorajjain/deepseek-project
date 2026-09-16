"""Helper for MANUAL verification by Codex: for each L2 item, show the source-document context
around its description so a human (Codex) can confirm the extracted qty/unit against the source."""
import re
import sys

import fitz
import pandas as pd
import psycopg2

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("verify_context")


def doc_text(path):
    if path.lower().endswith(".pdf"):
        d = fitz.open(path)
        text = "\n".join(pg.get_text() for pg in d)
        d.close()
        return text
    if path.lower().endswith((".xls", ".xlsx")):
        dfs = pd.read_excel(path, sheet_name=None, header=None)
        out = []
        for sheet, df in dfs.items():
            for _, row in df.iterrows():
                cells = [str(x) for x in row if pd.notna(x)]
                if cells:
                    out.append(" | ".join(cells))
        return "\n".join(out)
    return ""


def context_around(text, needle, width=220):
    """Return the source text around the first occurrence of needle (or a keyword)."""
    i = text.lower().find(needle.lower())
    if i < 0:
        # fallback: first 4 words
        words = needle.split()[:4]
        for w in words:
            j = text.lower().find(w.lower())
            if j >= 0:
                i = j
                break
    if i < 0:
        return "(not found in source)"
    lo = max(0, i - 80)
    hi = min(len(text), i + width)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def main():
    ids = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else []
    conn = connect()
    cur = conn.cursor()
    if ids:
        cur.execute("""
            SELECT i.boq_item_id, i.project_id, i.material_name, i.quantity, i.unit,
                   i.item_description, i.evidence_bundle_json->>'local_path' AS lp
            FROM ekmi_zero.project_boq_item i
            WHERE i.boq_item_id = ANY(%s) ORDER BY i.boq_item_id
        """, (ids,))
    else:
        cur.execute("""
            SELECT i.boq_item_id, i.project_id, i.material_name, i.quantity, i.unit,
                   i.item_description, i.evidence_bundle_json->>'local_path' AS lp
            FROM ekmi_zero.project_boq_item i
            WHERE i.verified_by_raj='FRESH' AND i.approved_by_deepseek_level=2
            ORDER BY i.project_id, i.boq_item_id
        """)
    rows = cur.fetchall()
    print(f"=== items to verify: {len(rows)} ===\n")
    for bid, pid, mat, qty, unit, desc, lp in rows:
        print("=" * 90)
        print(f"item={bid} proj={pid} | extracted: {qty} {unit} | {mat}")
        print(f"desc: {desc}")
        print(f"file: {lp}")
        if lp:
            try:
                text = doc_text(lp)
                print(f"SOURCE: {context_around(text, desc or mat or '')}")
            except Exception as e:
                print(f"SOURCE: (read error: {e})")
        print()
    conn.close()


if __name__ == "__main__":
    main()
