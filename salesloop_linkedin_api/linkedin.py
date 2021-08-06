"""
Provides linkedin api-related code
"""
import random
from time import sleep
from urllib import parse as urlparse
from urllib.parse import urlencode
import json
from .utils.helpers import parse_search_hits, get_default_regions, default_evade, get_random_base64, get_leads_from_html, get_pagination_data
from salesloop_linkedin_api.utils.helpers import get_id_from_urn
from salesloop_linkedin_api.utils.generate_search_urls import generate_clusters_search_url, \
    is_filtered_default_search
from pathlib import Path
from salesloop_linkedin_api.client import Client
from os.path import isfile
from urllib.parse import urlparse, parse_qs
import salesloop_linkedin_api.settings as settings
import requests
import re
import pickle
import logging
import backoff
from random import randrange

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('application')


class Linkedin(object):
    """
    Class for accessing Linkedin API.
    """
    _MAX_UPDATE_COUNT = 100  # max seems to be 100
    _MAX_SEARCH_COUNT = 49  # max seems to be 49
    _MAX_REPEATED_REQUESTS = (
        200
    )  # VERY conservative max requests count to avoid rate-limit
    _DEFAULT_GET_TIMEOUT = settings.REQUEST_TIMEOUT
    _DEFAULT_POST_TIMEOUT = settings.REQUEST_TIMEOUT

    def __init__(self, username, password, *, refresh_cookies=False, debug=False, proxies={},
                 api_cookies=None,
                 cached_login=False,
                 ua=None,
                 default_retry_max_time=600):
        self.logger = logger
        self.client = Client(refresh_cookies=refresh_cookies, debug=debug, proxies=proxies, api_cookies=api_cookies,
                             ua=ua)
        self.username = username

        logger.info('Initialize basic linkedin api class with %.2f max_retry_time. '
                    'Default GET timeout: %.2f, Default POST timeout: %.2f',
                    default_retry_max_time, self._DEFAULT_GET_TIMEOUT, self._DEFAULT_POST_TIMEOUT)

        if cached_login:
            self.client.alternate_authenticate()
        else:
            self.client.authenticate(username, password)

        self.api_cookies = self.client.api_cookies
        self.api_headers = self.client.api_headers
        self.api_proxies = proxies

        self.results = None
        self.results_length = None
        self.default_retry_max_time = default_retry_max_time
        self.pagination = None
        self.limit_data = None
        self.results_success_urls = []

    def _get_max_retry_time(self):
        return self.default_retry_max_time

    def backoff_hdlr(self, details):
        self.logger.debug("Backing off {wait:0.1f} seconds afters {tries} tries "
              "calling function {target} with args {args} and kwargs "
              "{kwargs}".format(**details))

    def _fetch(self, uri, evade=default_evade, raw_url=False, **kwargs):
        """
        GET request to Linkedin API
        """
        evade()

        if uri == '/relationships/connectionsSummary/':
            max_time = 20
        else:
            max_time = self._get_max_retry_time()

        @backoff.on_exception(backoff.expo,
                              (requests.exceptions.Timeout,
                               requests.exceptions.ProxyError,
                               requests.exceptions.SSLError,
                               requests.exceptions.ReadTimeout,
                               requests.exceptions.HTTPError,
                               requests.exceptions.ConnectionError),
                              max_time=max_time,
                              on_backoff=self.backoff_hdlr)
        def fetch_data():
            if raw_url:
                url = uri
            else:
                url = f"{self.client.API_BASE_URL}{uri}"

            if not kwargs.get('timeout'):
                # Use default timeout
                kwargs['timeout'] = Linkedin._DEFAULT_GET_TIMEOUT
            return self.client.session.get(url, **kwargs)

        return fetch_data()

    def _post(self, uri, evade=default_evade, raw_url=False, **kwargs):
        """
        POST request to Linkedin API
        """
        evade()

        @backoff.on_exception(backoff.expo,
                              (requests.exceptions.Timeout,
                               requests.exceptions.ProxyError,
                               requests.exceptions.SSLError,
                               requests.exceptions.ReadTimeout,
                               requests.exceptions.HTTPError,
                               requests.exceptions.ConnectionError),
                              max_time=self._get_max_retry_time,
                              on_backoff=self.backoff_hdlr)
        def post_data():
            if raw_url:
                url = uri
            else:
                url = f"{self.client.API_BASE_URL}{uri}"

            if not kwargs.get('timeout'):
                # Use default timeout
                kwargs['timeout'] = Linkedin._DEFAULT_POST_TIMEOUT
            return self.client.session.post(url,  **kwargs)

        return post_data()

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
            limit
            if limit > -1 and limit - len(results) < count:
                count = limit - len(results)
            default_params = {
                "count": str(count),
                "filters": "List()",
                "origin": "GLOBAL_SEARCH_HEADER",
                "q": "all",
                "start": len(results) + offset,
                "queryContext": "List(spellCorrectionEnabled->true,relatedSearchesEnabled->true,kcardTypes->PROFILE|COMPANY)",
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
                # not entirely sure what extendedElements generally refers to - keyword search gives back a single job?
                # new_elements.extend(data["data"]["elements"][i]["extendedElements"])
            results.extend(new_elements)

            # break the loop if we're done searching
            # NOTE: we could also check for the `total` returned in the response.
            # This is in data["data"]["paging"]["total"]
            if (
                (
                    limit > -1 and len(results) >= limit
                )  # if our results exceed set limit
                or len(results) / count >= Linkedin._MAX_REPEATED_REQUESTS
            ) or len(new_elements) == 0:
                break

            self.logger.debug(f"results grew to {len(results)}")

        return results

    def clusters_search_people(self, linkedin_url):
        """
        Do a people search using voyager/api/search/dash/cluster
        """

        url_params_str = '&'.join(
            [f"{k}={v}" for k, v in generate_clusters_search_url(linkedin_url).items()])

        res = self._fetch(
            f"/search/dash/clusters?{url_params_str}",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        res.raise_for_status()
        data = res.json()
        return [data]

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
            filters.append(f'title->{title}')

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
            f"/relationships/connectionsSummary/",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        data = res.json()
        connections_summary = data["data"]
        return connections_summary

    def get_profile_contact_info(self, public_id=None, urn_id=None):
        """
        Return data for a single profile.

        [public_id] - public identifier i.e. tom-quirk-1928345
        [urn_id] - id provided by the related URN
        """
        res = self._fetch(
            f"/identity/profiles/{public_id or urn_id}/profileContactInfo"
        )
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
        res = self._fetch(
            f"/identity/profiles/{public_id or urn_id}/skills", params=params
        )
        data = res.json()

        skills = data.get("elements", [])
        for item in skills:
            del item["entityUrn"]

        return skills

    def get_profile(self, public_id=None, urn_id=None, get_skills=False):
        """
        Return data for a single profile.

        [public_id] - public identifier i.e. tom-quirk-1928345
        [urn_id] - id provided by the related URN
        """
        # NOTE this still works for now, but will probably eventually have to be converted to
        # https://www.linkedin.com/voyager/api/identity/profiles/ACoAAAKT9JQBsH7LwKaE9Myay9WcX8OVGuDq9Uw
        res = self._fetch(f"/identity/profiles/{public_id or urn_id}/profileView")

        data = res.json()
        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data["message"]))
            return {}

        # massage [profile] data
        profile = data["profile"]
        if "miniProfile" in profile:
            if "picture" in profile["miniProfile"]:
                profile["displayPictureUrl"] = profile["miniProfile"]["picture"][
                    "com.linkedin.common.VectorImage"
                ]["rootUrl"]
            profile["profile_id"] = get_id_from_urn(profile["miniProfile"]["entityUrn"])
            profile['publicIdentifier'] = profile["miniProfile"].get("publicIdentifier")

            del profile["miniProfile"]

        del profile["defaultLocale"]
        del profile["supportedLocales"]
        del profile["versionTag"]
        del profile["showEducationOnProfileTopCard"]

        # massage [experience] data
        experience = data["positionView"]["elements"]
        for item in experience:
            if "company" in item and "miniCompany" in item["company"]:
                if "logo" in item["company"]["miniCompany"]:
                    logo = item["company"]["miniCompany"]["logo"].get(
                        "com.linkedin.common.VectorImage"
                    )
                    if logo:
                        item["companyLogoUrl"] = logo["rootUrl"]
                del item["company"]["miniCompany"]

        profile["experience"] = experience

        # massage [skills] data
        # skills = [item["name"] for item in data["skillView"]["elements"]]
        # profile["skills"] = skills
        if get_skills:
            profile["skills"] = self.get_profile_skills(public_id=public_id, urn_id=urn_id)

        # massage [education] data
        education = data["educationView"]["elements"]
        for item in education:
            if "school" in item:
                if "logo" in item["school"]:
                    item["school"]["logoUrl"] = item["school"]["logo"][
                        "com.linkedin.common.VectorImage"
                    ]["rootUrl"]
                    del item["school"]["logo"]

        profile["education"] = education

        return profile

    def get_profile_connections(self, urn_id, limit=None):
        """
        Return a list of profile ids connected to profile of given [urn_id]
        """
        return self.search_people(connection_of=urn_id, network_depth="F", limit=limit)

    def get_company_updates(
        self, public_id=None, urn_id=None, max_results=None, results=[]
    ):
        """"
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

        res = self._fetch(f"/feed/updates", params=params)

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

    def get_profile_updates(
        self, public_id=None, urn_id=None, max_results=None, results=[]
    ):
        """"
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

        res = self._fetch(f"/feed/updates", params=params)

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
        res = self._fetch(f"/identity/wvmpCards")

        data = res.json()

        return data["elements"][0]["value"][
            "com.linkedin.voyager.identity.me.wvmpOverview.WvmpViewersCard"
        ]["insightCards"][0]["value"][
            "com.linkedin.voyager.identity.me.wvmpOverview.WvmpSummaryInsightCard"
        ][
            "numViews"
        ]

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
        """
        Return data for a single company.

        [public_id] - public identifier i.e. univeristy-of-queensland
        """
        params = {
            "decorationId": "com.linkedin.voyager.deco.organization.web.WebFullCompanyMain-12",
            "q": "universalName",
            "universalName": public_id,
        }

        res = self._fetch(f"/organization/companies", params=params)

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: %s", data)
            return {}

        company = data["elements"][0]

        return company

    def create_conversation(self, entity_urn, message_body):
        """
        Create conversation
        """
        payload = json.dumps(
            {
                "keyVersion": "LEGACY_INBOX",
                "conversationCreate": {
                    "eventCreate": {
                        # "originToken": "3c809d5d-c58a-49b8-801d-9d42607db1c5", TODO: UNKNOWN FILED, just skipped
                        "value": {
                            "com.linkedin.voyager.messaging.create.MessageCreate": {
                                "body": message_body,
                                "attachments": [],
                                "attributedBody": {
                                    "attributes": [],
                                    "text": message_body
                                }
                            }
                        }
                    },
                    "recipients": [
                        entity_urn
                    ],
                    "subtype": "MEMBER_TO_MEMBER"
                }
            }
        )

        res = self._post(
            f"/messaging/conversations?action=create",
            data=payload,
        )

        return res.status_code != 201

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
        latest_reply_from_recipient = False
        only_first_message_found = None

        if data.get('elements'):
            item = data["elements"][0]
            item_id = get_id_from_urn(item["entityUrn"])
            if get_id:
                item = item_id
                return item
            else:
                item["id"] = item_id
        else:
            item = {}

        events = item.get('events')
        if profile_urn_id and events and isinstance(events, list):
            latest_event = events[-1]
            current_participant = latest_event.get('from', {}).get(
                'com.linkedin.voyager.messaging.MessagingMember', {}).get('miniProfile', {})

            from_urn_id = current_participant.get('entityUrn')
            if profile_urn_id == get_id_from_urn(from_urn_id):
                latest_reply_from_recipient = True

            first_message_urn = item.get('firstMessageUrn')
            latest_message_urn = latest_event.get('entityUrn')

            if first_message_urn and latest_message_urn:
                if first_message_urn == latest_message_urn:
                    only_first_message_found = True
                else:
                    only_first_message_found = False

        return {
            'details': item,
            'total_events': item.get('totalEventCount'),
            'latest_reply_from_recipient': latest_reply_from_recipient,
            'only_first_message_found': only_first_message_found
        }

    def get_conversations(self, createdBefore=None):
        """
        Return list of conversations the user is in.
        """
        if not createdBefore:
            params = {
                "keyVersion": "LEGACY_INBOX",
                "count": 20
            }
        else:
            params = {
                "keyVersion": "LEGACY_INBOX",
                "createdBefore": createdBefore
            }

        res = self._fetch(f"/messaging/conversations", params=params)

        return res.json()

    def get_conversation(self, conversation_urn_id):
        """
        Return the full conversation at a given [conversation_urn_id]
        """
        res = self._fetch(f"/messaging/conversations/{conversation_urn_id}/events")

        return res.json()

    def send_message(self, conversation_urn_id=None, recipients=[], message_body=None, parse_urn_id=False):
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
            res = self._post(
                f"/messaging/conversations", params=params, data=json.dumps(payload)
            )

            return res.status_code == 201

    def mark_conversation_as_seen(self, conversation_urn_id):
        """
        Send seen to a given conversation. If error, return True.
        """
        payload = json.dumps({"patch": {"$set": {"read": True}}})

        res = self._post(
            f"/messaging/conversations/{conversation_urn_id}", data=payload
        )

        return res.status_code != 200

    def get_user_profile(self):
        """"
        Return current user profile
        """
        res = self._fetch(f"/me")
        data = res.json()

        return data

    def get_premium_subscription(self):
        """"
        Return current user profile
        """
        random_page_instance_postfix = get_random_base64()
        res = self._fetch(f"https://www.linkedin.com/psettings/premium-subscription?asJson=true",
                          raw_url=True, headers={
            'authority': 'www.linkedin.com',
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'dnt': '1',
            'x-requested-with': 'XMLHttpRequest',
            'x-li-page-instance': f'urn:li:page:psettings-premium-subscription;{random_page_instance_postfix}',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
            'referer': 'https://www.linkedin.com/',
            'accept-language': 'en,en-GB;q=0.9,en;q=0.8,en-US;q=0.7',
        })
        data = res.json()

        return data


    def get_billings(self):
        """"
         Return current user billings
         """
        res = self._fetch(f"https://www.linkedin.com/psettings/premium-subscription/billings",
                          raw_url=True, headers={
            'authority': 'www.linkedin.com',
            'pragma': 'no-cache',
            'cache-control': 'no-cache',
            'accept': '*/*',
            'dnt': '1',
            'x-requested-with': 'XMLHttpRequest',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
            'referer': 'https://www.linkedin.com/',
        })
        data = res.json()
        return data

    def get_user_panels(self):
        """"
        Return current user profile
        """
        res = self._fetch(f"/identity/panels")
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
            "invitationType": 'CONNECTION',
            "q": "invitationType",
            "start": start,
        }

        res = self._fetch(
            f"/relationships/sentInvitationViewsV2", params=params
        )

        if res.status_code != 200:
            return []

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

        res = self._fetch(
            f"/relationships/invitationViews", params=params
        )

        if res.status_code != 200:
            return []

        response_payload = res.json()
        return [element["invitation"] for element in response_payload["elements"]]

    def get_invitations_summary(self):
        """
        Return list of new invites
        """
        res = self._fetch(
            f"/relationships/invitationsSummary"
        )

        if res.status_code != 200:
            return []

        response_payload = res.json()
        return response_payload

    def reply_invitation(
        self, invitation_entity_urn, invitation_shared_secret, action="accept"
    ):
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

    def get_profile_connections_raw(self, max_results=None, results=[], only_urn=False):
        count = (
            max_results
            if max_results and max_results <= Linkedin._MAX_SEARCH_COUNT
            else Linkedin._MAX_SEARCH_COUNT
        )

        default_params = {
            "count": count,
            "start": len(results),
            "sortType": "RECENTLY_ADDED"
        }

        res = self._fetch(
            f"/relationships/connections?" + urlencode(default_params)
        )

        data = res.json()
        total_found = data.get("paging", {}).get("count")

        # recursive base case
        if (
                len(data["elements"]) == 0
                or (max_results and len(results) >= max_results)
                or total_found is None
                or (max_results is not None and len(results) / max_results >= Linkedin._MAX_REPEATED_REQUESTS)
        ):
            if max_results and (len(results) > max_results):
                results = results[:max_results]

            return results

        if data and data.get('elements'):
            connections_list = data.get('elements')
            connections = []
            logger.debug('Found %d elements', len(connections_list))
            if only_urn:
                for profile in connections_list:
                    connections.append({
                        'publicIdentifier': profile.get('miniProfile', {}).get('publicIdentifier'),
                        'entityUrn': get_id_from_urn(profile['entityUrn'])
                    })
            else:
                for profile in connections_list:
                    current_profile_info = profile.get('miniProfile', {})
                    if current_profile_info:
                        current_profile_info = {k: v for k, v in current_profile_info.items() if k not in
                                                [
                                                    'firstName',
                                                    'lastName',
                                                    'occupation',
                                                    'picture',
                                                ]}
                        connections.append(current_profile_info)

            results = results + connections

        sleep(
            random.randint(1, 40)
        )

        return self.get_profile_connections_raw(max_results=max_results, results=results, only_urn=only_urn)

    def get_current_profile_urn(self, public_id=None):
        """
        Get profile view statistics, including chart data.
        """
        network_info = self._fetch(
            f"/identity/profiles/{public_id}/networkinfo"
        )

        network_info_data = network_info.json()
        entityUrn = network_info_data.get('entityUrn')

        if entityUrn:
            return get_id_from_urn(entityUrn)

    def sales_login(self, timeout=None):
        request_homepage = self._fetch('https://www.linkedin.com/sales/', raw_url=True, timeout=timeout)
        cookies = requests.utils.dict_from_cookiejar(self.client.session.cookies)
        session_id = cookies.get('JSESSIONID').strip('\"')
        client_page_instance = None

        client_page_instance_data_groups = re.search(r'id="clientPageInstance">([\S\s]*?)<\/code>',
                                                     request_homepage.content.decode())

        if client_page_instance_data_groups:
            client_page_instance = client_page_instance_data_groups.group(1).strip()
            logger.info('Page instance: %s', client_page_instance)

        if not client_page_instance:
            logger.error('No client_page_instance_data_groups groups found %s',
                         client_page_instance_data_groups)
            return [], None, {}

        if not session_id:
            logger.error('Session id not found, cookies: %s', cookies)
            return [], None, {}

        request_sales_api_identity = self._fetch(
            'https://www.linkedin.com/sales-api/salesApiIdentity?q=findLicensesByCurrentMember',
            raw_url=True,
            headers={
                'dnt': '1',
                'accept-encoding': 'gzip, deflate, br',
                'x-li-lang': 'en_US',
                'accept-language': 'en-US,en;q=0.9',
                'x-requested-with': 'XMLHttpRequest',
                'pragma': 'no-cache',
                'accept': '*/*',
                'cache-control': 'no-cache',
                'x-restli-protocol-version': '2.0.0',
                'authority': 'www.linkedin.com',
                'referer': 'https://www.linkedin.com/sales/',
                'Csrf-Token': session_id
            },
            timeout=timeout)

        sales_api_identity_data = request_sales_api_identity.json()

        if sales_api_identity_data.get('elements'):
            element = sales_api_identity_data['elements'][0]
            contractData = {'viewerDeviceType': 'DESKTOP',
                            'name': element['name'],
                            'identity': {'agnosticIdentity': element['agnosticIdentity'],
                                         'name': element['name']}}

            redirect = '/sales/search'
            redirect = urlencode({'redirect': redirect})

            SALES_API_AGONSITC_AUTH_URL = 'https://www.linkedin.com/sales-api/salesApiAgnosticAuthentication?%s' % (
            redirect,)
            request_api_agnostic = self._post(
                SALES_API_AGONSITC_AUTH_URL,
                raw_url=True,
                headers={'Csrf-Token': session_id,
                         'X-Restli-Protocol-Version': '2.0.0',
                         'X-Requested-With': 'XMLHttpRequest',
                         'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                         'X-Li-Page-Instance': client_page_instance,
                         'X-Li-Lang': 'en_US',
                         'Referer': 'https://www.linkedin.com/sales/contract-chooser?redirect=%2Fsales%2Fsearch'},
                data=json.dumps(contractData),
                timeout=timeout
            )

            location = request_api_agnostic.headers.get('Location')

            if location and 'checkpoint/enterprise/login' in location:
                request_enterprise_login = self._fetch(location, raw_url=True, timeout=timeout)

                parsedURL = urlparse(request_enterprise_login.url)
                parsedUrlQs = parse_qs(parsedURL.query)
                salesApiEnterpriseAuthenticationUrl = 'https://www.linkedin.com/sales-api/salesApiEnterpriseAuthentication?accountId=%s&appInstanceId=%s&budgetGroupId=%s&licenseType=%s&viewerDeviceType=DESKTOP'
                salesApiEnterpriseAuthenticationUrl = salesApiEnterpriseAuthenticationUrl % (
                    parsedUrlQs['accountId'][0], parsedUrlQs['appInstanceId'][0],
                    parsedUrlQs['budgetGroupId'][0], parsedUrlQs['licenseType'][0])

                headers = {'Csrf-Token': session_id,
                           'X-Restli-Protocol-Version': '2.0.0',
                           'X-Requested-With': 'XMLHttpRequest'}

                request_enterprise_auth = self._fetch(salesApiEnterpriseAuthenticationUrl,
                                                      raw_url=True, headers=headers, timeout=timeout)

                logger.info('Sales API - logged through %s url, using %s headers', salesApiEnterpriseAuthenticationUrl, headers)
                return request_enterprise_auth

    def get_leads(self, search_url, is_sales=False, timeout=None, get_raw=False,
                  send_sn_requests=True):
        logger.info('Leads quick search with %s timeout. Is Sales %s.', timeout, is_sales)

        if is_sales and send_sn_requests:
            self.sales_login(timeout=timeout)

        self.api_cookies = pickle.dumps(self.client.session.cookies, pickle.HIGHEST_PROTOCOL)
        self.api_headers = pickle.dumps(self.client.session.headers, pickle.HIGHEST_PROTOCOL)

        raw_html_request = self._fetch(search_url, raw_url=True, timeout=timeout)
        raw_html_request.raise_for_status()
        html = raw_html_request.text

        if get_raw:
            return html
        else:
            if is_sales:
                search_hits = get_leads_from_html(html, is_sales=True)
            else:
                search_hits = self.clusters_search_people(search_url)

            parsed_users, \
            pagination, \
            unknown_profiles, \
            limit_data = parse_search_hits(search_hits, is_sales=is_sales)

            if not is_sales:
                # TODO: remove this and merge into parse_search_hits
                logger.info('Use custom pagination data parsing '
                            'for default search (Compatibility)')

                pagination = get_pagination_data(html, is_sales=is_sales)

            if parsed_users:
                # default pagination params can be useful for debugging
                logger.debug('Override pagination, reason: we found parsed_users')
                pagination['logged_in'] = True
                pagination['results_length'] = len(parsed_users)

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

    def connect_with_someone(self, profile_urn_id, message=None):
        """
        Send a message to a given conversation. If error, return true.
        generate_tracking_id is not equal to API, gene
        """
        sleep(
            random.randint(3, 5)
        )  # sleep a random duration to try and evade suspention

        tracking_ids = [
            "/UUnvJmkTzOJJ06YAvOoBQ==",
            "b5sl31fLRsSu9sj07UuEGg==",
            "TbuG5+8HROWK3secP9ANyA==",
            "W0l2S+Y+RGOtvgBL8urqCw==",
            "D0Ol4WlyRG+CkKOWfmh3Eg==",
            "cHuko5LqRHqhfgVwFRMznA==",
            "+NKO3yrsRQWapoeO+n89bQ==",
            "HVoT0u/4QV+R1Na0/y2QFQ==",
            "LtE5LU6JTr2LxsTYD178gA==",
            "MYw79hqeRMmIUHoXJeKZvQ==",
            "q7WIXHYCQLq6r0vv3yPGUg==",
            "MujhS4ehRZGxxG67j5fNuA==",
            "ve0MfXubQA2LtrlyjW5fyg==",
            "K2yBASVLRQ6AfsAUeTUdOg==",
            "HK4tIiAwRr6COtryOy83dQ==",
            "R7A4hMDpQUipIHCCaFW1Dg==",
            "bQ0o99T2TJuhwuiDtCBZbw==",
            "RBnQ8W7DSPiXFIRtQI5W2w==",
            "XY5LRCUmSIOCnRAny+k5DQ==",
            "M3mF+N91Tru8KEScK8xWAw==",
            "vytODa2SR0iMsXxClvBu6g==",
            "1BMhTu89SxWBlo+J2/gdiA==",
            "VguB2Gl0R/W1EtAFy5AviA==",
            "fSyULbVWRDiyxBykagOmNg==",
            "sb2mWmSGRTmRXc9WzH/Pfw=="
        ]

        current_tracking_id = random.choice(tracking_ids)
        payload = {"emberEntityName":"growth/invitation/norm-invitation","invitee":{"com.linkedin.voyager.growth.invitation.InviteeProfile":{"profileId":profile_urn_id}},"trackingId":current_tracking_id}

        if message:
            payload["message"] = message

        res = self._post(
            f"/growth/normInvitations",
            data=json.dumps(payload),
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )

        return res.status_code != 201, res.status_code

    def remove_connection(self, public_profile_id):
        res = self._post(
            f"/identity/profiles/{public_profile_id}/profileActions?action=disconnect",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )

        return res.status_code != 200

    # TODO doesn't work
    # def view_profile(self, public_profile_id):
    #     res = self._fetch(
    #         f"/identity/profiles/{public_profile_id}/profileView",
    #         headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
    #     )

    #     return res.status_code != 200

    def get_profile_privacy_settings(self, public_profile_id):
        res = self._fetch(
            f"/identity/profiles/{public_profile_id}/privacySettings",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )
        if res.status_code != 200:
            return {}

        data = res.json()
        return data.get("data", {})

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
        return data.get("data", {})

    def search_companies(self, keywords=None, **kwargs):
        """Perform a LinkedIn search for companies.
        :param keywords: A list of search keywords (str)
        :type keywords: list, optional
        :return: List of companies
        :rtype: list
        """
        filters = ["resultType->COMPANIES"]

        params = {
            "filters": "List({})".format(",".join(filters)),
            "queryContext": "List(spellCorrectionEnabled->true)",
        }

        if keywords:
            params["keywords"] = keywords

        data = self.search(params, **kwargs)

        results = []
        for item in data:
            if item.get("type") != "COMPANY":
                continue
            results.append(
                {
                    "urn": item.get("targetUrn"),
                    "urn_id": get_id_from_urn(item.get("targetUrn")),
                    "name": item.get("title", {}).get("text"),
                    "headline": item.get("headline", {}).get("text"),
                    "subline": item.get("subline", {}).get("text"),
                }
            )

        return results


    def get_regions(self):
        """
        Get regions directly from linkedin, typehead API
        """

        if isfile(f'all_regions_codes.json'):
            print('Region exist, skip...')
            return

        regions_json = Path(__file__).parent / 'regions.json'
        input_regions = get_default_regions(regions_json)
        self.logger.info('Found %d regions at %s', len(input_regions), regions_json)
        output_regions = {}
        self.client.session.get('https://www.linkedin.com/sales/')
        cookies = self.client.session.cookies.get_dict()
        session_id = cookies.get('JSESSIONID').strip('\"')

        for i, region in enumerate(input_regions):
            region_name = region.get('name')
            region_code = region.get('code')
            # This headers usually outdated, need generate each times...
            headers = {
                'authority': 'www.linkedin.com',
                'pragma': 'no-cache',
                'cache-control': 'no-cache',
                'dnt': '1',
                'x-li-lang': 'en_US',
                'x-li-identity': 'dXJuOmxpOm1lbWJlcjo0MDAzMTE2Nzc',
                'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/84.0.4147.89 Safari/537.36',
                'x-li-page-instance': 'urn:li:page:d_sales2_search_people;g//MAJuSRwe6HvmrIEQK5g==',
                'accept': '*/*',
                'csrf-token': 'ajax:3360386482326297507',
                'x-restli-protocol-version': '2.0.0',
                'x-requested-with': 'XMLHttpRequest',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-mode': 'cors',
                'sec-fetch-dest': 'empty',
                'referer': 'https://www.linkedin.com/sales/search/people?preserveScrollPosition=true&selectedFilter=GE&viewAllFilters=true',
                'accept-language': 'en-GB,en;q=0.9,ru;q=0.8,en-US;q=0.7',
                'cookie': 'lissc=1; _ga=GA1.2.817185630.1595680069; bcookie="v=2&b267a042-616b-4b2f-8f12-4ae0b00f680c"; bscookie="v=1&202007251227483dda7178-a9b8-463e-87c5-fae71bfdf470AQGkrNKYdMPgidyycTCXDN0WiDwAQPLB"; AMCVS_14215E3D5995C57C0A495C55%40AdobeOrg=1; at_check=true; s_cc=true; s_sq=%5B%5BB%5D%5D; mbox=session#bc48aa41231441398889d03093010aed#1595681932|PC#bc48aa41231441398889d03093010aed.37_0#1611232078; aam_uuid=72341840729918861820283121381195274571; li_rm=AQEbogJ3c4XCfQAAAXOF8fMVKJL8RSpaPKWioqVI6ECB4vwm7knvwvQEaqm8AuZJBk3bnhFXv2NpPTtOWAR3mxNLDSpqKIjjCWADG0VwU7WH7uGhVgj3jTHl; spectroscopyId=a5078f81-bc27-41af-a711-227e63e115f5; _guid=f478b4d7-8a29-439f-b233-04efaeeabbd7; PLAY_LANG=en; sdsc=1%3A1SZM1shxDNbLt36wZwCgPgvN58iw%3D; g_state={"i_l":0}; visit=v=1&M; JSESSIONID="ajax:3360386482326297507"; PLAY_SESSION=eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InNlc3Npb25faWQiOiI3MDNjYzllMC1lMjhjLTQ3YmUtYjM4Ny01ZGQ0ODhlOGRlNzZ8MTU5NzI0MTAzNiIsInJlY2VudGx5LXNlYXJjaGVkIjoiIiwicmVmZXJyYWwtdXJsIjoiaHR0cHM6Ly93d3cuZ29vZ2xlLmNvbS8iLCJhaWQiOiIiLCJSTlQtaWQiOiJ8MCIsInJlY2VudGx5LXZpZXdlZCI6IjUwfDYwIiwiQ1BULWlkIjoiTlhGwqbDmUBdwrZvw5rCrTLCi1wiw7HCrSIsImV4cGVyaWVuY2UiOiJicm93c2UiLCJpc19uYXRpdmUiOiJmYWxzZSIsIndoaXRlbGlzdCI6Int9IiwidHJrIjoiIn0sIm5iZiI6MTU5NzI0MTA0NSwiaWF0IjoxNTk3MjQxMDQ1fQ.z0gkmgyyEKS317w--nerjADioRN6p5hSL9Uzh_KHIWM; _gid=GA1.2.1667230570.1597332167; SID=2c815bd8-cc49-4861-8e8e-937459ff91a1; VID=V_2020_08_13_20_99; li_at=AQEDARfcRX0CJ3hDAAABc-nSTPUAAAF0Dd7Q9U0AxW5nioq7GQ3aOYtJ796SZ-ruP2WWLFmaFc6Ngp5GnpLZXgWSa9DOxRNRDFsbGi0xaBkJaYE2dCFby3EPXxQyNtrjlnha9TeuN6oZHaQGJf5sxNq4; liap=true; lang=v=2&lang=en-us; li_a=AQJ2PTEmc2FsZXNfY2lkPTc1NzYyNzUwNSUzQSUzQTIwNDQxNDIwNfj1Et6cQqlojVhAGuYFOZP3HTSk; UserMatchHistory=AQLUjq0xlql-RgAAAXPtlWl2l8b6Z_KYEARu7XWsgZeNuRfwprxW5Uz5C6Mb0UmRnUCvw-9NaQiFhgBsoxd6gdd5gTKIIktaPYNUdptn-31g4AJcx6ZBquMG8It1NVn0qCV6x47XiYquS-8MnMDD9TI8028MmnL2d5SM3_-G8kmUvyTUUnCaYKw2aIyCQgb1VZARP0de2bCZNqQR-7FqMtdz7Mjr1QXmC1GB8C9V-Ojy0--aoNZFblDUX04zZYCsjsQeyCM; UserMatchHistory=AQIeIn6vjq-1JwAAAXPtlXMDHX-673nLhbkEbC6BRunYdON5ArvFoHLYrk3Bf6OE3oOBvWMK0TUxMIvMluZbTOmm9RAoCdo7H4CLvkg_Aihg7g; li_oatml=AQGe_pm9dp8lvgAAAXPtlXUVVH6Zr49QbRZr3XA13k46feZnY8t0TQooOpSmj6o7MlySjoyfU1AoXJfxFKKIFmJoG-9j9XtL; AMCV_14215E3D5995C57C0A495C55%40AdobeOrg=-408604571%7CMCIDTS%7C18488%7CMCMID%7C72541238052820563650226013797033486976%7CMCAAMLH-1598023657%7C6%7CMCAAMB-1598023657%7CRKhpRz8krg2tLO6pguXWp5olkAcUniQYPHaMWWgdJ3xzPWQmdj0y%7CMCOPTOUT-1597426057s%7CNONE%7CvVersion%7C4.6.0%7CMCCIDH%7C1796929203; lidc="b=VB77:s=V:r=V:g=2573:u=275:i=1597418872:t=1597504802:v=1:sig=AQGUSYD1OP1Yl63RaXeJXY9EJXE4nZSr"',
            }

            params = (
                ('q', 'query'),
                ('start', '0'),
                ('type', 'BING_GEO'),
                ('count', '25'),
                ('query', region_name),
            )

            res = requests.get('https://www.linkedin.com/sales-api/salesApiFacetTypeahead',
                                    headers=headers, params=params)

            data = res.json()
            elements = data.get('elements', [])
            subregions = []
            for element in elements:
                if element:
                    subregions.append(element)
                    self.logger.info('Region %d of %d - %s, %s', i, len(input_regions),
                                     region, element)

            output_regions[region_code] = subregions
            logger.debug(output_regions[region_code])

            sleep(random.randint(0, 3))

        return output_regions

    def reformat_results(self, results):
        # search public ids if not exists, use same method like in scrapy search
        processed_results = []
        for i, lead in enumerate(results):
            try:
                if lead.get('entityUrn') and not lead.get('publicIdentifier'):
                    profile = self.get_profile(urn_id=lead.get('entityUrn'))
                    lead['publicIdentifier'] = profile.get('publicIdentifier')

                    # fill additional fields
                    lead['headline'] = profile.get('headline')

                    if 'currentPositions' in lead:
                        for position in lead['currentPositions']:
                            if 'companyName' in position:
                                lead['companyName'] = position['companyName']

                            if 'title' in position:
                                lead['position'] = position['title']
                            break

                    processed_results.append(lead)
                else:
                    processed_results.append(lead)

            except Exception as e:
                processed_results.append(lead)
                logger.warning('Failed get profile data for %s lead', lead,
                               exc_info=e)

            # evade limit each N requests
            if i > 0 and randrange(0, 100) < 10:
                sleep(randrange(10, 15))

        return processed_results

    # TODO: remove this, when ve fix VQ
    def reformat_api_results(self):
        # search public ids if not exists, use same method like in scrapy search
        for i, lead in enumerate(self.results):
            try:
                if lead.get('entityUrn') and not lead.get('publicIdentifier'):
                    profile = self.get_profile(urn_id=lead.get('entityUrn'))
                    lead['publicIdentifier'] = profile.get('publicIdentifier')

                    # fill additional fields
                    lead['headline'] = profile.get('headline')

                    if 'currentPositions' in lead:
                        for position in lead['currentPositions']:
                            if 'companyName' in position:
                                lead['companyName'] = position['companyName']

                            if 'title' in position:
                                lead['position'] = position['title']
                            break

            except Exception as e:
                logger.warning('Failed get profile data for %s lead', lead,
                               exc_info=e)

            # evade limit each N requests
            if i > 0 and randrange(0, 100) < 10:
                sleep(randrange(10, 15))
