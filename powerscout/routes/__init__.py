import logging
import functools
import xml.etree.ElementTree
import io
import time
import struct
import pickle
import socket

REGISTRY = {}

logger = logging.getLogger(__name__)


def route(path):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            return func(*args, **kwargs)
        assert path not in REGISTRY, 'Cannot register {} path'.format(path)
        REGISTRY[path] = func
        return wrapped
    return wrapper
'''
<InstantaneousDemand>
    <DeviceMacId>0x00158d0000000004</DeviceMacId>
    <MeterMacId>0x00178d0000000004</MeterMacId>
    <TimeStamp>0x185adc1d</TimeStamp>
    <Demand>0x001738</Demand>
    <Multiplier>0x00000001</Multiplier>
    <Divisor>0x000003e8</Divisor>
    <DigitsRight>0x03</DigitsRight>
    <DigitsLeft>0x00</DigitsLeft>
    <SuppressLeadingZero>Y</SuppressLeadingZero>
</InstantaneousDemand>
'''

t_s = time.time()
queue = []
conn = socket.create_connection(('nyx.lan', 2004))


def post_metric(path, value, timestamp=None):
    '''
    [(path, (timestamp, value)), ...
    '''
    global t_s, conn
    queue.append((path, (timestamp or time.time(), value,)))

    if queue and (time.time() - t_s > 10 or len(queue) > 10):
        payload = pickle.dumps(queue, protocol=2)
        queue[:] = []

        header = struct.pack("!L", len(payload))
        message = header + payload
        try:
            conn.send(message)
        except Exception:
            conn = socket.create_connection(('nyx.lan', 2004))
        t_s = time.time()


@route('/ingest')
def consume(request):
    body = io.BytesIO(request.body)
    body.seek(0)
    try:
        e = xml.etree.ElementTree.parse(body).getroot()
    except Exception:
        logger.exception('Fault in decoding {!s}'.format(request.body))
    else:
        logger.info('Got body: {}'.format(request.body))
        for item in e:
            if item.tag == 'InstantaneousDemand':
                item = {
                    x.tag: x.text for x in item
                }
                # time_stamp = int(item['TimeStamp'], 16)
                instant_demand = \
                    int(item['Demand'], 16) * \
                    int(item['Multiplier'], 16) / int(item['Divisor'], 16)
                post_metric('power.instant_demand', instant_demand)
    return request.Response(text='')
