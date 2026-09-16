"""Stage 7: export the CRM (granite-selling list).

Two tiers, both source-verified:
  1. BOQ-confirmed projects  -> approved (level=3) granite items + total sqm/sqft + materials.
  2. Awarded granite-relevant projects -> verified winner + value + award date, but BOQ not public.

This matters because most awarded architectural-finishing contracts do NOT publish their
itemized BOQ; the winner + value is still a granite-selling lead even without public BOQ.
"""
import csv
import os
import re
import time

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("07_export")
OUT = os.path.join(config.STATE, "crm_export.csv")

GRANITE_POS = (
    r"architectural|finish|building|station|college|hostel|accommodation|barrack|"
    r"facade|cladding|tower|renovation|reconstruction|residence|quarters|depot"
)
GRANITE_NEG = (
    r"tunnel|highway|four-lane|road km|pavement|track|ballastless|signalling|telecom|"
    r"rolling stock|overhaul|transformation|property development|facility management|security"
)


def _open(path):
    try:
        return open(path, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        p2 = path.replace(".csv", "_%s.csv" % time.strftime("%H%M%S"))
        return open(p2, "w", newline="", encoding="utf-8-sig")


def main():
    conn = connect()
    cur = conn.cursor()

    # winners map (project_id -> winner names)
    cur.execute("""
        SELECT r.project_id, string_agg(c.name, '; ') AS winners
        FROM ekmi_zero.project_company_role r
        JOIN ekmi_zero.company c ON c.company_id=r.company_id
        WHERE r.is_winner=1 AND r.evidence_status='VERIFIED_SOURCE_EVIDENCE'
          AND r.is_deleted=false
        GROUP BY r.project_id
    """)
    winners = {r[0]: r[1] for r in cur.fetchall()}

    # Tier 1: BOQ-confirmed (level-3 granite items)
    cur.execute("""
        SELECT p.project_id, p.tender_number, p.project_title, p.city, p.state, p.department,
               p.project_value, p.award_date,
               round(sum(CASE WHEN upper(i.unit)='SQM' THEN i.quantity ELSE 0 END)::numeric, 1) AS total_sqm,
               round(sum(COALESCE(i.quantity_sqft, 0))::numeric, 1) AS total_sqft,
               string_agg(DISTINCT concat_ws(' ',
                   i.material_name,
                   CASE WHEN i.size_length IS NOT NULL AND i.size_width IS NOT NULL
                        THEN i.size_length::text || 'x' || i.size_width::text || COALESCE(i.size_unit,'') END,
                   CASE WHEN i.thickness_min_mm IS NOT NULL THEN i.thickness_min_mm::text || 'mm' END,
                   CASE WHEN i.finish IS NOT NULL THEN i.finish END), '; ') AS product_spec,
               count(i.boq_item_id) AS stone_items
        FROM ekmi_zero.project_boq_item i
        JOIN ekmi_zero.project p ON p.project_id=i.project_id
        WHERE i.approved_by_deepseek=true AND i.approved_by_deepseek_level=3 AND i.verified_by_raj='FRESH'
        GROUP BY p.project_id, p.tender_number, p.project_title, p.city, p.state, p.department,
                 p.project_value, p.award_date
        ORDER BY total_sqft DESC NULLS LAST
    """)
    boq_rows = cur.fetchall()
    boq_ids = {r[0] for r in boq_rows}

    # Tier 2: awarded granite-relevant projects (winner + value) with no public BOQ
    cur.execute("""
        SELECT p.project_id, p.tender_number, p.project_title, p.city, p.state, p.department,
               p.project_value, p.award_date, p.granite_relevance_status
        FROM ekmi_zero.project p
        WHERE p.project_id IN (SELECT project_id FROM ekmi_zero.project_company_role
                                WHERE is_winner=1 AND evidence_status='VERIFIED_SOURCE_EVIDENCE'
                                  AND is_deleted=false)
          AND coalesce(p.is_deleted,false)=false
          AND p.approved_by_deepseek_level < 3
        ORDER BY p.project_value DESC NULLS LAST
    """)
    awarded_rows = []
    for r in cur.fetchall():
        pid, tn, title, city, state, dept, val, award_date, rel = r
        t = (title or "")
        rel_granite = rel in ("candidate", "stone_candidate")
        title_granite = False
        if not rel_granite and ("building" in t.lower() or "construction" in t.lower()
                                or "renovation" in t.lower() or "college" in t.lower()
                                or "tower" in t.lower() or "accommodation" in t.lower()
                                or "barrack" in t.lower()):
            title_granite = True
        # authoritative rel flag always wins; only negate a title-based guess
        if title_granite and re.search(GRANITE_NEG, t, re.I):
            granite = False
        else:
            granite = rel_granite or title_granite
        if not granite:
            continue
        awarded_rows.append((pid, tn, title, city, state, dept, val, award_date))

    # Detailed stone items export (item-level product clarity for stock selling)
    cur.execute("""
        SELECT p.tender_number, i.material_name, i.color AS variety,
               i.size_length, i.size_width, i.size_unit,
               i.thickness_min_mm, i.thickness_max_mm, i.finish,
               i.quantity, i.unit, i.alternative_materials, i.source_url
        FROM ekmi_zero.project_boq_item i
        JOIN ekmi_zero.project p ON p.project_id = i.project_id
        WHERE i.approved_by_deepseek=true AND i.approved_by_deepseek_level=3 AND i.verified_by_raj='FRESH'
        ORDER BY p.tender_number, i.material_name, i.thickness_min_mm
    """)
    stone_rows = cur.fetchall()
    conn.close()

    fh = _open(OUT)
    with fh as f:
        w = csv.writer(f)
        w.writerow(["project_id", "tender_number", "title", "city", "state", "department",
                    "project_value", "award_date", "total_granite_sqm", "total_granite_sqft",
                    "stone_items", "product_spec", "winner", "boq_status"])
        for r in boq_rows:
            pid = r[0]
            w.writerow([pid, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[11], r[10],
                        winners.get(pid, ""), "boq_confirmed"])
        for r in awarded_rows:
            pid = r[0]
            if pid in boq_ids:
                continue
            w.writerow([pid, r[1], r[2], r[3], r[4], r[5], r[6], r[7], "", "", 0, "",
                        winners.get(pid, ""), "awarded_no_public_boq"])
    log.info("CRM export: %d BOQ-confirmed + %d awarded projects -> %s",
             len(boq_rows), len(awarded_rows), OUT)

    # write detailed stone items
    items_out = os.path.join(config.STATE, "stone_items_export.csv")
    fh2 = _open(items_out)
    with fh2 as f:
        w = csv.writer(f)
        w.writerow(["tender_number", "product", "variety", "size_length", "size_width",
                    "size_unit", "thickness_min_mm", "thickness_max_mm", "finish",
                    "quantity", "unit", "alternative_materials", "source_url"])
        for r in stone_rows:
            w.writerow(list(r))
    log.info("Stone items export: %d rows -> %s", len(stone_rows), items_out)


if __name__ == "__main__":
    main()
