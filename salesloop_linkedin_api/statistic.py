import logging
import re
from urllib.parse import urlparse

from salesloop_linkedin_api.settings import REQUESTS_TYPES

logger = logging.getLogger()


class APIRequestType:
    """
    Type of API endpoint
    """

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
                return request_type

        raise Exception(f"Found unknown url/request type: {url}, endpoint: {endpoint}")
