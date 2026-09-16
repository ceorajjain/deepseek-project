"""DeepSeek-based natural-stone BOQ extraction + batch judge (fixes regex false positives)."""

import json

from .deepseek_client import chat_json
from .logger import get_logger

log = get_logger("extract_stone")

SYSTEM = (
    "You are a natural-stone BOQ item extractor for EKMI, a granite/marble supplier. "
    "From the given BOQ text, extract ONLY rows where the contractor must "
    "SUPPLY/PROCURE/INSTALL natural stone material (granite, marble, kota, sandstone, "
    "slate, quartz, terracotta, sadarahalli). "
    "A row is NOT a stone item if: "
    "(a) stone is only mentioned incidentally (e.g. 'drilling a hole in granite tile', "
    "'SS railing to match granite floor', 'MS frame along with granite'); "
    "(b) the material is steel/SS/concrete/cement/wood/glass; "
    "(c) it is a general clause or instruction (e.g. 'contractor shall use own workshop for granite cutting'). "
    "Return ONLY a JSON array. Each element must be: "
    "{\"description\": string, \"material\": string|null, \"quantity\": number|null, "
    "\"unit\": string|null, \"is_stone\": true, \"confidence\": number (0..1), \"reason\": string}. "
    "If no stone supply items exist, return []."
)


def extract(text, model=None):
    """Extract stone items from a BOQ text chunk via DeepSeek."""
    result = chat_json(SYSTEM, text, model=model, temperature=0.0)
    if not isinstance(result, list):
        log.warning("deepseek returned non-list: %r", result)
        return []
    items = [r for r in result if isinstance(r, dict) and r.get("is_stone")]
    log.info("extracted %d stone items from %d chars", len(items), len(text))
    return items


JUDGE_SYSTEM = (
    "You classify natural-stone BOQ items for EKMI (a granite/marble supplier). "
    "You are given a JSON array of candidate items. Return a JSON array of booleans "
    "(same order, same length) where true = the item is a REAL granite-supply item "
    "(the contractor must SUPPLY/PROCURE/INSTALL granite/marble/kota/sandstone/slate/quartz/"
    "terracotta/sadarahalli). "
    "false = stone only mentioned incidentally (e.g. drilling a hole in granite tile, "
    "MS frame along with granite, SS railing to match granite floor) OR the item is "
    "steel/concrete/cement/wood/glass OR a general clause/instruction. "
    "Return ONLY the JSON array of booleans, nothing else."
)


def judge(items, model=None):
    """Judge a batch of candidate items in ONE DeepSeek call (token-optimized).
    Returns a list of booleans aligned to `items`. Falls back to keep-all on error."""
    if not items:
        return []
    payload = json.dumps([
        {"desc": (it.get("desc") or it.get("description") or "")[:200],
         "qty": it.get("qty", it.get("quantity")),
         "unit": it.get("unit")}
        for it in items
    ], ensure_ascii=False)
    try:
        result = chat_json(JUDGE_SYSTEM, payload, model=model, temperature=0.0)
        if isinstance(result, list) and len(result) == len(items):
            return [bool(x) for x in result]
        log.warning("judge returned wrong shape: %r", result)
    except Exception as e:
        log.warning("judge failed: %s", e)
    return [True] * len(items)  # safe fallback: keep all (never silently drop data)
