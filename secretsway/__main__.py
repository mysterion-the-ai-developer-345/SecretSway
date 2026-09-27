"""SecretSway -- a launcher that looks like a terminal."""

import sys

if __package__ in (None, ""):
    # Allow `python3 ~/.config/secretsway` without installing anything.
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from secretsway.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
