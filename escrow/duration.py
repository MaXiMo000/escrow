"""Parse a human-friendly duration string ("26h", "8d", "45m", "2w") into
seconds.

Single unit only for v1 -- "1d12h" isn't supported; write "36h" instead.
Combined-unit parsing is a real feature and not hard to add, but every
config in escrow.yaml only ever needs "how long can this job go quiet
before that's a problem," which a single unit answers just as well and
without ambiguity about ordering (is "12h1d" valid? "1d 12h"?) that a
combined format would have to define and test.
"""
from __future__ import annotations

import re

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_PATTERN = re.compile(r'^(\d+(?:\.\d+)?)([smhdw])$')


class DurationError(ValueError):
    pass


def parse_duration(text: str) -> float:
    m = _PATTERN.match(text.strip())
    if not m:
        raise DurationError(
            f"'{text}' is not a duration escrow understands -- use a number "
            f"followed by one unit: s, m, h, d, or w (e.g. '26h', '8d'). "
            f"Combined units like '1d12h' aren't supported; write '36h'.")
    value, unit = m.groups()
    return float(value) * _UNIT_SECONDS[unit]
