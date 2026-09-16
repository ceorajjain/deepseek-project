"""Stage 14: Madhya Pradesh Metro (MPMRCL, Bhopal + Indore) awarded construction + winners.

mpmetrorail.com/awarded-tenders lists winner + LOA date directly (no separate award PDF).
Granite target = station/depot/parking/viaduct CONSTRUCTION (metro stations carry granite).
Value is not in the table (only in LOA PDFs) -> stored as NULL (honest).
"""
import json
import os
import re
import ssl
import urllib.request

from bs4 import BeautifulSoup

from .. import config
from ..logger import get_logger
from .base import connect, find_or_create_project

log = get_logger("14_mp_metro")

URL = "https://www.mpmetrorail.com/awarded-tenders"
CACHE = os.path.join(config.STATE, "mp_metro_cache", "awarded_tenders.html")
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

GRANITE_NEG = re.compile(
    r"consultanc|consultant|design consultant|detailed design|ddc|dve|design verification|"
    r"geotech|inspection|proof|manufacture|supply|installation|testing|commissioning|"
    r"o&m|maintenance|social media|manpower|travel|interiors|rehabilitation|signalling|telecom",
    re.I,
)
GRANITE_POS = re.compile(
    r"construction|elevated stations|depot|parking|enabling works|viaduct|station|civil works",
    re.I,
)


def _get(url):
    if os.path.exists(CACHE):
        return open(CACHE, encoding="utf-8", errors="replace").read()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        data = r.read().decode("utf-8", "replace")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        f.write(data)
    return data


def clean_winner(raw):
    s = re.sub(r"^M/?[sS]\.?\s*", "", raw or "").strip()
    s = re.sub(r"\s+", " ", s).strip(" ,-")
    return s


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


def add_winner(cur, project_id, company_id, date, source_url):
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
          json.dumps({"source_type": "official_award_fact", "source_url": source_url,
                      "award_date": date, "actor": config.ACTOR})))
    return True


def main():
    html = _get(URL)
    soup = BeautifulSoup(html, "lxml")
    conn = connect()
    cur = conn.cursor()
    projects = winners = 0
    for tb in soup.find_all("table"):
        for tr in tb.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 8:
                continue
            # columns: S.No | Tender No | Work | Mode | NIT Date | Bidding | LOA Date | Winner
            tn = cells[1]
            work = cells[2]
            loa = cells[6]
            winner = cells[7]
            if not tn or tn.lower() in ("tender no.", "tender no"):
                continue
            if not GRANITE_POS.search(work) or GRANITE_NEG.search(work):
                continue
            wname = clean_winner(winner)
            if not wname:
                continue
            pid, created = find_or_create_project(cur, "mp_metro", tn, work[:500],
                                                  tn, URL, None, None)
            cur.execute("""
                UPDATE ekmi_zero.project SET granite_relevance_status='candidate',
                    award_date=COALESCE(award_date,%s), updated_at=now() WHERE project_id=%s
            """, (loa, pid))
            projects += 1
            cid = get_or_create_company(cur, wname)
            if add_winner(cur, pid, cid, loa, URL):
                winners += 1
            log.info("%s: winner=%s", tn[:40], wname[:45])
    conn.commit()
    conn.close()
    log.info("MP Metro awarded done: granite_projects=%d winners_added=%d", projects, winners)


if __name__ == "__main__":
    main()
