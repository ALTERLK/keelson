from config import MAX_RETRIES


def call_with_retries(fn):
    attempts = 0
    last_err = None
    while attempts < MAX_RETRIES:
        try:
            return fn()
        except Exception as e:
            last_err = e
            attempts += 1
    raise last_err
