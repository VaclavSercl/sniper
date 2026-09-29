"""CPCV — Combinatorial Purged Cross-Validation (López de Prado, Advances in Financial ML).

N_groups 8–10, k_test 2, embargo >= 1 % délky dat, purging povinný.
Vrací pro každou kombinaci: trénovací indexy, testovací indexy (purged + embargo).
"""

import itertools


def _embargo_size(n, embargo_pct=0.01, min_embargo=1):
    """Embargo >= 1 % délky dat (min. 1 perioda)."""
    e = max(int(n * embargo_pct), min_embargo)
    return max(e, 1)


def cpcv_splits(n, n_groups=8, k_test=2, embargo_pct=0.01, purge=True):
    """Vygeneruje CPCV dělení.

    n: počet pozorování (indexy 0..n-1)
    n_groups: počet skupin (8–10)
    k_test: počet testovacích skupin v jedné kombinaci
    Vrací seznam dict: {"train": [...], "test": [...], "test_groups": (i, j)}
    Trénovací indexy mají odstraněny purge (překryv s testovacími skupinami)
    a embargo (období po testu, kvůli look-ahead u překrývajících se labelů).
    """
    if not (2 <= k_test < n_groups):
        raise ValueError("k_test musí být v rozsahu 2..n_groups-1")
    group_size = n // n_groups
    if group_size == 0:
        raise ValueError("příliš málo dat pro daný počet skupin")
    # přiřazení indexů do skupin (poslední skupina může být menší)
    groups = [set(range(g * group_size, min((g + 1) * group_size, n)))
              for g in range(n_groups)]
    # zbylé indexy nakonec do poslední skupiny
    rest = set(range(n_groups * group_size, n))
    if rest:
        groups[-1] |= rest

    embargo = _embargo_size(n, embargo_pct)
    splits = []
    for test_combo in itertools.combinations(range(n_groups), k_test):
        test_idx = sorted(set().union(*[groups[g] for g in test_combo]))
        train_idx = []
        for g in range(n_groups):
            if g in test_combo:
                continue
            for i in groups[g]:
                # purging: vynechat indexy, jejichž okolí (okno) se dotýká testu
                if purge and _purged(i, test_idx, embargo):
                    continue
                train_idx.append(i)
        train_idx.sort()
        splits.append({
            "train": train_idx,
            "test": test_idx,
            "test_groups": test_combo,
            "embargo": embargo,
        })
    return splits


def _purged(i, test_idx, embargo):
    """Purge + embargo pro trénovací index i.
    Purge: překryv labelů — test v [i-1, i+1] (label překrývající test).
    Embargo: test před i v rozsahu (i-1-embargo, i) — kvůli look-aheadu
    (informace z testovacího období unikající do tréninku).
    Vrací True, má-li být i vyřazen."""
    import bisect
    lo = i - 1 - embargo
    hi = i + 1
    pos = bisect.bisect_left(test_idx, lo)
    return pos < len(test_idx) and test_idx[pos] <= hi


def assert_no_lookahead(split, horizon=1):
    """Ověření: žádný trénovací index není v budoucnosti (embargo) testovacích.
    Vrací True, pokud dělení nemá look-ahead."""
    test_max = max(split["test"])
    test_min = min(split["test"])
    # žádný train index bezprostředně po testovacím bloku (embargo) ani uvnitř
    for t in split["train"]:
        if t in split["test"]:
            return False
        # embargo za nejvyšším testovacím indexem kombinace
        if t > test_max and t <= test_max + split["embargo"] + horizon:
            return False
    # purge: žádný train bezprostředně před testem (překryv labelů)
    for t in split["train"]:
        if test_min - horizon <= t < test_min:
            return False
    return True
