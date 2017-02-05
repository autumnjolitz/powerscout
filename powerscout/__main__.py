import logging

from japronto import Application
from .routes import REGISTRY

logging.basicConfig(level=logging.DEBUG)


def main():
    app = Application()

    for path, func in REGISTRY.items():
        app.router.add_route(path, func)

    app.run()

if __name__ == '__main__':
    main()
