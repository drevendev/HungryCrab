"""The fetch layer: everything that talks to git or to a forge API.

GitHub is the first prey source. The miners never touch the network; they only read what this
layer has put into the cache.
"""

from __future__ import annotations
