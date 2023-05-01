import logging
import pickle
from datetime import timedelta

import requests
from requests_cache import CachedSession
import salesloop_linkedin_api.settings as settings

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
        "user-agent": " ".join(
            [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_5)",
                "AppleWebKit/537.36 (KHTML, like Gecko)",
                "Chrome/66.0.3359.181 Safari/537.36",
            ]
        ),
        # "accept": "application/vnd.linkedin.normalized+json+2.1",
        "accept-language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "x-li-lang": "en_US",
        "x-restli-protocol-version": "2.0.0",
        # "x-li-track": '{"clientVersion":"1.2.6216","osName":"web","timezoneOffset":10,"deviceFormFactor":"DESKTOP","mpName":"voyager-web"}',
    }

    # Settings for authenticating with Linkedin
    AUTH_BASE_URL = "https://www.linkedin.com"
    AUTH_REQUEST_HEADERS = {
        "X-Li-User-Agent": "LIAuthLibrary:3.2.4 \
                            com.linkedin.LinkedIn:8.8.1 \
                            iPhone:8.3",
        "User-Agent": "LinkedIn/8.8.1 CFNetwork/711.3.18 Darwin/14.0.0",
        "X-User-Language": "en",
        "X-User-Locale": "en_US",
        "Accept-Language": "en-us",
    }

    def __init__(
        self,
        *,
        debug=False,
        refresh_cookies=False,
        proxies=None,
        cookies=None,
        api_cookies=None,
        ua=None,
        use_request_cache=False
    ):
        self.logger = logger

        if use_request_cache:
            self.logger.info("Use cached requests session")
            self.session = CachedSession(
                'requests_cache',
                use_cache_dir=True,                 # Save files in the default user cache dir
                cache_control=True,                 # Use Cache-Control headers for expiration, if available
                expire_after=timedelta(days=1),     # Otherwise expire responses after one day
                allowable_methods=['GET', 'POST'],  # Cache POST requests to avoid sending the same data twice
                allowable_codes=[200, 400],         # Cache 400 responses as a solemn reminder of your failures
                ignored_parameters=['api_key'],     # Don't match this param or save it in the cache
                match_headers=True,                 # Match all request headers
                stale_if_error=False,               # In case of request errors, use stale cache data if possible
            )
        else:
            self.session = requests.session()

        self.session.max_redirects = 5

        self.session.proxies.update(proxies)
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)

        self.proxies = proxies
        self._use_cookie_cache = not refresh_cookies
        self._cookies = cookies
        self.api_cookies = api_cookies
        self.api_headers = {}

        if ua:
            Client.REQUEST_HEADERS["user-agent"] = ua

        self.session.headers.update(Client.REQUEST_HEADERS)

    def _request_session_cookies(self):
        """
        Return a new set of session cookies as given by Linkedin.
        """
        if self._use_cookie_cache and self.api_cookies:
            self.logger.info(f"Attempting to use cached cookies")

            try:
                cookies = self.api_cookies
                if cookies:
                    return cookies
            except Exception as e:
                self.logger.info(f"Cookies not found! Requesting new cookies. {str(e)}")

        res = requests.get(
            f"{Client.AUTH_BASE_URL}/uas/authenticate",
            headers=Client.AUTH_REQUEST_HEADERS,
            proxies=self.proxies,
        )

        return res.cookies

    def _set_session_cookies(self, cookiejar):
        """
        Set cookies of the current session and save them to a file.
        """
        self.session.cookies = cookiejar
        cookiejar_dict = requests.utils.dict_from_cookiejar(cookiejar)
        self.session.headers["csrf-token"] = cookiejar_dict["JSESSIONID"].strip('"')

    @property
    def cookies(self):
        return self.session.cookies

    def alternate_authenticate(self):
        self._set_session_cookies(self._request_session_cookies())
        self.logger.info("Used cached cookies!")
        self.api_cookies = pickle.dumps(self.session.cookies, pickle.HIGHEST_PROTOCOL)
        self.api_headers = pickle.dumps(self.session.headers, pickle.HIGHEST_PROTOCOL)

    def authenticate(self, username, password):
        """
        Authenticate with Linkedin.

        Return a session object that is authenticated.
        """
        self._set_session_cookies(self._request_session_cookies())

        payload = {
            "session_key": username,
            "session_password": password,
            "JSESSIONID": self.session.cookies["JSESSIONID"],
        }

        res = requests.post(
            f"{Client.AUTH_BASE_URL}/uas/authenticate",
            data=payload,
            cookies=self.session.cookies,
            headers=Client.AUTH_REQUEST_HEADERS,
            proxies=self.proxies,
            timeout=settings.LOGIN_TIMEOUT,
        )

        data = res.json()

        if data and data["login_result"] != "PASS":
            self.logger.warning("Linkedin auth error, username: %s, data: %s", username, data)
            raise ChallengeException(data["login_result"])

        if res.status_code == 401:
            raise UnauthorizedException()

        if res.status_code != 200:
            raise Exception()

        self._set_session_cookies(res.cookies)
        self.api_cookies = pickle.dumps(res.cookies, pickle.HIGHEST_PROTOCOL)
        self.api_headers = pickle.dumps(res.headers, pickle.HIGHEST_PROTOCOL)
