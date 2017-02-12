import logging
import os.path

from japronto import Application
from .routes import REGISTRY
from .config import load_config, load_environment_variables, PREFIX

logging.basicConfig(level=logging.DEBUG)


def main():
    if f'{PREFIX}CONFIG_PATH' in os.environ:
        load_config(os.path.expanduser(os.environ[f'{PREFIX}CONFIG_PATH']))
    load_environment_variables()

    app = Application()

    for path, func in REGISTRY.items():
        app.router.add_route(path, func)

    app.run()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', nargs='?', default=None, type=str, metavar='FILE')
    args = parser.parse_args()
    if args.config_path:
        os.environ[f'{PREFIX}CONFIG_PATH'] = args.config_path
    main()
