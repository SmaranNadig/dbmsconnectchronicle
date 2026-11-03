"""Download verified public connectome exports into data/raw/<dataset>/<release>/.

Files are stored exactly as published (archives are not unpacked) and every
file is recorded in data/metadata/dataset_manifest.json with its SHA-256,
size, source URL and the server's Last-Modified header.

    python scripts/download_datasets.py --dataset hemibrain --release v1.2
    python scripts/download_datasets.py --dataset all --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import MANIFEST_PATH, RAW_DIR, load_catalog  # noqa: E402


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"files": {}}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def download(url: str, dest: Path, client: httpx.Client) -> dict:
    head = client.head(url)
    head.raise_for_status()
    expected = int(head.headers.get("content-length", "0"))
    if dest.exists() and dest.stat().st_size == expected:
        print(f"  exists  {dest.relative_to(RAW_DIR)} ({expected/1e6:.1f} MB)")
    else:
        tmp = dest.with_suffix(dest.suffix + ".part")
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    print(f"\r  fetch   {dest.name}: {done/1e6:.1f}/{expected/1e6:.1f} MB", end="")
        print()
        if tmp.stat().st_size != expected:
            raise RuntimeError(f"size mismatch for {url}")
        tmp.replace(dest)
    return {
        "url": url,
        "size_bytes": dest.stat().st_size,
        "sha256": sha256_of(dest),
        "server_last_modified": head.headers.get("last-modified"),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="all", help="dataset code from config/datasets.yaml, or 'all'")
    ap.add_argument("--release", default="all", help="release label, or 'all'")
    ap.add_argument("--output", type=Path, default=RAW_DIR)
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    args = ap.parse_args()

    catalog = load_catalog()
    manifest = load_manifest()
    datasets = catalog if args.dataset == "all" else {args.dataset: catalog[args.dataset]}
    # identity encoding: store bytes exactly as published and keep
    # Content-Length comparable (GitHub raw otherwise gzips in transit)
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"Accept-Encoding": "identity"}) as client:
        for ds_code, ds in datasets.items():
            for rel, spec in ds["releases"].items():
                if args.release != "all" and rel != args.release:
                    continue
                for f in spec["files"]:
                    dest = args.output / ds_code / rel / f["filename"]
                    if args.dry_run:
                        print(f"would fetch {f['url']} -> {dest}")
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    entry = download(f["url"], dest, client)
                    entry.update(dataset=ds_code, release=rel)
                    manifest["files"][f"{ds_code}/{rel}/{f['filename']}"] = entry
                    save_manifest(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
