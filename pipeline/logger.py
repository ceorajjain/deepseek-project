import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import config

for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name="ekmi"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = RotatingFileHandler(os.path.join(config.LOGS, "ekmi_pipeline.log"),
                                 maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FMT))
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger
