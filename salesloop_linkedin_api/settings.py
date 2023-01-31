import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGIN_TIMEOUT = float(os.getenv("LINKEDIN_API_LOGIN_TIMEOUT", 220))
REQUEST_TIMEOUT = float(os.getenv("LINKEDIN_API_REQUEST_TIMEOUT", 220))

# statistics TTL 1 month, stored in redis
STATISTICS_TTL = int(os.getenv("LINKEDIN_API_STATISTICS_TTL", 2592000))

OLD_ACCOUNT_MIN_CONNECTIONS = 5000

# Based on https://linkedin.api-docs.io/v1.0
REQUESTS_TYPES = {
    "growth": (
        "growth/pageContent",
        "growth/channels",
        "growth/normInvitations",
    ),
    "jobs": ("entities/jobs",),
    "relationships": (
        "relationships/invitations",
        "relationships/invitationViews",
        "relationships/sentInvitationViewsV2",
        "relationships/badge",
        "relationships/peopleYouMayKnow",
        "relationships/invitationsSummary",
        "relationships/connectionsSummary",
        "relationships/connections",
    ),
    "companies": ("entities/companies", "organization/companies",),
    "search": (
        "search/history",
        "search/hits",
        "search/blended",
        "search/results",
        "search/dash",
    ),
    "feed": (
        "feed/updates",
        "feed/urlpreview",
        "feed/packageRecommendations",
        "feed/updates",
        "feed/social",
        "feed/urlpreview",
        "feed/likes",
        "feed/social",
    ),
    "messaging": (
        "messaging/conversations",
        "messaging/badge",
        "messaging/conversations",
        "messaging/stickerpacks",
        "messaging/conversations",
    ),
    "identity": (
        "identity/cards",
        "identity/profiles",
        "me",
        "identity/ge",
        "identity/badge",
        "identity/profiles",
        "identity/wvmpCards",
        "identity/panels",
    ),
    "other": (
        "legoWidgetActionEvents",
        "legoPageImpressionEvents",
        "fileUploadToken",
        "pushRegistration",
        "takeovers",
        "pushRegistration",
        "appUniverse",
        "lite/rum-track",
        "csp/sct",
        "li/track",
        "mux",
        "typeahead/hits",
        "legoWidgetImpressionEvents",
        "psettings/premium-subscription",
    ),
    "uas": ("uas/authenticate",),
}

# Maximum number of requests per day
# we use these multiples to calculate the requests limits
# 1. New account
# 2. Account with 5000 connections
# 3. Account with 5000 connections and premium subscription
# TODO: use actual values
BASE_REQUESTS_LIMITS = {
    "growth": (100, 100, 100),
    "jobs": (100, 100, 100),
    "relationships": (100, 100, 100),
    "companies": (100, 100, 100),
    "search": (500, 500, 500),
    "feed": (100, 100, 100),
    "messaging": (100, 100, 100),
    "identity": (100, 100, 100),
    "other": (100, 100, 100),
    "uas": (100, 100, 100),
}


def get_account_requests_limits(connections_number: int,
                                is_premium: bool):
    """
    Get account requests limits based on connections_number and is_premium
    Args:
        connections_number: number of connections
        is_premium: is LinkedIn premium account

    Returns: dict - requests limits per request type

    """

    new = 0
    old = 1  # OLD_ACCOUNT_MIN_CONNECTIONS
    old_premium = 2  # OLD_ACCOUNT_MIN_CONNECTIONS + premium

    account_requests_limits = {}

    for request_type, limits in BASE_REQUESTS_LIMITS.items():
        if connections_number < OLD_ACCOUNT_MIN_CONNECTIONS:
            account_requests_limits[request_type] = limits[new]
        elif is_premium:
            account_requests_limits[request_type] = limits[old_premium]
        else:
            account_requests_limits[request_type] = limits[old]

    return account_requests_limits


