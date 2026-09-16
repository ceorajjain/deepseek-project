"""Silent RAR handling via Python + 7-Zip console (no GUI window).

RAR is a proprietary format; there is no safe pure-Python RAR decompressor.
We use the 7-Zip console binary and Python ``rarfile``, both invoked through
``subprocess`` with ``CREATE_NO_WINDOW`` so nothing pops up on Windows.
"""
import os
import subprocess

import rarfile

from . import config

SEVEN_ZIP = os.path.join(config.ROOT, "work", "7zip", "7z.exe")
rarfile.SEVENZIP_TOOL = SEVEN_ZIP


def _no_window_kwargs():
    # Windows only: keep console subprocess hidden.
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def list_rar(path):
    """Return a list of member names inside a RAR archive."""
    rf = rarfile.RarFile(path)
    try:
        return rf.namelist()
    finally:
        rf.close()


def extract_rar(path, out_dir, members=None):
    """Extract a RAR archive into ``out_dir`` silently."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [SEVEN_ZIP, "x", path, f"-o{out_dir}", "-y"]
    if members:
        cmd += [m for m in members if not m.endswith("/")]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, **_no_window_kwargs())
    # 7z returns 0 on success and 1 for warnings; CRC warnings on one member
    # should not block valid BOQ members from being extracted.
    if proc.returncode not in (0, 1, 2):
        raise RuntimeError(f"7z extract failed rc={proc.returncode}: {proc.stdout[-2000:]}")
    return out_dir


def extract_rar_nested(path, out_dir, max_depth=4):
    """Extract RAR recursively until PDF/XLS files or max depth is reached."""
    os.makedirs(out_dir, exist_ok=True)
    todo = [(path, out_dir, 0)]
    extracted = []
    while todo:
        src, dest, depth = todo.pop(0)
        if depth > max_depth:
            continue
        if src.lower().endswith(".rar"):
            extract_rar(src, dest)
            # queue nested archives, keep documents
            for root, _dirs, files in os.walk(dest):
                for fn in files:
                    fp = os.path.join(root, fn)
                    if fn.lower().endswith(".rar"):
                        sub = os.path.join(os.path.dirname(fp), os.path.splitext(fn)[0])
                        todo.append((fp, sub, depth + 1))
                    elif fn.lower().endswith((".pdf", ".xls", ".xlsx")):
                        extracted.append(fp)
    return extracted
