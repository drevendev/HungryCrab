"""Trusted parser subprocess. Stdin is data; only our installed grammar allowlist is imported."""

from __future__ import annotations

import base64
import json
import sys

from .miners.symbols import GRAMMARS, READ_LIMIT, _index_source


def main() -> int:
    cap = 4 * 1024 * 1024
    raw = sys.stdin.buffer.read(cap + 1)
    if len(raw) > cap:
        return 1
    try:
        inputs = json.loads(raw)
        if not isinstance(inputs, list) or len(inputs) > 16:
            return 1
        result = []
        for data in inputs:
            grammar = tuple(data["grammar"])
            path = data["path"]
            source = base64.b64decode(data["source"], validate=True)
            if (
                grammar not in GRAMMARS.values()
                or not isinstance(path, str)
                or len(path) > 2000
                or len(source) > READ_LIMIT
            ):
                return 1
            result.append(_index_source(source, path, (str(grammar[0]), str(grammar[1]))))
        output = json.dumps(result, ensure_ascii=True).encode()
        if len(output) > 8 * 1024 * 1024:
            return 1
        sys.stdout.buffer.write(output)
        return 0
    except (KeyError, ValueError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
