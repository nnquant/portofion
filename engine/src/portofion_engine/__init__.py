"""Pure return-series analytics for portofion.

Every function here takes plain pandas/numpy inputs and has no I/O. Series are
daily simple returns indexed by date (``DatetimeIndex``, normalized, sorted,
unique), containing only observed days.
"""

from portofion_engine.calendar import align, as_returns, infer_periods_per_year, window_slice
from portofion_engine.metrics import METRIC_KEYS, summary

# Bump whenever any computed number can change; it is part of every cache key.
ENGINE_VERSION = "1"

__all__ = [
    "ENGINE_VERSION",
    "METRIC_KEYS",
    "align",
    "as_returns",
    "infer_periods_per_year",
    "summary",
    "window_slice",
]
