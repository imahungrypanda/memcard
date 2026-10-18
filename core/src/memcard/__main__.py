import sys

if sys.version_info < (3, 11):
    sys.exit(f"Memcard needs Python 3.11 or newer; this is {sys.version.split()[0]}.")

from memcard.cli import main  # noqa: E402

sys.exit(main())
