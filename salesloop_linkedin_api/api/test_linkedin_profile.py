from salesloop_linkedin_api.linkedin import Linkedin
from tests.config import TEST_PROFILE_ID
from tests.lib import validate_json_schema

PROFILE_CONNECTIONS_MAX_RESULTS = 39


# TODO: port to v2, auth
def test_get_profile(linkedin_api: Linkedin):
    profile = linkedin_api.get_profile_data(public_id=TEST_PROFILE_ID)

    # Test that profile is not empty
    for k, v in profile.items():
        assert k, v


def test_get_current_profile_urn(linkedin_api):
    profile_urn = linkedin_api.get_current_profile_urn()
    assert profile_urn, isinstance(profile_urn, str)


def test_get_connections_summary(linkedin_api):
    connections_summary = linkedin_api.get_connections_summary()
    validate_json_schema("connections_summary", connections_summary)


def test_get_profile_contact_info(linkedin_api):
    contact_info = linkedin_api.get_profile_contact_info(TEST_PROFILE_ID)
    validate_json_schema("contact_info", contact_info)


def test_get_profile_skills(linkedin_api):
    profile_skills = linkedin_api.get_profile_skills(TEST_PROFILE_ID)
    validate_json_schema("profile_skills", profile_skills)


def test_get_profile(linkedin_api):
    linkedin_profile = linkedin_api.get_profile(urn_id=TEST_PROFILE_ID)
    validate_json_schema("profile", linkedin_profile)

    first_name = linkedin_profile["firstName"]
    last_name = linkedin_profile["lastName"]
    headline = linkedin_profile["headline"]
    location = linkedin_profile["locationName"]

    for field in (first_name, last_name, headline, location):
        assert len(field) > 0, field


def test_get_profile_connections(linkedin_api: Linkedin):
    profile_urn = linkedin_api.get_current_profile_urn()
    connections = linkedin_api.get_profile_connections(
        profile_urn, limit=PROFILE_CONNECTIONS_MAX_RESULTS
    )
    assert connections
    assert len(connections) <= PROFILE_CONNECTIONS_MAX_RESULTS
    validate_json_schema("profile_connections", connections)


def test_get_profile_connections_raw(linkedin_api: Linkedin):
    connections = linkedin_api.get_profile_connections_raw(
        max_results=PROFILE_CONNECTIONS_MAX_RESULTS
    )
    public_identifiers = [c["publicIdentifier"] for c in connections if c["publicIdentifier"]]
    assert connections
    assert len(set(public_identifiers)) == len(public_identifiers)
    assert len(connections) <= PROFILE_CONNECTIONS_MAX_RESULTS
    validate_json_schema("profile_connections_raw", connections)


def test_get_current_profile_views(linkedin_api):
    # We use some old profiles with existing views
    current_profile_views = linkedin_api.get_current_profile_views()
    assert isinstance(current_profile_views, int), current_profile_views


def test_get_user_profile(linkedin_api: Linkedin):
    user_profile = linkedin_api.get_user_profile()
    validate_json_schema("user_profile", user_profile)


def test_get_premium_subscription(linkedin_api):
    premium_subscription = linkedin_api.get_premium_subscription()
    validate_json_schema("premium_subscription", premium_subscription)


def test_get_user_panels(linkedin_api):
    user_panels = linkedin_api.get_user_panels()
    validate_json_schema("user_panels", user_panels)


def test_get_profile_member_badges(linkedin_api):
    profile_member_badges = linkedin_api.get_profile_member_badges(TEST_PROFILE_ID)
    validate_json_schema("profile_member_badges", profile_member_badges)


def test_get_profile_network_info(linkedin_api: Linkedin):
    profile_network_info = linkedin_api.get_profile_network_info(TEST_PROFILE_ID)
    validate_json_schema("profile_network_info", profile_network_info)
