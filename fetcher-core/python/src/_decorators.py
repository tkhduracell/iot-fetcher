import time
import functools


def memoize_for_hours(hours):
    def decorator(func):
        cache = {}
        seconds = hours * 3600

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            current_time = time.time()
            if key in cache:
                result, timestamp = cache[key]
                if current_time - timestamp < seconds:
                    return result
            result = func(*args, **kwargs)
            cache[key] = (result, current_time)
            return result

        def clear() -> None:
            """Evict everything cached so far.

            For a token that expires server-side before its TTL here does --
            the memoize window is a ceiling on how often we ask, not a
            guarantee the cached value is still good. A caller that detects
            the server has rejected it can call this and retry once in the
            same run instead of waiting out the rest of the window.
            """
            cache.clear()

        wrapper.clear = clear
        return wrapper
    return decorator
