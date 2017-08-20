import logging
import os.path
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
import pprint

from . import make_app
from .routes import apc_routes, eagle_routes
from . import config
from .config import load_config, load_environment_variables, PREFIX
from .services.apc import update_apc_status

logger = logging.getLogger('powerscout')

logging.basicConfig(level=logging.DEBUG)


APC_WORKER = multiprocessing.Event()


def apc_worker():
    logger.info('Started APC worker')
    APC_WORKER.set()
    while APC_WORKER.is_set():
        logger.debug('Updating status')
        try:
            update_apc_status()
        except Exception as e:
            logger.exception('Unable to update the APC status!')
        time.sleep(2)


def main(port=8080, host='0.0.0.0'):
    if f'{PREFIX}CONFIG_PATH' in os.environ:
        load_config(os.path.expanduser(os.environ[f'{PREFIX}CONFIG_PATH']))
    load_environment_variables()
    logger.debug('Application config: {}'.format(pprint.pformat(config.config)))
    logger.debug('Environ: {}'.format(pprint.pformat(os.environ)))

    app = make_app()
    app.register_blueprint(apc_routes)
    app.register_blueprint(eagle_routes)

    with ProcessPoolExecutor(1) as exe:
        future = exe.submit(apc_worker)
        try:
            app.run(port=port, host=host)
        except KeyboardInterrupt:
            APC_WORKER.clear()
            future.cancel()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', nargs='?', default=None, type=str, metavar='FILE')
    args = parser.parse_args()
    if args.config_path:
        os.environ[f'{PREFIX}CONFIG_PATH'] = args.config_path
    main()
