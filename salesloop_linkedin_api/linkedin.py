"""
Provides linkedin api-related code
"""
import random
import logging
from time import sleep
from urllib.parse import urlencode
import json
from .utils.helpers import get_leads_from_html

from salesloop_linkedin_api.utils.helpers import get_id_from_urn

from salesloop_linkedin_api.client import Client

logger = logging.getLogger()
from datetime import datetime, timedelta
import salesloop_linkedin_api.settings as settings
import requests
import re
import lxml.html as LH

def default_evade():
    """
    A catch-all method to try and evade suspension from Linkedin.
    Currenly, just delays the request by a random (bounded) time
    """
    sleep(random.randint(2, 5))  # sleep a random duration to try and evade suspention


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
                 ua=None):
        self.logger = logger
        self.client = Client(refresh_cookies=refresh_cookies, debug=debug, proxies=proxies, api_cookies=api_cookies,
                             ua=ua)

        logger.info('Initialize basic linkedin api class...')

        if cached_login:
            self.client.alternate_authenticate()
        else:
            self.client.authenticate(username, password)

        self.api_cookies = self.client.api_cookies
        self.api_headers = self.client.api_headers
        self.results = None
        self.results_length = None

    def _fetch(self, uri, evade=default_evade, **kwargs):
        """
        GET request to Linkedin API
        """
        evade()

        url = f"{self.client.API_BASE_URL}{uri}"
        return self.client.session.get(url, timeout=Linkedin._DEFAULT_GET_TIMEOUT, **kwargs)

    def _post(self, uri, evade=default_evade, **kwargs):
        """
        POST request to Linkedin API
        """
        evade()

        url = f"{self.client.API_BASE_URL}{uri}"
        return self.client.session.post(url, timeout=Linkedin._DEFAULT_POST_TIMEOUT, **kwargs)

    def search(self, params, limit=None, results=[]):
        """
        Do a search.
        """
        count = (
            limit
            if limit and limit <= Linkedin._MAX_SEARCH_COUNT
            else Linkedin._MAX_SEARCH_COUNT
        )
        default_params = {
            "count": str(count),
            "filters": "List()",
            "origin": "GLOBAL_SEARCH_HEADER",
            "q": "all",
            "start": len(results),
            "queryContext": "List(spellCorrectionEnabled->true,relatedSearchesEnabled->true,kcardTypes->PROFILE|COMPANY)",
        }

        default_params.update(params)

        res = self._fetch(
            f"/search/blended?{urlencode(default_params, safe='(),')}",
            headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
        )

        data = res.json()

        new_elements = []
        for i in range(len(data["data"]["elements"])):
            new_elements.extend(data["data"]["elements"][i]["elements"])
            # not entirely sure what extendedElements generally refers to - keyword search gives back a single job?
            # new_elements.extend(data["data"]["elements"][i]["extendedElements"])

        results.extend(new_elements)
        results = results[
            :limit
        ]  # always trim results, no matter what the request returns

        # recursive base case
        if (
            limit is not None
            and (
                len(results) >= limit  # if our results exceed set limit
                or len(results) / count >= Linkedin._MAX_REPEATED_REQUESTS
            )
        ) or len(new_elements) == 0:
            return results

        self.logger.debug(f"results grew to {len(results)}")

        return self.search(params, results=results, limit=limit)

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

    def get_profile(self, public_id=None, urn_id=None):
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

    def get_profile_connections(self, urn_id):
        """
        Return a list of profile ids connected to profile of given [urn_id]
        """
        return self.search_people(connection_of=urn_id, network_depth="F")

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
            self.logger.info("request failed: {}".format(data["message"]))
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

        if data.get('elements'):
            item = data["elements"][0]
            item_id = get_id_from_urn(item["entityUrn"])
            if get_id:
                item = item_id
            else:
                item["id"] = item_id
        else:
            item = None

        return item

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

            return res.status_code != 201
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

            return res.status_code != 201

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
        sleep(
            random.randint(0, 1)
        )  # sleep a random duration to try and evade suspention

        res = self._fetch(f"/me")

        data = res.json()

        return data

    def get_user_panels(self):
        """"
        Return current user profile
        """
        sleep(
            random.randint(0, 1)
        )  # sleep a random duration to try and evade suspention

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

    # def add_connection(self, profile_urn_id):
    #     payload = {
    #         "emberEntityName": "growth/invitation/norm-invitation",
    #         "invitee": {
    #             "com.linkedin.voyager.growth.invitation.InviteeProfile": {
    #                 "profileId": profile_urn_id
    #             }
    #         },
    #     }

    #     print(payload)

    #     res = self._post(
    #         "/growth/normInvitations",
    #         data=payload,
    #         headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
    #     )

    #     return res.status_code != 201

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
        networkinfo = self._fetch(
            f"/identity/profiles/{public_id}/networkinfo"
        )

        networkinfo_data = networkinfo.json()
        entityUrn = networkinfo_data.get('entityUrn')

        if entityUrn:
            return get_id_from_urn(entityUrn)

    def get_leads(self, search_url, is_sales=False, timeout=None):
        if not timeout:
            timeout = Linkedin._DEFAULT_GET_TIMEOUT

        logger.info('Leads quick search with %s timeout', timeout)

        if is_sales:
            r1 = self.client.session.get('https://www.linkedin.com/sales/', timeout=timeout)
            cookies = self.client.session.cookies.get_dict()
            session_id = cookies.get('JSESSIONID').strip('\"')
            client_page_instance_data_groups = re.search(r'(urn\:li\:page\:d_sales2_contract_chooser.*?)\n', r1.content.decode())
            if client_page_instance_data_groups:
                client_page_instance = client_page_instance_data_groups.group(1).strip()
            else:
                return []

            r2 = self.client.session.get('https://www.linkedin.com/sales-api/salesApiIdentity?q=findLicensesByCurrentMember',
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


            data = r2.json()
            element = data['elements'][0]
            contractData = {'viewerDeviceType': 'DESKTOP',
                            'name': element['name'],
                            'identity': {'agnosticIdentity': element['agnosticIdentity'],
                                         'name': element['name']}}

            redirect = '/sales/search'
            redirect = urlencode({'redirect': redirect})

            r3 = self.client.session.post('https://www.linkedin.com/sales-api/salesApiAgnosticAuthentication?%s' % (redirect,),
                                  headers={'Csrf-Token': session_id,
                                           'X-Restli-Protocol-Version': '2.0.0',
                                           'X-Requested-With': 'XMLHttpRequest',
                                           'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                           'X-Li-Page-Instance': client_page_instance,
                                           'X-Li-Lang': 'en_US',
                                           'Referer': 'https://www.linkedin.com/sales/contract-chooser?redirect=%2Fsales%2Fsearch'
                                           },
                                  data=json.dumps(contractData),
                                  timeout=timeout)

        html = self.client.session.get(search_url, timeout=timeout).content
        return get_leads_from_html(html, is_sales=is_sales, get_pagination=True)

    def random_user_actions(self, public_id=None, force_check=False):
        action = random.randint(1, 4)
        results = []

        if force_check:
            results.append(self.get_user_profile())
            results.append(self.get_user_panels())
            results.append(self.get_profile_network_info(public_id))
            results.append(self.get_current_profile_urn(public_id))
        else:
            if action == 1:
                results.append(self.get_user_profile())
            elif action == 2:
                results.append(self.get_user_profile())
            elif action == 3 and public_id:
                results.append(self.get_profile_network_info(public_id))
            elif action == 3:
                results.append(self.get_user_profile())
            elif public_id:
                results.append(self.get_current_profile_urn(public_id))
            else:
                results.append(self.get_user_profile())

        return results

    def connect_with_someone(self, profile_urn_id, message=None, get_json=False):
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

        if get_json:
            return res.status_code != 201, res.status_code, res.json()
        else:
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
