"""Provision an ephemeral fixed-size Linux runner volume; never run against caller paths."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def cleanup(root: Path) -> None:
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    root = root.resolve(strict=True)
    if root.parent != temp or not root.name.startswith("crab-acquisition-"):
        raise ValueError("refusing cleanup outside this runner's acquisition temporary directory")
    volume = root / "volume"
    if os.path.ismount(volume):
        subprocess.run(["sudo", "-n", "umount", "--", str(volume)], check=True, timeout=60)
    if os.path.ismount(volume):
        raise ValueError("acquisition volume is still mounted; retaining its image")
    shutil.rmtree(root)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "cleanup":
        cleanup(Path(sys.argv[2]))
        return 0
    if len(sys.argv) != 3 or sys.argv[1] != "prepare" or sys.platform != "linux":
        raise ValueError("usage on Linux: acquisition_volume.py prepare BYTES | cleanup ROOT")
    capacity = int(sys.argv[2])
    if not 64 * 1024 * 1024 <= capacity <= 16 * 1024 * 1024 * 1024:
        raise ValueError("runner acquisition disk capacity must be between 64 MiB and 16 GiB")
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    root = Path(tempfile.mkdtemp(prefix="crab-acquisition-", dir=temp))
    volume = root / "volume"
    volume.mkdir()
    try:
        image = root / "disk.ext4"
        with image.open("xb") as handle:
            handle.truncate(capacity)
        subprocess.run(["mkfs.ext4", "-q", "-F", str(image)], check=True, timeout=60)
        subprocess.run(
            ["sudo", "-n", "mount", "-o", "loop,nosuid,nodev,noexec", str(image), str(volume)],
            check=True,
            timeout=60,
        )
        if not os.path.ismount(volume) or shutil.disk_usage(volume).total > capacity:
            raise ValueError("runner did not establish the fixed-capacity acquisition filesystem")
        subprocess.run(
            ["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(volume)],
            check=True,
            timeout=30,
        )
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"root={root}\nvolume={volume}\n")
    except BaseException:
        cleanup(root)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
