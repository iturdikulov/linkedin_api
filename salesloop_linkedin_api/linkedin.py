"""
Provides linkedin api-related code
"""
import base64
import json
import random
import re
import uuid
from datetime import datetime
from os import environ
from os.path import isfile
from pathlib import Path
from random import randrange
from time import sleep
from urllib.parse import urlencode, urlparse, quote_plus

import backoff
from curl_cffi.requests.exceptions import RequestsException

from redis.client import StrictRedis
from salesloop_linkedin_api.parser import parse_messenger_messages, parse_profile_from_source

import salesloop_linkedin_api.settings as settings
from application.integrations.linkedin import (
    LinkedinLoginError,
    LinkedinUnauthorized,
    LinkedinAPIError,
)
from application.auto_throtle import AutoThrottleFunc
from application.integrations.linkedin.linkedin_html_parser_company import (
    LinkedinJSONParserCompany,
)
from application.integrations.linkedin.linkedin_html_parser_people import LinkedinJSONParser
from application.integrations.linkedin.utils import get_object_by_path, validate_search_url
from application.utlis_sales_search import generate_sales_search_url
from salesloop_linkedin_api.client import Client, LinkedinParsingError
from salesloop_linkedin_api.properties import LinkedinApFeatureAccess, LinkedinConnectionState
from salesloop_linkedin_api.utils.generate_search_urls import (
    generate_grapqhl_search_url,
    generate_graphql_companies_search_url,
)
from salesloop_linkedin_api.statistic import APIRequestType
from salesloop_linkedin_api.utils.helpers import (
    cffi_set_cookies,
    cffi_set_headers,
    parse_search_hits,
    get_default_regions,
    default_evade,
    get_random_base64,
    get_id_from_urn,
)

from celery.utils.log import get_task_logger


logger = get_task_logger(__name__)
RetryExceptions = (RequestsException,)

def generate_tracking_id():
    """Generates and returns a random trackingId
    :return: Random trackingId string
    :rtype: str
    TODO: propably not needed
    """
    random_int_array = [random.randrange(256) for _ in range(16)]
    rand_byte_array = bytearray(random_int_array)
    return str(base64.b64encode(rand_byte_array))[2:-1]


class LinkedinInvitesRateLimit(Exception):
    pass


class Linkedin(object):
    """
    Class for accessing LinkedIn API.
    """

    _MAX_UPDATE_COUNT = 100  # max seems to be 100
    _MAX_SEARCH_COUNT = 49  # max seems to be 49
    _MAX_SEARCH_LEN = settings.MAX_SEARCH_LEN
    _MAX_SEARCH_LEN_SALES_NAV = settings.MAX_SEARCH_LEN_SALES_NAV
    _MAX_REPEATED_REQUESTS = 200  # VERY conservative max requests count to avoid rate-limit
    _DEFAULT_GET_TIMEOUT = settings.REQUEST_TIMEOUT
    _DEFAULT_POST_TIMEOUT = settings.REQUEST_TIMEOUT


    def __init__(
        self,
        username,
        password,
        *,
        refresh_cookies=False,
        debug=False,
        proxies={},
        api_cookies=None,
        api_headers=None,
        cached_login=False,
        ua=None,
        default_retry_max_time=600,
        linkedin_login_id=None,
        cookies=None,
    ):
        self.proxies = proxies
        self.logger = logger

        self.client = Client(
            refresh_cookies=refresh_cookies,
            debug=debug,
            proxies=proxies,
            api_cookies=api_cookies,
            api_headers=api_headers,
            cookies=cookies,
            ua=ua,
        )
        self.username = username

        logger.info(
            "Initialize basic linkedin api class with %.2f max_retry_time. "
            "Default GET timeout: %.2f, Default POST timeout: %.2f",
            default_retry_max_time,
            self._DEFAULT_GET_TIMEOUT,
            self._DEFAULT_POST_TIMEOUT,
        )

        self.api_proxies = proxies

        self.results = None
        self.results_length = None
        self.default_retry_max_time = default_retry_max_time
        self.pagination = None
        self.limit_data = None
        self.results_success_urls = []

        # Count each request and save amount per X timerange
        self.requests_amount = {k: 0 for k in settings.REQUESTS_TYPES.keys()}
        self.requests_amount["start_timestamp"] = 0
        self.requests_amount["end_timestamp"] = 0

        # First and last request timestamps
        self.requests_start_timestamp = None
        self.requests_end_timestamp = None

        # Generated proxy url, used for proxy error message
        proxy_url = next(iter(proxies.values()))
        self.parsed_proxy = urlparse(proxy_url)

        # Redis connection
        redis_url = urlparse(environ["BROKER_URL"])
        redis_host, redis_port = redis_url.netloc.split(":")
        self.rds = StrictRedis(redis_host, port=int(redis_port), decode_responses=True, charset="utf-8")

        # Unique session id, based on UUID
        self.session_id = uuid.uuid4()
        self.linkedin_login_id = linkedin_login_id
        self.auto_throttle = AutoThrottleFunc()

    def _get_max_retry_time(self):
        return self.default_retry_max_time

    def backoff_hdlr(self, details):
        # TODO: remove/verify this after curl_cifi upgrade!
        if details['exception'].args and 'HTTP Error' in details['exception'].args[0]:
            raise Exception("HTTP fatal error, stop retrying")

        error_type = f"LinkedINAPIError_{details['target'].__name__}"
        error_message = "Backing off {wait:0.1f} seconds afters {tries} tries "
        "calling function {target} with args {args} and kwargs "
        "{kwargs}".format(**details)

        logger.warning(settings.LOG_PROXY_ERROR_MSG.format(
                parsed_proxy=self.parsed_proxy,
                error_type=error_type,
                error_message=error_message))

    def _update_statistics(self, url):
        request_type = APIRequestType.get_request_type(url)
        self.requests_amount[request_type] += 1
        if not self.requests_amount["start_timestamp"]:
            self.requests_amount["start_timestamp"] = int(datetime.utcnow().timestamp())

        self.requests_amount["end_timestamp"] = int(datetime.utcnow().timestamp())

        # Write statistics to redis, key contains username and uuid of the task
        # ln.api -> LinkedIn API statistics
        # ttl is 1 month
        if self.linkedin_login_id:
            logger.debug(f"New request {request_type} to {url}, updating statistics in redis")
            self.rds.set(
                f"ln.api:{self.linkedin_login_id}:{self.session_id}",
                json.dumps(self.requests_amount),
                ex=settings.STATISTICS_TTL,
            )
        else:
            logger.warning("No linkedin_login_id provided, skipping statistics store in redis")

    def _fetch(self, uri, evade=default_evade, raw_url=False, **kwargs):
        """
        GET request to LinkedIn API
        """
        evade()

        if uri == "/relationships/connectionsSummary/":
            max_time = 20
        else:
            max_time = self._get_max_retry_time()

        @backoff.on_exception(
            backoff.expo,
            RetryExceptions,
            max_time=max_time,
            on_backoff=self.backoff_hdlr,
        )
        def fetch_data():
            if raw_url:
                url = uri
            else:
                url = f"{self.client.API_BASE_URL}{uri}"

            if not kwargs.get("timeout"):
                # Use default timeout
                kwargs["timeout"] = Linkedin._DEFAULT_GET_TIMEOUT

            fetch_response = self.client.session.get(url, **kwargs)
            fetch_response.raise_for_status()

            self._update_statistics(url)
            return fetch_response

        return fetch_data()

    def _post(self, uri, evade=default_evade, raw_url=False, allowed_status_codes=(), **kwargs):
        """
        POST request to LinkedIn API
        """
        evade()

        @backoff.on_exception(
            backoff.expo,
            RetryExceptions,
            max_time=self._get_max_retry_time,
            on_backoff=self.backoff_hdlr,
        )
        def post_data():
            if raw_url:
                url = uri
            else:
                url = f"{self.client.API_BASE_URL}{uri}"

            if not kwargs.get("timeout"):
                # Use default timeout
                kwargs["timeout"] = Linkedin._DEFAULT_POST_TIMEOUT

            post_response = self.client.session.post(url, **kwargs)

            if (
                post_response.status_code != 400 # TODO: need to remove this?
                and post_response.status_code not in allowed_status_codes
            ):
                # Some responses, such as ln connection, can be valid with 400 code!
                post_response.raise_for_status()

            # Update statistics if request was successful
            self._update_statistics(url)
            return post_response

        return post_data()

    def get_ln_user_metadata(self, get_email=False):
        """
        Fetch basic metadata from Linkedin API.
        Also used to check if we are logged in.
        """
        metadata = {}
        feature_access = LinkedinApFeatureAccess(linkedin=False, premium=False)

        # Check if we can access the network page
        response = self._fetch("https://www.linkedin.com/mynetwork/", raw_url=True)
        if response.status_code == 200:
            try:
                user_metadata = self._parse_user_metadata(response.text, get_email=get_email)
                metadata.update(user_metadata)
            except (IndexError, LinkedinParsingError):
                raise LinkedinUnauthorized("Unable to parse metadata/email from response")

            # If we accessed to included data, this means we have access to the base API
            feature_access.linkedin = True
            feature_access.premium = user_metadata["premium"]

            # Set cookies
            metadata["session_cookies"] = cffi_set_cookies(self.client.session)
            metadata["session_headers"] = cffi_set_headers(self.client.session)

        if not feature_access.linkedin:
            raise LinkedinLoginError("Linkedin account has no minimum access to Linkedin API")

        metadata["feature_access"] = feature_access

        return metadata

    def _parse_user_metadata(self, response_text: str, get_email: bool = False) -> dict:
        """
        Parse email from response text
        Args:
            response_text: html response text, usually from home page

        Returns:
            email address

        """
        my_info = self.dash_global_navs()
        mini_profile = my_info["included"][0]

        logger.debug("Parsing user metadata from response: %s", mini_profile)

        # Get profile urn
        urn = get_id_from_urn(mini_profile["entityUrn"])

        # TODO: cover this with tests
        avatar = None
        try:
            picture = get_object_by_path(
                mini_profile, "profilePicture.displayImageReferenceResolutionResult.vectorImage"
            )

            segment = get_object_by_path(picture, "artifacts.2.fileIdentifyingUrlPathSegment")
            root_url = get_object_by_path(picture, "rootUrl")

            # Prefix with correct url
            avatar = f"{root_url}{segment}"
        except (KeyError, IndexError) as e:
            logger.critical("Could not parse avatar from search_hit_data: %s", mini_profile, exc_info=e)

        # TODO: cover this with tests
        email = None
        if get_email:
            self._fetch(
                "https://www.linkedin.com/mypreferences/d/categories/account", raw_url=True
            )
            response = self._fetch(
                "https://www.linkedin.com/mysettings-api/settingsApiSneakPeeks?category=SIGN_IN_AND_SECURITY&q=category",
                raw_url=True,
            )
            if response.status_code == 401:
                raise LinkedinLoginError()
            else:
                response.raise_for_status()

            current_settings = response.json()
            elements = current_settings["elements"]
            for element in elements:
                if element["settingCardKey"] == "manageEmailAddresses":
                    email = element["displayText"]

            if not email:
                raise LinkedinParsingError(
                    "Could not parse email from response: %s", response_text
                )

        # NEXT: do we need to get this?
        # premium_data = api.get_premium_subscription()["map"]["headerData"]["premium"]
        # logger.debug(premium_data)

        return {
            "premium": None,
            "urn": urn,
            "email": email,
            "avatar": avatar,
        }

    def search(self, params, limit=-1, offset=0):
        """Perform a LinkedIn search.
        :param params: Search parameters (see code)
        :type params: dict
        :param limit: Maximum length of the returned list, defaults to -1 (no limit)
        :type limit: int, optional
        :param offset: Index to start searching from
        :type offset: int, optional
        :return: List of search results
        :rtype: list
        """
        count = Linkedin._MAX_SEARCH_COUNT
        if limit is None:
            limit = -1

        results = []
        while True:
            if limit > -1 and limit - len(results) < count:
                count = limit - len(results)
            default_params = {
                "count": str(count),
                "filters": "List()",
                "origin": "GLOBAL_SEARCH_HEADER",
                "q": "all",
                "start": len(results) + offset,
                "queryContext": "List(spellCorrectionEnabled->true,"
                "relatedSearchesEnabled->true,kcardTypes->PROFILE|COMPANY)",
            }
            default_params.update(params)

            res = self._fetch(
                f"/search/blended?{urlencode(default_params, safe='(),')}",
                headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
            )
            data = res.json()

            new_elements = []
            elements = data.get("data", {}).get("elements", [])
            for i in range(len(elements)):
                new_elements.extend(elements[i]["elements"])
                # not entirely sure what extendedElements generally
                # refers to - keyword search gives back a single job?
                # new_elements.extend(data["data"]["elements"][i]["extendedElements"])
            results.extend(new_elements)

            # break the loop if we're done searching
            # NOTE: we could also check for the `total` returned in the response.
            # This is in data["data"]["paging"]["total"]
            if (
                (limit > -1 and len(results) >= limit)  # if our results exceed set limit
                or len(results) / count >= Linkedin._MAX_REPEATED_REQUESTS
            ) or len(new_elements) == 0:
                break

            self.logger.debug(f"results grew to {len(results)}")

        return results

    def cluster_sales_search_people(self, linkedin_url):
        generated_url = generate_sales_search_url(linkedin_url)
        random_page_instance_postfix = get_random_base64()
        res = self._fetch(
            generated_url,
            headers={
                "authority": "www.linkedin.com",
                "dnt": "1",
                "x-li-lang": "en_US",
                "sec-ch-ua-mobile": "?0",
                "x-li-page-instance": f"urn:li:page:d_sales2_search_people;"
                f"{random_page_instance_postfix}",
                "x-restli-protocol-version": "2.0.0",
                "accept": "*/*",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": linkedin_url,
            },
            raw_url=True,
        )
        res.raise_for_status()
        data = res.json()
        return data

    def search_people(
        self,
        keywords=None,
        connection_of=None,
        network_depth=None,
        current_company=None,
        past_companies=None,
        nonprofit_interests=None,
        profile_languages=None,
        regions=None,
        industries=None,
        schools=None,
        title=None,
        include_private_profiles=False,  # profiles without a public id, "Linkedin Member"
        limit=None,
    ):
        """
        Do a people search.
        """
        filters = ["resultType->PEOPLE"]
        if connection_of:
            filters.append(f"connectionOf->{connection_of}")
        if network_depth:
            filters.append(f"network->{network_depth}")
        if regions:
            filters.append(f'geoRegion->{"|".join(regions)}')
        if industries:
            filters.append(f'industry->{"|".join(industries)}')
        if current_company:
            filters.append(f'currentCompany->{"|".join(current_company)}')
        if past_companies:
            filters.append(f'pastCompany->{"|".join(past_companies)}')
        if profile_languages:
            filters.append(f'profileLanguage->{"|".join(profile_languages)}')
        if nonprofit_interests:
            filters.append(f'nonprofitInterest->{"|".join(nonprofit_interests)}')
        if schools:
            filters.append(f'schools->{"|".join(schools)}')
        if title:
            filters.append(f"title->{title}")

        params = {"filters": "List({})".format(",".join(filters))}

        if keywords:
            params["keywords"] = keywords

        data = self.search(params, limit=limit)

        results = []
        for item in data:
            if "publicIdentifier" not in item:
                continue
            results.append(
                {
                    "urn_id": get_id_from_urn(item.get("targetUrn")),
                    "distance": item.get("memberDistance", {}).get("value"),
                    "public_id": item.get("publicIdentifier"),
                }
            )

        return results

    def get_connections_summary(self):
        res = self._fetch(
            "/relationships/connectionsSummary/",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        data = res.json()
        connections_summary = data["data"]
        return connections_summary

    # TODO: outdated, need to remove
    def get_profile_contact_info(self, public_id=None, urn_id=None):
        """
        Return data for a single profile.

        [public_id] - public identifier i.e. tom-quirk-1928345
        [urn_id] - id provided by the related URN
        """
        res = self._fetch(f"/identity/profiles/{public_id or urn_id}/profileContactInfo")
        data = res.json()

        contact_info = {
            "email_address": data.get("emailAddress"),
            "websites": [],
            "twitter": data.get("twitterHandles"),
            "birthdate": data.get("birthDateOn"),
            "ims": data.get("ims"),
            "phone_numbers": data.get("phoneNumbers", []),
        }

        websites = data.get("websites", [])
        for item in websites:
            if "com.linkedin.voyager.identity.profile.StandardWebsite" in item["type"]:
                item["label"] = item["type"][
                    "com.linkedin.voyager.identity.profile.StandardWebsite"
                ]["category"]
            elif "" in item["type"]:
                item["label"] = item["type"][
                    "com.linkedin.voyager.identity.profile.CustomWebsite"
                ]["label"]

            del item["type"]

        contact_info["websites"] = websites

        return contact_info

    def get_profile_skills(self, public_id=None, urn_id=None):
        """
        Return the skills of a profile.

        [public_id] - public identifier i.e. tom-quirk-1928345
        [urn_id] - id provided by the related URN
        """
        params = {"count": 100, "start": 0}
        res = self._fetch(f"/identity/profiles/{public_id or urn_id}/skills", params=params)
        data = res.json()

        skills = data.get("elements", [])
        for item in skills:
            del item["entityUrn"]

        return skills

    def sn_profile(self, urn_id) -> dict:
        profile_url = f"https://www.linkedin.com/profile/view/?id={urn_id}"
        profile_data = self._fetch(profile_url, raw_url=True)
        profile_data = parse_profile_from_source(profile_data.text)
        return profile_data

    def profile(self, public_id: str) -> dict:
        random_page_instance_postfix = get_random_base64()
        # Fetch profile page
        self._fetch("https://www.linkedin.com/in/" + public_id, raw_url=True)

        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-page-instance": f"urn:li:page:d_flagship3_profile_view_base;{random_page_instance_postfix}",
            "x-restli-protocol-version": "2.0.0",
        }

        # Get profile data
        params = {
            "includeWebMetadata": "true",
            "variables": f"(vanityName:{public_id})",
            "queryId": "voyagerIdentityDashProfiles.99846ade1cc203e6f684e7369b01d501",
        }

        response = self._fetch(
            f"/graphql?{urlencode(params, safe='(),:')}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def profile_cards(self, profile_urn: str) -> dict:
        random_page_instance_postfix = get_random_base64()
        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-page-instance": f"urn:li:page:d_flagship3_profile_view_base;{random_page_instance_postfix}",
            "x-restli-protocol-version": "2.0.0",
        }

        response = self._fetch(
            f"/graphql?includeWebMetadata=true&variables=(profileUrn:urn%3Ali%3Afsd_profile%3A{profile_urn})&queryId=voyagerIdentityDashProfileCards.5ba28aea1970071579633b9f449b8a7e",
            headers=headers,
        )

        response.raise_for_status()
        return response.json()

    # NEXT: need to remove
    def profile_contacts(self, public_id: str) -> dict:
        random_page_instance_postfix = get_random_base64()
        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-page-instance": f"urn:li:page:d_flagship3_profile_view_base;{random_page_instance_postfix}",
            "x-restli-protocol-version": "2.0.0",
        }
        response = self._fetch(
            f"/graphql?variables=(memberIdentity:{public_id})&queryId=voyagerIdentityDashProfiles.84cab0be7183be5d0b8e79cd7d5ffb7b",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def get_profile_connections(self, urn_id, limit=None):
        """
        Return a list of profile ids connected to profile of given [urn_id]
        """
        return self.search_people(connection_of=urn_id, network_depth="F", limit=limit)

    def get_company_updates(self, public_id=None, urn_id=None, max_results=None, results=[]):
        """
        Return a list of company posts

        [public_id] - public identifier ie - microsoft
        [urn_id] - id provided by the related URN
        """
        params = {
            "companyUniversalName": {public_id or urn_id},
            "q": "companyFeedByUniversalName",
            "moduleKey": "member-share",
            "count": Linkedin._MAX_UPDATE_COUNT,
            "start": len(results),
        }

        res = self._fetch("/feed/updates", params=params)

        data = res.json()

        if (
            len(data["elements"]) == 0
            or (max_results is not None and len(results) >= max_results)
            or (
                max_results is not None
                and len(results) / max_results >= Linkedin._MAX_REPEATED_REQUESTS
            )
        ):
            return results

        results.extend(data["elements"])
        self.logger.debug(f"results grew: {len(results)}")

        return self.get_company_updates(
            public_id=public_id, urn_id=urn_id, results=results, max_results=max_results
        )

    def get_profile_updates(self, public_id=None, urn_id=None, max_results=None, results=[]):
        """
        Return a list of profile posts

        [public_id] - public identifier i.e. tom-quirk-1928345
        [urn_id] - id provided by the related URN
        """
        params = {
            "profileId": {public_id or urn_id},
            "q": "memberShareFeed",
            "moduleKey": "member-share",
            "count": Linkedin._MAX_UPDATE_COUNT,
            "start": len(results),
        }

        res = self._fetch("/feed/updates", params=params)

        data = res.json()

        if (
            len(data["elements"]) == 0
            or (max_results is not None and len(results) >= max_results)
            or (
                max_results is not None
                and len(results) / max_results >= Linkedin._MAX_REPEATED_REQUESTS
            )
        ):
            return results

        results.extend(data["elements"])
        self.logger.debug(f"results grew: {len(results)}")

        return self.get_profile_updates(
            public_id=public_id, urn_id=urn_id, results=results, max_results=max_results
        )

    def get_current_profile_views(self):
        """
        Get profile view statistics, including chart data.
        """
        res = self._fetch("/identity/wvmpCards")

        data = res.json()

        return data["elements"][0]["value"][
            "com.linkedin.voyager.identity.me.wvmpOverview.WvmpViewersCard"
        ]["insightCards"][0]["value"][
            "com.linkedin.voyager.identity.me.wvmpOverview.WvmpSummaryInsightCard"
        ]["numViews"]

    def get_school(self, public_id):
        """
        Return data for a single school.

        [public_id] - public identifier i.e. uq
        """
        params = {
            "decorationId": "com.linkedin.voyager.deco.organization.web.WebFullCompanyMain-12",
            "q": "universalName",
            "universalName": public_id,
        }

        res = self._fetch(f"/organization/companies?{urlencode(params)}")

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data))
            return {}

        school = data["elements"][0]

        return school

    def get_company(self, public_id):
        """Fetch data about a given LinkedIn company.

        :param public_id: LinkedIn public ID for a company
        :type public_id: str

        :return: Company data
        :rtype: dict
        """
        params = {
            "decorationId": "com.linkedin.voyager.deco.organization.web.WebFullCompanyMain-12",
            "q": "universalName",
            "universalName": public_id,
        }

        res = self._fetch("/organization/companies", params=params)

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data["message"]))
            return {}

        company = data["elements"][0]

        return company

    def get_company_id(self, public_id):
        """

        Args:
            public_id: company identifier

        Returns:
            numeric company id or None
        """
        company = self.get_company(public_id)
        if company:
            company_id = get_id_from_urn(company.get("entityUrn"))

            if company_id and company_id.isnumeric():
                return company_id

    def create_conversation(self, entity_urn, message_body):
        """
        Create conversation
        """
        payload = json.dumps(
            {
                "keyVersion": "LEGACY_INBOX",
                "conversationCreate": {
                    "eventCreate": {
                        # TODO: UNKNOWN FILED, just skipped
                        # "originToken": "3c809d5d-c58a-49b8-801d-9d42607db1c5",
                        "value": {
                            "com.linkedin.voyager.messaging.create.MessageCreate": {
                                "body": message_body,
                                "attachments": [],
                                "attributedBody": {"attributes": [], "text": message_body},
                            }
                        }
                    },
                    "recipients": [entity_urn],
                    "subtype": "MEMBER_TO_MEMBER",
                },
            }
        )

        res = self._post(
            "/messaging/conversations?action=create",
            data=payload,
        )

        return res.status_code != 201

    def event_bodies(self, receiver_urn_id, user_elements):
        """
        Args:
            user_elements: conversation event data
            receiver_urn_id: user urn id
            sender: get event bodies from sender

        Returns:
            event_body: last message from receiver
        """
        receiver_messages = []
        sender_messages = []
        conversation_urn_id = None

        for element in user_elements:
            # Step 1. Users who replied to our message
            for event in element.get("events", []):
                event_body = (
                    event.get("eventContent", {})
                    .get("com.linkedin.voyager.messaging.event.MessageEvent", {})
                    .get("attributedBody", {})
                    .get("text")
                )

                if event_body:
                    current_participant = (
                        event.get("from", {})
                        .get("com.linkedin.voyager.messaging.MessagingMember", {})
                        .get("miniProfile", {})
                    )
                    urn_id = get_id_from_urn(current_participant.get("entityUrn"))
                    conversation_urn_id = get_id_from_urn(element["entityUrn"])
                    if receiver_urn_id == urn_id:
                        receiver_messages.append(event_body)
                    else:
                        sender_messages.append(event_body)

        return receiver_messages, sender_messages, conversation_urn_id

    def get_conversation_details(self, profile_urn_id, get_id=False):
        """
        Return the conversation (or "message thread") details for a given [public_profile_id]
        """
        # passing `params` doesn't work properly, think it's to do with List().
        # Might be a bug in `requests`?
        res = self._fetch(
            f"/messaging/conversations?\
            keyVersion=LEGACY_INBOX&q=participants&recipients=List({profile_urn_id})"
        )

        data = res.json()
        elements = data.get("elements", [])
        latest_reply_from_recipient = False
        only_first_message_found = None

        if data.get("elements"):
            item = data["elements"][0]
            item_id = get_id_from_urn(item["entityUrn"])
            if get_id:
                item = item_id
                return item
            else:
                item["id"] = item_id
        else:
            item = {}

        events = item.get("events")
        if profile_urn_id and events and isinstance(events, list):
            latest_event = events[-1]
            current_participant = (
                latest_event.get("from", {})
                .get("com.linkedin.voyager.messaging.MessagingMember", {})
                .get("miniProfile", {})
            )

            from_urn_id = current_participant.get("entityUrn")
            if profile_urn_id == get_id_from_urn(from_urn_id):
                latest_reply_from_recipient = True

            first_message_urn = item.get("firstMessageUrn")
            latest_message_urn = latest_event.get("entityUrn")

            if first_message_urn and latest_message_urn:
                if first_message_urn == latest_message_urn:
                    only_first_message_found = True
                else:
                    only_first_message_found = False

        receiver_messages, sender_messages, conversation_urn_id = self.event_bodies(
            profile_urn_id, elements
        )

        return {
            "details": item,
            "total_events": item.get("totalEventCount"),
            "latest_reply_from_recipient": latest_reply_from_recipient,
            "only_first_message_found": only_first_message_found,
            "receiver_messages": receiver_messages,
            "sender_messages": sender_messages,
            "conversation_urn_id": conversation_urn_id,
        }

    # NEXT: outdated, need to remove
    def get_conversations(self, createdBefore=None):
        """
        Return list of conversations the user is in.
        """
        if not createdBefore:
            params = {"keyVersion": "LEGACY_INBOX", "count": 20}
        else:
            params = {"keyVersion": "LEGACY_INBOX", "createdBefore": createdBefore}

        res = self._fetch("/messaging/conversations", params=params)

        return res.json()

    def get_conversation(self, conversation_urn_id):
        """
        Return the full conversation at a given [conversation_urn_id]
        """
        res = self._fetch(f"/messaging/conversations/{conversation_urn_id}/events")

        return res.json()

    def send_message(
        self, conversation_urn_id=None, recipients=[], message_body=None, parse_urn_id=False
    ) -> bool:
        """
        Send a message to a given conversation. If error, return true.

        Recipients: List of profile urn id's
        """
        params = {"action": "create"}

        if not (conversation_urn_id or recipients) and not message_body:
            return True

        message_event = {
            "eventCreate": {
                "value": {
                    "com.linkedin.voyager.messaging.create.MessageCreate": {
                        "body": message_body,
                        "attachments": [],
                        "attributedBody": {"text": message_body, "attributes": []},
                        "mediaAttachments": [],
                    }
                }
            }
        }

        if conversation_urn_id and not recipients:
            if parse_urn_id:
                conversation_urn_id = get_id_from_urn(conversation_urn_id)

            res = self._post(
                f"/messaging/conversations/{conversation_urn_id}/events",
                params=params,
                data=json.dumps(message_event),
            )

            return res.status_code == 201
        elif recipients and not conversation_urn_id:
            message_event["recipients"] = recipients
            message_event["subtype"] = "MEMBER_TO_MEMBER"
            payload = {
                "keyVersion": "LEGACY_INBOX",
                "conversationCreate": message_event,
            }
            res = self._post("/messaging/conversations", params=params, data=json.dumps(payload))

            return res.status_code == 201

    def mark_conversation_as_seen(self, conversation_urn_id):
        """
        Send seen to a given conversation. If error, return True.
        """
        payload = json.dumps({"patch": {"$set": {"read": True}}})

        res = self._post(f"/messaging/conversations/{conversation_urn_id}", data=payload)

        return res.status_code != 200

    # NEXT: trigger deauth, need to remove
    def get_user_profile(self):
        """
        Return current user profile
        """
        res = self._fetch("/me")
        data = res.json()

        return data

    def dash_global_navs(self):
        """
        Return current user profile
        """

        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-page-instance": f"urn:li:page:d_flagship3_feed;{get_random_base64()}",
            "x-restli-protocol-version": "2.0.0",
        }

        # Get profile data
        params = {
            "includeWebMetadata": "true",
            "variables": "()",
            "queryId": "voyagerFeedDashGlobalNavs.392ef5b3577c3f317acf6087b30391ff",
        }

        # NEXT: parmetrize?
        response = self._fetch(
            f"/graphql?{urlencode(params, safe='(),:')}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def conversations(self, inbox_user_urn):
        """
        Return list of conversations from users inbox
        NOTE: not support next/previous page (newSyncToken), but it can be added
        """

        response = self._fetch(
            f"/voyagerMessagingGraphQL/graphql?queryId=messengerConversations.0df6f006f938bcf4f6be8f8fdfc2fe4c&variables=(mailboxUrn:urn%3Ali%3Afsd_profile%3A{inbox_user_urn})",
            headers={"Accept": "application/graphql"},
        )
        conversations = (
            get_object_by_path(response.json(), "data.messengerConversationsBySyncToken.elements")
            or []
        )
        if not conversations:
            logger.warning("No converations found")

        parcipiants = []
        parsed_messages = {}
        for conversation in conversations:
            creator = None
            for parcipant in conversation["conversationParticipants"]:
                parcipant_urn = parcipant["entityUrn"].split(":")[-1]
                if parcipant_urn != inbox_user_urn:
                    parcipiants.append(parcipant_urn)

            messages = (
                get_object_by_path(
                    conversation,
                    "messages.elements",
                )
                or []
            )

            for message in messages:
                if not message or not isinstance(message, dict):
                    raise ValueError("Invalid message")

                creator = message["sender"]["entityUrn"].split(":")[-1]
                parsed_messages[creator] = {
                    "message_body": message["body"]["text"],
                    "creatorEntityUrn": creator,
                    "entityUrn": message["entityUrn"],
                    "deliveredAt": datetime.fromtimestamp(message["deliveredAt"] / 1000),
                    "type": message["_type"],
                }

        return parcipiants, parsed_messages

    def messenger_conversations(self, inbox_user_urn, recipient_urn) -> dict:
        """Get conversation data between two users.
        :param inbox_user_urn: the URN of the inbox user (who is logged in)
        :param recipient_urn: the URN of the recipient
        """

        response = self._fetch(
            f"https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql?queryId=messengerConversations.c6e2778ef6f5c2b617c06261738cd193&variables=(mailboxUrn:urn%3Ali%3Afsd_profile%3A{inbox_user_urn},recipients:List(urn%3Ali%3Afsd_profile%3A{recipient_urn}))",
            raw_url=True,
            headers={"Accept": "application/graphql"},
        )
        response.raise_for_status()
        elements = response.json()["data"]["messengerConversationsByRecipients"]["elements"]
        if not elements:
            logger.debug("No conversations found")
            return {}

        root_element = elements[0]
        return {
            "creator": root_element["creator"]["entityUrn"],
            "creatorEntityUrn": root_element["creator"]["entityUrn"].split(":")[-1],
            "entityUrn": root_element["entityUrn"],
            "conversationUrl": root_element["conversationUrl"],
            "conversationParticipants": [p for p in root_element["conversationParticipants"]],
            "categories": root_element["categories"],
            "createdAt": root_element["createdAt"],
            "lastActivityAt": root_element["lastActivityAt"],
        }

    def messenger_messages(self, recipient_urn) -> list:
        recipient_urn = quote_plus(recipient_urn)
        url = f"https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql?queryId=messengerMessages.fcaf6a3aca4ff63c4d1585bddb1e1a8e&variables=(conversationUrn:{recipient_urn})"
        response = self._fetch(url, raw_url=True, headers={"Accept": "application/graphql"})
        response.raise_for_status()
        return parse_messenger_messages(response.json())

    def get_premium_subscription(self):
        """
        Return current user profile
        """
        random_page_instance_postfix = get_random_base64()
        res = self._fetch(
            "https://www.linkedin.com/psettings/premium-subscription?asJson=true",
            raw_url=True,
            headers={
                "authority": "www.linkedin.com",
                "accept": "application/json, text/javascript, */*; q=0.01",
                "dnt": "1",
                "x-requested-with": "XMLHttpRequest",
                "x-li-page-instance": f"urn:li:page:psettings-premium-subscription;"
                f"{random_page_instance_postfix}",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": "https://www.linkedin.com/",
                "accept-language": "en,en-GB;q=0.9,en;q=0.8,en-US;q=0.7",
            },
        )
        data = res.json()

        return data

    def get_billings(self):
        """ "
        Return current user billings
        """
        res = self._fetch(
            "https://www.linkedin.com/psettings/premium-subscription/billings",
            raw_url=True,
            headers={
                "authority": "www.linkedin.com",
                "pragma": "no-cache",
                "cache-control": "no-cache",
                "accept": "*/*",
                "dnt": "1",
                "x-requested-with": "XMLHttpRequest",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": "https://www.linkedin.com/",
            },
        )
        data = res.json()
        return data

    def get_user_panels(self):
        """
        Return current user profile
        """
        res = self._fetch("/identity/panels")
        data = res.json()
        return data

    def get_sent_invitations(self, start=0, limit=100):
        """
        Return list of new invites
        # original request
        # https://www.linkedin.com/voyager/api/relationships/sentInvitationViewsV2?count=10&invitationType=CONNECTION&q=invitationType&start=0
        """
        params = {
            "count": limit,
            "invitationType": "CONNECTION",
            "q": "invitationType",
            "start": start,
        }

        res = self._fetch("/relationships/sentInvitationViewsV2", params=params)

        res.raise_for_status()

        response_payload = res.json()
        return [element["invitation"] for element in response_payload["elements"]]

    def get_invitations(self, start=0, limit=3):
        """
        Return list of new invites
        """
        params = {
            "start": start,
            "count": limit,
            "includeInsights": True,
            "q": "receivedInvitation",
        }

        res = self._fetch("/relationships/invitationViews", params=params)

        if res.status_code != 200:
            return []

        response_payload = res.json()
        return [element["invitation"] for element in response_payload["elements"]]

    def get_invitations_summary(self):
        """
        Return list of new invites
        """
        res = self._fetch("/relationships/invitationsSummary")

        if res.status_code != 200:
            return []

        response_payload = res.json()
        return response_payload

    def reply_invitation(self, invitation_entity_urn, invitation_shared_secret, action="accept"):
        """
        Reply to an invite, the default is to accept the invitation.
        @Param: invitation_entity_urn: str
        @Param: invitation_shared_secret: str
        @Param: action: "accept" or "ignore"
        Returns True if sucess, False otherwise
        """
        invitation_id = get_id_from_urn(invitation_entity_urn)
        params = {"action": action}
        payload = json.dumps(
            {
                "invitationId": invitation_id,
                "invitationSharedSecret": invitation_shared_secret,
                "isGenericInvitation": False,
            }
        )

        res = self._post(
            f"{self.client.API_BASE_URL}/relationships/invitations/{invitation_id}",
            params=params,
            data=payload,
        )

        return res.status_code == 200

    def get_profile_connections_raw(self, max_results=None, results=[]) -> list:
        count = (
            max_results
            if max_results and max_results <= Linkedin._MAX_SEARCH_COUNT
            else Linkedin._MAX_SEARCH_COUNT
        )

        params = {
            "decorationId": "com.linkedin.voyager.dash.deco.web.mynetwork.ConnectionListWithProfile-16",
            "count": count,
            "q": "search",
            "sortType": "RECENTLY_ADDED",
            "start": len(results),
        }

        res = self._fetch("/relationships/dash/connections", params=params)
        data = res.json()

        if data and data["elements"]:
            connections_list = data["elements"]
            connections = []
            logger.debug("Found %d elements", len(connections_list))
            for profile in connections_list:
                try:
                    connections.append(
                        {
                            "publicIdentifier": profile["connectedMemberResolutionResult"][
                                "publicIdentifier"
                            ],
                            "entityUrn": get_id_from_urn(profile["entityUrn"]),
                        }
                    )
                except KeyError:
                    # This is probably a deleted profile or canceled invitation
                    logger.warning("Failed to parse connection data: %s", profile)
                    continue

            results = results + connections

        total_found = data["paging"]["count"]

        # recursive base case
        if (
            len(data["elements"]) == 0
            or (max_results and len(results) >= max_results)
            or total_found is None
            or (
                max_results is not None
                and len(results) / max_results >= Linkedin._MAX_REPEATED_REQUESTS
            )
        ):
            if max_results and (len(results) > max_results):
                results = results[:max_results]

            return results

        sleep(random.randint(1, 40))  # sleep to avoid throttling
        return self.get_profile_connections_raw(max_results=max_results, results=results)

    def get_current_profile_urn(self, public_id=None):
        """
        Get profile view statistics, including chart data.
        """
        network_info = self._fetch(f"/identity/profiles/{public_id}/networkinfo")

        network_info_data = network_info.json()
        entityUrn = network_info_data.get("entityUrn")

        if entityUrn:
            return get_id_from_urn(entityUrn)

    def sales_login(self, timeout=None):
        request_homepage = self._fetch(
            "https://www.linkedin.com/sales/", raw_url=True, timeout=timeout
        )
        client_page_instance = None

        client_page_instance_data_groups = re.search(
            r'name="bprPageInstance" content="([\S\s]*?)"', request_homepage.content.decode()
        )

        if client_page_instance_data_groups:
            client_page_instance = client_page_instance_data_groups.group(1).strip()
            logger.info("Page instance: %s", client_page_instance)

        if not client_page_instance:
            logger.error(
                "No client_page_instance_data_groups groups found %s",
                client_page_instance_data_groups,
            )
            raise LinkedinLoginError("No client_page_instance_data_groups groups")

        request_sales_api_identity = self._fetch(
            "https://www.linkedin.com/sales-api/salesApiIdentity?q=findLicensesByCurrentMember",
            raw_url=True,
            headers={
                "dnt": "1",
                "accept-encoding": "gzip, deflate, br",
                "x-li-lang": "en_US",
                "accept-language": "en-US,en;q=0.9",
                "x-requested-with": "XMLHttpRequest",
                "pragma": "no-cache",
                "accept": "*/*",
                "cache-control": "no-cache",
                "x-restli-protocol-version": "2.0.0",
                "authority": "www.linkedin.com",
                "referer": "https://www.linkedin.com/sales/",
            },
            timeout=timeout,
        )

        sales_api_identity_data = request_sales_api_identity.json()

        if sales_api_identity_data.get("elements"):
            element = sales_api_identity_data["elements"][0]
            contractData = {
                "viewerDeviceType": "DESKTOP",
                "name": element["name"],
                "identity": {
                    "agnosticIdentity": element["agnosticIdentity"],
                    "name": element["name"],
                },
            }

            redirect = "/sales/search"
            redirect = urlencode({"redirect": redirect})

            SALES_API_AGONSITC_AUTH_URL = (
                "https://www.linkedin.com/sales-api/salesApiAgnosticAuthentication?%s"
                % (redirect,)
            )
            request_api_agnostic = self._post(
                SALES_API_AGONSITC_AUTH_URL,
                raw_url=True,
                headers={
                    "X-Restli-Protocol-Version": "2.0.0",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Li-Page-Instance": client_page_instance,
                    "X-Li-Lang": "en_US",
                    "Referer": "https://www.linkedin.com/sales/contract-chooser?redirect=%2Fsales%2Fsearch",
                },
                data=json.dumps(contractData),
                timeout=timeout,
            )
            request_api_agnostic.raise_for_status()
            return True

        return False

    def get_leads(
        self, search_url, is_sales=False, timeout=None, get_raw=False, send_sn_requests=True
    ):
        logger.info("Leads quick search %s url, with %s timeout. Is Sales %s.", search_url, timeout, is_sales)

        if not validate_search_url(search_url):
            raise LinkedinAPIError("Invalid search URL")

        if search_url.startswith("https://www.linkedin.com/sales/search"):
            is_sales = True

        if is_sales and send_sn_requests:
            self.sales_login(timeout=timeout)

        raw_html_request = self._fetch(search_url, raw_url=True, timeout=timeout)
        raw_html_request.raise_for_status()
        html = raw_html_request.text

        if get_raw:
            return html
        else:
            if is_sales:
                search_hits = self.cluster_sales_search_people(search_url)
                parsed_users, pagination, unknown_profiles, limit_data = parse_search_hits(
                    search_hits, is_sales=is_sales
                )

                # Normalize pagination total, can't be more than _MAX_SEARCH_LEN_SALES_NAV
                if pagination.get("total") and pagination["total"] > self._MAX_SEARCH_LEN_SALES_NAV:
                    pagination["total"] = self._MAX_SEARCH_LEN_SALES_NAV
            else:
                search_url = generate_grapqhl_search_url(search_url)
                search_json = self._fetch(
                    search_url,
                    raw_url=True,
                ).json()
                search_parser = LinkedinJSONParser(search_json)
                pagination = search_parser.get_paging()

                # Normalize pagination total, can't be more than _MAX_SEARCH_LEN
                if pagination.get("total") and pagination["total"] > self._MAX_SEARCH_LEN:
                    pagination["total"] = self._MAX_SEARCH_LEN

                parsed_users = search_parser.parse_users()
                unknown_profiles = []
                limit_data = {}

            if parsed_users:
                # default pagination params can be useful for debugging
                logger.debug("Override pagination, reason: we found parsed_users")
                pagination["logged_in"] = True
                pagination["results_length"] = len(parsed_users)

            return parsed_users, pagination, unknown_profiles, limit_data

    def random_user_actions(self, public_id=None):
        results = []

        if public_id:
            if random.randint(0, 1):
                results.append(self.get_profile_network_info(public_id))
            else:
                results.append(self.get_current_profile_urn(public_id))
        else:
            results.append(self.get_user_profile())

        return results

    def get_profile_data(self, public_id: str) -> dict:
        random_page_instance_postfix = get_random_base64()
        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-page-instance": f"urn:li:page:d_flagship3_profile_view_base;{random_page_instance_postfix}",
            "x-restli-protocol-version": "2.0.0",
            "Referer": f"https://www.linkedin.com/in/{public_id}/",
        }

        # Fetch profile page
        self._fetch("https://www.linkedin.com/in/" + public_id, raw_url=True)

        # Get profile data
        params = {
            "includeWebMetadata": "true",
            "variables": f"(vanityName:{public_id})",
            "queryId": "voyagerIdentityDashProfiles.a1941bc56db02d2a36a03dd81313f3c7",
        }

        response = self._fetch(
            f"/graphql?{urlencode(params, safe='(),:')}",
            headers=headers,
        )
        response.raise_for_status()
        profile = response.json()

        entity_urn = profile["data"]["data"]["identityDashProfilesByMemberIdentity"]["*elements"][
            0
        ]

        keys_to_extract = ("publicIdentifier", "firstName", "lastName", "headline")

        for item in profile["included"]:
            if (
                item["$type"] == "com.linkedin.voyager.dash.identity.profile.Profile"
                and item["entityUrn"] == entity_urn
            ):
                return {key: item[key] for key in keys_to_extract}

        raise LinkedinAPIError("Profile data not found")

    def get_profile_urn_v2(self, json_data: dict) -> str:
        return json_data["included"][0]["entityUrn"]

    def connect_with_someone(
        self, profile_urn_id: str, message: str | None = None
    ) -> LinkedinConnectionState:
        """
        Send a message to a given conversation. If error, return true.
        generate_tracking_id is not equal to API, gene
        """
        params = {
            "action": "verifyQuotaAndCreateV2",
            "decorationId": "com.linkedin.voyager.dash.deco.relationships.InvitationCreationResultWithInvitee-2",
        }

        random_page_instance_postfix = get_random_base64()
        headers = {
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-lang": "en_US",
            "x-li-page-instance": f"urn:li:page:d_flagship3_profile_view_base;{random_page_instance_postfix}",
            "x-li-pem-metadata": "Voyager - Invitations - Actions=invite-send",
            "x-li-deco-include-micro-schema": "true",
            "content-type": "application/json; charset=utf-8",
            "Origin": "https://www.linkedin.com",
            "DNT": "1",
        }

        message_data = {
            "invitee": {
                "inviteeUnion": {
                    "memberProfile": f"urn:li:fsd_profile:{profile_urn_id}",
                },
            },
        }
        if message:
            message_data["customMessage"] = message

        res_data = self._post(
            "/voyagerRelationshipsDashMemberRelationships",
            headers=headers,
            params=params,
            json=message_data,
            allowed_status_codes=(406, 429)
        ).json()["data"]

        error_code = res_data.get("code")
        if error_code == "CANT_RESEND_YET":
            return LinkedinConnectionState.CANT_RESEND_YET
        elif error_code == "CUSTOM_INVITE_LIMIT_REACHED":
            return LinkedinConnectionState.CUSTOM_INVITE_LIMIT_REACHED
        else:
            try:
                if res_data["value"]["invitationUrn"]:
                    return LinkedinConnectionState.SUCCESS
            except KeyError:
                logger.warning("No invitation urn found in response: %s", res_data)
                return LinkedinConnectionState.FAILED

        raise Exception(f"Unknown connection error: {res_data}")

    def remove_connection(self, public_profile_id):
        res = self._post(
            f"/identity/profiles/{public_profile_id}/profileActions?action=disconnect",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )

        return res.status_code != 200

    def get_profile_member_badges(self, public_profile_id):
        res = self._fetch(
            f"/identity/profiles/{public_profile_id}/memberBadges",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        if res.status_code != 200:
            return {}

        data = res.json()
        return data.get("data", {})

    def get_profile_network_info(self, public_profile_id):
        res = self._fetch(
            f"/identity/profiles/{public_profile_id}/networkinfo",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        if res.status_code != 200:
            return {}

        data = res.json()
        data = data.get("data", {})
        if data:
            distance = data.get("distance", {}).get("value")
            data["approved"] = distance == "DISTANCE_1"

        return data

    def search_companies(self, keywords=None):
        """Perform a LinkedIn search for companies.
        NOTE: we search only 1st page, no recursive search

        :param keywords: A list of search keywords (str)
        :rtype: list
        """
        search_url = generate_graphql_companies_search_url(keywords)
        res = self._fetch(
            search_url,
            raw_url=True,
        )

        json_parser = LinkedinJSONParserCompany(res.text)
        return json_parser.parse_companies()

    def get_regions(self):
        """
        Get regions directly from linkedin, typehead API
        """

        if isfile("all_regions_codes.json"):
            print("Region exist, skip...")
            return

        regions_json = Path(__file__).parent / "regions.json"
        input_regions = get_default_regions(regions_json)
        self.logger.info("Found %d regions at %s", len(input_regions), regions_json)
        output_regions = {}
        self.client.session.get("https://www.linkedin.com/sales/")
        cookies = self.client.session.cookies.get_dict()
        cookies.get("JSESSIONID").strip('"')

        for i, region in enumerate(input_regions):
            region_name = region.get("name")
            region_code = region.get("code")
            # This headers usually outdated, need generate each times...
            headers = {
                "authority": "www.linkedin.com",
                "pragma": "no-cache",
                "cache-control": "no-cache",
                "dnt": "1",
                "x-li-lang": "en_US",
                "x-li-identity": "dXJuOmxpOm1lbWJlcjo0MDAzMTE2Nzc",
                "x-li-page-instance": "urn:li:page:d_sales2_search_people;"
                "g//MAJuSRwe6HvmrIEQK5g==",
                "accept": "*/*",
                "x-restli-protocol-version": "2.0.0",
                "x-requested-with": "XMLHttpRequest",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": "https://www.linkedin.com/sales/search/people?"
                "preserveScrollPosition=true&selectedFilter=GE&viewAllFilters=true",
                "accept-language": "en-GB,en;q=0.9,ru;q=0.8,en-US;q=0.7",
            }

            params = (
                ("q", "query"),
                ("start", "0"),
                ("type", "BING_GEO"),
                ("count", "25"),
                ("query", region_name),
            )

            res = self._fetch(
                "https://www.linkedin.com/sales-api/salesApiFacetTypeahead",
                headers=headers,
                params=params,
                raw_url=True,
            )

            data = res.json()
            elements = data.get("elements", [])
            subregions = []
            for element in elements:
                if element:
                    subregions.append(element)
                    self.logger.info(
                        "Region %d of %d - %s, %s", i, len(input_regions), region, element
                    )

            output_regions[region_code] = subregions
            logger.debug(output_regions[region_code])

            sleep(random.randint(0, 3))

        return output_regions

    def reformat_results(self, results):
        # search public ids if not exists, use same method like in scrapy search
        processed_results = []
        reformat_max_errors = 6
        for i, lead in enumerate(results):
            try:
                if lead.get("publicIdentifier"):
                    # evade limit each N requests
                    if i > 0 and i % randrange(4, 6) == 0:
                        sleep(randrange(15, 25))

                    profile = self.get_profile_data(public_id=lead.get("publicIdentifier"))
                    lead["publicIdentifier"] = profile["publicIdentifier"]

                    # fill additional fields
                    lead["firstname"] = profile["firstName"]
                    lead["lastname"] = profile["lastName"]
                    lead["headline"] = profile["headline"]

                    if "currentPositions" in lead:
                        for position in lead["currentPositions"]:
                            if "companyName" in position:
                                lead["companyName"] = position["companyName"]

                            if "title" in position:
                                lead["position"] = position["title"]
                            break

                    processed_results.append(lead)
                else:
                    processed_results.append(lead)

            except Exception as e:
                processed_results.append(lead)
                reformat_max_errors -= 1
                logger.warning("Failed get profile data for %s lead", lead, exc_info=e)

            if reformat_max_errors <= 0:
                raise Exception("Too many reformat errors, break parsing...")

        return processed_results

    def get_invites_sent_per_interval(self, interval=86400.0) -> list:
        """
        Get invites sent per interval
        :param interval: seconds, default 86400 seconds (1 day)
        """
        sent_invites_data = []
        max_pages_to_parse = 8 + random.randint(0, 5)

        # Get connections from conversation history
        for i in range(max_pages_to_parse):
            get_sent_invites_start = i * 100

            # check app sending limits
            current_invites_data = self.get_sent_invitations(start=get_sent_invites_start)

            for invite in current_invites_data:
                if invite in sent_invites_data:
                    logger.debug("%s invite already exist, skipping", invite)
                    continue

                sent_time = datetime.fromtimestamp(invite["sentTime"] / 1000)
                sent_time_timedelta = datetime.utcnow() - sent_time
                if sent_time_timedelta.total_seconds() <= interval:
                    sent_invites_data.append(invite)

            if not current_invites_data:
                logger.debug("No more invites found, break parsing")
                break

        return sent_invites_data
