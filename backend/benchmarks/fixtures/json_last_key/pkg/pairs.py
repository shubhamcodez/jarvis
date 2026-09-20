def parse_pairs(text):
    if not text:
        return {}
    parts = text.split(",")
    out = {}
    for p in parts[:-1]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k] = v
    return out
