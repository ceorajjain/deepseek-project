import argparse
import json
import sys
import signal

from . import config, db
from .logger import get_logger
from .extract_stone import extract

log = get_logger("runner")


def validate_config():
    if not config.DEEPSEEK_API_KEY:
        log.warning("DEEPSEEK_API_KEY is not set (extract will fail until set)")
    import os
    assert os.path.isdir(config.VAULT), f"vault missing: {config.VAULT}"
    assert os.path.isdir(config.LOGS), f"logs dir missing: {config.LOGS}"


def cmd_status(args):
    log.info("checking DB state")
    rows = db.count_approved_items()
    total = sum(n for _, n in rows)
    log.info("FRESH items total=%d", total)
    for lvl, n in rows:
        log.info("  level %s = %d", lvl, n)
    conn = db.connect(readonly=True)
    cur = conn.cursor()
    cur.execute("""
        SELECT count(*) AS active_projects,
               count(*) FILTER (WHERE approved_by_deepseek_level>=3) AS verified_projects
        FROM ekmi_zero.project
        WHERE COALESCE(is_deleted,false)=false
    """)
    proj = cur.fetchone()
    cur.execute("""
        SELECT p.source_id, count(*) AS verified_items
        FROM ekmi_zero.project_boq_item i
        JOIN ekmi_zero.project p ON p.project_id=i.project_id
        WHERE i.approved_by_deepseek_level=3 AND i.verified_by_raj='FRESH'
          AND COALESCE(p.is_deleted,false)=false
        GROUP BY p.source_id
        ORDER BY verified_items DESC
    """)
    by_source = cur.fetchall()
    cur.execute("""
        SELECT count(*) FROM ekmi_zero.project_company_role r
        JOIN ekmi_zero.project p ON p.project_id=r.project_id
        WHERE r.is_winner=1 AND r.approved_by_deepseek_level>=3
          AND COALESCE(p.is_deleted,false)=false AND COALESCE(r.is_deleted,false)=false
    """)
    winners = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ekmi_zero.project_boq_document WHERE fetch_status='fetched'")
    docs = cur.fetchone()[0]
    conn.close()
    report = {
        "fresh_items": total,
        "by_level": dict(rows),
        "active_projects": proj[0],
        "verified_projects": proj[1],
        "verified_winners": winners,
        "fetched_documents": docs,
        "verified_items_by_source": dict(by_source),
    }
    log.info("active projects=%s verified projects=%s", proj[0], proj[1])
    log.info("verified winners=%s fetched documents=%s", winners, docs)
    for src, n in by_source:
        log.info("  %s = %d verified items", src, n)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def cmd_health(args):
    log.info("health check")
    out = {}
    # DB
    try:
        conn = db.connect(readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        out["db"] = "ok"
    except Exception as e:
        out["db"] = f"FAIL: {e}"
    # vault
    import os
    out["vault"] = "ok" if os.path.isdir(config.VAULT) else "FAIL"
    # deepseek
    out["deepseek_key"] = "set" if config.DEEPSEEK_API_KEY else "MISSING"
    print(json.dumps(out, indent=2))
    log.info("health: %s", out)


def cmd_extract(args):
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    if not text or not text.strip():
        log.error("no input text provided")
        sys.exit(1)
    log.info("extracting stone items (len=%d chars)", len(text))
    items = extract(text, model=args.model)
    print(json.dumps(items, indent=2, ensure_ascii=False))
    log.info("done: %d stone items", len(items))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        log.info("saved to %s", args.out)


def main():
    p = argparse.ArgumentParser(description="EKMI granite pipeline runner")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="show DB state")
    s.set_defaults(fn=cmd_status)

    h = sub.add_parser("health", help="check DB + vault + DeepSeek")
    h.set_defaults(fn=cmd_health)

    e = sub.add_parser("extract", help="extract stone items from BOQ text via DeepSeek")
    e.add_argument("--text", default="", help="BOQ text inline")
    e.add_argument("--file", default="", help="file containing BOQ text")
    e.add_argument("--model", default=None)
    e.add_argument("--out", default="", help="write JSON output to file")
    e.set_defaults(fn=cmd_extract)

    args = p.parse_args()
    if not getattr(args, "cmd", None):
        p.print_help()
        sys.exit(1)
    validate_config()
    log.info("pipeline run start: %s", args.cmd)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        log.warning("interrupted (Ctrl+C) — state already committed per stage")
        sys.exit(130)
    log.info("pipeline run done")


if __name__ == "__main__":
    main()
