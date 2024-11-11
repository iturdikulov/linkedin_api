import logging
import pickle

from curl_cffi.requests import Session
logger = logging.getLogger()


class ChallengeException(Exception):
    pass


class UnauthorizedException(Exception):
    pass


class LinkedinParsingError(Exception):
    pass


class Client:
    """
    Class to act as a client for the Linkedin API.
    """

    # Settings for general Linkedin API calls
    API_BASE_URL = "https://www.linkedin.com/voyager/api"
    REQUEST_HEADERS = {
        "accept-language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "x-li-lang": "en_US",
        "x-restli-protocol-version": "2.0.0",
    }

    # Settings for authenticating with Linkedin
    AUTH_BASE_URL = "https://www.linkedin.com"

    def __init__(
        self,
        *,
        debug=False,
        refresh_cookies=False,
        proxies=None,
        cookies=None,
        api_cookies=None,
        api_headers=None,
        ua=None,
    ):
        self.logger = logger

        self.session = Session(proxies=proxies)
        self.session.max_redirects = 5

        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)

        self.proxies = proxies

        if not api_cookies and cookies:
            logger.debug("Initialize new api cookies")

            for cookie in cookies:
                self.session.cookies.set(
                    name=cookie["name"],
                    value=cookie["value"],
                    domain=cookie["domain"],
                    secure=cookie["secure"],
                )
        else:
            self.session.cookies.jar._cookies.update(pickle.loads(api_cookies))

        session_id = self.session.cookies.get("JSESSIONID")
        if not session_id:
            raise Exception("Session id not found")

        if api_headers:
            logger.debug("Use saved api headers")
            self.session.headers.update(pickle.loads(api_headers))
        else:
            if not ua:
                raise Exception("User-agent not provided")

            api_headers = Client.REQUEST_HEADERS
            api_headers["csrf-token"] = session_id.strip('"')
            api_headers["User-Agent"] = ua
            self.session.headers.update(api_headers)
