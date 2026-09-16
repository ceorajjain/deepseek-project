"""Stage 5: manual verification.
Without --apply: generate a human review queue (CSV) of L2 items + evidence.
With --apply <csv>: apply human decisions (approve -> L3, reject -> L0).
approved_by_deepseek=true is set ONLY by this manual step, never automatically.
"""
import csv
import os
import sys

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("05_verify")
REVIEW_FILE = os.path.join(config.STATE, "review_queue.csv")
VALID_UNITS = {"SQM", "SQFT", "CFT", "CUM", "RFT", "RMT", "MT", "NOS", "KG"}


def generate():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE ekmi_zero.project_boq_item
        SET approved_by_deepseek=false, approved_by_deepseek_level=0, quantity=NULL, unit=NULL,
            verification_remarks='failed_sanity', updated_at=now()
        WHERE verified_by_raj='FRESH' AND approved_by_deepseek_level=2
          AND (quantity IS NULL OR quantity <= 0 OR upper(coalesce(unit,'')) NOT IN %s OR material_name IS NULL)
    """, (tuple(VALID_UNITS),))
    log.info("auto-demoted invalid items=%d", cur.rowcount)

    cur.execute("""
        SELECT i.boq_item_id, i.project_id, p.tender_number, i.material_name, i.quantity, i.unit,
               i.item_description, i.source_url, i.evidence_bundle_json->>'local_path' AS lp
        FROM ekmi_zero.project_boq_item i
        JOIN ekmi_zero.project p ON p.project_id=i.project_id
        WHERE i.verified_by_raj='FRESH' AND i.approved_by_deepseek_level=2
        ORDER BY i.project_id, i.boq_item_id
    """)
    rows = cur.fetchall()
    with open(REVIEW_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["boq_item_id", "project_id", "tender_number", "material", "quantity", "unit",
                    "description", "source_url", "local_path", "decision"])
        for r in rows:
            w.writerow(list(r) + [""])
    cur.execute("UPDATE ekmi_zero.project SET status='pending_review' WHERE status='extracted'")
    conn.commit()
    conn.close()
    log.info("review queue generated: %d items -> %s", len(rows), REVIEW_FILE)
    log.info("MANUAL VERIFICATION (by Codex): run `verify_context` to read each item's source "
             "PDF/XLS, confirm qty/unit, then run 05_verify --apply to approve/reject.")


def apply(csv_path):
    conn = connect()
    cur = conn.cursor()
    approved = rejected = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bid = (row.get("boq_item_id") or "").strip()
            decision = (row.get("decision") or "").strip().lower()
            if not bid or not decision:
                continue
            if decision in ("approve", "approved", "yes", "y", "ok"):
                cur.execute("""
                    UPDATE ekmi_zero.project_boq_item
                    SET approved_by_deepseek=true, approved_by_deepseek_level=3,
                        verification_remarks='manually_approved', updated_at=now()
                    WHERE boq_item_id=%s AND verified_by_raj='FRESH' AND approved_by_deepseek_level=2
                """, (int(bid),))
                approved += cur.rowcount
            elif decision in ("reject", "rejected", "no", "n", "fake"):
                cur.execute("""
                    UPDATE ekmi_zero.project_boq_item
                    SET approved_by_deepseek=false, approved_by_deepseek_level=0, quantity=NULL, unit=NULL,
                        verification_remarks='manually_rejected', updated_at=now()
                    WHERE boq_item_id=%s AND verified_by_raj='FRESH'
                """, (int(bid),))
                rejected += cur.rowcount
    cur.execute("""
        UPDATE ekmi_zero.project p
        SET approved_by_deepseek_level=sub.mx, approved_by_deepseek=(sub.mx>=3),
            status=CASE WHEN sub.mx>=3 THEN 'verified' ELSE status END, updated_at=now()
        FROM (SELECT project_id, COALESCE(MAX(approved_by_deepseek_level),0) mx
              FROM ekmi_zero.project_boq_item WHERE verified_by_raj='FRESH' GROUP BY project_id) sub
        WHERE p.project_id=sub.project_id AND p.status='pending_review'
    """)
    conn.commit()
    conn.close()
    log.info("manual review applied: approved=%d rejected=%d", approved, rejected)


def main():
    if "--apply" in sys.argv:
        i = sys.argv.index("--apply")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else REVIEW_FILE
        apply(path)
    else:
        generate()


if __name__ == "__main__":
    main()
