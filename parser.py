from datetime import UTC, datetime
from json import JSONDecodeError
from bs4 import BeautifulSoup
from flask import json
from application.integrations.linkedin.utils import get_object_by_path
from salesloop_linkedin_api.utils.helpers import get_id_from_urn, logger
import re

class ProfileParsingError(Exception):
    pass

def profile_element_urn(element_name, profile_item):
    profile_elements = profile_item[element_name].get("*elements")
    return profile_elements[0] if profile_elements else None


def extract_included_item(item_type, item_entity_urn, included):
    for item in included:
        if item["$type"] == item_type and item["entityUrn"] == item_entity_urn:
            return item

    raise ProfileParsingError("item not found")


def extracte_code_chunks(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    code_chunks = soup.find_all("code")
    return code_chunks


def parse_profile(response_data: dict) -> dict:
    # TODO: need validate is somewhere used this fields,
    # they are were removed from parser
    # spider name?
    # Removed
    # memberId
    # degree
    # educations
    # canSendInMail
    # premium
    # companyid
    # languages
    # location
    # tags
    # degreename

    included = response_data["included"]
    try:
        item_entity_urn = response_data["data"]["data"]["identityDashProfilesByMemberIdentity"]["*elements"][0]
        profile_item = extract_included_item(
            item_entity_urn=item_entity_urn,
            item_type="com.linkedin.voyager.dash.identity.profile.Profile",
            included=included,
        )

        member_distance = extract_included_item(
            item_entity_urn=f"urn:li:fsd_memberRelationship:{get_id_from_urn(profile_item['entityUrn'])}",
            item_type="com.linkedin.voyager.dash.relationships.MemberRelationship",
            included=included,
        )
    except (KeyError, IndexError, TypeError):
        raise ProfileParsingError("profile not found, can't parse it")

    if not profile_item:
        raise ProfileParsingError("profile not found")

    # Extract base profile data
    profile_data = {}
    keys_to_extract = {
        "publicIdentifier": "publicIdentifier",
        "firstName": "firstname",
        "lastName": "lastname",
        "headline": "headline",
        "entityUrn": "entityUrn",
    }

    for key, nkey in keys_to_extract.items():
        try:
            value = profile_item[key]
        except KeyError:
            raise ProfileParsingError(f"Can't find key {key} in profile item")

        profile_data[nkey] = value  # We use own key names,
        # this is why nkey is used here


    # Check user connected with us
    profile_data["connection"] = member_distance["memberRelationship"].get("*connection")

    # Fill generated data and adjust some fields
    profile_data["entityUrn"] = get_id_from_urn(profile_data["entityUrn"])
    profile_data["profilelink"] = f"https://www.linkedin.com/in/{profile_data['publicIdentifier']}"
    profile_data["fullname"] = f"{profile_data['firstname']} {profile_data['lastname']}"
    profile_data[
        "profileLinkSN"
    ] = f"https://www.linkedin.com/sales/people/{profile_data['entityUrn']}"

    return profile_data


def parse_profile_from_source(html: str) -> dict:
    profile_item = None
    code_chunks = extracte_code_chunks(html)
    for code in code_chunks:
        code_text = code.get_text()

        try:
            chunk = json.loads(code_text)
            chunk_data = chunk["data"]
        except (JSONDecodeError, KeyError):
            logger.debug(f"Can't parse chunk: {code_text}")
            continue

        try:
            chunk_type = chunk_data["$type"]
            elements = chunk["data"]["*elements"]
        except KeyError:
            logger.debug("Can't find type or elements in chunk, trying next parsing mode")

        try:
            identity_block = chunk_data["data"]["identityDashProfilesByMemberIdentity"]
            if not identity_block:
                logger.debug(f"chunk is empty or not valid: {chunk_data['data']}")
                continue

            chunk_type = identity_block["$type"]
            elements = identity_block["*elements"]
        except (KeyError, IndexError):
            logger.debug(f"chunk is empty or not valid: {code_text}")
            continue

        try:
            if chunk_type == "com.linkedin.restli.common.CollectionResponse":
                element = elements[0]
                included = chunk["included"]
                profile_item = extract_included_item(
                    item_type="com.linkedin.voyager.dash.identity.profile.Profile",
                    item_entity_urn=element,
                    included=included,
                )
                break
        except (KeyError, IndexError):
            logger.debug(f"chunk is empty or not valid: {code_text}")
            continue

    if not profile_item:
        raise ProfileParsingError("profile data not found")

    # Extract base profile data
    profile_data = {}
    keys_to_extract = {
        "publicIdentifier": "public_id",
        "firstName": "first_name",
        "lastName": "last_name",
        "headline": "headline",
        "entityUrn": "URN",
    }

    for key, nkey in keys_to_extract.items():
        try:
            value = profile_item[key]
        except KeyError:
            raise ProfileParsingError(f"Can't find key {key} in profile item")

        profile_data[nkey] = value  # We use own key names,
        # this is why nkey is used here

    # Fill generated data and adjust some fields
    profile_data["URN"] = get_id_from_urn(profile_data["URN"])
    return profile_data


# def parse_ln_sn_profile(profile: dict) -> dict:
#     # Extract full entity urn
#     match = re.search(r"\(([^)]+)\)", profile["entityUrn"])
#     if not match:
#         raise ValueError(f"No objectUrn found for {profile}")
#     full_entity_urn = match.group(1)
#
#     short_entity_urn = full_entity_urn.split(",")[0].split("(")[0]
#     logger.debug("Short entity urn: %s", short_entity_urn)
#     logger.debug("Full entity urn: %s", full_entity_urn)
#
#     # Extract public id
#     proflie_url = profile["flagshipProfileUrl"]
#     public_id = proflie_url.split("/in/", 1)[1]
#
#     profile_data = {
#         "public_id": public_id,
#         "firstname": profile["firstName"],
#         "full_name": profile["fullName"],
#         "lastname": profile["lastName"],
#         "headline": profile["headline"],
#         "URN": short_entity_urn,
#         "profileLinkSN": f"https://www.linkedin.com/sales/lead/{full_entity_urn}",
#     }
#
#     # Extract position company name
#     default_position = profile.get("defaultPosition")
#     if default_position and default_position.get("current"):
#         profile_data["companyName"] = default_position["companyName"]
#         profile_data["title"] = default_position["title"]
#
#     return profile_data


def parse_profile_cards(response_data) -> dict:
    item_entity_urn = response_data["data"]["data"]["identityDashProfileCardsByDeferredCards"][
        "*elements"
    ][0]
    item_entity_urn = get_id_from_urn(item_entity_urn)

    profile_item = extract_included_item(
        item_type="com.linkedin.voyager.dash.identity.profile.Profile",
        item_entity_urn=f"urn:li:fsd_profile:{item_entity_urn}",
        included=response_data["included"],
    )

    picture = get_object_by_path(
        profile_item, "profilePicture.displayImageReferenceResolutionResult.vectorImage"
    )

    profile_card = {"profilePicture": None}
    if picture and isinstance(picture, dict):
        segment = get_object_by_path(picture, "artifacts.2.fileIdentifyingUrlPathSegment")
        root_url = get_object_by_path(picture, "rootUrl")
        if segment and root_url:
            profile_card["profilePicture"] = f"{root_url}{segment}"

    return profile_card


def parse_profile_contacts(response_data) -> dict:
    contact_info = {
        "email_address": None,
        "phone_numbers": None,
        "twitter": None,
        "birthdate": None,
        "websites": [],
    }

    profile_item = extract_included_item(
        item_type="com.linkedin.voyager.dash.identity.profile.Profile",
        item_entity_urn=response_data["data"]["data"]["identityDashProfilesByMemberIdentity"][
            "*elements"
        ][0],
        included=response_data["included"],
    )

    if not profile_item:
        return contact_info

    email = (profile_item.get("emailAddress", {}) or {}).get("emailAddress") or None
    phone_numbers = []
    for item in profile_item.get("phoneNumbers", []):
        number = item.get("phoneNumber", {}).get("number")
        phone_numbers.append(number)

    contact_info["email_address"] = email
    contact_info["phone_numbers"] = phone_numbers
    # TODO: data is untested, need to check
    contact_info["twitter"] = profile_item.get("twitterHandles")
    contact_info["birthdate"] = profile_item.get("birthDateOn")
    contact_info["websites"] = []

    for website in profile_item.get("websites", []):
        if website["$type"] == "com.linkedin.voyager.dash.identity.profile.Website":
            contact_info["websites"].append(website["url"])

    return contact_info


def parse_messenger_messages(response_data: dict) -> list:
    """Extract limited fields from messenger messages response"""
    elements = response_data["data"]["messengerMessagesBySyncToken"]["elements"]
    messages = []

    for i, message in enumerate(elements):
        sender = message["sender"]["participantType"]["member"]

        messages.append(
            {
                "body": message["body"]["text"],
                "profileUrl": sender["profileUrl"],
                "sender_distance": sender["distance"],
                "delivered_at": datetime.fromtimestamp(message["deliveredAt"] / 1000, tz=UTC),
            }
        )

    return messages
