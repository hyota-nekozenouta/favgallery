"""Allow `python -m favgallery`."""

import sys

from favgallery.cli import main

if __name__ == "__main__":
    sys.exit(main())
