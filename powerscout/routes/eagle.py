import logging
import functools
import xml.etree.ElementTree
import io
import time
import datetime
import json
import collections
from sanic.blueprints import Blueprint
from sanic.response import text as as_text, json as as_json
from ..services.graphite import post_metric

YEAR_2000_OFFSET = \
    (datetime.datetime(2000, 1, 1) - datetime.datetime.utcfromtimestamp(0)).total_seconds()

logger = logging.getLogger(__name__)
eagle = Blueprint(__name__)


def handle(key, value):
    if key.endswith(b'timestamp'):
        if isinstance(value, bytes):
            value = value.decode('ascii')
        if isinstance(value, str) and value.startswith('0x'):
            value = int(value, 16)
        return key, float(value)
    return key, value.decode('ascii')


@eagle.route('/')
async def index(request):
    meters = request.redis.smembers('meters')
    with request.redis.pipeline() as p:
        for meter in meters:
            p.hgetall(meter)
        meters = {
            meter.split(b'-', 1)[1].decode('ascii'): {
                key.decode('ascii'): value
                for key, value in (handle(key, value) for key, value in data.items())
            } for meter, data in zip(meters, p.execute())
        }
    return await request.async_render_template('index.html', meters=meters)


@eagle.route('/fastpoll/<mac_id>')
@eagle.route('/fastpoll/<mac_id>/<seconds>')
def fastpoll(request, mac_id, seconds=4):
    seconds = int(seconds)
    assert seconds > 0, 'wtf'
    assert seconds <= 255, 'wtf'

    if not request.redis.sismember('meters', 'meter-{}'.format(mac_id)):
        return as_json({
            'code': -1,
            'message': f'{mac_id} is not recognized'
            }, 400)
    try:
        eagle_id = request.redis.hget('eagles', mac_id).decode('ascii')
    except AttributeError:
        return as_json({
            'code': -2,
            'message': 'Eagle mapping not ready. Cannot reverse.'
            }, 400)
    request.redis.rpush(f'{eagle_id}-commands', f'fastpoll|{seconds}')
    return as_json({
            'code': 0,
            'message': 'Ok'
        })


def handle_exc(func):
    @functools.wraps(func)
    def wrapped(request):
        try:
            return func(request)
        except Exception:
            logger.exception('Unhandled exception!')
            return request.Response(code=500)
    return wrapped


FAST_POLL_FRAG = '''<RavenCommand>
<Name>set_fast_poll</Name>
 <MacId>{eagle_id}</MacId>
<Frequency>{period}</Frequency>
<Duration>{duration}</Duration>
</RavenCommand>'''


@eagle.route('/ingest')
@handle_exc
def consume(request):
    body = io.BytesIO(request.body)
    body.seek(0)
    try:
        root = xml.etree.ElementTree.parse(body).getroot()
        eagle_id = root.attrib['macId']
        # Requirement:
        #   - Set the Eagle in UTC timezone
        eagle_timestamp = int(root.attrib['timestamp'][:-1], 10)
        request.redis.set('eagle_utc_timestamp', root.attrib['timestamp'][:-1])

        eagle_timestamp_utc = datetime.datetime.fromtimestamp(eagle_timestamp).timestamp()
        body = {
            element.tag: {
                leaf.tag: leaf.text for leaf in element
            } for element in root
        }
    except Exception:
        logger.exception('Fault in decoding {!s}'.format(request.body))
        return as_text('', 400)

    # Left in as a teaching moment:
    #   The below is wrong:
    # utc_now = datetime.datetime.utcnow().timestamp()
    #   The correction is:
    # utc_now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).timestamp()
    #   The solves:
    # assert abs(time.time() - utc_now) < 1.5
    utc_now = time.time()
    with request.redis.pipeline() as p:
        p.rpush('recent_eagle_pushes', json.dumps(body))
        p.ltrim('recent_eagle_pushes', -100, -1)
        p.execute()

    if 'InstantaneousDemand' in body:
        item = body['InstantaneousDemand']
        instant_demand = \
            int(item['Demand'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)

        timestamp_utc = int(item['TimeStamp'], 16) + YEAR_2000_OFFSET

        name = item['MeterMacId']
        key = f'meter-{name}'
        with request.redis.pipeline() as p:
            p.hset(key, 'instand_demand', instant_demand)
            p.hset(key, 'instant_demand_timestamp', timestamp_utc)
            p.hset(key, 'instant_demand_raw_timestamp', item['TimeStamp'])
            p.sadd('meters', key)
            p.hset('eagles', name, eagle_id)
            p.execute()

        # Graphite considers UTC vectored data
        post_metric(f'meters.{name}.instant_demand.kilowatts', instant_demand, timestamp_utc)
        post_metric(f'meters.{name}.instant_demand.watts', instant_demand * 1000., timestamp_utc)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.eagle.device',
                    eagle_timestamp_utc - timestamp_utc)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.server.eagle',
                    utc_now - eagle_timestamp_utc)
        post_metric(f'meters.{name}.instant_demand.tx_info.delay.server.device',
                    utc_now - timestamp_utc)
        post_metric(f'meters.{name}.instant_demand.tx_info.ping', 1)
        del timestamp_utc

    if 'PriceCluster' in body:
        item = body['PriceCluster']
        name = item['MeterMacId']
        timestamp_utc = int(item['TimeStamp'], 16) + YEAR_2000_OFFSET
        current_tier = item['RateLabel']
        price = int(item['Price'], 16) / int('1' + ('0'*int(item['TrailingDigits'], 16)))

        key = f'meter-{name}'
        with request.redis.pipeline() as p:
            p.hset(key, 'current_price', price)
            p.hset(key, 'current_tier', current_tier)
            p.sadd('meters', key)
            p.hset('eagles', name, eagle_id)
            p.execute()
        post_metric(f'meters.{name}.price_cluster.price', price, timestamp_utc)
        post_metric(f'meters.{name}.price_cluster.tier', current_tier, timestamp_utc)

    if 'CurrentSummationDelivered' in body:
        item = body['CurrentSummationDelivered']
        logger.info('Current debug: {}'.format(item))
        timestamp_utc = int(item['TimeStamp'], 16) + YEAR_2000_OFFSET

        utility_kwh_delivered = \
            int(item['SummationDelivered'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)
        utility_kwh_sent = \
            int(item['SummationReceived'], 16) * \
            (int(item['Multiplier'], 16) or 1) / (int(item['Divisor'], 16) or 1)

        name = item['MeterMacId']
        key = f'meter-{name}'
        with request.redis.pipeline() as p:
            p.hset(key, 'sum_delivered', utility_kwh_delivered)
            p.hset(key, 'sum_received', utility_kwh_sent)
            p.sadd('meters', key)
            p.hset('eagles', name, eagle_id)
            p.execute()

        post_metric(f'meters.{name}.current_sum.delivered', utility_kwh_delivered, timestamp_utc)
        post_metric(f'meters.{name}.current_sum.received', utility_kwh_sent, timestamp_utc)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.eagle.device',
                    eagle_timestamp_utc - timestamp_utc)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.server.eagle',
                    utc_now - eagle_timestamp_utc)
        post_metric(f'meters.{name}.current_sum.tx_info.delay.server.device',
                    utc_now - timestamp_utc)
        post_metric(f'meters.{name}.current_sum.tx_info.ping', 1)

    if 'FastPollStatus' in body:
        item = body['FastPollStatus']
        name = item['MeterMacId']
        key = f'meter-{name}'

        period_to_poll = int(body['Frequency'], 16)
        end = int(body['EndTime'], 16) + YEAR_2000_OFFSET
        with request.redis.pipeline() as p:
            p.hset(key, 'fast_poll_period_s', period_to_poll)
            p.hset(key, 'fast_poll_end_utc_timestamp', end)
            p.hset(key, 'fast_poll_timestamp_utc', utc_now)
            p.hset('eagles', name, eagle_id)
            p.execute()
        post_metric(f'meters.{name}.fast_poll.tx_info.ping'.format(item['MeterMacId']), 1)
    commands = request.redis.lrange(f'{eagle_id}-commands', 0, -1)
    num = len(commands)
    if not commands:
        return as_text('')

    queue = []
    commands = {
        command: args
        for command, args in (x.decode('ascii').split('|', 1) for x in commands)
    }
    for command, value in commands.items():
        if command == 'fastpoll':
            period = '0x{}'.format(hex(int(value))[2:])
            duration = 15
            if period == 0:
                duration = 0
            duration = '0x{}'.format(hex(duration)[2:])
            queue.append(FAST_POLL_FRAG.format_map(collections.ChainMap(locals(), globals())))
    if queue:
        request.redis.delete(f'{eagle_id}-commands')
        if queue[1:]:
            request.redis.rpush(f'{eagle_id}-commands', *queue[1:])
        logger.info('Sending back {}'.format(queue[0]))
    try:
        value = queue[0]
    except IndexError:
        value = ''
    return as_text(value)
