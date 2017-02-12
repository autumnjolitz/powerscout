import logging
import time

import redis

from . import route
from .. import config, _json


logger = logging.getLogger(__name__)


db = redis.from_url(config.config['REDIS_URI'])


@route('/apc/status')
def apc_status():
    data = db.hgetall('apc_status')
    if not data:
        return {
            'msg': 'No data',
            'code': -1,
        }
    ups_status, timestamp = _json.loads(data[b'data']), float(data[b'timestamp'])
    data = {
        'msg': 'Ok',
        'code': 0,
        'data': ups_status,
        'timestamp': timestamp
    }
    if time.time() - timestamp > 300:
        data['msg'] = 'Stale data'
        data['code'] = -2
    return data
