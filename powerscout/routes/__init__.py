import logging

from .apc import apc as apc_routes
from .eagle import eagle as eagle_routes

logger = logging.getLogger(__name__)

__all__ = ['apc_routes', 'eagle_routes']
