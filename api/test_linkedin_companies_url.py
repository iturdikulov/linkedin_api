from application.salesloop_get_api import get_api
from tests.config import LINKEDIN_API_ACCOUNT_ID
from tests.lib import get_app

flask_app = get_app()


def test_linkedin_companies_url():
    with flask_app.app_context():
        parsed_leads = {
            "millicom-international-cellular-tigo": {
                "id": "12836369",
                "name": "MILLICOM INTERNATIONAL CELLULAR",
                "company_id": None,
                "country_code": "sv",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/millicom-international-cellular-tigo",
                    "https://twitter.com/millicom",
                    "http://www.crunchbase.com/organization/millicom-systems",
                ],
            },
            "central-kentucky-educational-cooperative": {
                "id": "12829585",
                "name": "Central Kentucky Educational Cooperative",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/central-kentucky-educational-cooperative",
                    "https://twitter.com/ckyec",
                    "https://www.facebook.com/pages/central-kentucky-educational-cooperative/181498181878333",
                ],
            },
            "harding-university-17753": {
                "id": "12829075",
                "name": "Harding University",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/edu/harding-university-17753",
                    "https://twitter.com/hardingu",
                    "https://www.facebook.com/hardingu",
                    "http://www.crunchbase.com/organization/harding-university-searcy",
                ],
            },
            "loughborough-university": {
                "id": "12828161",
                "name": "Mathematics Learning Support Centre",
                "company_id": None,
                "country_code": "gb",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/loughborough-university",
                    "https://twitter.com/lborouniversity",
                    "http://www.crunchbase.com/organization/loughborough-university",
                    "https://youtube.com/user/lborouniversity",
                ],
            },
            "passion-gaming": {
                "id": "12780233",
                "name": "Passion Gaming",
                "company_id": None,
                "country_code": "in",
                "valid": False,
                "social_urls": ["https://www.linkedin.com/company/passion-gaming"],
            },
            "flsmidth": {
                "id": "12778254",
                "name": "FLSmidth",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/flsmidth",
                    "https://twitter.com/flsmidth",
                    "http://www.crunchbase.com/organization/flsmidth",
                    "https://youtube.com/user/flsmidth",
                ],
            },
            "minneapolis-college-of-art-and-design-18662": {
                "id": "12777360",
                "name": "Minneapolis College Of Art and Design",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/edu/minneapolis-college-of-art-and-design-18662",
                    "https://twitter.com/mcad",
                    "https://www.facebook.com/pages/minneapolis-college-of-art-design/53327920220",
                    "https://instagram.com/mcadedu",
                    "http://www.crunchbase.com/organization/minneapolis-college-of-art-and-design",
                ],
            },
            "school": {
                "id": "12774496",
                "name": "University of Maryland",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/edu/school",
                    "https://twitter.com/uofmaryland",
                    "http://www.crunchbase.com/organization/university-of-maryland",
                ],
            },
            "bwx-technologies": {
                "id": "12727505",
                "name": "BWX Technologies, Inc.",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/bwx-technologies",
                    "https://twitter.com/BWXTech",
                    "https://www.facebook.com/BWXTech",
                    "http://www.crunchbase.com/organization/the-babcock-wilcox-company",
                ],
            },
            "wex-photographic": {
                "id": "12726340",
                "name": "Wex Photographic Ltd",
                "company_id": None,
                "country_code": "gb",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/wex-photographic",
                    "https://twitter.com/wextweets",
                ],
            },
            "menlo-security": {
                "id": "12591761",
                "name": "Menlo Security Inc.",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/menlo-security",
                    "https://twitter.com/menlosecurity",
                    "https://www.facebook.com/pages/Menlo-Security/411677528985544",
                    "http://www.crunchbase.com/organization/menlo-security",
                    "https://youtube.com/channel/UCN0AikN5dKnhEhmtQddAYqg",
                ],
            },
            "merck": {
                "id": "12418861",
                "name": "Merck",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/merck",
                    "https://twitter.com/merck",
                    "http://www.crunchbase.com/organization/merck-co-inc",
                    "https://youtube.com/user/Merck",
                ],
            },
            "netwurx": {
                "id": "12691830",
                "name": "NetwurX Inc",
                "company_id": None,
                "country_code": "us",
                "valid": False,
                "social_urls": ["https://www.linkedin.com/company/netwurx"],
            },
            "shree-tirupati-balajee-fibc-pvt-ltd---india": {
                "id": "12691729",
                "name": "Shree Tirupati Balajee FIBC Pvt Ltd - India",
                "company_id": None,
                "country_code": "in",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/shree-tirupati-balajee-fibc-pvt-ltd---india"
                ],
            },
            "9184758": {
                "id": "12690764",
                "name": "Polimex Holding Ltd.",
                "company_id": None,
                "country_code": "bg",
                "valid": False,
                "social_urls": ["https://www.linkedin.com/company/9184758"],
            },
            "kfw": {
                "id": "12679665",
                "name": "KFW Group",
                "company_id": None,
                "country_code": "de",
                "valid": False,
                "social_urls": [
                    "https://www.linkedin.com/company/kfw",
                    "https://twitter.com/KfW",
                    "https://instagram.com/kfw.stories",
                    "http://www.crunchbase.com/organization/kfw-group",
                    "https://youtube.com/user/kfw",
                ],
            },
        }
        companies_ids = []

        # Login using cached cookies
        code, message, api = get_api(
            LINKEDIN_API_ACCOUNT_ID,
            search_query_url=None,
            invalidate_linkedin_account=False,
            get_connections_summary=True,
        )

        assert code == 200, message is None

        for i, (company_name, company_data) in enumerate(parsed_leads.items()):
            company_id = company_data.get("company_id")
            if not company_id:
                company_id = api.get_company_id(company_name)
                if company_id:
                    companies_ids.append(company_id)

        assert companies_ids
