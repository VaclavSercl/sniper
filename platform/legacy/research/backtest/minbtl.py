"""MinBTL — Minimum Backtest Length pre-screen (Bailey & López de Prado).

T_min >= (Z_N / SR_period)^2, kde Z_N je kritická hodnota pro N testů
(SR0 bez škálování variance), SR_period = NEanualizovaný SR per perioda.
"""

import math

try:
    from .dsr import norm_ppf
except ImportError:  # spuštění přímo z adresáře (unittest)
    from dsr import norm_ppf


def min_backtest_length(sr_period, n_trials, alpha=0.05):
    """Minimální délka backtestu (v periodách) daného SR pro N testů.
    T_min = (Z_N / SR)^2, Z_N = Z^{-1}(1 - alpha/N) (Bonferroni-style)."""
    if sr_period <= 0:
        raise ValueError("SR_period > 0")
    if n_trials < 1:
        raise ValueError("N >= 1")
    if n_trials == 1:
        z_n = norm_ppf(1 - alpha)
    else:
        z_n = norm_ppf(1 - alpha / n_trials)
    return (z_n / sr_period) ** 2


def minbtl_prescreen(sr_period, n_trials, T_available, alpha=0.05):
    """Pre-screen: dost dost dat pro daný SR a N? Vrací dict s T_min, verdikt."""
    t_min = min_backtest_length(sr_period, n_trials, alpha)
    return {
        "T_min": t_min,
        "T_available": T_available,
        "pass": T_available >= t_min,
        "N": n_trials,
        "SR_period": sr_period,
    }
