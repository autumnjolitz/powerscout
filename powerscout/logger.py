import logging
import logging.handlers
import sys
import os.path
import asyncio
import syslog
from enum import IntEnum
from types import SimpleNamespace
from logging import DEBUG, WARNING, CRITICAL, INFO, ERROR
from typing import Optional, Dict, Tuple, Iterable, Any, AsyncIterable, Union, Type, TypeVar

from contextlib import suppress

T = TypeVar("T")

raiseExceptions = True


import functools


def make_setattr(self, name, value, setattr=None):
    cls = type(self)
    print("instane set", type(self), name, "->", value)
    if name in cls._class_variable_definitions:
        setattr(cls, name, value)
        return
    if setattr is None:
        setattr = object.__setattr__
    setattr(self, name, value)


class ClassVariableMeta(type):
    def __init__(self, name, bases, namespace):
        self._class_variable_definitions = {
            name: value for name, value in namespace.items() if isinstance(value, ClassVariable)
        }
        super().__init__(name, bases, namespace)

    def __getattribute__(self, name):
        if name == "_class_variable_definitions":
            return type.__getattribute__(self, name)
        locked = self._class_variable_definitions
        if name in locked:
            value = locked[name]
            return value.value
        value = type.__getattribute__(self, name)
        return value

    def __setattr__(self, name, value):
        if name == "_class_variable_definitions":
            type.__setattr__(self, name, value)
            return
        locked = self._class_variable_definitions
        if name in locked:
            prop = locked[name]
            prop.value = value
            return
        type.__setattr__(self, name, value)


class ClassVariable:
    def __init__(self, on_setter_func):
        self.value = None
        self.name = None
        self.cls = None
        self.on_setter_func = on_setter_func

    def __set_name__(self, cls: Type[T], name: str):
        assert self.name is None
        assert self.cls is None
        self.name = name
        self.cls = cls
        self.set_value(self.value)

    def set_value(self, value):
        self.value = self.on_setter_func(self.cls, self.value, value)
        return self

    def __get__(self, instance, cls=None):
        print(self, instance, cls)
        if instance is not None:
            setattr(instance, self.name, InstanceClassVariable(self))
        return self.value


class InstanceClassVariable:
    def __init__(self, parent: ClassVariable):
        self.parent = parent

    def __get__(self, instance: T, cls: Optional[Type[T]] = None):
        print(self, instance, cls)
        return self.parent.value

    def __set__(self, instance, value):
        self.parent = self.parent.set_value(value)


def classvariable(func):
    return ClassVariable(func)


class SysLogHandler(logging.handlers.SysLogHandler):
    @classvariable
    def ident(cls, identifier: Optional[str] = None, old_identifier: Optional[str] = None):
        if not identifier:
            identifier = os.path.basename(sys.argv[0])
        if identifier != old_identifier:
            syslog.closelog()
        syslog.openlog(identifier, syslog.LOG_PID | syslog.LOG_NDELAY)
        return identifier

    def __init__(
        self,
        address=("localhost", logging.handlers.SYSLOG_UDP_PORT),
        facility=logging.handlers.SysLogHandler.LOG_USER,
        socktype=None,
    ):
        if address is None:
            logging.Handler.__init__(self)
            self.address = None
            self.facility = facility
            self.socktype = None
            self.socket = None
            # self.set_global_syslog()
            return
        super().__init__(address, facility, socktype)

    def emit(self, record):
        if self.address is None:
            msg = self.format(record)
            if self.append_nul:
                msg += "\000"
            facility = self.facility
            if isinstance(facility, str):
                facility = self.facility_names[facility]
            priority = self.mapPriority(record.levelname)
            if isinstance(priority, str):
                priority = self.priority_names[priority]
            syslog.syslog(facility | priority, msg)
            return
        return super().emit(record)

    def close(self):
        if self.address is None:
            self.acquire()
            try:
                syslog.closelog()
                logging.Handler.close(self)
            finally:
                self.release()
            return
        return super().close()


def getLogger(name=None):
    return get_logger(name)


def get_logger(name=None):
    logger = logging.getLogger(name)
    return AsyncLoggerView(logger)


class MessageSendFailure(OverflowError):
    logger: "AsyncLoggerView"
    record: logging.LogRecord

    def __init__(self, message, logger, record):
        super().__init__(message)
        self.logger = logger
        self.record = record

    def retry(self) -> Optional[asyncio.Future]:
        if self.record is None:
            return
        record = self.record
        task = self.logger.handle(record)
        self.record = None
        self.logger = None
        return task


ALWAYS_DONE_TRUE = asyncio.Future()
ALWAYS_DONE_TRUE.set_result(True)
ALWAYS_DONE_FALSE = asyncio.Future()
ALWAYS_DONE_FALSE.set_result(False)
ALWAYS_DONE_NONE = asyncio.Future()
ALWAYS_DONE_NONE.set_result(None)


class DeliveryPolicy(IntEnum):
    WAIT_FOR_BUFFER = 0  # if we don't have buffer space, wait until we do.
    DURABLE_EXCEPTION = 1  # Raise MessageSendFailure exception if unable to send timely.
    DURABLE_RETURN = 2  # Return MessageSendFailure if unable to send timely
    FIRE_AND_FORGET = 3  # If it sends, it sends, if it doesn't, whatever.


def iter_queue(queue: asyncio.Queue) -> Iterable[Any]:
    while True:
        try:
            element = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        yield element
        queue.task_done()


async def aiter_queue(queue: asyncio.Queue) -> AsyncIterable[Any]:
    while True:
        element = await queue.get()
        yield element


class AsyncLoggerView:
    __slots__ = (
        "_buffer_size",
        "_delivery_policies",
        "_drain_tasks",
        "_logger",
        "_buffer_sizes",
        "_queues",
        "default_buffer_size",
        "default_delivery_policy",
        "in_process_count",
        "sent_count",
    )

    DEFAULT_DELIVERY_POLICY = DeliveryPolicy.WAIT_FOR_BUFFER
    DEFAULT_BUFFER_SIZE = 10_000

    WAIT_FOR_BUFFER = DeliveryPolicy.WAIT_FOR_BUFFER
    DURABLE_EXCEPTION = DeliveryPolicy.DURABLE_EXCEPTION
    DURABLE_RETURN = DeliveryPolicy.DURABLE_RETURN
    FIRE_AND_FORGET = DeliveryPolicy.FIRE_AND_FORGET

    def __init__(
        self,
        logger,
        *,
        default_buffer_size: int = DEFAULT_BUFFER_SIZE,
        default_delivery_policy: DeliveryPolicy = DEFAULT_DELIVERY_POLICY,
    ):
        self.default_delivery_policy = default_delivery_policy
        self.default_buffer_size = default_buffer_size

        self.sent_count = 0
        self.in_process_count = 0

        self._logger = logger
        self._queues = {}
        self._drain_tasks = {}
        self._buffer_sizes = {}
        self._delivery_policies = {}

    def set_delivery_policy(
        self, policy: DeliveryPolicy, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        if loop is None:
            loop = asyncio.get_running_loop()
        self._delivery_policies[loop] = policy
        return self

    def get_delivery_policy(
        self, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> DeliveryPolicy:
        if loop is None:
            loop = asyncio.get_running_loop()
        with suppress(KeyError):
            return self._delivery_policies[loop]
        return self.default_delivery_policy

    def get_buffer_size(self, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> int:
        queue = self._get_queue_for(loop=loop)
        if queue is None:
            return self.default_buffer_size
        return queue.maxsize

    def set_buffer_size(self, new_size: int, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        assert new_size > 0, "size must be non-zero!"
        if loop is None:
            loop = asyncio.get_running_loop()
        self._buffer_sizes[loop] = new_size
        old_queue = self._get_queue_for(loop=loop)
        if old_queue is None or old_queue.maxsize == new_size:
            # yay, nothing to do!
            return
        self._start_drain_of_buffer(
            self._set_queue(
                self._resize_buffer(old_queue, asyncio.Queue(new_size), loop=loop), loop=loop
            ),
            loop=loop,
        )

    def _resize_buffer(
        self,
        old_queue: asyncio.Queue,
        new_queue: asyncio.Queue,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> asyncio.Queue:
        """ """
        if loop is None:
            loop = asyncio.get_running_loop()
        # Immediately stop the drain task!
        drain_task = self._get_drain_task_for(loop=loop)
        if drain_task and not drain_task.done():
            drain_task.cancel()
        # Now we can safely mess with its guts
        num_messages_to_forget = old_queue.qsize() - new_queue.maxsize
        # Crap, we need to resize this.
        policy = self.get_delivery_policy(loop=loop)
        for record, future in iter_queue(old_queue):
            if num_messages_to_forget > 0:
                num_messages_to_forget -= 1
                if policy is DeliveryPolicy.FIRE_AND_FORGET:
                    future.set_result(None)
                else:
                    error = MessageSendFailure(
                        "Lost message on queue resize, retry it!", self, record
                    )
                    if policy is DeliveryPolicy.DURABLE_EXCEPTION:
                        future.set_exception(error)
                    elif policy is DeliveryPolicy.DURABLE_RETURN:
                        future.set_result(error)
                continue
            new_queue.put_nowait((record, future))
        return new_queue

    def __getattr__(self, name):
        return getattr(self._logger, name)

    @property
    def buffer_size(self):
        return self._buffer_size

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        self._start_drain_of_buffer(self._ensure_task_queue(), loop=loop)
        return self

    async def __aexit__(self):
        await self.close()

    async def drain(self):
        loop = asyncio.get_running_loop()
        queue = self._get_queue_for(loop=loop)
        if queue is not None:
            return await queue.join()

    async def close(self):
        loop = asyncio.get_running_loop()
        task = self._get_drain_task_for(loop=loop)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return self

    @property
    def unsent_message_count(self) -> int:
        return sum(queue.qsize() for queue in self._queues.values())

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        # Get ephemeral loop in current running thread T
        with suppress(RuntimeError):
            return asyncio.get_running_loop()

    def _get_drain_task_for(
        self, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> Optional[asyncio.Queue]:
        if loop is None:
            loop = asyncio.get_running_loop()
        with suppress(KeyError):
            return self._drain_tasks[loop]

    def _start_drain_of_buffer(
        self, queue: asyncio.Queue, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> asyncio.Task:
        """
        This ensures we have an asyncio task that's constantly trying to
        drain the queue.
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        drain_task = self._get_drain_task_for()
        if drain_task and drain_task.done():
            # ensure our drain task is consumed silently.
            async def _wakeup_dead_drain():
                with suppress(asyncio.CancelledError):
                    await drain_task

            loop.create_task(_wakeup_dead_drain())
            drain_task = None
        if drain_task is None:
            drain_task = self._drain_tasks[loop] = loop.create_task(
                self._drain_queue_task(loop, queue)
            )
        return drain_task

    def _get_queue_for(self, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        if loop is None:
            loop = asyncio.get_running_loop()
        with suppress(KeyError):
            return self._queues[loop]

    def _set_queue(self, queue: asyncio.Queue, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        if loop is None:
            loop = asyncio.get_running_loop()
        self._queues[loop] = queue
        return queue

    def __repr__(self):
        msg = repr(self._logger)[1:-1]
        return (
            f"<{type(self).__qualname__} logger={msg!r} "
            f"sent_count={self.sent_count} in_process_count={self.in_process_count} "
            f"unsent_message_count={self.unsent_message_count}>"
        )

    def _ensure_task_queue(
        self, *, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> asyncio.Queue:
        if loop is None:
            loop = asyncio.get_running_loop()
        queue = self._get_queue_for(loop=loop)
        if queue is not None:
            return queue
        try:
            buffer_size = self._buffer_sizes[loop]
        except KeyError:
            buffer_size = self.default_buffer_size
        return self._set_queue(asyncio.Queue(maxsize=buffer_size), loop=loop)

    async def _handle_simple(
        self, record: logging.LogRecord, queue: asyncio.Queue, future: asyncio.Future
    ):
        # Wait for the record to go into the butfer, then wait on it to signal
        # end to end completion
        await queue.put((record, future))
        return await future

    def handle(self, record: logging.LogRecord) -> Union[asyncio.Future, asyncio.Task]:
        loop = self.loop
        if not loop:
            self.handle(record)
            return ALWAYS_DONE_NONE

        future = loop.create_future()
        queue = self._ensure_task_queue(loop=loop)
        self._start_drain_of_buffer(queue, loop=loop)
        policy = self.get_delivery_policy(loop=loop)

        if policy is DeliveryPolicy.WAIT_FOR_BUFFER:
            return loop.create_task(self._handle_simple(record, queue, future))
        return self._handle_complex(record, queue, future, policy)

    def _handle_complex(
        self,
        record: logging.LogRecord,
        queue: asyncio.Queue,
        future: asyncio.Future,
        policy: DeliveryPolicy,
    ) -> asyncio.Future:
        """
        This case deals with potentially losing records.
        """
        lost_records = None
        lost_record: Tuple[logging.LogRecord, asyncio.Future]
        while True:
            try:
                queue.put_nowait((record, future))
            except asyncio.QueueFull:
                if lost_records is None:
                    lost_records = []
                with suppress(asyncio.QueueEmpty):
                    lost_record = queue.get_nowait()
                    queue.task_done()
                    lost_records.append(lost_record)
            else:
                break
        if lost_records:
            for record, is_done_task in lost_records:
                if policy is DeliveryPolicy.FIRE_AND_FORGET:
                    is_done_task.set_result(None)
                else:
                    error = MessageSendFailure("Queue overflown, message lost", self, record)
                    if policy is DeliveryPolicy.DURABLE_EXCEPTION:
                        is_done_task.set_exception(error)
                    elif policy is DeliveryPolicy.DURABLE_RETURN:
                        is_done_task.set_result(error)
        return future

    async def _drain_queue_task(self, loop, queue):
        this = asyncio.current_task()
        tasks: Dict[asyncio.Task, asyncio.Future] = {}
        task: asyncio.Future

        def _on_done(task: asyncio.Task) -> None:
            future = tasks.pop(task)
            self.sent_count += 1
            self.in_process_count -= 1
            future.set_result(None)
            # I can accept more work now
            queue.task_done()

        try:
            async for record, is_done_task in aiter_queue(queue):
                self.in_process_count += 1
                task = loop.run_in_executor(None, self._logger.handle, record)
                task.add_done_callback(_on_done)
                tasks[task] = is_done_task

        except asyncio.CancelledError as e:
            for _, future in iter_queue(queue):
                future.set_exception(e)
                queue.task_done()
            kill_tasks = []
            for task in tasks:
                if not task.done():
                    task.remove_done_callback(_on_done)
                    task.cancel()
                    kill_tasks.append(task)
            if kill_tasks:
                await asyncio.gather(*kill_tasks, return_exceptions=True)
            if loop in self._queues and self._queues[loop] is queue:
                del self._queues[loop]
            if loop in self._drain_tasks and self._drain_tasks[loop] is this:
                del self._drain_tasks[loop]
            raise e from None

    def _log(
        self,
        level,
        msg,
        args,
        exc_info=None,
        extra=None,
        stack_info=False,
        stacklevel=2,
        *,
        bypass_enabled_check: bool = False,
    ) -> asyncio.Future:
        """
        Low-level logging routine which creates a LogRecord and then calls
        all the handlers of this logger to handle the record.

        returns a future signalling the publish status.
        """
        if not self.isEnabledFor(level) and not bypass_enabled_check:
            return ALWAYS_DONE_FALSE
        sinfo = None
        if logging._srcfile:
            try:
                fn, lno, func, sinfo = self.findCaller(stack_info, stacklevel)
            except ValueError:
                fn, lno, func = "(unknown file)", 0, "(unknown function)"
        else:
            fn, lno, func = "(unknown file)", 0, "(unknown function)"
        if exc_info:
            if isinstance(exc_info, BaseException):
                exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
            elif not isinstance(exc_info, tuple):
                exc_info = sys.exc_info()
        record = self.makeRecord(self.name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)
        return self.handle(record)

    def debug(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'DEBUG'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.debug("Houston, we have a %s", "thorny problem", exc_info=1)
        """
        return self._log(DEBUG, msg, args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'INFO'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.info("Houston, we have a %s", "interesting problem", exc_info=1)
        """
        return self._log(INFO, msg, args, **kwargs)

    def warn(self, *args, **kwargs):
        return self.warning(*args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'WARNING'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.warning("Houston, we have a %s", "bit of a problem", exc_info=1)
        """
        return self._log(WARNING, msg, args, **kwargs)

    def error(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'ERROR'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.error("Houston, we have a %s", "major problem", exc_info=1)
        """
        return self._log(ERROR, msg, args, **kwargs)

    def exception(self, msg, *args, exc_info=True, **kwargs):
        """
        Convenience method for logging an ERROR with exception information.
        """
        return self.error(msg, *args, exc_info=exc_info, **kwargs)

    def critical(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'CRITICAL'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.critical("Houston, we have a %s", "major disaster", exc_info=1)
        """
        return self._log(CRITICAL, msg, args, **kwargs)

    def fatal(self, msg, *args, **kwargs):
        """
        Don't use this method, use critical() instead.
        """
        return self.critical(msg, *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        """
        Log 'msg % args' with the integer severity 'level'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.log(level, "We have a %s", "mysterious problem", exc_info=1)
        """
        if not isinstance(level, int):
            if raiseExceptions:
                raise TypeError("level must be an integer")
            else:
                return
        return self._log(level, msg, args, **kwargs, bypass_enabled_check=True)
