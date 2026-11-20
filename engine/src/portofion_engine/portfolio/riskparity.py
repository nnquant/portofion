"""Equal-risk-contribution weights via Spinu's convex formulation.

Solve  min_y  0.5 yᵀCy − b·Σ log(y_i)  on the correlation matrix C with damped
Newton (the objective is self-concordant, so the step 1/(1+λ) keeps y > 0).
Then x = y / Σy is ERC for C and w ∝ x / σ is ERC for Σ = D C D.
"""

from __future__ import annotations

import numpy as np


class RiskParityError(RuntimeError):
    pass


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    m = cov @ w
    total = float(w @ m)
    if total <= 0:
        return np.full_like(w, np.nan)
    return w * m / total


def erc_weights(cov: np.ndarray, tol: float = 1e-10, max_iter: int = 200) -> np.ndarray:
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    sd = np.sqrt(np.diag(cov))
    if np.any(sd <= 0) or not np.all(np.isfinite(cov)):
        raise RiskParityError("covariance has non-positive variance")
    C = cov / np.outer(sd, sd)
    b = np.full(n, 1.0 / n)
    y = np.full(n, 1.0 / np.sqrt(n))
    for _ in range(max_iter):
        Cy = C @ y
        g = Cy - b / y
        H = C + np.diag(b / y**2)
        try:
            dy = np.linalg.solve(H, g)
        except np.linalg.LinAlgError as e:
            raise RiskParityError(str(e)) from e
        lam = float(np.sqrt(max(g @ dy, 0.0)))
        step = 1.0 if lam < 0.25 else 1.0 / (1.0 + lam)
        y = y - step * dy
        if np.any(y <= 0):
            raise RiskParityError("iterate left the domain")
        if lam < 1e-14:
            break
    x = y / y.sum()
    w = x / sd
    w = w / w.sum()
    rc = risk_contributions(w, cov)
    if not np.all(np.isfinite(rc)) or float(rc.max() - rc.min()) > max(tol, 1e-8):
        raise RiskParityError("did not converge")
    return w
