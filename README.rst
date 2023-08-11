Power Scout
-------------------------------------

Thingy to ingest into graphite my power data. Written using Python 3.6 and `Japronto <https://github.com/squeaky-pl/japronto>`_. I use supervisord to run it.

Capabilities
--------------

- Handle HTTP posts from the `Rainforest Eagle <https://rainforestautomation.com/rfa-z109-eagle/>`_ when configured as a LOCAL "Cloud" service.
   + You can try it in their cloud solution if you open up a tunnel to it.
   + Advise you run it locally.
- Aggressively query/scrape an `APC BackUPS Pro 500 <http://www.apc.com/shop/us/en/products/APC-Back-UPS-Pro-500-Lithium-Ion-UPS/P-BG500>`_.
- Ingest metrics from the above into Graphite
- Write snapshot data into Redis

Development
--------------

.. code-block:: console

    $ python3.10 -m pip install --user invoke
    $ invoke setup
    $ . python/bin/activate
    (python) $ ...
