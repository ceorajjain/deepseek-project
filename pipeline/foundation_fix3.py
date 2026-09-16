"""Fix 1: merge DGON duplicate projects (930031 canonical; 930041/930042 merged into it)."""
import hashlib
import sys
import psycopg2

from . import config
from .logger import get_logger

log = get_logger("foundation_fix3")
COMMIT = "--commit" in sys.argv
CANON = 930031
DUPS = [930041, 930042]


def dedupe_key(pid, sha, loc, desc):
    return hashlib.sha256(f"{pid}:{sha}:{loc}:{desc}".encode()).hexdigest()


def main():
    conn = psycopg2.connect(**config.DB)
    cur = conn.cursor()
    log.info("foundation fix3 (DGON merge) mode=%s", "COMMIT" if COMMIT else "DRY-RUN")

    moved_items = 0
    moved_docs = 0
    for dup in DUPS:
        # fetch items to recompute dedupe_key
        cur.execute("""
            SELECT boq_item_id, evidence_bundle_json->>'sha256' AS sha,
                   item_no, item_description, source_url
            FROM ekmi_zero.project_boq_item
            WHERE project_id=%s AND verified_by_raj='FRESH'
        """, (dup,))
        items = cur.fetchall()
        for bid, sha, loc, desc, url in items:
            new_key = dedupe_key(CANON, sha or "", loc or "", desc or "")
            cur.execute("""
                UPDATE ekmi_zero.project_boq_item
                SET project_id=%s, dedupe_key=%s
                WHERE boq_item_id=%s
            """, (CANON, new_key, bid))
            moved_items += cur.rowcount

        # move documents
        cur.execute("""
            UPDATE ekmi_zero.project_boq_document
            SET project_id=%s
            WHERE project_id=%s
        """, (CANON, dup))
        moved_docs += cur.rowcount

        # mark duplicate project deleted
        cur.execute("""
            UPDATE ekmi_zero.project
            SET is_deleted=true, deleted_at=now()
            WHERE project_id=%s
        """, (dup,))
        log.info("merged project %s -> %s (fresh_items=%d)", dup, CANON, len(items))

    log.info("moved_items=%d moved_docs=%d", moved_items, moved_docs)

    if COMMIT:
        conn.commit()
        log.info("COMMITTED")
    else:
        conn.rollback()
        log.info("DRY-RUN (rolled back)")
    conn.close()


if __name__ == "__main__":
    main()
