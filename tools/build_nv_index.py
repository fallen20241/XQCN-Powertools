"""Build a compact addons/nv_index.json from the three Qualcomm NV definition XMLs.

This is a build-time tool — run it whenever the source XMLs change. The runtime
(XQCNPowerTools) loads the JSON, never the XMLs.

Usage:
    python tools/build_nv_index.py            # writes addons/nv_index.json
    python tools/build_nv_index.py --check    # exit 1 if JSON is stale vs XMLs

Schema:
    {
      "_meta": {
        "version": 1,
        "generated": "YYYY-MM-DD HH:MM:SS",
        "sources": [
          {"file": "NvDefinition.xml",        "sha256": "..."},
          {"file": "NvDefinition5g.xml",      "sha256": "..."},
          {"file": "nv_efs_data_format.xml",  "sha256": "..."}
        ]
      },
      "by_id":   { "<numeric_id>": ["<name>", "<description>"], ... },
      "by_path": { "<efs_full_path>": ["<name>", "<description>"], ... }
    }
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET

REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS_DIR = os.path.join(REPO_ROOT, "addons")
INDEX_PATH = os.path.join(ADDONS_DIR, "nv_index.json")

SOURCES = [
    "NvDefinition.xml",
    "NvDefinition5g.xml",
    "nv_efs_data_format.xml",
]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_index() -> dict:
    by_id:   dict[str, list[str]] = {}
    by_path: dict[str, list[str]] = {}

    # nv_efs_data_format.xml — richest source: id, fullpathname, name, description
    efs_path = os.path.join(ADDONS_DIR, "nv_efs_data_format.xml")
    root = ET.parse(efs_path).getroot()
    for elem in root.iter():
        if elem.tag not in ("NvEfsItemData", "NvItemData"):
            continue
        name  = elem.get("name", "")
        desc  = elem.get("description", "")
        nv_id = elem.get("id", "")
        fpath = elem.get("fullpathname", "")
        if fpath:
            by_path[fpath] = [name, desc]
        if nv_id and nv_id not in by_id:
            by_id[nv_id] = [name, desc]

    # NvDefinition.xml — RF NV items by numeric id (first-write-wins respects
    # priority of the EFS file's richer descriptions).
    rf_path = os.path.join(ADDONS_DIR, "NvDefinition.xml")
    root = ET.parse(rf_path).getroot()
    for elem in root.iter("NvItem"):
        nv_id = elem.get("id", "")
        name  = elem.get("name", "")
        if nv_id and name and nv_id not in by_id:
            by_id[nv_id] = [name, ""]

    # NvDefinition5g.xml — 5G tree files / items by numeric id
    g5_path = os.path.join(ADDONS_DIR, "NvDefinition5g.xml")
    root = ET.parse(g5_path).getroot()
    for elem in root.iter():
        if elem.tag not in ("NvItem", "NvTreeFile"):
            continue
        nv_id = elem.get("id", "")
        name  = elem.get("name", "")
        if nv_id and name and nv_id not in by_id:
            by_id[nv_id] = [name, ""]

    sources = [
        {"file": fn, "sha256": _sha256(os.path.join(ADDONS_DIR, fn))}
        for fn in SOURCES
    ]

    return {
        "_meta": {
            "version":   1,
            "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sources":   sources,
        },
        "by_id":   dict(sorted(by_id.items(),   key=lambda kv: int(kv[0]) if kv[0].isdigit() else 1 << 30)),
        "by_path": dict(sorted(by_path.items())),
    }


def write_index(data: dict, path: str = INDEX_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def is_stale() -> bool:
    """Return True if any source XML's hash doesn't match the index's recorded hash."""
    if not os.path.isfile(INDEX_PATH):
        return True
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            recorded = json.load(f).get("_meta", {}).get("sources", [])
    except Exception:
        return True
    recorded_by_file = {s["file"]: s["sha256"] for s in recorded}
    for fn in SOURCES:
        actual = _sha256(os.path.join(ADDONS_DIR, fn))
        if recorded_by_file.get(fn) != actual:
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true",
                   help="Exit 1 if the index is stale (does not regenerate).")
    args = p.parse_args()

    if args.check:
        stale = is_stale()
        print("STALE" if stale else "FRESH")
        return 1 if stale else 0

    data = build_index()
    write_index(data)
    size = os.path.getsize(INDEX_PATH)
    print(f"Wrote {INDEX_PATH}")
    print(f"  by_id   entries : {len(data['by_id']):>6}")
    print(f"  by_path entries : {len(data['by_path']):>6}")
    print(f"  size            : {size:>6} bytes ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
