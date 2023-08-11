import logging
import time

from sanic.blueprints import Blueprint
from sanic.response import json as as_json, text as as_text
from .. import _json


logger = logging.getLogger(__name__)

apc = Blueprint("apc", url_prefix="/apc")


@apc.route("/status", methods=("GET", "HEAD"))
async def apc_status(request):
    if request.method == "HEAD":
        return as_text("")

    async with request.redis() as db:
        data = await db.hgetall("apc_status")
    if not data:
        return as_json(
            {
                "msg": "No data",
                "code": -1,
            },
            status=404,
        )
    ups_status, timestamp = _json.loads(data[b"data"]), float(data[b"timestamp"])
    data = {"msg": "Ok", "code": 0, "data": ups_status, "timestamp": timestamp}
    if time.time() - timestamp > 300:
        data["msg"] = "Stale data"
        data["code"] = -2
    return as_json(data, dumps=_json.dumps)
