"""
Put 03_src and tests/ on sys.path.

WHY THIS FILE EXISTS: `03_src` starts with a digit, so it is not a legal Python
package name and `import 03_src.contracts` can never work. Rather than let four
people each invent a different workaround at 03:00, the convention is settled here:
03_src is added to sys.path and modules are imported flat — `from contracts import
AisTrack`. Every module in 03_src does the same. Do not rename the directory; the
numeric prefixes are what keep the repo readable to a judge.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "03_src", _ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
