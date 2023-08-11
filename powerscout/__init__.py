import asyncio
import functools
import datetime
import os
import os.path
import platform
from enum import Enum
from contextlib import asynccontextmanager

import aioredis
import asyncpg
import logging
from asyncpg.connection import Connection
from asyncpg.exceptions import PostgresLogMessage, PostgresWarning
from async_timeout import timeout

from sanic_jinja2 import SanicJinja2
from sanic import Sanic
from sanic.request import Request as _Request

from .logger import get_logger, SysLogHandler

try:
    from .about import __version__
except ImportError:
    __version__ = "unknown"

try:
    import orjson as _json
except ImportError:
    try:
        import ujson as _json
    except ImportError:
        import json as _json


_json
__version__  # Silence unused import warning.

SYSLOG_KWARGS = {"facility": SysLogHandler.LOG_LOCAL0}
SYSLOG_FORMAT = logging.Formatter(
    "%(name)s: [%(asctime)s] [PID %(process)d] [%(levelname)s] %(message)s"
)
CONSOLE_FORMAT = logging.Formatter(
    "[%(name)s] [%(asctime)s] [PID %(process)d] [%(levelname)s] %(message)s"
)

if os.path.exists("/dev/log"):
    SYSLOG_KWARGS["address"] = "/dev/log"
elif platform.system() == "Darwin":
    SYSLOG_KWARGS["address"] = None


def configure_logging(debug=False):
    from sanic.log import logger as sanic_logger

    sanic_logger.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)
    if debug:
        handler = logging.StreamHandler()
        handler.setFormatter(CONSOLE_FORMAT)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        sanic_logger.addHandler(handler)

    handler = SysLogHandler(**SYSLOG_KWARGS)
    handler.ident = __name__
    handler.setFormatter(SYSLOG_FORMAT)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    sanic_logger.addHandler(handler)


logger = get_logger(__name__)


def _log_postgres_message(conn: Connection, message: PostgresLogMessage):
    func = logger.info
    if isinstance(message, PostgresWarning):
        func = logger.warning
    func(str(message))


class Request(_Request):
    __slots__ = ("jinja2", "redis_pool", "flash", "render_template", "database_pool")

    def __init__(self, *args, **kwargs):
        self.flash = None
        self.jinja2 = None
        self.redis_pool = None
        self.database_pool = None
        self.render_template = None
        super().__init__(*args, **kwargs)

    @asynccontextmanager
    async def db(self):
        async with self.database_pool.acquire() as conn:
            conn.add_log_listener(_log_postgres_message)
            try:
                yield conn
            finally:
                conn.remove_log_listener(_log_postgres_message)

    def bind_jinja(self, env):
        self.jinja2 = env
        self.render_template = functools.partial(env.render_async, request=self)
        self.flash = functools.partial(env._flash, self)

    def bind_redis(self, redis):
        self.redis_pool = aioredis.Redis(connection_pool=redis)

    def bind_db(self, database_pool):
        self.database_pool = database_pool

    def bind(self, database_pool, redis_pool, env):
        self.bind_redis(redis_pool)
        self.bind_jinja(env)
        self.bind_db(database_pool)
        return self

    def unbind(self):
        del self.flash
        del self.jinja2
        del self.redis_pool
        del self.database_pool
        del self.render_template
        return self


class ServerStatus(Enum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEGRADED = "degraded"


def create_app(app_name=None, config={}, debug=False):
    if app_name is None:
        app_name = __name__
    from .routes import apc_routes, eagle_routes
    from sanic.response import json as json_response

    print("config is", config)

    app = Sanic(
        app_name,
        request_class=Request,
        configure_logging=False,
        loads=_json.loads,
        dumps=_json.dumps,
    )
    app.update_config(config)
    configure_logging(debug=debug)

    app.ctx.server_status = ServerStatus.NOT_STARTED

    connection_count = 0

    def _decrement_connection_count(conn: Connection):
        nonlocal connection_count
        connection_count -= 1

    async def setup_database_connection(conn: Connection):
        nonlocal connection_count
        connection_count += 1
        conn.add_termination_listener(_decrement_connection_count)

    @app.listener("before_server_start")
    async def setup_db(app):
        url = config["POSTGRES_URI"]
        await logger.info(f"Creating database pools to {url}")
        app.ctx.server_status = ServerStatus.STARTING
        try:
            app.ctx.db = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=100,
                server_settings={"application_name": f"{__name__}@{__version__}"},
                init=setup_database_connection,
            )
        except Exception:
            logger.exception(f"Unable to contact {url!r}")
            raise
        await logger.info("Creating redis pools")
        app.ctx.cache = aioredis.ConnectionPool.from_url(app.config.REDIS_URI)
        await logger.info("Setup complete!")

    async def check_database_status(app, postgres_pool, redis_pool):
        async def _check_postgres(details):
            try:
                async with timeout(30) as cm:
                    async with postgres_pool.acquire() as conn:
                        await conn.fetchval("SELECT NOW()")
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                if cm.expired:
                    details["postgres"] = "timed out (block)"
                else:
                    details["postgres"] = "time out from asyncpg"
            except Exception as e:
                logger.exception("Unhandled exception in _check_postgres!", exc_info=e)
                details["postgres"] = str(e)

        async def _check_redis(details):
            try:
                async with timeout(30) as cm:
                    conn = await redis_pool.get_connection("_")
                    try:
                        await conn.check_health()
                    finally:
                        await redis_pool.release(conn)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                if cm.expired:
                    details["redis"] = "timed out (block)"
                else:
                    details["redis"] = "timed out in aioredis"
            except Exception as e:
                logger.exception("Unhandled exception in _check_redis!", exc_info=e)
                details["redis"] = str(e)

        while True:
            details = {"postgres": "ok", "redis": "ok"}
            await asyncio.gather(_check_postgres(details), _check_redis(details))
            delay = 30
            if all(x == "ok" for x in details.values()):
                app.ctx.server_status = ServerStatus.RUNNING
            else:
                app.ctx.server_status = ServerStatus.DEGRADED
                delay = 5
            app.ctx.server_status_details = details
            await asyncio.sleep(delay)

    @app.listener("after_server_start")
    async def mark_server_status(app):
        app.add_task(
            check_database_status(app, app.ctx.db, app.ctx.cache),
            name=check_database_status.__name__,
        )

    @app.listener("before_server_stop")
    async def pre_shutdown(app):
        app.ctx.server_status = ServerStatus.STOPPING
        await logger.info(f"Stopping {check_database_status.__name__} task")
        await app.cancel_task(check_database_status.__name__)

    @app.listener("after_server_stop")
    async def destroy_db(app):
        loop = asyncio.get_running_loop()
        cache_task = loop.create_task(asyncio.wait_for(app.ctx.cache.disconnect(True), 5))
        db_task = loop.create_task(asyncio.wait_for(app.ctx.db.close(), 15))
        cache_result, db_result = await asyncio.gather(cache_task, db_task, return_exceptions=True)
        if isinstance(db_result, Exception):
            await logger.exception(
                "Unable to close database timely, terminated pool.", exc_info=db_result
            )
        if isinstance(cache_result, Exception):
            await logger.exception(
                "Unable to close redis timely, terminated pool.", exc_info=cache_result
            )
        app.purge_tasks()
        await logger.drain()
        app.ctx.server_status = ServerStatus.STOPPED
        del app.ctx.cache, app.ctx.db

    app.blueprint(apc_routes)
    app.blueprint(eagle_routes)

    @app.get("/_health")
    async def show_server_health(request):
        status_code = 200
        headers = {}
        server_status = request.app.ctx.server_status
        if server_status in {ServerStatus.DEGRADED, ServerStatus.STARTING}:
            status_code = 503
            headers["X-Reason"] = f"Server status {server_status.name}. Please retry again."
            headers["Retry-After"] = formatdate(
                datetime.datetime.now().timestamp() + 30, usegmt=True
            )
        elif server_status is {
            ServerStatus.STOPPING,
            ServerStatus.STOPPED,
            ServerStatus.NOT_STARTED,
        }:
            status_code = 500
            headers["X-Reason"] = "Server shut down or not started."
        return json_response(
            {"status": server_status.name, "details": request.app.ctx.server_status_details},
            status=status_code,
            headers=headers,
        )

    jinja2_env = SanicJinja2(enable_async=True)
    jinja2_env.env.globals["len"] = len
    jinja2_env.env.globals["sorted"] = sorted

    # Add in fix for Sanic-Jinja2 making an oopsie w.r.t common sense
    # https://github.com/lixxu/sanic-jinja2/issues/12
    jinja2_env.init_app(app, pkg_name=app.name)

    @app.middleware("request")
    def install_jinja2(request):
        request.bind(app.ctx.db, app.ctx.cache, jinja2_env)

    @app.middleware("response")
    async def save_session(request, response):
        request.unbind()

    return app
