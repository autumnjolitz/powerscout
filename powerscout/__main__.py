import logging
import os.path
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
import pprint
import platform

from . import make_app
from .routes import apc_routes, eagle_routes
from . import config
from .config import load_config, load_environment_variables, PREFIX, ensure_config_schema
from .services.apc import update_apc_status

logger = logging.getLogger('powerscout')

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

SYSLOG_KWARGS = {'facility': logging.handlers.SysLogHandler.LOG_LOCAL0}
SYSLOG_FORMAT = logging.Formatter(
    '%(name)s: [%(asctime)s] [PID %(process)d] [%(levelname)s] %(message)s')

if os.path.exists('/dev/log'):
    SYSLOG_KWARGS['address'] = '/dev/log'
elif platform.system() == 'Darwin':
    SYSLOG_KWARGS['address'] = "/var/run/syslog"


def main(port=8080, host='0.0.0.0', *, debug=False):
    logger.setLevel(logging.INFO)
    if debug:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    handler = logging.handlers.SysLogHandler(**SYSLOG_KWARGS)
    handler.setFormatter(SYSLOG_FORMAT)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)

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
    parser.add_argument('-d', '--debug', action='store_true', default=False)

    subparsers = parser.add_subparsers(help='Choose a launch method.')
    env_mode = subparsers.add_parser('env', help='From environment')
    env_mode.set_defaults(mode='env')

    config_mode = subparsers.add_parser('from', help='From config file')
    config_mode.set_defaults(mode='file')
    config_mode.add_argument('config_path', nargs='?', default=None, type=str, metavar='FILE')

    cli = subparsers.add_parser('cli', help='From cli')
    cli.set_defaults(mode='cli')
    cli.add_argument('port', default=8080, type=int, nargs='?')
    cli.add_argument('host', nargs='?', default='127.0.0.1', type=str, metavar='IP')

    cli_from = {}
    for key in config.config:
        arg_type = config.config_schema.get(key, str)
        if isinstance(arg_type, tuple):
            arg_type, _ = arg_type
        argified_key = key.replace('_', '-').lower()

        cli_from[argified_key] = key
        cli.add_argument(
            '--{}'.format(argified_key),
            type=arg_type, default=config.config[key],
            help='defaults to {!r}'.format(config.config[key]))

    args = parser.parse_args()
    print(args, dir(args))
    if args.mode == 'file':
        config_path = os.path.expanduser(args.config_path)
        if not os.path.exists(config_path):
            raise FileNotFoundError(f'{config_path} is not found')
        load_config(config_path)
        del args.config_path
    elif args.mode == 'env':
        if f'{PREFIX}CONFIG_PATH' in os.environ:
            load_config(os.path.expanduser(os.environ[f'{PREFIX}CONFIG_PATH']))
        load_environment_variables()
    elif args.mode == 'cli':
        for arg_key, config_key in cli_from.items():
            config.config[config_key] = getattr(args, arg_key.replace('-', '_'))
            delattr(args, arg_key.replace('-', '_'))

    del args.mode
    ensure_config_schema()

    main(**args.__dict__)
