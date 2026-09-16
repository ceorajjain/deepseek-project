"""Stage 10: source coverage report — per-source pipeline state + blockers."""
import json
import os
import time

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("10_report")
OUT = os.path.join(config.STATE, "coverage_report.json")


def main():
    conn = connect()
    cur = conn.cursor()
    new_sources = ("DMRC", "rites_public_tenders", "CPPP", "CMRL")
    cur.execute("""
        SELECT source_id,
               count(*) AS total,
               count(*) FILTER (WHERE status='discovered') AS discovered,
               count(*) FILTER (WHERE status='classified') AS classified,
               count(*) FILTER (WHERE status='downloaded') AS downloaded,
               count(*) FILTER (WHERE status='extracted') AS extracted,
               count(*) FILTER (WHERE status='pending_review') AS pending_review,
               count(*) FILTER (WHERE status='verified') AS verified,
               count(*) FILTER (WHERE granite_relevance_status='candidate') AS candidate,
               count(*) FILTER (WHERE granite_relevance_status='uncertain') AS uncertain,
               count(*) FILTER (WHERE granite_relevance_status='rejected') AS rejected
        FROM ekmi_zero.project
        WHERE source_id IN %s
          AND COALESCE(is_deleted,false)=false
        GROUP BY source_id ORDER BY source_id
    """, (new_sources,))
    sources = {}
    for r in cur.fetchall():
        sources[r[0]] = dict(zip(["total", "discovered", "classified", "downloaded", "extracted",
                                  "pending_review", "verified", "candidate", "uncertain", "rejected"], r[1:]))
    cur.execute("SELECT count(*) FROM ekmi_zero.project WHERE source_id IS NOT NULL AND source_id NOT IN %s AND COALESCE(is_deleted,false)=false", (new_sources,))
    other_sources_count = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT source_id) FROM ekmi_zero.project WHERE source_id IS NOT NULL AND source_id NOT IN %s AND COALESCE(is_deleted,false)=false", (new_sources,))
    other_sources_distinct = cur.fetchone()[0]
    # approved items + winners
    cur.execute("""
        SELECT count(*)
        FROM ekmi_zero.project_boq_item i
        JOIN ekmi_zero.project p ON p.project_id=i.project_id
        WHERE i.verified_by_raj='FRESH' AND i.approved_by_deepseek_level=3
          AND COALESCE(p.is_deleted,false)=false
    """)
    approved_items = cur.fetchone()[0]
    cur.execute("""
        SELECT count(*)
        FROM ekmi_zero.project_company_role r
        JOIN ekmi_zero.project p ON p.project_id=r.project_id
        WHERE r.is_winner=1 AND r.evidence_status='VERIFIED_SOURCE_EVIDENCE'
          AND r.approved_by_deepseek_level>=3
          AND r.source_url IS NOT NULL AND COALESCE(p.is_deleted,false)=false
          AND COALESCE(r.is_deleted,false)=false
    """)
    winners = cur.fetchone()[0]
    cur.execute("""
        SELECT count(*)
        FROM ekmi_zero.project_company_role r
        JOIN ekmi_zero.project p ON p.project_id=r.project_id
        WHERE r.is_winner=0 AND r.role_type='bidder'
          AND r.evidence_status='VERIFIED_SOURCE_EVIDENCE'
          AND r.approved_by_deepseek_level>=3
          AND COALESCE(p.is_deleted,false)=false
          AND COALESCE(r.is_deleted,false)=false
    """)
    bidders = cur.fetchone()[0]
    conn.close()

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "approved_items": approved_items,
        "verified_winners": winners,
        "verified_bidders": bidders,
        "sources": sources,
        "old_other_sources": {"distinct_source_ids": other_sources_distinct, "total_projects": other_sources_count},
        "blockers": {
            "CPPP/GePNIC": "captcha-protected BOQ + search (2captcha answers currently rejected)",
            "metro (CMRL/BMRCL/MMRCL/KMRL)": "JS-rendered / GePNIC, needs browser",
            "DFCCIL tender list": "JS-rendered",
            "RITES/DFCCIL winners 2023-25": "not published yet",
            "Apollo contacts": "browser login",
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log.info("coverage report -> %s", OUT)
    print(json.dumps({"approved_items": approved_items, "verified_winners": winners,
                      "verified_bidders": bidders, "new_sources": sources,
                      "old_other_sources": report["old_other_sources"]}, indent=2))


if __name__ == "__main__":
    main()
