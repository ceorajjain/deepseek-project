"""Common stage helpers (upsert project, claim pending rows)."""
import re

import psycopg2

from .. import config


def upsert_project(cur, source_id, source_project_id, title, tender_number=None,
                   source_url=None, due=None):
    """Upsert a discovered project by (source_id, source_project_id). Returns project_id."""
    cur.execute("""
        INSERT INTO ekmi_zero.project
          (project_title, tender_number, source_id, source_url, source_project_id,
           country, country_code, bid_due_date, status, granite_relevance_status,
           is_synthetic, provenance_class, lineage_status, approved_by_deepseek,
           approved_by_deepseek_level)
        VALUES (%s,%s,%s,%s,%s,'India','IN',%s,'discovered','pending',
                false,'REAL','SOURCE_VERIFIED',false,0)
        ON CONFLICT (source_id, source_project_id)
          WHERE source_id IS NOT NULL AND source_project_id IS NOT NULL
            AND COALESCE(is_deleted, false) = false
        DO UPDATE SET project_title=EXCLUDED.project_title,
                      tender_number=COALESCE(EXCLUDED.tender_number, project.tender_number),
                      source_url=COALESCE(EXCLUDED.source_url, project.source_url),
                      bid_due_date=COALESCE(EXCLUDED.bid_due_date, project.bid_due_date)
        RETURNING project_id
    """, (title, tender_number, source_id, source_url, str(source_project_id), due))
    return cur.fetchone()[0]


def connect():
    return psycopg2.connect(**config.DB)


def find_or_create_project(cur, source_id, source_project_id, title, tender_number=None,
                           source_url=None, due=None, value=None):
    """Dedup by (source_id, source_project_id), then by (source_id, normalized tender_number).
    Returns (project_id, created_bool)."""
    norm = re.sub(r"[^A-Z0-9]", "", (tender_number or "")).upper() or None
    cur.execute("""
        SELECT project_id FROM ekmi_zero.project
        WHERE source_id=%s AND source_project_id=%s AND coalesce(is_deleted,false)=false
    """, (source_id, str(source_project_id)))
    r = cur.fetchone()
    pid = r[0] if r else None
    if pid is None and norm:
        cur.execute("""
            SELECT project_id FROM ekmi_zero.project
            WHERE source_id=%s AND upper(regexp_replace(tender_number,'[^A-Z0-9]','','g'))=%s
              AND coalesce(is_deleted,false)=false
            ORDER BY project_id LIMIT 1
        """, (source_id, norm))
        r = cur.fetchone()
        pid = r[0] if r else None
    if pid is not None:
        cur.execute("""
            UPDATE ekmi_zero.project
            SET project_title=%s, tender_number=COALESCE(%s, tender_number),
                source_project_id=COALESCE(source_project_id, %s),
                source_url=COALESCE(%s, source_url), bid_due_date=COALESCE(%s, bid_due_date),
                project_value=COALESCE(%s, project_value)
            WHERE project_id=%s
        """, (title, tender_number, str(source_project_id), source_url, due, value, pid))
        return pid, False
    cur.execute("""
        INSERT INTO ekmi_zero.project
          (project_title, tender_number, source_id, source_url, source_project_id,
           country, country_code, bid_due_date, status, granite_relevance_status,
           project_value, is_synthetic, provenance_class, lineage_status, approved_by_deepseek,
           approved_by_deepseek_level)
        VALUES (%s,%s,%s,%s,%s,'India','IN',%s,'discovered','pending',%s,
                false,'REAL','SOURCE_VERIFIED',false,0)
        RETURNING project_id
    """, (title, tender_number, source_id, source_url, str(source_project_id), due, value))
    return cur.fetchone()[0], True
