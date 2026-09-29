"""PBO přes CSCV — Probability of Backtest Overfitting (Bailey et al.).

Combinatorially Symmetric Cross-Validation: S bloků (S=12–16, sudé),
rozdělíme na polovinu C (in-sample) / complement (out-of-sample),
počítáme relativní rank matice M -> lambdy, PBO = P(lambda <= 0).
"""

import itertools


def pbo_cscv(returns_matrix, n_blocks=16, score=None):
    """returns_matrix: seznam N strategií, každá seznam T výnosů (stejná délka).
    score: funkce (výnosy -> skóre, vyšší = lepší), default NEanualizovaný SR.
    Vrací dict: PBO, lambdy, logits."""
    import math as _m
    n_strats = len(returns_matrix)
    T = len(returns_matrix[0])
    if n_strats < 2:
        raise ValueError("potřebuji >= 2 strategie")
    if n_blocks % 2 != 0 or not (4 <= n_blocks <= 20):
        raise ValueError("n_blocks sudé, typicky 12–16")
    block = T // n_blocks
    if block == 0:
        raise ValueError("málo dat pro daný počet bloků")
    if score is None:
        def _sr_score(rs):
            mu = sum(rs) / len(rs)
            var = sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)
            return mu / _m.sqrt(var) if var > 0 else 0.0
        score = _sr_score

    blocks = [range(b * block, min((b + 1) * block, T)) for b in range(n_blocks)]
    half = n_blocks // 2
    lambdas = []
    for combo in itertools.combinations(range(n_blocks), half):
        is_idx = [i for b in combo for i in blocks[b]]
        oos_idx = [i for b in range(n_blocks) if b not in combo for i in blocks[b]]
        # skóre IS/OOS pro každou strategii
        is_scores = [score([returns_matrix[s][i] for i in is_idx])
                     for s in range(n_strats)]
        oos_scores = [score([returns_matrix[s][i] for i in oos_idx])
                      for s in range(n_strats)]
        # relativní rank v IS
        order_is = sorted(range(n_strats), key=lambda s: is_scores[s])
        rank_is = {s: r / (n_strats - 1) for r, s in enumerate(order_is)}
        # relativní rank ve stejném pořadí podle OOS
        order_oos = sorted(range(n_strats), key=lambda s: oos_scores[s])
        rank_oos = {s: r / (n_strats - 1) for r, s in enumerate(order_oos)}
        # matice M: řádky = IS rank, hodnoty = OOS rank -> lambda quantily
        # standardní CSCV: lambda = F(-1) quantile OOS ranků pro nejlepší IS strategii
        # zde jednoduše pro každou strategii: logit rank_oos vs rank_is
        best_is = order_is[-1]  # nejlepší IS strategie
        w = rank_oos[best_is]
        if w in (0.0, 1.0):
            lam = _m.inf if w == 1.0 else -_m.inf
        else:
            lam = _m.log(w / (1.0 - w))
        lambdas.append(lam)
    finite = [l for l in lambdas if _m.isfinite(l)]
    n_neg = sum(1 for l in finite if l <= 0)
    pbo = n_neg / len(finite) if finite else 0.0
    return {"PBO": pbo, "lambdas": lambdas, "n_combos": len(lambdas)}
