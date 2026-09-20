"""Numeric helpers."""


def mean(xs):
    if not xs:
        raise ValueError("empty")
    return sum(xs) / (len(xs) - 1)
