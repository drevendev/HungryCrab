"""Acquire only the three owned receiving snapshots for the frozen qualification setup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qualification_acquire import VOLUME_BYTES, export_objects, write_json

from hungry_crab.cache import Slug
from hungry_crab.fetch.catch import CatchOptions, catch
from hungry_crab.fetch.github import GitHubClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--volume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=False)
    cache = args.volume / "receiving-cache"
    receipts = []
    for name in ("HungryCrab", "Devostasis", "BeautifulRepo"):
        slug = Slug("drevendev", name)
        metadata = GitHubClient(retries=0).repo(slug)
        if metadata.get("private") is not False:
            raise ValueError("receiving setup exports public owner repositories only")
        result = catch(
            slug,
            CatchOptions(
                shallow=True,
                wiki=False,
                issues=0,
                acquisition_volume=args.volume,
                max_download_bytes=314_572_800,
                max_disk_bytes=VOLUME_BYTES,
            ),
            cache_root=cache,
        )
        if result.sha != plan["receiving"][name]["sha"]:
            raise ValueError("receiving default changed before sealing; refresh its context")
        output = args.output / name
        output.mkdir()
        amount = export_objects(Path(result.repo_dir), output / "objects", slug, 128 * 1024 * 1024)
        receipt = {
            "repository": str(slug),
            "sha": result.sha,
            "object_bytes": amount,
            "catch": result.to_dict(),
            "model_trials_started": 0,
            "receiving_code_executed": False,
        }
        receipts.append(receipt)
        write_json(output / "receipt.json", receipt)
        print(
            json.dumps({"repository": str(slug), "sha": result.sha, "object_bytes": amount}),
            flush=True,
        )
    write_json(args.output / "receiving.json", receipts)


if __name__ == "__main__":
    main()
