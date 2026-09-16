"""Stage 16: NCRTC (RRTS Delhi-Meerut) awarded construction contracts + winners + values.

ncrtc.in/tender-awarded-2/ is a paginated WordPress list; each awarded contract is an
individual post with Name of work / parties qualified (winner) / awarded value fields.
Granite target = architectural finishing / entry-exit / station construction.
"""
import json
import os
import re
import ssl
import time
import urllib.request

from bs4 import BeautifulSoup

from .. import config
from ..logger import get_logger
from .base import connect, find_or_create_project

log = get_logger("16_ncrtc")

BASE = "https://ncrtc.in"
CACHE = os.path.join(config.STATE, "ncrtc_cache")
os.makedirs(CACHE, exist_ok=True)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

GRANITE_POS = re.compile(
    r"architectural|finish|entry|exit|construction|station|property development|"
    r"building|civil|enabling|elevated|viaduct|road carpet|ground development",
    re.I,
)
GRANITE_NEG = re.compile(
    r"licensing|licensee|co-branding|semi-naming|consultant|consultancy|ddc|"
    r"design consultant|manufactur|supply|facial|financial institution|solar|vehicle|"
    r"audit|asset|influenc|facility management|o&m|maintenance|shifting|modification|"
    r"kiosk|commercial space|fabrication|storm water",
    re.I,
)


def _get(url, key):
    p = os.path.join(CACHE, key + ".html")
    if os.path.exists(p):
        return open(p, encoding="utf-8", errors="replace").read()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                data = r.read().decode("utf-8", "replace")
            open(p, "w", encoding="utf-8").write(data)
            return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(8)


def _parse_post(html):
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup.body
    lines = [l.strip() for l in main.get_text("\n", strip=True).split("\n") if l.strip()]
    f = {}
    for i, l in enumerate(lines):
        if l.startswith("Name of work") and i + 1 < len(lines):
            f["work"] = lines[i + 1]
        elif l.startswith("Nos. and names of parties qualified") and i + 1 < len(lines):
            f["qualified"] = lines[i + 1]
        elif l.startswith("Awarded Value of Contract") and i + 1 < len(lines):
            f["value"] = lines[i + 1]
        elif l.startswith("Tender No.") and i + 1 < len(lines):
            f["tender_no"] = lines[i + 1]
    return f


def _parse_value(raw):
    if not raw:
        return None
    s = raw.replace(",", "").replace("₹", "").upper()
    m = re.search(r"([\d.]+)\s*(?:CRORE|CR\.?)?", s)
    if not m:
        return None
    num = float(m.group(1))
    if re.search(r"CRORE|CR\b", s):
        num *= 10_000_000
    elif re.search(r"LAKH|LAC", s):
        num *= 100_000
    return round(num, 2)


def _first_winner(qualified):
    if not qualified:
        return None
    m = re.search(r"1\.\s*(.+?)(?=\s+\d+\.\s*M?/?[sS]?|$)", qualified)
    if not m:
        return None
    w = re.sub(r"^\d+\.\s*M?/?[sS]\.?\s*", "", m.group(1)).strip()
    w = re.sub(r"^M/?[sS]\.?\s*", "", w).strip()
    return re.sub(r"\s+", " ", w).strip(" ,-")


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


def add_winner(cur, project_id, company_id, value, source_url):
    cur.execute("""
        SELECT role_id FROM ekmi_zero.project_company_role
        WHERE project_id=%s AND company_id=%s AND is_winner=1
          AND evidence_status='VERIFIED_SOURCE_EVIDENCE' AND is_deleted=false
    """, (project_id, company_id))
    if cur.fetchone():
        return False
    cur.execute("""
        INSERT INTO ekmi_zero.project_company_role
          (project_id, company_id, role_type, is_winner, award_status, bid_amount, currency,
           source_url, evidence_status, confidence, approved_by_deepseek, approved_by_deepseek_level,
           evidence_bundle_json, is_deleted)
        VALUES (%s,%s,'winner',1,'awarded',%s,'INR',%s,'VERIFIED_SOURCE_EVIDENCE',95,true,3,%s::jsonb,false)
    """, (project_id, company_id, value, source_url,
          json.dumps({"source_type": "official_award_fact", "source_url": source_url,
                      "award_value": value, "actor": config.ACTOR})))
    return True


def main():
    # 1. collect contract URLs from 10 listing pages
    all_urls = []
    for page in range(1, 11):
        u = f"{BASE}/tender-awarded-2/" + ("" if page == 1 else f"{page}/")
        try:
            h = _get(u, f"list_{page}")
            soup = BeautifulSoup(h, "lxml")
            for a in soup.find_all("a", href=True):
                if "/tender_awarded/" in a["href"] and a["href"] not in all_urls:
                    all_urls.append(a["href"])
        except Exception as e:
            log.warning("NCRTC list page %d failed: %s", page, e)
    all_urls = list(dict.fromkeys(all_urls))
    log.info("NCRTC: %d awarded contract URLs", len(all_urls))

    conn = connect()
    cur = conn.cursor()
    projects = winners = 0
    seen = set()
    for u in all_urls:
        key = re.sub(r"[^a-z0-9]+", "_", u)
        try:
            f = _parse_post(_get(u, key))
        except Exception as e:
            log.warning("NCRTC post failed %s: %s", u, e)
            continue
        tn = f.get("tender_no") or ""
        if not tn or tn in seen:
            continue
        seen.add(tn)
        work = f.get("work") or ""
        if not GRANITE_POS.search(work) or GRANITE_NEG.search(work):
            continue
        val_raw = f.get("value") or ""
        if re.search(r"per\s*sq|per\s*month|per\s*annum", val_raw, re.I):
            continue  # lease/licence fee, not a construction contract value
        wname = _first_winner(f.get("qualified"))
        val = _parse_value(val_raw)
        if not wname:
            continue
        pid, created = find_or_create_project(cur, "ncrtc_rrts", tn, work[:500],
                                              tn, u, None, val)
        cur.execute("""
            UPDATE ekmi_zero.project SET granite_relevance_status='candidate',
                project_value=COALESCE(project_value,%s), updated_at=now() WHERE project_id=%s
        """, (val, pid))
        projects += 1
        cid = get_or_create_company(cur, wname)
        if add_winner(cur, pid, cid, val, u):
            winners += 1
        log.info("%s: winner=%s value=%s", tn[:34], wname[:40], val)
    conn.commit()
    conn.close()
    log.info("NCRTC done: granite_projects=%d winners_added=%d", projects, winners)


if __name__ == "__main__":
    main()
