"""Step 1: Foundation fix — safe data repairs (dry-run by default, --commit to apply)."""
import argparse
import sys
import psycopg2

from . import config
from .logger import get_logger

log = get_logger("foundation_fix")
COMMIT = "--commit" in sys.argv


def main():
    conn = psycopg2.connect(**config.DB)
    cur = conn.cursor()
    log.info("foundation fix mode=%s", "COMMIT" if COMMIT else "DRY-RUN")

    # Fix 5: fill city/state for DMRC-942/938/931 (Delhi)
    cur.execute("""
        UPDATE ekmi_zero.project
        SET city='New Delhi', state='Delhi', department='Delhi Metro Rail Corporation', updated_at=now()
        WHERE source_id='DMRC' AND source_project_id IN ('942','938','931')
          AND (city IS NULL OR city='')
    """)
    log.info("fix5 city/state rows=%d", cur.rowcount)

    # Fix 4: normalize project approved level to match its items (max level)
    cur.execute("""
        UPDATE ekmi_zero.project p
        SET approved_by_deepseek_level = sub.mx,
            approved_by_deepseek = (sub.mx >= 3),
            updated_at = now()
        FROM (
            SELECT project_id, COALESCE(MAX(approved_by_deepseek_level), 0) AS mx
            FROM ekmi_zero.project_boq_item
            WHERE verified_by_raj='FRESH'
            GROUP BY project_id
        ) sub
        WHERE p.project_id = sub.project_id
          AND p.approved_by_deepseek_level IS DISTINCT FROM sub.mx
    """)
    log.info("fix4 normalize flags rows=%d", cur.rowcount)

    # Fix 6: quarantine any remaining non-verified winners on fresh projects
    cur.execute("""
        UPDATE ekmi_zero.project_company_role
        SET evidence_status='quarantined', approved_by_deepseek=false, approved_by_deepseek_level=0, updated_at=now()
        WHERE is_winner=1
          AND coalesce(is_deleted,false)=false
          AND evidence_status NOT IN ('VERIFIED_SOURCE_EVIDENCE','quarantined')
          AND project_id IN (SELECT DISTINCT project_id FROM ekmi_zero.project_boq_item WHERE verified_by_raj='FRESH')
    """)
    log.info("fix6 quarantine winners rows=%d", cur.rowcount)

    # Fix 7: mark verified companies with fresh marker
    cur.execute("""
        UPDATE ekmi_zero.company
        SET created_by_run = COALESCE(created_by_run, 'codex_fresh_v2'),
            approved_by_deepseek_level = GREATEST(approved_by_deepseek_level, 3),
            updated_at = now()
        WHERE evidence_level='VERIFIED_IDENTITY'
    """)
    log.info("fix7 mark companies rows=%d", cur.rowcount)

    if COMMIT:
        conn.commit()
        log.info("COMMITTED")
    else:
        conn.rollback()
        log.info("DRY-RUN (rolled back)")
    conn.close()


if __name__ == "__main__":
    main()
