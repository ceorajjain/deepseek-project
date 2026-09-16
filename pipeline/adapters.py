"""Source adapters — one class per website, with domain-specific rules.
Common core stays in the stages; only source-specific logic lives here.
"""
import json
import re
import ssl
import urllib.parse
import urllib.request

import fitz
import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


class SourceAdapter:
    source_id = "base"
    authority_host = ""
    pos = None
    neg = None

    def discover(self):
        raise NotImplementedError

    def extract_docs(self, tender):
        raise NotImplementedError

    def classify(self, title):
        if self.neg and self.neg.search(title):
            return "rejected", "hard_negative"
        if self.pos and self.pos.search(title):
            return "candidate", "positive_signal"
        return "uncertain", "no_signal"

    def is_boq_doc(self, doc):
        l = ((doc.get("label") or "") + " " + (doc.get("url") or "")).lower()
        return any(k in l for k in ("addendum", "boq", "price", "bill"))

    def parse_boq(self, path, label=""):
        raise NotImplementedError

    def tender_number(self, tender):
        return tender.get("tenderRefNo") or tender.get("tenderidnew") or tender.get("tender_number")


class DmrcAdapter(SourceAdapter):
    source_id = "DMRC"
    authority_host = "backend.delhimetrorail.com"
    API = "https://backend.delhimetrorail.com/api/v2/en"
    UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
          "Referer": "https://www.delhimetrorail.com/"}
    neg = re.compile(
        r"cab|taxi|insurance|housekeeping|cleaning|vending|hiring|advertis|licensing|"
        r"co-branding|rate contract|maintenance|signalling|telecom|rolling stock|overhaul|"
        r"pantograph|ballast|rail/crossing|bearing|escalat|lift|hvac|fire|led|bms|battery|"
        r"nitrogen|stp|sewage|horticulture|landscap|tree|borewell|painting|rain gutter|buffer|gear|coupler",
        re.I)
    pos = re.compile(
        r"architectural|finish|floor|cladd|facade|granite|marble|kota|stone|tile|"
        r"station building|building work|civil work|depot|interior|platform|structural|toilet|entry/exit|peb",
        re.I)

    def tender_number(self, tender):
        alltxt = (tender.get("title_in_english") or "") + " " + (tender.get("content_english") or "")
        m = re.search(r"\b(D[2]?C[- ]?\d{2}[A-Z]?)\b", alltxt, re.I)
        return re.sub(r"\s+", "", m.group(1)).upper() if m else None

    def _json(self, url):
        req = urllib.request.Request(url, headers=self.UA)
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return json.loads(r.read())

    def discover(self):
        out = {}
        for cat in range(2, 18):
            for page in range(1, 6):
                try:
                    j = self._json(f"{self.API}/tenders_by_category/{cat}/?page={page}")
                except Exception:
                    break
                if not j.get("results"):
                    break
                for t in j["results"]:
                    if t["id"] not in out:
                        out[t["id"]] = t
        return list(out.values())

    def extract_docs(self, tender):
        ce = tender.get("content_english") or ""
        docs = []
        for m in re.finditer(
            r'href=["\']([^"\']*backend\.delhimetrorail\.com/documents/[^"\']+)["\'][^>]*>(.*?)</a>',
            ce, re.I | re.S):
            label = re.sub(r"<[^>]+>", "", m.group(2))
            label = re.sub(r"\s+", " ", label).strip()
            docs.append({"url": m.group(1), "label": label})
        return docs

    def parse_boq(self, path, label=""):
        # DMRC addendum BOQ: unit line then quantity line (not qty then unit)
        STONE = re.compile(r"granite|marble|kota|sandstone|slate|quartz|terracotta|sadarahalli|jet black|tan brown|red granite", re.I)
        FALSE = re.compile(r"cement concrete|reinforced|rcc|pcc|steel|fabrication|fastener|"
                           r"cutter|grinder|chips|aggregate|crushed|trap|basalt|terrazzo|"
                           r"vitrified|ceramic tile|glass|waterproof|signage|handrail|fencing|"
                           r"bidding procedure|corrigendum|revised boq", re.I)
        UNIT_MAP = {"sqm": "SQM", "sq m": "SQM", "sq.m": "SQM", "sqmt": "SQM", "m2": "SQM",
                    "sqft": "SQFT", "sq ft": "SQFT", "sft": "SQFT", "cft": "CFT", "cum": "CUM",
                    "rft": "RFT", "rmt": "RMT", "mtr": "MTR", "mt": "MT", "nos": "NOS", "no": "NOS",
                    "number": "NOS", "each": "NOS", "kg": "KG", "kgs": "KG"}
        NUM = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*$")
        BOUND = re.compile(r"\bINR\b|₹|Zero Only|Only$|Sub Total|Grand Total|SECTION", re.I)
        if not str(path).lower().endswith(".pdf"):
            return []
        d = fitz.open(path)
        text = "\n".join(pg.get_text() for pg in d)
        d.close()
        lines = text.split("\n")
        items, seen = [], set()
        for i, line in enumerate(lines):
            s = line.strip()
            if not s or s.lower().startswith("note") or not STONE.search(s):
                continue
            if FALSE.search(s):
                continue
            unit = unit_j = None
            j = i + 1
            while j < len(lines) and j < i + 40:
                sj = lines[j].strip()
                if not sj:
                    j += 1
                    continue
                if BOUND.search(sj):
                    break
                u = UNIT_MAP.get(sj.lower().rstrip("."))
                if u:
                    unit, unit_j = u, j
                    break
                j += 1
            if unit is None:
                continue
            qty = None
            k = unit_j + 1
            while k < len(lines) and k < unit_j + 6:
                sk = lines[k].strip()
                if not sk:
                    k += 1
                    continue
                if BOUND.search(sk):
                    break
                m = NUM.match(sk)
                if m:
                    qty = float(m.group(1).replace(",", ""))
                    break
                k += 1
            if qty is None:
                continue
            key = (round(qty, 4), unit)
            if key in seen:
                continue
            seen.add(key)
            mat = STONE.search(s)
            items.append({"desc": s[:255], "qty": qty, "unit": unit,
                          "material": mat.group(0).title() if mat else None})
        return items


class RitesAdapter(SourceAdapter):
    source_id = "rites_public_tenders"
    authority_host = "www.rites.com"
    neg = re.compile(r"cab|taxi|insurance|geotech|consultancy|hiring|lease|maintenance|repair|supply of(?!.*granite)|rate contract", re.I)
    pos = re.compile(r"granite|marble|kota|sandstone|slate|quartz|terracotta|sadarahalli|floor|cladd|facade|tile|building|civil|station|finish|hostel|college|quarters|residence|depot|construction", re.I)

    def _post(self, length, cat, start=0):
        data = {"draw": "1", "start": str(start), "length": str(length), "search[value]": "",
                "search[regex]": "false", "order[0][column]": "1", "order[0][dir]": "desc",
                "CatId": cat, "TenderId": "", "AssignedTender": ""}
        req = urllib.request.Request(
            "https://www.rites.com/Home/GetTenderForPublic",
            data=urllib.parse.urlencode(data).encode(),
            headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
                     "Referer": "https://www.rites.com/tender"})
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def discover(self):
        out = {}
        for cat in ("", "3", "4", "5", "6", "7", "11", "12"):
            try:
                j = self._post(200, cat)
            except Exception:
                continue
            for r in j.get("data", []):
                tid = r.get("tenderId")
                if tid and tid not in out:
                    out[tid] = r
        return list(out.values())

    def extract_docs(self, tender):
        docs = []
        for k in ("download", "extradoc", "extradoc1", "extradoc2", "viewFile",
                  "tenderDocumentExtra", "tenderDocument", "tenderDocument1", "tenderDocument2"):
            v = tender.get(k)
            if not v:
                continue
            for m in re.finditer(r'href=["\']([^"\']+)["\']', str(v)):
                u = m.group(1)
                if u.startswith("/"):
                    u = "https://www.rites.com" + u
                docs.append({"url": u, "label": k})
        return docs

    def is_boq_doc(self, doc):
        u = (doc.get("url") or "").lower()
        return "boq" in u or u.endswith(".xls") or u.endswith(".xlsx")

    def parse_boq(self, path, label=""):
        STONE = re.compile(r"granite|marble|kota|sandstone|slate|quartz|terracotta|sadarahalli", re.I)
        FALSE = re.compile(r"cutter|grinder|chips|polishing machine|sheet|"
                           r"cement concrete|aggregate|crushed|trap|basalt", re.I)
        if str(path).lower().endswith((".xls", ".xlsx")):
            items = []
            try:
                dfs = pd.read_excel(path, sheet_name=None, header=None)
            except Exception:
                return items
            for sheet, df in dfs.items():
                for _, row in df.iterrows():
                    cells = [str(x) for x in row if pd.notna(x)]
                    row_text = " ".join(cells).lower()
                    if not (STONE.search(row_text) and not FALSE.search(row_text)):
                        continue
                    desc = cells[1] if len(cells) > 1 else row_text
                    # RITES XLS: col3 = quantity, col4 = unit (NOT first number = Sl.No)
                    if len(cells) < 5:
                        continue
                    qty_c, unit_c = cells[3], cells[4]
                    m = re.search(r"(\d+(?:\.\d+)?)", qty_c)
                    u = re.search(r"(sqm|sqf|sq\.?m|sq\.?ft|cft|cum|rft|nos|kg|rmt|mtr)", unit_c, re.I)
                    if not m or not u:
                        continue
                    unit = u.group(1).upper()
                    if unit in ("SQF", "SQ.FT", "SQ FT"):
                        unit = "SQFT"
                    elif unit in ("MTR",):
                        unit = "MTR"
                    mat = STONE.search(desc)
                    items.append({"desc": desc[:255], "qty": float(m.group(1)), "unit": unit,
                                  "material": mat.group(0).title() if mat else None})
            return items
        # RITES PDF: qty/unit line after description
        return []


class CpppAdapter(SourceAdapter):
    source_id = "CPPP"
    authority_host = "eprocure.gov.in"
    neg = re.compile(r"cab|taxi|insurance|consultancy|hiring|lease|maintenance|repair|"
                     r"supply of(?!.*granite)|rate contract|software|printer|stationery|vehicle", re.I)
    pos = re.compile(r"granite|marble|kota|sandstone|slate|quartz|terracotta|floor|cladd|facade|"
                     r"tile|building|civil|station|finish|hostel|college|quarters|residence|depot|"
                     r"construction|renovation|redevelopment|upgradation", re.I)

    def discover(self):
        req = urllib.request.Request("https://eprocure.gov.in/cppp/latestactivetendersnew",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            html = r.read().decode("utf-8", "replace")
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S)
        out = {}
        for row in rows:
            m = re.search(r'href=["\'](https://eprocure\.gov\.in/cppp/tendersfullview/[^"\']+)["\'][^>]*>(.*?)</a>', row, re.I | re.S)
            if not m:
                continue
            url, title = m.group(1), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
            tid = url.rstrip("=").split("==")[-1][-12:] or url[-40:]
            out[tid] = {"id": tid, "title": title, "url": url, "org": "CPPP"}
        return list(out.values())

    def extract_docs(self, tender):
        return [{"url": tender.get("url"), "label": "tender_detail"}]

    def is_boq_doc(self, doc):
        return False  # BOQ is behind captcha on the tendersfullview page

    def tender_number(self, tender):
        m = re.search(r"/([\w./-]+?)/\d{4,6}\s*$", tender.get("title") or "")
        return m.group(1) if m else None

    def parse_boq(self, path, label=""):
        return []


class CmrlAdapter(SourceAdapter):
    source_id = "CMRL"
    authority_host = "chennaimetrorail.org"
    neg = re.compile(r"feasibility|consultancy|dpr|detailed project report|cable propelled|"
                     r"high-altitude|feasibility study|market assessment|signage|rolling stock|"
                     r"signalling|telecom|track|vht|ps & ohe|afc", re.I)
    pos = re.compile(r"construction|building|property development|entry/exit|civil|structural|"
                     r"architecture|facade|design and construction|high rise|station|grade separator|"
                     r"bridge|demolition|re-construction", re.I)
    PAGES = ["https://chennaimetrorail.org/nit-project/civil/?post_type=nit",
             "https://chennaimetrorail.org/nit-project/business-development/?post_type=nit"]

    def discover(self):
        out = {}
        for page in self.PAGES:
            try:
                req = urllib.request.Request(page, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
                    html = r.read().decode("utf-8", "replace")
            except Exception:
                continue
            for m in re.finditer(r'href=["\']([^"\']+\.pdf[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
                href = m.group(1)
                label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
                # keep only NIT/tender PDFs (title has CMRL/CP/TDR/NIT/CON, exclude maps/parking/policies)
                if not re.search(r"cmrl|tdr|nit|con/|construction|property development|entry/exit|"
                                 r"grade separator|bridge|demolition|building", label, re.I):
                    continue
                if re.search(r"line map|route map|parking|ridership|policy|brochure|newsletter|"
                             r"carriage|bicycle", label, re.I):
                    continue
                if not href.startswith("http"):
                    href = "https://chennaimetrorail.org" + href
                tid = re.search(r"(?:CMRL|CMAML)/[\w/.-]+", label)
                key = tid.group(0) if tid else href[-40:]
                out[key] = {"id": key, "title": label[:300], "url": href, "org": "CMRL"}
        return list(out.values())

    def extract_docs(self, tender):
        return [{"url": tender.get("url"), "label": tender.get("title") or "NIT"}]

    def is_boq_doc(self, doc):
        return True  # NIT PDF is the tender doc (BOQ is on CPP portal, captcha)

    def tender_number(self, tender):
        m = re.search(r"(?:CMRL|CMAML)/[\w/.-]+", tender.get("title") or "")
        return m.group(0) if m else None

    def parse_boq(self, path, label=""):
        return []  # NIT has no BOQ; stone items are on CPP portal (captcha-blocked)


class PuneMetroAdapter(SourceAdapter):
    """Pune Metro Rail Project (Maha-Metro) — punemetrorail.org/tenders.

    Single HTML table lists every tender (active + awarded). For awarded contracts the
    "Submission Date" column is replaced by the awarded contractor name. So the winner is
    discovered together with the tender — no separate award list needed.
    """
    source_id = "pune_metro"
    authority_host = "punemetrorail.org"
    TENDERS_URL = "https://punemetrorail.org/tenders"
    neg = re.compile(
        r"track|signalling|telecom|rolling stock|hvac|escalat|lift|signage|advertis|"
        r"licensing|co-branding|housekeeping|maintenance|o&m|consultancy|land acquisition|"
        r"tree|noise|vibration|insurance|vehicle|taxi|travel|manpower|security|it infra|"
        r"optical fibre|in-building|kiosk|retail|train wrapping|empanelment|proof checking|"
        r"feeder bus|canteen|display",
        re.I)
    pos = re.compile(
        r"station|architectural|finish|civil|structural|building|depot|underground|"
        r"viaduct|facade|cladding|floor|plumbing|site development|corridor|elevated",
        re.I)

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return r.read().decode("utf-8", "replace")

    def discover(self):
        from bs4 import BeautifulSoup
        html = self._get(self.TENDERS_URL)
        soup = BeautifulSoup(html, "lxml")
        tenders = {}
        order = []
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if not cells:
                continue
            code = cells[0]
            m = re.match(r"^(P[12][A-Za-z0-9&/\-]+/\d{4}|N1[A-Za-z0-9&/\-]+/\d{4}|ICB-P1[^\s]+)", code)
            if not m:
                continue
            key = m.group(1)
            hrefs = []
            for a in tr.find_all("a", href=True):
                h = a["href"]
                if h.startswith("/"):
                    h = "https://punemetrorail.org" + h
                hrefs.append({"url": h, "label": a.get_text(" ", strip=True)[:40]})
            if key not in tenders:
                tenders[key] = {
                    "id": key,
                    "title": cells[1] if len(cells) > 1 else "",
                    "sub": cells[2] if len(cells) > 2 else "",
                    "opn": cells[3] if len(cells) > 3 else "",
                    "docs": hrefs,
                }
                order.append(key)
            else:
                for h in hrefs:
                    if h not in tenders[key]["docs"]:
                        tenders[key]["docs"].append(h)
        return [tenders[k] for k in order]

    def tender_number(self, tender):
        return tender.get("id")

    def classify(self, title):
        # granite-selling target = station/depot CONSTRUCTION (flooring/cladding) or
        # architectural finishing works. Viaduct/corridor/tunnel are structural — reject.
        t = (title or "").lower()
        structural = re.search(r"shaft|tunnelling|tunneling|utility|drain|road|demolition", t)
        station = ("station" in t and ("construction" in t or "design and construction" in t)
                   and not structural)
        depot = "depot" in t and "construction" in t
        arch = "architectural" in t and ("work" in t or "finish" in t or "plumbing" in t)
        if station or depot or arch:
            return "candidate", "station/depot/architectural construction"
        return "rejected", "structural/systems/consultancy (not granite supply)"

    def winner_name(self, tender):
        sub = tender.get("sub") or ""
        if not re.search(r"M/s|JV|Consortium|Pvt|Ltd|Private|Limited|Infra|Construction|Joint Venture", sub, re.I):
            return None
        if re.search(r"Technical Bid Evaluation|Financial|Discharged|Notice", sub, re.I):
            return None
        return re.sub(r"\s+", " ", sub).strip()

    def extract_docs(self, tender):
        return tender.get("docs") or []

    def is_boq_doc(self, doc):
        l = ((doc.get("label") or "") + " " + (doc.get("url") or "")).lower()
        return any(k in l for k in ("nit", "loa", "awarded", "zip", "rar", "boq", "tender"))

    def parse_boq(self, path, label=""):
        return []  # BOQ is inside ZIP/RAR archives or LOA; handled separately


SOURCES = [DmrcAdapter(), RitesAdapter(), CpppAdapter(), CmrlAdapter()]
