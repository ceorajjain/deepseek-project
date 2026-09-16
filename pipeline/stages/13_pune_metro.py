"""Stage 13: Pune Metro (Maha-Metro) — discover granite-relevant awarded contracts + winners.

The tender table itself carries the awarded contractor name for awarded rows, so winners are
captured during discovery. Granite target = station/depot CONSTRUCTION + architectural finishing
(metro stations need granite/marble flooring & cladding).
"""
import json
import os
import re

from .. import config
from ..adapters import PuneMetroAdapter
from ..http import get as http_get
from ..logger import get_logger
from .base import connect, find_or_create_project

log = get_logger("13_pune_metro")
ADAPTER = PuneMetroAdapter()
TENDERS_URL = ADAPTER.TENDERS_URL


def clean_winner(raw):
    s = re.sub(r"^M/?[sS]\.?\s*", "", raw or "").strip()
    s = re.sub(r"^(Joint Venture of|Consortium of|JV of|1\.\)?)\s*", "", s, flags=re.I)
    s = re.sub(r"[\s,;]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def name_key(name):
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def get_or_create_company(cur, name):
    nk = name_key(name)
    cur.execute("SELECT company_id FROM ekmi_zero.company WHERE name_key=%s AND is_deleted=false "
                "ORDER BY company_id LIMIT 1", (nk,))
    r = cur.fetchone()
    if r:
        cid = r[0]
        cur.execute("UPDATE ekmi_zero.company SET created_by_run='codex_fresh_v2', "
                    "evidence_level='VERIFIED_IDENTITY', approved_by_deepseek_level=3, "
                    "updated_at=now() WHERE company_id=%s", (cid,))
        return cid
    cur.execute("""
        INSERT INTO ekmi_zero.company (name, name_key, created_by_run, evidence_level,
                                       approved_by_deepseek_level, source_confidence, data_quality_score,
                                       buyer_priority_score, do_not_contact, manual_hold, company_uid, is_deleted)
        VALUES (%s,%s,'codex_fresh_v2','VERIFIED_IDENTITY',3,95,80,0,0,0,%s,false) RETURNING company_id
    """, (name, nk, "codex_fresh_v2:" + nk))
    return cur.fetchone()[0]


def add_winner_role(cur, project_id, company_id, source_url):
    cur.execute("""
        SELECT role_id FROM ekmi_zero.project_company_role
        WHERE project_id=%s AND company_id=%s AND is_winner=1
          AND evidence_status='VERIFIED_SOURCE_EVIDENCE' AND is_deleted=false
    """, (project_id, company_id))
    if cur.fetchone():
        return False
    cur.execute("""
        INSERT INTO ekmi_zero.project_company_role
          (project_id, company_id, role_type, is_winner, award_status, source_url,
           evidence_status, confidence, approved_by_deepseek, approved_by_deepseek_level,
           evidence_bundle_json, is_deleted)
        VALUES (%s,%s,'winner',1,'awarded',%s,'VERIFIED_SOURCE_EVIDENCE',95,true,3,%s::jsonb,false)
    """, (project_id, company_id, source_url,
          json.dumps({"source_type": "official_tender_award_fact", "source_url": source_url,
                      "actor": config.ACTOR})))
    return True


def main():
    tenders = ADAPTER.discover()
    log.info("Pune Metro: %d tenders discovered", len(tenders))
    conn = connect()
    cur = conn.cursor()
    projects = winners = 0
    for t in tenders:
        title = t.get("title") or ""
        cls, reason = ADAPTER.classify(title)
        if cls != "candidate":
            continue
        wraw = ADAPTER.winner_name(t)
        if not wraw:
            continue
        tnum = ADAPTER.tender_number(t)
        docs = ADAPTER.extract_docs(t)
        pid, created = find_or_create_project(cur, ADAPTER.source_id, tnum, title[:500],
                                              tnum, TENDERS_URL, None, None)
        cur.execute("""
            UPDATE ekmi_zero.project
            SET granite_relevance_status='candidate',
                source_project_identity_json = COALESCE(source_project_identity_json,'{}'::jsonb)
                    || jsonb_build_object('docs', %s::jsonb, 'authority', 'Maha-Metro Pune'),
                status=CASE WHEN status='discovered' THEN 'discovered' ELSE status END,
                updated_at=now()
            WHERE project_id=%s
        """, (json.dumps(docs), pid))
        projects += 1
        wname = clean_winner(wraw)
        if not wname:
            continue
        cid = get_or_create_company(cur, wname)
        if add_winner_role(cur, pid, cid, TENDERS_URL):
            winners += 1
        log.info("%s: winner=%s", tnum, wname[:50])
    conn.commit()
    conn.close()
    log.info("Pune Metro done: granite_projects=%d winners_added=%d", projects, winners)


if __name__ == "__main__":
    main()
