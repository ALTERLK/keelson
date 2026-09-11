import re


def tokenize(text):
    return re.findall(r"[a-zA-Z']+", text.lower())
