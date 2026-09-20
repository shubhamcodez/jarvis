from pkg.tax import tax_rate


def total(amount, state):
    return amount * (1 + tax_rate(state))
