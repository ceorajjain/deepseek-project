"""2captcha client — solve image captchas via the 2captcha API."""
import base64
import json
import time
import urllib.parse
import urllib.request

from .logger import get_logger

log = get_logger("captcha")
CAPTCHA_KEY = "751123ab0cdf12efab8aae6088ffd574"


def _call(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def solve_image(image_bytes, timeout=120):
    b64 = base64.b64encode(image_bytes).decode()
    body = urllib.parse.urlencode({"key": CAPTCHA_KEY, "method": "base64",
                                   "body": b64, "json": 1}).encode()
    res = json.loads(_call("https://2captcha.com/in.php", body))
    if res.get("status") != 1:
        raise RuntimeError("captcha submit failed: %r" % res)
    captcha_id = res["request"]
    log.info("captcha submitted id=%s", captcha_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        res2 = json.loads(_call(
            "https://2captcha.com/res.php?key=%s&action=get&id=%s&json=1" % (CAPTCHA_KEY, captcha_id)))
        if res2.get("status") == 1:
            log.info("captcha solved id=%s", captcha_id)
            return res2["request"]
        if res2.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError("captcha solve failed: %r" % res2)
    raise TimeoutError("captcha solve timeout")
