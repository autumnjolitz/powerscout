import logging
import pickle
import socket
import struct
import time

from .. import config


t_s = 0
queue = None
conn = None
logger = logging.getLogger(__name__)


def post_metric(path, value, timestamp=None):
    '''
    [(path, (timestamp, value)), ...
    '''
    global t_s, conn, queue
    if queue is None:
        queue = []

    if conn is None:
        conn = socket.create_connection(
            (config.config['GRAPHITE_HOSTNAME'], config.config['GRAPHITE_PORT']))

    # time.time() -> UTC Epoch by definition, so it works with
    # Graphite as-is
    queue.append((path, (timestamp or time.time(), value,)))

    if queue and (
            time.time() - t_s > config.config['GRAPHITE_FLUSH_PERIOD_SECONDS'] or
            len(queue) > config.config['MAX_GRAPHITE_QUEUE_LENGTH']):

        payload = pickle.dumps(queue, protocol=2)
        queue[:] = []

        header = struct.pack("!L", len(payload))
        message = header + payload
        try:
            conn.send(message)
        except Exception:
            conn = socket.create_connection(
                (config.config['GRAPHITE_HOSTNAME'], config.config['GRAPHITE_PORT']))
        t_s = time.time()
