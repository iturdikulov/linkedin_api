import pytest

from tests.config import LINKEDIN_SALES_ACCOUNT_ID, LINKEDIN_SALES_NAV_SEARCH_URL


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_SALES_ACCOUNT_ID], indirect=True)
def test_sales_login(linkedin_api):
    linkedin_api.sales_login()
    parsed_users, pagination, unknown_profiles, limit_data = linkedin_api.get_leads(
        search_url=LINKEDIN_SALES_NAV_SEARCH_URL, send_sn_requests=False, is_sales=True
    )
    assert len(parsed_users) == 25
