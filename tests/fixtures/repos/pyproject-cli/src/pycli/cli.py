from __future__ import annotations

import sys
from pathlib import Path

import click

from pycli.core import count_pools


@click.group()
def main() -> None:
    """Count tide pools."""


@main.command()
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--unique", is_flag=True, help="count distinct names only")
def count(path: Path, unique: bool) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        click.echo(f"cannot read {path}: {exc}", err=True)
        sys.exit(2)
    click.echo(count_pools(text, unique=unique))
