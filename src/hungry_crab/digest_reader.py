"""Reader-side resolution from a prey target to a logical digest location.

A digest's logical identity is its prey commit SHA. The bytes may live in the legacy
``digests/<sha>`` directory, an immutable canonical generation selected by ``.refs``,
or a non-canonical explicit/scratch output. Callers that need both identity and bytes
must not infer the SHA from the physical directory name.
"""

from __future__ import annotations

from .cache import Target
from .digest import DigestOptions, prepare_context
from .digest_location import DigestLocation, resolve_canonical_digest


def locate_digest_location(target: Target, options: DigestOptions | None = None) -> DigestLocation:
    """Resolve ``target`` once into its logical SHA and physical digest directory.

    Canonical clean full-digest reads honor an active immutable-generation ref. Explicit
    outputs, selective runs, and dirty/unknown worktrees are non-canonical and keep the
    physical path selected by ``prepare_context`` while retaining ``ctx.sha`` as their
    logical prey identity.
    """
    opts = options or DigestOptions()
    ctx, out_dir = prepare_context(target, opts)
    if opts.out is None and opts.miners is None and ctx.worktree == "clean":
        return resolve_canonical_digest(out_dir.parent, ctx.sha)
    return DigestLocation(sha=ctx.sha, path=out_dir)
