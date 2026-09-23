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

    The path selected by ``prepare_context`` is authoritative. An implicit selection of
    the legacy ``<digests>/<sha>`` slot is canonical and may resolve through an active
    immutable-generation ref. Any other selected path stays non-canonical, including
    future scratch-routing reasons that this reader does not know about. Explicit outputs
    remain non-canonical even if the caller makes their path resemble the canonical slot.
    """
    opts = options or DigestOptions()
    ctx, out_dir = prepare_context(target, opts)
    selected_canonical = opts.out is None and out_dir == out_dir.parent / ctx.sha
    if selected_canonical:
        return resolve_canonical_digest(out_dir.parent, ctx.sha)
    return DigestLocation(sha=ctx.sha, path=out_dir)
