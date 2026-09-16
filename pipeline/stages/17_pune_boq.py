"""Stage 17: Pune Metro granite/stone BOQ extraction from text-based ZIP BOQ archives.

Pune Metro station contracts publish a standard "Station Architectural Finishing" BOQ
(flamed platform-edge granite, prepolished flooring, mirror-polished risers/cladding,
coping, Jet Black counters, Kota stone) inside ZIP archives. These BOQ PDFs are TEXT-based
(no OCR). This stage downloads the ZIP, extracts BOQ PDFs, parses stone items (description
+ qty + unit + thickness/finish/color) and stores them idempotently.
"""
import os
import re
import ssl
import urllib.request
import zipfile

from .. import config
from ..boq_granite import parse_text_boq, store_items
from ..logger import get_logger
from .base import connect

log = get_logger("17_pune_boq")

BASE = "https://punemetrorail.org"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# tender_number -> ZIP doc path (text-based BOQ; RAR archives need an external tool)
ZIP_DOCS = {
    "P1C-06/2018": "/download/P1C-06_2018.zip",
    "P1UGC-02/2018": "/download/P1UGC-02_2018.zip",
    "P1C-07/2018": "/download/P1C-07_2018.zip",
}


def _download(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                "Referer": BASE + "/tenders"})
    with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
        data = r.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main():
    conn = connect()
    cur = conn.cursor()
    total = 0
    for tn, rel in ZIP_DOCS.items():
        cur.execute("SELECT project_id FROM ekmi_zero.project "
                    "WHERE source_id='pune_metro' AND tender_number=%s AND is_deleted=false LIMIT 1", (tn,))
        r = cur.fetchone()
        if not r:
            log.warning("skip %s: no project", tn)
            continue
        pid = r[0]
        url = BASE + rel
        local = os.path.join(config.STATE, "pune_boq", os.path.basename(rel))
        try:
            path = _download(url, local)
        except Exception as e:
            log.warning("download failed %s: %s", rel, e)
            continue
        z = zipfile.ZipFile(path)
        boq_pdfs = [n for n in z.namelist()
                    if re.search(r"BOQ|Part[1-4]", n, re.I) and n.lower().endswith(".pdf")]
        added = 0
        for n in boq_pdfs:
            out = os.path.join(config.STATE, "pune_boq", tn.replace("/", "_"),
                               os.path.basename(n))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if not os.path.exists(out):
                with open(out, "wb") as f:
                    f.write(z.read(n))
            items = parse_text_boq(out, min_qty=50)
            # keep only the last/top BOQ revision (Corrigendum II) to avoid dup qty
            added += store_items(cur, pid, url, items, doc_type="boq", local_path=out,
                                 file_name=os.path.basename(n),
                                 extra_evidence={"zip": rel, "pdf": os.path.basename(n)})
        conn.commit()
        total += added
        log.info("%s: stored %d stone items", tn, added)
    conn.close()
    log.info("Pune BOQ done: total items stored=%d", total)


if __name__ == "__main__":
    main()
