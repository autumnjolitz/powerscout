import os
import json

import yaml

PREFIX = "POWERSCOUT_"
PREFIX_LEN = len(PREFIX)

config = {
    "GRAPHITE_HOSTNAME": "localhost",
    "GRAPHITE_PORT": 2004,
    "POSTGRES_URI": "",
    "APC_ENDPOINT": "",
    "APC_USERNAME": "",
    "APC_PASSWORD": "",
    "REDIS_URI": "redis://localhost:6379/0",
    "GRAPHITE_FLUSH_PERIOD_SECONDS": 10,
    "MAX_GRAPHITE_QUEUE_LENGTH": 10,
    "LOWEST_GRID_VAC_ALLOWED": 110,  # US
}


def ensure_protocol(item):
    if not item.startswith("http"):
        item = f"http://{item}"
    if item.endswith("/"):
        item = item[:-1]
    return item


config_schema = {
    "GRAPHITE_PORT": int,
    "APC_ENDPOINT": (str, ensure_protocol),
}

IDENTITY = lambda x: x


def load_config(path):
    with open(path, "rb") as fh:
        if path.lower().endswith(".yml"):
            config.update(yaml.load(fh, Loader=yaml.SafeLoader))
        elif path.lower().endswith(".json"):
            config.update(json.load(fh))
        else:
            extension = os.path.basename(path).rsplit(".", 1)[-1]
            raise ValueError(f'Unrecognized extension ".{extension}". Use `.json` or `.yml`')
    return config


def ensure_config_schema():
    for key in config.keys() & config_schema.keys():
        value = config[key]
        type = config_schema[key]
        validation_func = IDENTITY
        if isinstance(type, tuple):
            type, validation_func = type
        if not isinstance(value, type):
            value = type(value)
        config[key] = validation_func(value)


def load_environment_variables():
    environ_keys = frozenset(
        key[PREFIX_LEN:] for key in os.environ.keys() if key.startswith(PREFIX)
    )
    for key in environ_keys & config.keys():
        value = os.environ[f"{PREFIX}{key}"]
        if key in config_schema and not isinstance(value, config_schema[key]):
            value = config_schema[key](value)
        config[key] = value
    return config
