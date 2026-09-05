from click.testing import CliRunner

from pycli.cli import main


def test_count_missing_file() -> None:
    result = CliRunner().invoke(main, ["count", "missing.txt"])
    assert result.exit_code == 2
