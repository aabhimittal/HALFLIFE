"""Run the guided HALFLIFE walkthrough: python examples/demo.py [--quick]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halflife.demo import main  # noqa: E402

if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
