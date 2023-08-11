import logging
import os.path
import pprint
import functools

from . import create_app
from . import config
from .config import load_config, load_environment_variables, PREFIX, ensure_config_schema
from sanic.worker.loader import AppLoader
from sanic import Sanic

logger = logging.getLogger("powerscout")


def main(port=8080, host="0.0.0.0", *, debug=False):
    logger.debug("Application config: {}".format(pprint.pformat(config.config)))
    logger.debug("Environ: {}".format(pprint.pformat(os.environ)))

    loader = AppLoader(factory=functools.partial(create_app, config=config.config, debug=debug))
    app = loader.load()
    app.prepare(port=port, host=host, dev=debug)
    Sanic.serve(primary=app, app_loader=loader)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--debug", action="store_true", default=False)

    subparsers = parser.add_subparsers(help="Choose a launch method.")
    env_mode = subparsers.add_parser("env", help="From environment")
    env_mode.set_defaults(mode="env")

    config_mode = subparsers.add_parser("from", help="From config file")
    config_mode.set_defaults(mode="file")
    config_mode.add_argument("config_path", nargs="?", default=None, type=str, metavar="FILE")

    cli = subparsers.add_parser("cli", help="From cli")
    cli.set_defaults(mode="cli")
    cli.add_argument("port", default=8080, type=int, nargs="?")
    cli.add_argument("host", nargs="?", default="127.0.0.1", type=str, metavar="IP")

    cli_from = {}
    for key in config.config:
        arg_type = config.config_schema.get(key, str)
        if isinstance(arg_type, tuple):
            arg_type, _ = arg_type
        argified_key = key.replace("_", "-").lower()

        cli_from[argified_key] = key
        cli.add_argument(
            "--{}".format(argified_key),
            type=arg_type,
            default=config.config[key],
            help="defaults to {!r}".format(config.config[key]),
        )

    args = parser.parse_args()
    if not getattr(args, "mode", None):
        raise ValueError("No command specified")
    if args.mode == "file":
        config_path = os.path.expanduser(args.config_path)
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"{config_path} is not found")
        load_config(config_path)
        del args.config_path
    elif args.mode == "env":
        if f"{PREFIX}CONFIG_PATH" in os.environ:
            load_config(os.path.expanduser(os.environ[f"{PREFIX}CONFIG_PATH"]))
        load_environment_variables()
    elif args.mode == "cli":
        for arg_key, config_key in cli_from.items():
            config.config[config_key] = getattr(args, arg_key.replace("-", "_"))
            delattr(args, arg_key.replace("-", "_"))
        load_environment_variables()

    del args.mode
    ensure_config_schema()
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.DEBUG)

    main(**args.__dict__)
