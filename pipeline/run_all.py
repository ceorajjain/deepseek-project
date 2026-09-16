"""Run the full staged pipeline in order (each stage resumable, idempotent)."""
import sys
import time
import importlib

from .logger import get_logger

log = get_logger("run_all")

STAGE_MODULES = ["01_discover", "02_classify", "03_download", "04_extract",
                 "05_verify", "06_winners", "08_normalize", "07_export", "09_contacts",
                 "10_report", "11_cmrl_meta", "12_proof_sync", "13_pune_metro", "14_mp_metro",
                 "15_gujarat_metro", "16_ncrtc", "17_pune_boq", "18_generic_docs",
                 "19_winner_sync"]


def main():
    started = time.time()
    log.info("=== run_all start ===")
    failed = []
    for name in STAGE_MODULES:
        log.info("--- stage %s ---", name)
        ok = False
        for attempt in range(2):
            try:
                mod = importlib.import_module(f"pipeline.stages.{name}")
                mod.main()
                ok = True
                break
            except Exception as e:
                log.error("stage %s attempt %d FAILED: %s", name, attempt + 1, e)
                time.sleep(3)
        if not ok:
            failed.append(name)
    if failed:
        log.info("run_all finished with errors in stages: %s", ", ".join(failed))
    else:
        log.info("=== run_all done in %.1fs ===", time.time() - started)


if __name__ == "__main__":
    main()
