from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

Scheme = Literal["fixed", "equal", "inverse_vol", "risk_parity", "reported"]
Rebalance = Literal["daily", "weekly", "monthly", "quarterly", "none"]

SCHEMES: tuple[str, ...] = ("fixed", "equal", "inverse_vol", "risk_parity", "reported")
REBALANCES: tuple[str, ...] = ("daily", "weekly", "monthly", "quarterly", "none")


@dataclass(frozen=True)
class PortfolioSpec:
    scheme: Scheme = "fixed"
    rebalance: Rebalance = "monthly"
    lookback: int = 63
    min_obs: int | None = None
    start: Literal["common", "earliest"] | date = "common"
    cost_bps: float = 0.0
    gross: float = 1.0
    renormalize: bool = True
    stale_days: int = 7
    shrinkage: float = 0.1

    @property
    def is_vol_scheme(self) -> bool:
        return self.scheme in ("inverse_vol", "risk_parity")

    @property
    def effective_min_obs(self) -> int:
        if self.min_obs is not None:
            return self.min_obs
        return max(20, self.lookback // 2)
