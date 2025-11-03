import json
from json.decoder import JSONDecodeError
from pathlib import Path
from bs4 import BeautifulSoup

from application.config import CURRENT_DIR
from tests.lib import logger

CURRENT_DIR = Path(CURRENT_DIR).resolve()
LINKEDIN_FEED_FILE = CURRENT_DIR / ".." / "tests" / "api" / "ln_feed.html"
LINKEDIN_PROFILE_FILE = CURRENT_DIR / ".." / "tests" / "api" / "ln_profile.html"


def load_html():
    with open(LINKEDIN_FEED_FILE) as f:
        html = f.read()
    return html


def profile_chunk(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    code_chunks = soup.find_all("code")
    SEARCH_TYPE = "com.linkedin.voyager.common.Me"

    for code in code_chunks:
        try:
            chunk = json.loads(code.get_text())
            chunk_type = chunk.get("data", {}).get("$type")
            if chunk_type == SEARCH_TYPE:
                return chunk
        except JSONDecodeError:
            logger.error("Failed to parse item... %s", code, exc_info=True)

    raise ValueError("Profile chunk not found")


def parse_user_profile(profile: dict) -> dict:
    data = profile["data"]
    included = profile["included"][0]

    return {
        "premiumSubscriber": data["premiumSubscriber"],
        "firstName": included["firstName"],
        "lastName": included["lastName"],
        "objectUrn": included["objectUrn"],
        "entityUrn": included["entityUrn"],
        "publicIdentifier": included["publicIdentifier"],
        "trackingId": included["trackingId"],
    }


def test_parse_code_chunks():
    html = load_html()

    # Check that we can find profile chunk
    profile = profile_chunk(html)
    profile_data = parse_user_profile(profile)
    for key, value in profile_data.items():
        logger.info("%s: %s", key, value)
        assert value is not None and isinstance(value, (str, bool))

    logger.info("Profile data: %s", profile_data)
