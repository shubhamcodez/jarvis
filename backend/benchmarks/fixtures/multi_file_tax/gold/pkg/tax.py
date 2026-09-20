from pkg.rates import RATES


def tax_rate(state):
    return float(RATES.get(state, 0.0))
