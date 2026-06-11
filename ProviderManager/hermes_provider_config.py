#!/usr/bin/env python3
"""Safe merge of Hermes provider entries into config.yaml with autobackup."""
from __future__ import annotations

import shutil
import hashlib
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

DEFAULT_CONFIG_CANDIDATES = [
    Path.home() / ".hermes" / "config.yaml",
]

PROVIDER_ENTRIES = {
    "custom:freeqwen": {
        "base_url": "http://127.0.0.1:3264/api/v2",
        "api_key": "dummy",
        "models": {
            "qwen3.7-max": {},
            "qwen3-coder-plus": {},
        },
    },
    "custom:freedeep": {
        "base_url": "http://127.0.0.1:9655/v1",
        "api_key": "dummy",
        "models": {
            "deepseek-chat": {},
        },
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rotate_backups(backup_dir: Path, keep: int = 5) -> None:
    backups = sorted(backup_dir.glob("config.yaml.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except Exception:
            pass


def _acquire_lock(lock_path: Path, timeout: float = 5.0) -> Optional[Path]:
    """Best-effort file lock via atomic create. Returns lock path or None."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = lock_path.open("x")
            fd.close()
            return lock_path
        except FileExistsError:
            time.sleep(0.1)
        except Exception:
            # On some Windows configs open(x) may not be available; skip
            return None
    return None


def _release_lock(lock_path: Optional[Path]) -> None:
    if lock_path and lock_path.exists():
        try:
            lock_path.unlink()
        except Exception:
            pass


def _load_config(path: Path) -> Dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    # Minimal fallback: very naive YAML subset
    # (if PyYAML is missing, we still want something useful)
    return _naive_yaml_load(text)


def _naive_yaml_load(text: str) -> Dict:
    # Extremely naive top-level key loader. Not a YAML parser.
    # Only used for detecting existing provider keys.
    out: Dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k = line.split(":", 1)[0].strip()
            out[k] = {}
    return out


def _safe_dump(config: Dict) -> str:
    if yaml is not None:
        return yaml.dump(config, allow_unicode=True, sort_keys=False)
    # Fallback: not round-trippable, just enough to keep keys.
    lines = []
    for k, v in config.items():
        lines.append(f"{k}:")
        if isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, dict):
                    lines.append(f"  {kk}:")
                else:
                    lines.append(f"  {kk}: {vv}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def resolve_config_path(preferred: Optional[Path] = None) -> Path:
    if preferred:
        return preferred
    for p in DEFAULT_CONFIG_CANDIDATES:
        if p.exists():
            return p
    # Default to first candidate (will be created)
    return DEFAULT_CONFIG_CANDIDATES[0]


def merge_providers(
    config_path: Optional[Path] = None,
    entries: Optional[Dict[str, Dict]] = None,
    keep: int = 5,
) -> Dict:
    """Merge provider entries into config.yaml with backup.

    Returns {"ok": True/False, "path": str, "backup": str, "added": [str], "error": ""}.
    """
    if entries is None:
        entries = PROVIDER_ENTRIES

    target = resolve_config_path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = target.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    lock_path = backup_dir / f".{target.name}.lock"
    lock = _acquire_lock(lock_path, timeout=5.0)
    if lock is None:
        return {"ok": False, "path": str(target), "backup": "", "added": [], "error": "config_locked"}

    try:
        backup_path = backup_dir / f"{target.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        if target.exists():
            shutil.copy2(target, backup_path)
            _rotate_backups(backup_dir, keep=keep)

        config = _load_config(target)
        added: List[str] = []
        for key, value in entries.items():
            # Do NOT add 'tools:' under provider entries ever.
            if key not in config:
                config[key] = value
                added.append(key)
            else:
                existing = config[key]
                if not isinstance(existing, dict):
                    config[key] = value
                    added.append(key)
                else:
                    # Merge minimal fields without nuking user extras.
                    merged = dict(existing)
                    for sk, sv in value.items():
                        if sk not in merged:
                            merged[sk] = sv
                    config[key] = merged

        tmp_path = target.with_suffix(".yaml.tmp")
        tmp_path.write_text(_safe_dump(config), encoding="utf-8")

        # Atomic replace
        if tmp_path.exists():
            tmp_path.replace(target)

        # Validate: can we read it back?
        reloaded = _load_config(target)
        for key in entries:
            if key not in reloaded:
                raise RuntimeError(f"validation_failed: {key} missing after write")

        return {
            "ok": True,
            "path": str(target),
            "backup": str(backup_path),
            "added": added,
            "error": "",
        }
    except Exception as e:
        # Restore from backup if available
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, target)
            except Exception:
                pass
        return {"ok": False, "path": str(target), "backup": str(backup_path), "added": [], "error": str(e)}
    finally:
        _release_lock(lock)
        for p in [target.with_suffix(".yaml.tmp")]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


def restore_config(from_path: Optional[str] = None, keep: int = 5) -> Dict:
    """Restore config.yaml from a specific backup or the latest."""
    target = resolve_config_path(None)
    backup_dir = target.parent / "backups"
    if not backup_dir.exists():
        return {"ok": False, "error": "no_backups"}

    src: Optional[Path] = None
    if from_path:
        src = Path(from_path)
    else:
        backups = sorted(backup_dir.glob("config.yaml.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            return {"ok": False, "error": "no_backups"}
        src = backups[0]

    if not src.exists():
        return {"ok": False, "error": f"missing: {src}"}

    lock_path = backup_dir / f".{target.name}.lock"
    lock = _acquire_lock(lock_path, timeout=5.0)
    if lock is None:
        return {"ok": False, "error": "config_locked"}

    try:
        shutil.copy2(src, target)
        return {"ok": True, "path": str(target), "from": str(src)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _release_lock(lock)


if __name__ == "__main__":
    res = merge_providers()
    print(res)
