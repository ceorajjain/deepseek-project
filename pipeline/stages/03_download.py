"""Stage 3: download BOQ/addendum docs for candidate projects (parallel, vault + evidence)."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config
from ..adapters import SOURCES
from ..http import get as http_get
from ..logger import get_logger
from .base import connect

log = get_logger("03_download")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def media_type(url):
    p = url.lower()
    if ".pdf" in p:
        return "application/pdf"
    if ".xlsx" in p:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ".xls" in p:
        return "application/vnd.ms-excel"
    return "application/octet-stream"


def download_one(doc, adapter):
    url = doc["url"]
    try:
        data = http_get(url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        return {"url": url, "error": str(e)}
    h = sha256(data)
    mtype = media_type(url)
    ext = os.path.splitext(url.split("?")[0])[1] or (".pdf" if mtype == "application/pdf" else ".bin")
    path = os.path.join(config.VAULT, h + ext)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return {"url": url, "label": doc.get("label"), "sha256": h, "local_path": path,
            "media_type": mtype, "size": len(data)}


def main():
    conn = connect()
    cur = conn.cursor()
    for adapter in SOURCES:
        src = adapter.source_id
        cur.execute("""
            SELECT project_id, source_id, source_project_id, source_project_identity_json->'docs' AS docs
            FROM ekmi_zero.project
            WHERE granite_relevance_status='candidate' AND status='classified'
              AND coalesce(is_deleted,false)=false AND source_id=%s
            ORDER BY project_id
            FOR UPDATE SKIP LOCKED
        """, (src,))
        rows = cur.fetchall()
        if not rows:
            continue
        jobs = []
        already = set()
        cur.execute("SELECT DISTINCT source_url FROM ekmi_zero.project_boq_document WHERE local_path IS NOT NULL AND local_path <> ''")
        for r in cur.fetchall():
            already.add(r[0])
        for pid, s, spid, docs in rows:
            for d in (docs or []):
                if isinstance(d, dict) and d.get("url") and d.get("url") not in already and adapter.is_boq_doc(d):
                    jobs.append((pid, s, spid, d))
        log.info("%s: %d candidate projects, %d docs to download", src, len(rows), len(jobs))
        downloaded = failed = 0
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
            futures = {ex.submit(download_one, j[3], adapter): j for j in jobs}
            for fut in as_completed(futures):
                pid, s, spid, doc = futures[fut]
                r = fut.result()
                if not r or "error" in r:
                    failed += 1
                    if r:
                        log.warning("download fail proj=%s %s: %s", pid, doc.get("url", "")[:60], r.get("error"))
                    continue
                cur.execute("""
                    INSERT INTO ekmi_zero.source_evidence_asset
                      (sha256, source_url, local_path, file_name, media_type, size_bytes, authority_host,
                       retrieval_status, provenance_status, raw_metadata_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'captured','official_source_captured',
                            jsonb_build_object('actor', %s, 'source_id', %s, 'source_project_id', %s, 'doc_type', %s))
                    ON CONFLICT (sha256) DO NOTHING
                """, (r["sha256"], r["url"], r["local_path"], os.path.basename(r["url"]), r["media_type"],
                      r["size"], adapter.authority_host, config.ACTOR, s, spid, r["label"]))
                url_hash = sha256(r["url"].encode())
                cur.execute("""
                    INSERT INTO ekmi_zero.project_boq_document
                      (project_id, source_url, document_type, file_name, content_hash, local_path, fetch_status,
                       sha256_hash, source_url_hash, document_role, raw_metadata_json, approved_by_deepseek)
                    VALUES (%s,%s,'boq',%s,%s,%s,'fetched',%s,%s,'boq',
                            jsonb_build_object('actor', %s, 'label', %s), false)
                    ON CONFLICT (project_id, source_url) DO NOTHING
                """, (pid, r["url"], os.path.basename(r["url"]), r["sha256"], r["local_path"],
                      r["sha256"], url_hash, config.ACTOR, r["label"]))
                downloaded += 1
        cur.execute("""
            UPDATE ekmi_zero.project p
            SET status='downloaded'
            WHERE granite_relevance_status='candidate' AND source_id=%s AND status='classified'
              AND EXISTS (SELECT 1 FROM ekmi_zero.project_boq_document d WHERE d.project_id=p.project_id)
        """, (src,))
        log.info("%s download done: downloaded=%d failed=%d", src, downloaded, failed)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
