"""Minimális .env betöltő (külső csomag nélkül). A már beállított környezeti változókat nem írja felül."""
import os
from pathlib import Path


def load_env(path=None):
    p = Path(path) if path else Path(__file__).with_name(".env")
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v
