import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGIN_TIMEOUT = float(os.getenv("LINKEDIN_API_LOGIN_TIMEOUT", 220))
REQUEST_TIMEOUT = float(os.getenv("LINKEDIN_API_REQUEST_TIMEOUT", 220))

# Based on https://linkedin.api-docs.io/v1.0
REQUESTS_TYPES = {
    "growth": (
        "voyagerGrowthEmailConfirmationTask",
        "growth/pageContent/voyager_abi_flow",
        "growth/channels/{param1}",
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
    "companies": ("entities/companies", "organization/companies"),
    "search": ("search/history", "search/hits", "/search/blended", "/search/dash"),
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
        "/lite/rum-track",
        "/csp/sct",
        "/li/track",
        "mux",
        "typeahead/hits",
        "legoWidgetImpressionEvents",
        "psettings/premium-subscription",
    ),
    "uas": ("uas/authenticate",),
}
