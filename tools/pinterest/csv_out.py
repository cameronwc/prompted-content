"""Pinterest bulk-upload CSVs: schema-driven columns, batches of N rows,
cohorts interleaved by schedule order, every image_url validated."""
from __future__ import annotations

import csv
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


class RowError(ValueError):
    pass


def check_image_url(url: str, verify: bool = True, timeout: float = 10.0) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RowError(f"image_url is not an absolute http(s) URL: {url}")
    if not verify:
        return
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                raise RowError(f"image_url returned HTTP {resp.status}: {url}")
    except urllib.error.HTTPError as exc:
        raise RowError(f"image_url returned HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RowError(f"image_url unreachable: {url} ({exc})") from exc


def pin_values(pin: dict, cfg_csv: dict) -> dict:
    """Pin record plus the derived published_at column value."""
    values = dict(pin)
    if pin.get("scheduled_at"):
        values["published_at"] = datetime.fromisoformat(pin["scheduled_at"]).strftime(
            cfg_csv["published_at_format"])
    return values


def validate_row(pin: dict, cfg_csv: dict, title_max: int = 90, desc_max: int = 300) -> None:
    values = pin_values(pin, cfg_csv)
    for col in cfg_csv["columns"]:
        if not values.get(col["field"]):
            raise RowError(f"{pin.get('pin_id')}: missing field '{col['field']}' "
                           f"for column '{col['name']}'")
    if len(pin["title"]) > title_max:
        raise RowError(f"{pin['pin_id']}: title longer than {title_max}")
    if len(pin["description"]) > desc_max:
        raise RowError(f"{pin['pin_id']}: description longer than {desc_max}")


def row_for(pin: dict, cfg_csv: dict) -> dict:
    values = pin_values(pin, cfg_csv)
    return {col["name"]: values.get(col["field"], "") for col in cfg_csv["columns"]}


def write_batches(pins: list[dict], out_dir: Path, cfg_csv: dict, batch_size: int,
                  start_index: int = 1) -> list[tuple[str, list[str]]]:
    """Write pins (already validated, in schedule order) into batch files.
    Returns [(filename, [pin_id, ...])]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    n = start_index
    for i in range(0, len(pins), batch_size):
        chunk = pins[i:i + batch_size]
        name = cfg_csv["batch_filename"].format(n=n)
        with open(out_dir / name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[c["name"] for c in cfg_csv["columns"]])
            writer.writeheader()
            for pin in chunk:
                writer.writerow(row_for(pin, cfg_csv))
        written.append((name, [p["pin_id"] for p in chunk]))
        n += 1
    return written
