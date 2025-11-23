import asyncio
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable


def memoize_with_ttl(ttl_hours: float = 24.0):
    """
    Decorator to memoize function results with a time-to-live.

    Args:
        ttl_hours: Time to live in hours before cache expires (default: 24)

    Works with both sync and async functions.
    Cache is stored per function, not per arguments.
    """
    def decorator(func: Callable) -> Callable:
        cache = {
            'data': None,
            'timestamp': None
        }

        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                # Check if we have a valid cached result
                if cache['data'] is not None and cache['timestamp'] is not None:
                    cache_age = datetime.now() - cache['timestamp']
                    if cache_age < timedelta(hours=ttl_hours):
                        logging.info(f"Using cached result for {func.__name__} (age: {cache_age})")
                        return cache['data']
                    else:
                        logging.info(f"Cache expired for {func.__name__} (age: {cache_age}), refreshing")

                # Call the function and cache the result
                logging.info(f"Executing {func.__name__} and caching result")
                result = await func(*args, **kwargs)
                cache['data'] = result
                cache['timestamp'] = datetime.now()
                logging.info(f"Cached result for {func.__name__}")

                return result

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Check if we have a valid cached result
                if cache['data'] is not None and cache['timestamp'] is not None:
                    cache_age = datetime.now() - cache['timestamp']
                    if cache_age < timedelta(hours=ttl_hours):
                        logging.info(f"Using cached result for {func.__name__} (age: {cache_age})")
                        return cache['data']
                    else:
                        logging.info(f"Cache expired for {func.__name__} (age: {cache_age}), refreshing")

                # Call the function and cache the result
                logging.info(f"Executing {func.__name__} and caching result")
                result = func(*args, **kwargs)
                cache['data'] = result
                cache['timestamp'] = datetime.now()
                logging.info(f"Cached result for {func.__name__}")

                return result

            return sync_wrapper

    return decorator
