from datetime import timedelta
from random import shuffle

import pytest

from tests.config import (
    LINKEDIN_DEFAULT_SEARCH_URL,
    LINKEDIN_SALES_NAV_SEARCH_URL,
    LINKEDIN_SALES_ACCOUNT_ID,
    LINKEDIN_API_ACCOUNT_ID,
)
from tests.lib import validate_json_schema


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_invitations(linkedin_api):
    assert linkedin_api.get_invitations(), linkedin_api.get_invitations(start=10, limit=10)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_invitations_summary(linkedin_api):
    invitations_summary = linkedin_api.get_invitations_summary()
    validate_json_schema("invitations_summary", invitations_summary)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_sent_invitations(linkedin_api):
    sent_invitations = linkedin_api.get_sent_invitations()
    validate_json_schema("sent_invitations", sent_invitations)

    sent_invitations = linkedin_api.get_sent_invitations(start=20)
    validate_json_schema("sent_invitations", sent_invitations)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_invites_sent_per_interval(linkedin_api):
    INTERVAL = timedelta(days=31).total_seconds()
    invites_sent = linkedin_api.get_invites_sent_per_interval(INTERVAL)
    assert len(invites_sent) >= 0


def test_get_company(linkedin_api):
    company = linkedin_api.get_company("Linkedin")
    assert company["name"] == "LinkedIn"
    validate_json_schema("company", company)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_SALES_ACCOUNT_ID], indirect=True)
def test_sales_login(linkedin_api):
    linkedin_api.sales_login()
    parsed_users, pagination, unknown_profiles, limit_data = linkedin_api.get_leads(
        search_url=LINKEDIN_SALES_NAV_SEARCH_URL, send_sn_requests=False, is_sales=True
    )
    assert len(parsed_users) == 25


def test_connect_with_someone(linkedin_api):
    search_url = LINKEDIN_DEFAULT_SEARCH_URL

    parsed_users, pagination, unknown_profiles, limit_data = linkedin_api.get_leads(
        search_url=search_url
    )

    shuffle(parsed_users)

    assert len(parsed_users) >= 2

    first_entity_urn = parsed_users[0]["entityUrn"]
    second_entity_urn = parsed_users[1]["entityUrn"]

    first_entity_connected, status_code = linkedin_api.connect_with_someone(first_entity_urn)
    assert status_code in [200, 400]
    second_entity_connected, status_code = linkedin_api.connect_with_someone(
        second_entity_urn, message="Hello, can we connect?"
    )
    assert status_code in [200, 400]

    # TODO: we can parse sent connections and check is connections were sent
    assert first_entity_connected, second_entity_connected


def test_reformat_results(linkedin_api):
    linkedin_users, pagination, unknown_profiles, limit_data = linkedin_api.get_leads(
        LINKEDIN_DEFAULT_SEARCH_URL
    )
    reformat_results = linkedin_api.reformat_results(linkedin_users)

    assert all((linkedin_users, reformat_results, len(linkedin_users) == len(reformat_results)))
    validate_json_schema("default_search_people", linkedin_users)
    validate_json_schema("default_search_people", reformat_results)


@pytest.mark.skip(reason="Not used")
def test_reply_invitation(linkedin_api):
    pass


@pytest.mark.skip(reason="Not used")
def test_get_company_updates(linkedin_api):
    pass


@pytest.mark.skip(reason="Not used")
def test_get_school(linkedin_api):
    school = linkedin_api.get_school("Harvard College")
    validate_json_schema("school", school)


@pytest.mark.skip(reason="We run this in search - get_api method")
def test_get_leads(linkedin_api):
    pass


@pytest.mark.skip(reason="Very specific, not required to test")
def test_get_regions(linkedin_api):
    regions = linkedin_api.get_regions()
    validate_json_schema("regions", regions)


@pytest.mark.skip(reason="Not used")
def test_accept_invitation(linkedin_api):
    """
    NOTE: this test relies on the existence of invitations. If you'd like to test this
    functionality, make sure the test account has at least 1 invitation.
    """
    invitations = linkedin_api.get_invitations(linkedin_api)
    if not invitations:
        # If we've got no invitations, just force test to pass
        assert True
        return
    num_invitations = len(invitations)
    invite = invitations[0]
    invitation_response = linkedin_api.reply_invitation(
        invitation_entity_urn=invite["entityUrn"],
        invitation_shared_secret=invite["sharedSecret"],
        action="accept",
    )
    assert invitation_response

    invitations = linkedin_api.get_invitations(linkedin_api)
    assert len(invitations) == num_invitations - 1


@pytest.mark.skip(reason="Not used")
def test_remove_connection(linkedin_api):
    pass


@pytest.mark.skip(reason="Not used")
def test_reject_invitation(linkedin_api):
    """
    NOTE: this test relies on the existence of invitations. If you'd like to test this
    functionality, make sure the test account has at least 1 invitation.
    """
    invitations = linkedin_api.get_invitations(linkedin_api)
    if not invitations:
        # If we've got no invitations, just force test to pass
        assert True
        return
    num_invitations = len(invitations)
    invite = invitations[0]
    invitation_response = linkedin_api.reply_invitation(
        invitation_entity_urn=invite["entityUrn"],
        invitation_shared_secret=invite["sharedSecret"],
        action="reject",
    )
    assert invitation_response

    invitations = linkedin_api.get_invitations(linkedin_api)
    assert len(invitations) == num_invitations - 1
