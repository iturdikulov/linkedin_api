import pytest

from application.models import LinkedinLogins, AppUsers, Tasks
from application.salesloop_get_api import get_api
from application.send_invites import check_linkedin_account_state
from tests.config import LINKEDIN_API_ACCOUNT_ID, LINKEDIN_RECRUITER_ACCOUNT_ID
from tests.lib import get_app

flask_app = get_app()


def test_api():
    with flask_app.app_context():
        # Login using cached cookies
        code, message, api = get_api(
            LINKEDIN_API_ACCOUNT_ID,
            search_query_url=None,
            invalidate_linkedin_account=False,
            get_connections_summary=True,
        )

        assert code == 200, message is None


def test_check_linkedin_account_state(linkedin_api):
    """
    Test account invalidation (semi-manual, TODO: this require to complete/add assert)
    NOTE: stub test
    """
    with flask_app.app_context():
        code, message, api = get_api(
            LINKEDIN_API_ACCOUNT_ID,
            search_query_url=None,
            invalidate_linkedin_account=False,
            get_connections_summary=True,
        )

        # check linkedin_login
        linkedin_login = LinkedinLogins.query.filter_by(id=LINKEDIN_API_ACCOUNT_ID).first()
        if linkedin_login:
            app_user = AppUsers.query.filter_by(id=int(linkedin_login.user_id)).first()

        current_task = Tasks.query.filter_by(id=27).first()

        code = 400
        linkedin_login.failed_login_attempts = 5
        check_linkedin_account_state(api, app_user, code, current_task, linkedin_login)


def test_api_disconnected():
    pass


def test_iterate_until_captcha():
    pass


def test_iterate_until_pin_code():
    pass


def test_mfa_login():
    pass


@pytest.mark.skip(reason="Not used")
def test_api_recruiter():
    with flask_app.app_context():
        code, message, api = get_api(
            LINKEDIN_RECRUITER_ACCOUNT_ID,
            search_query_url=None,
            invalidate_linkedin_account=False,
            get_connections_summary=True,
        )

        api.requiter_login()
