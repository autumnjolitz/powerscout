import logging
import functools
import xml.etree.ElementTree
import io
import time
import struct
import datetime
import pickle
import redis
import socket
from ..templates import render_template


REGISTRY = {}
YEAR_2000_OFFSET = \
    (datetime.datetime(2000, 1, 1) - datetime.datetime.fromtimestamp(0)).total_seconds()
db = redis.Redis()

logger = logging.getLogger(__name__)


def route(path):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped(request):
            kwargs = request.match_dict
            try:
                return func(request, **kwargs)
            except Exception:
                logger.exception('Unhandled exception for {}'.format(func.__name__))
                return request.Response(code=500, text='Bad error')
        assert path not in REGISTRY, 'Cannot register {} path'.format(path)
        REGISTRY[path] = wrapped
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

def handle(key, value):
    if key.endswith(b'timestamp'):
        return key, float(value)
    return key, value.decode('ascii')


@route('/')
def index(request):
    meters = db.smembers('meters')
    with db.pipeline() as p:
        for meter in meters:
            p.hgetall(meter)
        meters = {
            meter.split(b'-', 1)[1].decode('ascii'): {
                key.decode('ascii'): value
                for key, value in (handle(key, value) for key, value in data.items())
            } for meter, data in zip(meters, p.execute())
        }
    return request.Response(
        mime_type='text/html',
        text=render_template('index.html', meters=meters))

@route('/fastpoll/{mac_id}')
@route('/fastpoll/{mac_id}/{seconds}')
def fastpoll(request, mac_id, seconds=4):
    seconds = int(seconds)
    assert seconds > 0, 'wtf'
    assert seconds <= 255, 'wtf'
    if mac_id.startswith('meter-'):
        mac_id = mac_id[7:]
    if not db.sismember('meters', mac_id):
        return request.Response(code=400, json={
            'code': -1,
            'message': f'{mac_id} is not recognized'
            })
    db.rpush(f'{mac_id}-commands', f'fastpoll|{seconds}')
    return request.Response(json={
            'code': 0,
            'message': 'Ok'
        })



@route('/ingest')
def consume(request):
    body = io.BytesIO(request.body)
    body.seek(0)
    try:
        root = xml.etree.ElementTree.parse(body).getroot()
        eagle_id = root.attrib['macId']
        eagle_timestamp = int(root.attrib['timestamp'][:-1], 10)
        body = {
            element.tag: {
                leaf.tag: leaf.text for leaf in element
            } for element in root
        }
    except Exception:
        logger.exception('Fault in decoding {!s}'.format(request.body))
        return request.Response(code=400)

    now = time.time()
    if 'InstantaneousDemand' in body:
        item = body['InstantaneousDemand']
        instant_demand = \
            int(item['Demand'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)
        timestamp_s = int(item['TimeStamp'], 16) + YEAR_2000_OFFSET

        name = item['MeterMacId']
        key = f'meter-{name}'
        with db.pipeline() as p:
            p.hset(key, 'instand_demand', instant_demand)
            p.hset(key, 'instant_demand_timestamp', timestamp_s)
            p.sadd('meters', key)
            p.execute()

        post_metric(f'meters.{name}.instant_demand', instant_demand, timestamp_s)
        post_metric(f'meters.{name}.instant_demand.watts', instant_demand * 1000., timestamp_s)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.eagle.device',
                    eagle_timestamp - timestamp_s)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.server.eagle',
                    now - eagle_timestamp)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.server.device', now - timestamp_s)
        post_metric(f'meters.{name}.instant_demand.tx_info.ping', 1)
        del timestamp_s

    if 'CurrentSummationDelivered' in body:
        item = body['CurrentSummationDelivered']
        timestamp_s = int(item['TimeStamp'], 16) + YEAR_2000_OFFSET
        utility_kwh_delivered = \
            int(item['SummationDelivered'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)
        utility_kwh_sent = \
            int(item['SummationReceived'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)

        name = item['MeterMacId']
        key = f'meter-{name}'
        with db.pipeline() as p:
            p.hset(key, 'sum_delivered', utility_kwh_delivered)
            p.hset(key, 'sum_received', utility_kwh_sent)
            p.sadd('meters', key)
            p.execute()

        post_metric(f'meters.{name}.current_sum.delivered', utility_kwh_delivered)
        post_metric(f'meters.{name}.current_sum.received', utility_kwh_sent)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.eagle.device',
                    eagle_timestamp - timestamp_s)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.server.eagle', now - eagle_timestamp)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.server.device', now - timestamp_s)
        post_metric(f'meters.{name}.current_sum.tx_info.ping', 1)

    if 'FastPollStatus' in body:
        item = body['FastPollStatus']
        name = item['MeterMacId']
        key = f'meter-{name}'

        period_to_poll = int(body['Frequency'], 16)
        end = int(body['EndTime'], 16) + YEAR_2000_OFFSET
        with db.pipeline() as p:
            p.hset(key, 'fast_poll_period_s', period_to_poll)
            p.hset(key, 'fast_poll_end_utc_timestamp', end)
            p.hset(key, 'fast_poll_timestamp', now)
            p.execute()
        post_metric(f'meters.{name}.fast_poll.tx_info.ping'.format(item['MeterMacId']), 1)
    commands = db.lrange(f'{eagle_id}-commands', 0, -1)
    num = len(commands)
    if not commands:
        return request.Response(text='')
    queue = []
    commands = {
        command: args
        for command, args in (x.decode('ascii').split('|', 1) for x in commands)
    }
    for command, value in commands.items():
        if command == 'fastpoll':
            period = hex(int(value)).upper()
            duration = 15
            if period == 0:
                duration = 0
            duration = hex(duration).upper()
            queue.append(f'''<RavenCommand>
<Name>set_fast_poll</Name>
 <MacId>{eagle_id}</MacId>
<Frequency>{period}</Frequency>
<Duration>{duration}</Duration>
</RavenCommand>''')
    if not queue:
        return request.Response(text='')
    with db.pipeline() as p:
        for _ in range(num):
            p.lpop()
        p.execute()
    if queue[1:]:
        db.lpush(f'{eagle_id}-commands', *queue[1:])
    logger.info('Sending back {}'.format(queue[0]))
    return request.Response(text=queue[0])
