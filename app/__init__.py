"""Application package compatibility helpers."""

# ``datetime.UTC`` was added in Python 3.11.  The project also runs on
# Python 3.10, so expose the equivalent timezone constant before submodules
# import it.
import datetime as _datetime

if not hasattr(_datetime, "UTC"):
    _datetime.UTC = _datetime.timezone.utc
