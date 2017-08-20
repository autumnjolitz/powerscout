import functools

import redis
from redis.connection import ConnectionPool
from sanic_jinja2 import SanicJinja2
from sanic import Sanic
from sanic.request import Request as _Request
try:
    import __pypy__
    __pypy__
except ImportError:
    try:
        import ujson as _json
    except ImportError:
        import json as _json
else:
    import json as _json
_json
from . import config
from .about import __version__
__version__  # Silence unused import warning.


class Request(_Request):
    __slots__ = ('jinja2_env', 'redis', 'flash', 'async_render_template')

    def __init__(self, *args, **kwargs):
        self.flash = None
        self.jinja2_env = None
        self.redis = None
        self.async_render_template = None
        super().__init__(*args, **kwargs)

    def bind_jinja(self, env):
        self.jinja2_env = env
        self.async_render_template = functools.partial(
            env.render_async, request=self)
        self.flash = functools.partial(env._flash, self)

    def bind_redis(self, conn):
        self.redis = conn


def make_app():
    app = Sanic(__name__, request_class=Request, log_config=None)
    app.config['redis_db'] = ConnectionPool.from_url(config.config['REDIS_URI'])

    jinja2_env = SanicJinja2(enable_async=True)
    jinja2_env.env.globals['len'] = len
    jinja2_env.env.globals['sorted'] = sorted

    # Add in fix for Sanic-Jinja2 making an oopsie w.r.t common sense
    # https://github.com/lixxu/sanic-jinja2/issues/12
    jinja2_env.init_app(app, pkg_name=app.name)

    @app.middleware('request')
    def install_jinja2(request):
        request.bind_jinja(jinja2_env)
        request.bind_redis(redis.Redis(connection_pool=app.config['redis_db']))

    @app.middleware('response')
    async def save_session(request, response):
        del request.flash
        del request.jinja2_env
        del request.redis
        del request.async_render_template

    return app
