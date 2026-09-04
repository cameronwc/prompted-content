"""Load the hand-editable Pinterest configs from config/."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from common import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "config"
STATE_DIR = REPO_ROOT / "state"
PINS_DIR = REPO_ROOT / "dist" / "pins"
MANIFEST_PATH = STATE_DIR / "pinterest_manifest.json"

FILES = {
    "exclusions": "pinterest_exclusions.yaml",
    "cohorts": "pinterest_cohorts.yaml",
    "boards": "pinterest_boards.yaml",
    "links": "pinterest_links.yaml",
    "csv": "pinterest_csv_schema.yaml",
    "copy": "pinterest_copy.yaml",
    "seasons": "pinterest_seasons.yaml",
}


class ConfigError(SystemExit):
    def __init__(self, message: str):
        super().__init__(f"error: {message}")


def _load(name: str, config_dir: Path) -> dict:
    path = config_dir / FILES[name]
    if not path.is_file():
        raise ConfigError(f"missing config file {path.relative_to(REPO_ROOT)}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping")
    return data


def load_all(config_dir: Path = CONFIG_DIR) -> dict:
    cfg = {name: _load(name, config_dir) for name in FILES}
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    ex = cfg["exclusions"]
    for key in ("filename_patterns", "excluded_shoots", "excluded_pose_ids"):
        if not isinstance(ex.get(key, []), list):
            raise ConfigError(f"pinterest_exclusions.yaml: {key} must be a list")
    ex.setdefault("filename_patterns", ["ACTNATURALLY_PHOTOS-*"])
    ex.setdefault("excluded_shoots", [])
    ex.setdefault("excluded_pose_ids", [])
    ex.setdefault("apply_to_text_pins", True)

    co = cfg["cohorts"]
    cohorts = co.get("cohorts") or {}
    if set(cohorts) != {"text", "photo_real", "photo_ai"}:
        raise ConfigError("pinterest_cohorts.yaml must define exactly the cohorts "
                          "text, photo_real, photo_ai")
    total = sum(float(c.get("share", 0)) for c in cohorts.values())
    if abs(total - 1.0) > 0.01:
        raise ConfigError(f"pinterest_cohorts.yaml: cohort shares sum to {total:.2f}, not 1.0")
    for name, c in cohorts.items():
        if not c.get("utm_campaign"):
            raise ConfigError(f"cohort {name} has no utm_campaign")
    if "ai_disclosure" not in co:
        raise ConfigError("pinterest_cohorts.yaml: ai_disclosure key is missing")
    co.setdefault("share_tolerance", 0.10)
    co.setdefault("utm", {"source": "pinterest", "medium": "pin"})

    csv_cfg = cfg["csv"]
    cols = csv_cfg.get("columns")
    if not cols or not all(isinstance(c, dict) and c.get("name") and c.get("field")
                           for c in cols):
        raise ConfigError("pinterest_csv_schema.yaml: columns must be a list of {name, field}")
    csv_cfg.setdefault("published_at_format", "%Y-%m-%dT%H:%M:%S")
    csv_cfg.setdefault("batch_size", 100)
    csv_cfg.setdefault("batch_filename", "pins_batch_{n:03d}.csv")
    media = csv_cfg.setdefault("media", {})
    media["public_base_url"] = os.environ.get(
        "PROMPTED_PINS_PUBLIC_BASE_URL",
        media.get("public_base_url", "https://content.cooperindustries.cc"),
    ).rstrip("/")
    media.setdefault("prefix", "pins")

    for name in ("boards", "links"):
        rules = cfg[name].get("rules") or []
        if not isinstance(rules, list):
            raise ConfigError(f"pinterest_{name}.yaml: rules must be a list")
    if not cfg["boards"].get("default"):
        raise ConfigError("pinterest_boards.yaml: default board is required")
    tp = cfg["boards"].get("text_pins")
    if isinstance(tp, str):  # legacy: a single board name
        tp = {"secondary_board": tp, "secondary_share": 1.0}
    tp = tp or {}
    tp.setdefault("secondary_board", cfg["boards"]["default"])
    tp["secondary_share"] = float(tp.get("secondary_share", 0.25))
    if not 0 <= tp["secondary_share"] <= 1:
        raise ConfigError("pinterest_boards.yaml: text_pins.secondary_share must be 0..1")
    cfg["boards"]["text_pins"] = tp
    copy_cfg = cfg["copy"]
    if len(copy_cfg.get("text_middle_clauses") or []) < 8:
        raise ConfigError("pinterest_copy.yaml: text_middle_clauses needs at least 8 variants")
    if not copy_cfg.get("rules"):
        raise ConfigError("pinterest_copy.yaml: rules are required")
    se = cfg["seasons"]
    se.setdefault("windows", {})
    se["windows"].setdefault("none", None)
    se.setdefault("lookahead_days", 45)
    se.setdefault("threshold", 3)
    se.setdefault("keywords", {})
    se.setdefault("overrides", {})
    for pid, season in se["overrides"].items():
        if season not in se["windows"]:
            raise ConfigError(f"pinterest_seasons.yaml: override {pid} -> unknown season {season}")
    if not cfg["links"].get("fallback"):
        raise ConfigError("pinterest_links.yaml: fallback URL is required")


def require_disclosure(cfg: dict) -> str:
    """The AI disclosure line; a missing/empty value aborts the run."""
    text = cfg["cohorts"].get("ai_disclosure")
    if not isinstance(text, str) or not text.strip():
        print("error: pinterest_cohorts.yaml ai_disclosure is empty or missing while "
              "photo_ai pins are being generated. The disclosure cannot be disabled.",
              file=sys.stderr)
        raise SystemExit(2)
    return text.strip()
