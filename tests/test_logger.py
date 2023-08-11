import logging
from powerscout.logger import get_logger


logger = get_logger("test")
logger._buffer_size = 1
logger.addHandler(logging.StreamHandler())
logger.handlers[0].setLevel(logging.DEBUG)

logger.setLevel(logging.DEBUG)

logger.info("ready?")


async def test_logger():
    # logger.set_delivery_policy(logger.DURABLE_RETURN)
    logger.set_buffer_size(1)
    await logger.info("hello!1")
    await logger.info("hello!2")
    await logger.info("hello!3")
    error = await logger.info("goodbye!")
    if error:
        print("overflow!", logger)
        await error.retry()
    print("goodbye sent", logger)
    try:
        1 / 0
    except Exception:
        print("before exc sending", logger)
        await logger.exception("wtf")
        print("after exc sending", logger)
    print("last", logger.sent_count, logger.unsent_message_count)
