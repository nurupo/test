# -*- coding: utf-8 -*-

import requests

from . import config

def requests_retry():
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=config.retries())
    session.mount('http://', requests.adapters.HTTPAdapter)
    session.mount('https://', requests.adapters.HTTPAdapter)
    return session
