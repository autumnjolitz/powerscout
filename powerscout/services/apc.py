import logging
import string
import pickle
from urllib.parse import urlparse
import time

import redis
import requests
from bs4 import BeautifulSoup

from .. import config
from .. import _json
from ..services.graphite import post_metric

logger = logging.getLogger(__name__)


db = redis.from_url(config.config['REDIS_URI'])


def check_status(session, status_url=None):
    if status_url:
        logger.debug(f'Attempting prior URI: {status_url}')
        status_page = session.get(f'{config.config["APC_ENDPOINT"]}{status_url}')
        status_page_url = urlparse(status_page.url)
        if status_url != status_page_url.path:
            logger.warn(f'{status_url} != {status_page_url.path}. Clearing cookies and URI.')
            db.delete('apc_session')
            session.cookies.clear()
            return check_status(session, None)
        logger.debug(f'Got {status_page.url}')
        return status_page, status_page_url

    logger.info('config.config: {}'.format(config.config))
    login_page = session.get(config.config["APC_ENDPOINT"])
    soup = BeautifulSoup(login_page.text, "lxml")
    post_endpoint = '{APC_ENDPOINT}{}'.format(
        soup.find('form').attrs['action'], **config.config)
    logger.debug('Derived login point to be {}'.format(post_endpoint))

    data = {
        'prefLanguage': '01000000',
        'login_username': config.config['APC_USERNAME'],
        'login_password': config.config['APC_PASSWORD'],
        'submit': 'Log On',
    }
    status_page = session.post(post_endpoint, data=data)
    status_page_url = urlparse(status_page.url)
    return status_page, status_page_url


def update_apc_status():
    prior_session = db.get('apc_session')
    prior_session_url = db.get('apc_path')
    if prior_session_url:
        prior_session_url = prior_session_url.decode('utf8')
    if prior_session:
        prior_session = pickle.loads(prior_session)
    else:
        prior_session = {}

    with requests.session() as session:
        session.cookies.update(prior_session)

        status_page, status_page_url = check_status(session, prior_session_url)


        db.set('apc_path', status_page_url.path)
        db.set('apc_session', pickle.dumps(session.cookies))

        soup = BeautifulSoup(status_page.text, "lxml")

        ups_status, outlet_status, event_log = soup.find_all("td", {'id': 'events'})
        _, data, _ = tuple(ups_status.find('table').find('table').find('table').children)
        *_, capacity, life_mins_raw = data.find_all('td')

        vac = ups_status.find('span', {'id': 'langVAC'}).previous_sibling.strip()
        if ' ' in vac:
            vac = 0
        vac = float(vac)
        battery_life_status = ups_status.find(
            'span', {'id': 'langBatteryLifeStatus'}).parent.parent.find_all(
            'td')[-1].text.strip()

        capacity = float(capacity.text.strip()[:-1].strip())
        life_mins_raw = life_mins_raw.text.strip()
        life_value = []
        life_span_metric = []
        target = life_value
        for char in life_mins_raw:
            if char not in (string.digits + '.'):
                target = life_span_metric
            target.append(char)
        life_value = float(''.join(life_value))
        life_span_metric = ''.join(life_span_metric)

        outlet_loads = {}

        for outlet in outlet_status.find_all('td', {'class': 'dataName'}):
            outlet_name = outlet.text
            outlet_status[outlet_name] = None
            queue = []
            for _ in range(4):
                outlet = outlet.next_sibling
            javascript_data = outlet.text.strip()
            value = javascript_data.index('parseFloat(')
            value += len('parseFloat(')
            value = javascript_data[value:]
            for char in value:
                if char not in (string.digits + '.'):
                    break
                queue.append(char)
            if queue:
                outlet_loads[outlet_name] = {
                    'value': float(''.join(queue)),
                    'unit': 'watts'
                }
    status = 'ok'
    if vac < 110:
        status = 'warning'
        if capacity < 50:
            status = 'critical'
        if capacity < 30:
            status = 'failure_imminent'
    post_metric('apc.vac.volts', vac)
    post_metric('apc.capacity_left.percentage', capacity)
    for key, value in outlet_loads.items():
        key = key.lower().replace(' ', '_')
        post_metric(f'apc.outlets.{key}.watts', value['value'])

    ups_status = {
        'vac': vac,
        'on_battery': vac < config.config['LOWEST_GRID_VAC_ALLOWED'],
        'status': status,
        'battery_status': battery_life_status,
        'capacity': capacity,
        'time_left': {
            'value': life_value,
            'unit': life_span_metric
        },
        'load': outlet_loads
    }

    db.hmset('apc_status', {
        'data': _json.dumps(ups_status),
        'timestamp': time.time()
    })
