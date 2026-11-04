"""Allow `python -m sealed_panel`."""

import sys

from .cli import main

sys.exit(main())
