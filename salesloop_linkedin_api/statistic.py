import logging
import re
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from salesloop_linkedin_api.settings import REQUESTS_TYPES

logger = logging.getLogger()


class APIRequestType(Enum):
    """
    Type of API endpoint
    """

    growth = 0
    jobs = 1
    relationships = 2
    companies = 3
    search = 4
    feed = 5
    messaging = 6
    identity = 7
    uas = 8
    other = 9

    @classmethod
    def get_url_endpoint(cls, url):
        path = urlparse(url).path
        if not path:
            raise SyntaxError(f"Invalid url detected: {url}")

        path_parts = re.sub("^/voyager/api/", "", path).strip("/").split("/")

        if len(path_parts) == 1:
            return path_parts[0]
        else:
            return f"{path_parts[0]}/{path_parts[1]}"

    @classmethod
    def get_request_type(cls, url):
        """
        Based on url detect endpoint
        Requests types are based on this https://linkedin.api-docs.io/v1.0

        Args:
            url: LinkedIn url

        Returns:
            endpoint string like "search/blended"
        """
        endpoint = cls.get_url_endpoint(url)
        for request_type, request_tuple in REQUESTS_TYPES.items():
            if endpoint in request_tuple:
                return cls[request_type]

        raise Exception(f"Found unknown url: {url}")


class APIRequestAmount:
    """
    Used to store all requests number, additionally it's stored start and end timestamp

    Usage:
    api_requests = APIRequestAmount()
    api_requests[profile] += 1
    """

    def __init__(self):
        # Set initial values per each request type
        self.record = {i.name: 0 for i in APIRequestType}

    def __getitem__(self, request_type):
        return self.record[request_type.name]

    def __setitem__(self, request_type, value):
        self.record[request_type.name] = value
