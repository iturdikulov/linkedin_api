import logging
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

logger = logging.getLogger()


class APIRequestType(Enum):
    """
    Type of api endpoint
    """
    profile = 0
    search_results = 1
    company = 2
    connect = 3
    messages = 4
    auth = 5
    current_user = 6
    unknown = 7

    @classmethod
    def get_url_endpoint(cls, url):
        path = urlparse(url).path
        return path.rpartition('/')[0]

    @classmethod
    def get_request_type(cls, url):
        endpoint = cls.get_url_endpoint(url)
        logger.debug(f"Detected this url endpoint: {endpoint}")


class APIRequestAmount:
    """
    Used to store all requests number, additionally it's stored start and end timestamp

    Usage:
    api_requests = APIRequestAmount()
    api_requests[profile] += 1
    api_requests.requests_end_timestamp = datetime.utcnow()
    """
    def __init__(self):
        self.requests_start_timestamp = datetime.utcnow()
        self.requests_end_timestamp = None

        # Set initial values per each request type
        self.record = {i.name: 0 for i in APIRequestType}

    def __getitem__(self, key):
        return self.record[key.name]

    def __setitem__(self, key, value):
        self.record[key.name] = value

