import re
from collections import Counter


def tokenize(text):
    return re.findall(r"[a-zA-Z']+", text.lower())


def top_words(text, n):
    counts = Counter(tokenize(text))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
