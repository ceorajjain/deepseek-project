import json
import urllib.request

from . import config
from .logger import get_logger

log = get_logger("deepseek")


def chat(system, user, model=None, temperature=0.0, timeout=120):
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    body = {
        "model": model or config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
        "max_tokens": config.DEEPSEEK_MAX_TOKENS,
    }
    req = urllib.request.Request(
        config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage", {})
    log.info("deepseek ok: %d chars, prompt=%s total=%s tokens",
             len(content), usage.get("prompt_tokens"), usage.get("total_tokens"))
    return content


def chat_json(system, user, model=None, temperature=0.0, timeout=120):
    """Call DeepSeek and parse the reply as JSON (tolerant to ```json fences)."""
    raw = chat(system, user, model=model, temperature=temperature, timeout=timeout)
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    return json.loads(s)
