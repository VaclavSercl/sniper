"""DSR — Deflated Sharpe Ratio (Bailey & López de Prado).

SR0 = sqrt(V[SR_n]) * [(1-gamma)*Z^{-1}(1-1/N) + gamma*Z^{-1}(1-1/(N*e))]
DSR = Z[ (SR_hat - SR0) * sqrt(T-1) / sqrt(1 - g3*SR_hat + ((g4-1)/4)*SR_hat^2) ]

SR_hat i SR0 NEanualizované (per perioda). gamma = 0.5772 (Euler-Mascheroni).
gamma3 = skewness, gamma4 = kurtosis (NE excess).
V[SR_n] = 1/T na periodu — rozptyl za nulové hypotézy,
NIKDY výběrový rozptyl pozorovaných Sharpů.
"""

import math
import statistics

EULER_GAMMA = 0.5772


def norm_cdf(x):
    """CDF standardního normálního rozdělení (Abramowitz-Stegun)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Inverzní CDF normálního rozdělení (Acklamova aprox., stdlib nemá)."""
    if not (0.0 < p < 1.0):
        raise ValueError("p musí být v (0,1)")
    # racionální aproximační koeficienty
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def sharpe_ratio(returns):
    """NEanualizovaný (per perioda) Sharpe ratio."""
    if len(returns) < 2:
        raise ValueError("potřebuji alespoň 2 pozorování")
    mu = statistics.fmean(returns)
    sd = statistics.stdev(returns)
    if sd == 0:
        return 0.0
    return mu / sd


def variance_sr(T):
    """V[SR_n] = 1/T na periodu — rozptyl SR za nulové hypotézy
    (T = počet pozorování výnosů, NIKDY výběrový rozptyl pozorovaných
    Sharpů mezi pokusy)."""
    if T <= 0:
        raise ValueError("T > 0")
    return 1.0 / float(T)


def expected_max_sr(n_trials, var_sr=None):
    """SR0 — očekávané maximum |SR| za N nezávislých pokusů (E[max Z]).
    SR0 = sqrt(V[SR_n]) * [(1-gamma)*Z^{-1}(1-1/N) + gamma*Z^{-1}(1-1/(N*e))]
    gamma = 0.5772 (Euler-Mascheroni), e = Eulerovo číslo.
    var_sr = V[SR_n] = 1/T (rozptyl za nulové hypotézy); není-li zadáno,
    použije se jednotkový rozptyl (volající má předat 1/T)."""
    if n_trials < 1:
        raise ValueError("N >= 1")
    if n_trials == 1:
        return 0.0
    if var_sr is None:
        var_sr = 1.0
    z1 = norm_ppf(1.0 - 1.0 / n_trials)
    z2 = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_sr) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe_ratio(returns, n_trials, var_sr=None):
    """DSR + diagnostika. Vrací dict: DSR, SR_hat, SR0, N, V[SR_n], gamma3, gamma4.

    returns: výnosy strategie per perioda (NEanualizované)
    n_trials: počet nezávislých testů N (včetně vybrané strategie)
    """
    T = len(returns)
    if T < 3:
        raise ValueError("T >= 3")
    sr_hat = sharpe_ratio(returns)
    if var_sr is None:
        var_sr = variance_sr(T)  # V[SR_n] = 1/T za nulové hypotézy
    sr0 = expected_max_sr(n_trials, var_sr)
    mu = statistics.fmean(returns)
    m2 = statistics.fmean([(r - mu) ** 2 for r in returns])
    m3 = statistics.fmean([(r - mu) ** 3 for r in returns])
    m4 = statistics.fmean([(r - mu) ** 4 for r in returns])
    g3 = m3 / (m2 ** 1.5) if m2 > 0 else 0.0   # skewness
    g4 = m4 / (m2 ** 2) if m2 > 0 else 3.0     # kurtosis (NE excess)
    denom = 1.0 - g3 * sr_hat + ((g4 - 1.0) / 4.0) * sr_hat ** 2
    if denom <= 0:
        raise ValueError("neplatný jmenovatel DSR")
    stat = (sr_hat - sr0) * math.sqrt(T - 1) / math.sqrt(denom)
    dsr = norm_cdf(stat)
    return {
        "DSR": dsr,
        "SR_hat": sr_hat,
        "SR0": sr0,
        "N": n_trials,
        "V_SR_n": var_sr,
        "T": T,
        "gamma3": g3,
        "gamma4": g4,
    }
