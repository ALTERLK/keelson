import re


def normalize(s):
    return re.sub(r"\s+", " ", s.strip())
