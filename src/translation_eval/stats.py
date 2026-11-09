"""信賴區間。"""
import math


def _binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k)，X ~ Binomial(n, p)；用對數算，n 大也不會溢位。"""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q for i in range(k + 1)]
    peak = max(terms)
    return min(1.0, math.exp(peak) * sum(math.exp(t - peak) for t in terms))


def clopper_pearson_upper(failures: int, total: int, confidence: float = 0.95) -> float:
    """單尾 exact binomial（Clopper–Pearson）的上限：真正的失敗率最大可以是多少，才讓「最多 failures 次失敗」
    的機率仍不小於 1 − confidence。0 次失敗時是 1 − (1 − confidence)^(1/total)，約 3 ÷ total。"""
    if total <= 0 or not 0 <= failures <= total:
        raise ValueError("failures 要在 0 到 total 之間，total 要大於 0")
    if failures == total:
        return 1.0
    alpha, low, high = 1 - confidence, 0.0, 1.0
    for _ in range(100):   # 二分法：CDF 隨 p 遞減，找 CDF = alpha 的 p
        mid = (low + high) / 2
        if _binomial_cdf(failures, total, mid) > alpha:
            low = mid
        else:
            high = mid
    return high
