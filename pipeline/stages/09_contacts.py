"""Stage 9: export outreach contacts (winners + bidders) with project + proof."""
import csv
import os
import time

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("09_contacts")
OUT = os.path.join(config.STATE, "contacts_export.csv")


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.name, r.role_type, r.bid_rank, p.tender_number, p.project_title, p.city, p.state,
               r.bid_amount, r.source_url
        FROM ekmi_zero.project_company_role r
        JOIN ekmi_zero.company c ON c.company_id=r.company_id
        JOIN ekmi_zero.project p ON p.project_id=r.project_id
        WHERE r.evidence_status='VERIFIED_SOURCE_EVIDENCE' AND r.approved_by_deepseek=true
          AND coalesce(r.is_deleted,false)=false
        ORDER BY r.role_type DESC, p.project_id, r.bid_rank NULLS LAST
    """)
    rows = cur.fetchall()
    conn.close()

    out = OUT
    try:
        fh = open(out, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        out = OUT.replace(".csv", "_%s.csv" % time.strftime("%H%M%S"))
        fh = open(out, "w", newline="", encoding="utf-8-sig")
    with fh as f:
        w = csv.writer(f)
        w.writerow(["company", "role", "rank", "tender_number", "project_title", "city", "state",
                    "contract_value", "source_url"])
        for r in rows:
            w.writerow(list(r))
    log.info("contacts export: %d rows -> %s", len(rows), out)


if __name__ == "__main__":
    main()
