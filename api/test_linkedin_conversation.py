import time
from datetime import datetime
from random import choice

import pytest

from tests.config import (
    CONVERSATION_PROFILE_ID,
    CONVERSATION_URN_ID,
    CONVERSATION_RANDOM_PHRASES,
    OLDEST_CONVERSATION_DATE_MS,
    MESSAGE_SENT_MAX_TIME,
    LINKEDIN_API_ACCOUNT_ID,
)
from tests.lib import validate_json_schema


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_conversation_details(linkedin_api):
    profile_urn = linkedin_api.get_current_profile_urn(CONVERSATION_PROFILE_ID)
    conversation_details = linkedin_api.get_conversation_details(profile_urn)
    validate_json_schema("conversation_details", conversation_details)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_conversations(linkedin_api):
    conversations = linkedin_api.get_conversations()
    validate_json_schema("conversations", conversations)

    conversations = linkedin_api.get_conversations(createdBefore=OLDEST_CONVERSATION_DATE_MS)
    validate_json_schema("conversations", conversations)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_get_conversation(linkedin_api):
    conversation = linkedin_api.get_conversation(CONVERSATION_URN_ID)
    validate_json_schema("conversation", conversation)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def validate_last_message_data(linkedin_api, phrase):
    # Checking is message sent
    conversation = linkedin_api.get_conversation(CONVERSATION_URN_ID)
    last_message_object = conversation["elements"][-1]
    last_message = last_message_object["eventContent"][
        "com.linkedin.voyager.messaging.event.MessageEvent"
    ]["attributedBody"]["text"]
    created_at = last_message_object["createdAt"]
    created_at_time = datetime.fromtimestamp(created_at / 1000)
    created_at_timedelta_sec = (datetime.utcnow() - created_at_time).total_seconds()

    # Delay before checking the sent message
    time.sleep(2)

    assert last_message == phrase, created_at_timedelta_sec < MESSAGE_SENT_MAX_TIME


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_send_message(linkedin_api):
    phrase = choice(CONVERSATION_RANDOM_PHRASES)

    message_sent = linkedin_api.send_message(
        conversation_urn_id=CONVERSATION_URN_ID,
        message_body=phrase,
    )

    assert message_sent

    validate_last_message_data(linkedin_api, phrase)


@pytest.mark.parametrize("linkedin_api", [LINKEDIN_API_ACCOUNT_ID], indirect=True)
def test_send_message_to_recipients(linkedin_api):
    phrase = choice(CONVERSATION_RANDOM_PHRASES)

    profile_urn = linkedin_api.get_current_profile_urn(CONVERSATION_PROFILE_ID)
    message_sent = linkedin_api.send_message(recipients=[profile_urn], message_body=phrase)
    assert message_sent

    validate_last_message_data(linkedin_api, phrase)
