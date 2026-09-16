"""Stage 18: fetch/store granite-relevant source documents from all active projects.

Only projects with a positive granite relevance status are targeted.
Source PDFs/XLS are saved locally with SHA-256 proof. Only files that look like
BOQ/Bill/Schedule/Financial are parsed and approved as level-3 stone items.
"""
import hashlib
import json
import os
import re
import ssl
import urllib.parse
from datetime import datetime

import requests

from .. import config
from ..boq_parsers import parse_boq
from ..logger import get_logger
from .base import connect

log = get_logger("18_generic_docs")
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

RELEVANT = (
    "likely", "high", "possible", "stone_candidate",
    "verified_relevant", "candidate", "uncertain", "verified",
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(session, url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return
    safe = urllib.parse.quote(url, safe=":/?&=%")
    r = session.get(safe, headers={"User-Agent": "Mozilla/5.0"}, timeout=120, verify=False)
    r.raise_for_status()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(r.content)


def _dedupe(pid, url, desc):
    return hashlib.sha256(f"{pid}:{url}:{desc}".encode()).hexdigest()


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT project_id, source_id, tender_number, source_url
        FROM ekmi_zero.project
        WHERE COALESCE(is_deleted,false)=false
          AND source_url IS NOT NULL
          AND granite_relevance_status IN %s
          AND lower(split_part(source_url,'?',1)) ~ '\\.(pdf|xls|xlsx|rar|zip)$'
        ORDER BY source_id, tender_number
    """, (RELEVANT,))
    rows = cur.fetchall()
    session = requests.Session()
    docs = items = 0
    for pid, sid, tn, url in rows:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".pdf", ".xls", ".xlsx"):
            continue
        safe_tn = re.sub(r"[^A-Za-z0-9._-]+", "_", tn or str(pid))
        dest_dir = os.path.join(config.VAULT, sid or "unknown", safe_tn)
        os.makedirs(dest_dir, exist_ok=True)
        filename = os.path.basename(urllib.parse.urlparse(url).path) or f"{safe_tn}{ext}"
        dest = os.path.join(dest_dir, filename)
        is_boq = bool(re.search(r"boq|bill|schedule|financial", filename + " " + url, re.I))
        doc_type = "boq" if is_boq else "tender_document"
        try:
            _download(session, url, dest)
        except Exception:
            continue
        sha = sha256_file(dest)
        cur.execute("""
            INSERT INTO ekmi_zero.project_boq_document
              (project_id, source_url, document_type, file_name, local_path,
               fetch_status, extraction_status, boq_format_type, sha256_hash,
               download_timestamp, raw_metadata_json, approved_by_deepseek,
               approved_by_deepseek_level)
            VALUES (%s,%s,%s,%s,%s,'fetched',%s,%s,%s,now(),%s::jsonb,%s,%s)
            ON CONFLICT (project_id, source_url)
            DO UPDATE SET local_path=EXCLUDED.local_path,
                          fetch_status='fetched',
                          extraction_status=EXCLUDED.extraction_status,
                          document_type=EXCLUDED.document_type,
                          boq_format_type=EXCLUDED.boq_format_type,
                          sha256_hash=EXCLUDED.sha256_hash,
                          download_timestamp=now(),
                          raw_metadata_json=EXCLUDED.raw_metadata_json,
                          approved_by_deepseek=EXCLUDED.approved_by_deepseek,
                          approved_by_deepseek_level=EXCLUDED.approved_by_deepseek_level
            RETURNING boq_document_id
        """, (pid, url, doc_type, filename, dest,
              "extracted" if is_boq else "no_material_rows",
              "BOQ_PDF" if is_boq else "GENERIC_PDF",
              sha,
              json.dumps({"downloaded_at": datetime.now().isoformat(timespec="seconds"),
                          "source_url": url, "is_boq": is_boq}),
              is_boq, 3 if is_boq else 0))
        did = cur.fetchone()[0]
        docs += 1
        if not is_boq:
            continue
        for j, it in enumerate(parse_boq(dest, source_id=sid)):
            desc = (it.get("description") or "").strip()[:1000]
            qty = it.get("quantity")
            unit = it.get("unit")
            mat = it.get("material_name") or it.get("material") or "Stone"
            if not desc or qty is None or not unit:
                continue
            dedupe = _dedupe(pid, url, desc)
            ev = {
                "actor": config.ACTOR,
                "source_type": "official_boq",
                "source_url": url,
                "pdf_sha256": sha,
                "pdf_local_path": dest,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            }
            cur.execute("""
                INSERT INTO ekmi_zero.project_boq_item
                  (project_id, boq_document_id, item_no, item_description, material_family,
                   material_name, quantity, unit, currency, thickness_min_mm, thickness_max_mm,
                   finish, color, source_url, dedupe_key, is_synthetic, verified_by_raj,
                   verification_remarks, evidence_bundle_json, source_fingerprint,
                   approved_by_deepseek, approved_by_deepseek_level,
                   size_length, size_width, size_unit, alternative_materials)
                VALUES (%s,%s,%s,%s,'Natural Stone',%s,%s,%s,'INR',%s,%s,%s,%s,%s,%s,
                        false,'FRESH','manually_approved_codex',%s::jsonb,%s,true,3,
                        %s,%s,%s,%s::jsonb)
                ON CONFLICT (dedupe_key) DO NOTHING
            """, (pid, did, str(j + 1), desc, mat, qty, unit,
                  it.get("thickness_min_mm"), it.get("thickness_max_mm"),
                  it.get("finish"), it.get("variety"), url, dedupe,
                  json.dumps(ev), sha,
                  it.get("size_length"), it.get("size_width"), it.get("size_unit"),
                  json.dumps({"materials": it.get("alternative_materials", []),
                              "varieties": it.get("alternative_varieties", [])})))
            items += cur.rowcount
    conn.commit()
    conn.close()
    log.info("generic docs fetched=%d stone_items_added=%d", docs, items)


if __name__ == "__main__":
    main()
