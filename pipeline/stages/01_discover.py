"""Stage 1: discover tenders from every registered source and upsert as projects."""
import json
import os
import re
import sys
import time

from .. import config
from ..adapters import SOURCES
from ..logger import get_logger
from .base import find_or_create_project, connect

log = get_logger("01_discover")


def tender_title(t):
    return (t.get("title_in_english") or t.get("tenderTitle") or t.get("title") or "")[:500]


def tender_due(t):
    return (t.get("last_submission_date_english") or t.get("lastDateForSubmission")
            or t.get("submissionDate") or t.get("due"))


def tender_value(t):
    v = t.get("tenderValue") or t.get("tender_value")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    for adapter in SOURCES:
        src = adapter.source_id
        stamp_file = os.path.join(config.STATE, f"{src.lower()}_last_discovered.txt")
        if "--force" not in sys.argv and os.path.exists(stamp_file):
            try:
                age = time.time() - float(open(stamp_file).read().strip())
                if age < config.DISCOVERY_MIN_INTERVAL_SECONDS:
                    log.info("skip discover %s (last run %.0fs ago)", src, age)
                    continue
            except Exception:
                pass
        log.info("discovering %s tenders", src)
        try:
            tenders = adapter.discover()
        except Exception as e:
            log.error("discover %s FAILED: %s", src, e)
            continue
        log.info("found %d %s tenders", len(tenders), src)
        conn = connect()
        cur = conn.cursor()
        new = upd = changed = 0
        for t in tenders:
            title = tender_title(t)
            tid = t.get("id") or t.get("tenderId")
            docs = adapter.extract_docs(t)
            due = tender_due(t)
            value = tender_value(t)
            tnum = adapter.tender_number(t)
            # fetch old docs for change detection
            cur.execute("""
                SELECT project_id, source_project_identity_json->'docs' AS old_docs
                FROM ekmi_zero.project WHERE source_id=%s AND source_project_id=%s
            """, (src, str(tid)))
            row = cur.fetchone()
            old_docs = (row[1] if row else []) or []
            pid, created = find_or_create_project(cur, src, tid, title, tnum, None, due, value)
            if created:
                new += 1
            else:
                upd += 1
                old_urls = {d.get("url") for d in old_docs if isinstance(d, dict)}
                new_urls = {d["url"] for d in docs}
                if new_urls - old_urls:
                    cur.execute("UPDATE ekmi_zero.project SET status='discovered', updated_at=now() WHERE project_id=%s", (pid,))
                    changed += 1
            cur.execute("""
                UPDATE ekmi_zero.project
                SET source_project_identity_json = COALESCE(source_project_identity_json, '{}'::jsonb)
                        || jsonb_build_object('docs', %s::jsonb, 'authority', %s)
                WHERE project_id=%s
            """, (json.dumps(docs), t.get("inviting_authority"), pid))
        conn.commit()
        conn.close()
        log.info("%s discover done: new=%d updated=%d doc_changed=%d", src, new, upd, changed)
        with open(stamp_file, "w") as f:
            f.write(str(time.time()))


if __name__ == "__main__":
    main()
