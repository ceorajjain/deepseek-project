"""Stage 12: ensure every proof document is saved locally (vault) + DB references the sha256.
Downloads missing evidence, fixes stale local_path, and stamps winner/role evidence_bundle_json."""
import hashlib
import json
import os
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("12_proof_sync")
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

_DEAD_HOSTS = set()
_SKIP_SECONDS = 24 * 3600


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def ensure_vault(sha, url):
    """Ensure the document exists in the vault; download from url if missing. Returns local_path."""
    ext = ".pdf" if ".pdf" in (url or "") else ".bin"
    path = os.path.join(config.VAULT, sha + ext)
    if os.path.isfile(path):
        return path
    # try alternate ext
    for e in (".pdf", ".xls", ".xlsx"):
        p2 = os.path.join(config.VAULT, sha + e)
        if os.path.isfile(p2):
            return p2
    # download
    if url:
        host = urlparse(url).netloc
        if host in _DEAD_HOSTS:
            return None
        last_err = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                    data = r.read()
                if sha256(data) == sha:
                    with open(path, "wb") as f:
                        f.write(data)
                    return path
                last_err = "sha mismatch"
                break
            except Exception as e:
                last_err = e
                code = getattr(e, "code", None)
                if code in (429, 500, 502, 503, 504):
                    time.sleep(1 + attempt)
                    continue
                break
        if last_err is not None:
            _DEAD_HOSTS.add(host)
            log.warning("proof download failed host=%s url=%s error=%s", host, url[:60], last_err)
    return None


def main():
    conn = connect()
    cur = conn.cursor()
    # 1) fix source_evidence_asset local_path (missing file -> download/save)
    cur.execute("""
        SELECT sha256, source_url, local_path, raw_metadata_json
        FROM ekmi_zero.source_evidence_asset
        WHERE local_path IS NOT NULL AND local_path <> ''
    """)
    fixed = 0
    for sha, url, lp, meta in cur.fetchall():
        if lp and os.path.isfile(lp):
            continue
        meta = meta or {}
        last_fail = meta.get("proof_last_failed_at")
        if last_fail:
            try:
                last_dt = datetime.fromisoformat(last_fail)
                if datetime.now(timezone.utc) - last_dt < timedelta(seconds=_SKIP_SECONDS):
                    continue
            except Exception:
                pass
        p = ensure_vault(sha, url)
        if p:
            cur.execute("UPDATE ekmi_zero.source_evidence_asset SET local_path=%s WHERE sha256=%s", (p, sha))
            fixed += 1
        else:
            meta["proof_last_failed_at"] = datetime.now(timezone.utc).isoformat()
            cur.execute("""
                UPDATE ekmi_zero.source_evidence_asset
                SET raw_metadata_json = COALESCE(raw_metadata_json, '{}'::jsonb) || %s::jsonb,
                    retrieval_status='failed'
                WHERE sha256=%s
            """, (json.dumps(meta), sha))
    log.info("proof_sync: fixed %d evidence local_path", fixed)

    # 2) stamp winners/roles evidence_bundle_json with sha256 + local_path
    cur.execute("""
        SELECT r.role_id, r.source_url
        FROM ekmi_zero.project_company_role r
        WHERE r.is_winner=1 AND r.evidence_status='VERIFIED_SOURCE_EVIDENCE'
          AND (r.evidence_bundle_json IS NULL OR r.evidence_bundle_json->>'sha256' IS NULL)
    """)
    stamped = 0
    for role_id, url in cur.fetchall():
        if not url:
            continue
        # find the evidence asset for this url
        cur.execute("SELECT sha256, local_path FROM ekmi_zero.source_evidence_asset WHERE source_url=%s LIMIT 1", (url,))
        r = cur.fetchone()
        if not r:
            continue
        sha, lp = r
        cur.execute("""
            UPDATE ekmi_zero.project_company_role
            SET evidence_bundle_json = COALESCE(evidence_bundle_json, '{}'::jsonb)
                    || jsonb_build_object('sha256', %s, 'local_path', %s)
            WHERE role_id=%s
        """, (sha, lp, role_id))
        stamped += 1
    log.info("proof_sync: stamped %d winner roles with sha256", stamped)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
