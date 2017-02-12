import time
import inspect
import functools
import logging
import traceback

try:
    from japronto.response.cresponse import Response
except ImportError:
    from japronto.response.py import Response

from ..services.graphite import post_metric

logger = logging.getLogger(__name__)
REGISTRY = {}


def route(path):
    def wrapper(func):
        spec = inspect.getargspec(func)
        argless_mode = not spec.args
        module_name = __name__

        @functools.wraps(func)
        def wrapped(request):
            kwargs = request.match_dict
            args = (request,)
            if argless_mode:
                args = ()
            duration = None
            try:
                t_s = time.time()
                result = func(*args, **kwargs)
                duration = time.time() - t_s

                if argless_mode or not isinstance(result, Response):
                    code = 200
                    if isinstance(result, tuple):
                        result, code = result
                    if not isinstance(result, str):
                        return request.Response(json=result, code=code)
                    return request.Response(text=result, code=code)

                return result
            except Exception:
                logger.exception('Unhandled exception for {}'.format(func.__name__))
                return request.Response(code=500, text='{}'.format(traceback.format_exc()))
            finally:
                if duration is None:
                    duration = time.time() - t_s
                    post_metric(
                        f'{module_name}.{func.__name__}.durations.errored.seconds',
                        duration)
                else:
                    post_metric(
                        f'{module_name}.{func.__name__}.durations.okay.seconds',
                        duration)
                logger.debug(f'{module_name}.{func.__name__} took {duration:.2f}')

        assert path not in REGISTRY, 'Cannot register {} path'.format(path)
        REGISTRY[path] = wrapped
        return wrapped
    return wrapper

from . import apc
from . import eagle

__all__ = ['apc', 'eagle']
