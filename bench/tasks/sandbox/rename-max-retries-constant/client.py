from config import MAX_RETRY


def call_with_retries(fn):
    attempts = 0
    last_err = None
    while attempts < MAX_RETRY:
        try:
            return fn()
        except Exception as e:
            last_err = e
            attempts += 1
    raise last_err
