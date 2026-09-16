"""Stage 2: classify discovered projects (per-source keyword rules via adapters)."""
from ..adapters import SOURCES
from ..logger import get_logger
from .base import connect

log = get_logger("02_classify")


def main():
    conn = connect()
    cur = conn.cursor()
    for adapter in SOURCES:
        src = adapter.source_id
        cur.execute("""
            SELECT project_id, project_title FROM ekmi_zero.project
            WHERE status='discovered' AND coalesce(is_deleted,false)=false AND source_id=%s
            ORDER BY project_id
        """, (src,))
        rows = cur.fetchall()
        if not rows:
            continue
        counts = {"candidate": 0, "rejected": 0, "uncertain": 0}
        for pid, title in rows:
            bucket, reason = adapter.classify(title or "")
            cur.execute("""
                UPDATE ekmi_zero.project
                SET granite_relevance_status=%s, status='classified',
                    source_project_identity_json = COALESCE(source_project_identity_json, '{}'::jsonb)
                        || jsonb_build_object('classify_reason', %s)
                WHERE project_id=%s
            """, (bucket, reason, pid))
            counts[bucket] += 1
        log.info("%s classify done: %s", src, counts)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
