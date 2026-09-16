"""Fix 2 (evidence backfill) + Fix 3 (repair document paths)."""
import os
import sys
import urllib.parse
import psycopg2

from . import config
from .logger import get_logger

log = get_logger("foundation_fix2")
COMMIT = "--commit" in sys.argv


def media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".xls",):
        return "application/vnd.ms-excel"
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def authority(url):
    try:
        return (urllib.parse.urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def main():
    conn = psycopg2.connect(**config.DB)
    cur = conn.cursor()
    log.info("foundation fix2 mode=%s", "COMMIT" if COMMIT else "DRY-RUN")

    # ---- Fix 2: backfill source_evidence_asset from item evidence ----
    cur.execute("""
        SELECT DISTINCT ON (sha)
               i.evidence_bundle_json->>'sha256' AS sha,
               i.evidence_bundle_json->>'local_path' AS lp,
               min(i.source_url) AS url,
               i.evidence_bundle_json->>'actor' AS actor
        FROM ekmi_zero.project_boq_item i
        WHERE i.verified_by_raj='FRESH'
          AND i.evidence_bundle_json->>'sha256' IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM ekmi_zero.source_evidence_asset a WHERE a.sha256 = i.evidence_bundle_json->>'sha256')
        GROUP BY i.evidence_bundle_json->>'sha256', i.evidence_bundle_json->>'local_path', i.evidence_bundle_json->>'actor'
    """)
    rows = cur.fetchall()
    backfilled = 0
    for sha, lp, url, actor in rows:
        if not lp or not os.path.isfile(lp):
            continue
        mtype = media_type(lp)
        size = os.path.getsize(lp)
        host = authority(url or "")
        fname = os.path.basename(url or lp)
        cur.execute("""
            INSERT INTO ekmi_zero.source_evidence_asset
              (sha256, source_url, local_path, file_name, media_type, size_bytes, authority_host,
               retrieval_status, provenance_status, raw_metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'captured','official_source_captured',
                    jsonb_build_object('actor', COALESCE(%s,'codex_fresh_v1'), 'backfilled', true))
            ON CONFLICT (sha256) DO NOTHING
        """, (sha, url, lp, fname, mtype, size, host, actor))
        backfilled += cur.rowcount
    log.info("fix2 backfilled evidence assets=%d (of %d)", backfilled, len(rows))

    # ---- Fix 3: repair document local_path + sha256 from item evidence ----
    cur.execute("""
        UPDATE ekmi_zero.project_boq_document d
        SET local_path = sub.lp,
            sha256_hash = sub.sha
        FROM (
            SELECT DISTINCT ON (project_id) project_id,
                   evidence_bundle_json->>'local_path' AS lp,
                   evidence_bundle_json->>'sha256' AS sha
            FROM ekmi_zero.project_boq_item
            WHERE verified_by_raj='FRESH' AND evidence_bundle_json->>'local_path' IS NOT NULL
            ORDER BY project_id, boq_item_id
        ) sub
        WHERE d.project_id = sub.project_id
          AND (d.local_path LIKE '%%2026-06-08%%' OR d.local_path LIKE '%%project_boq_intelligence%%' OR d.sha256_hash IS NULL)
    """)
    repaired = cur.rowcount
    log.info("fix3 repaired document paths=%d", repaired)

    if COMMIT:
        conn.commit()
        log.info("COMMITTED")
    else:
        conn.rollback()
        log.info("DRY-RUN (rolled back)")
    conn.close()


if __name__ == "__main__":
    main()
