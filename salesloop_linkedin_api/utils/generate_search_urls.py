from salesloop_linkedin_api.utils.helpers import get_id_from_urn, logger, quote_query_param, fast_evade
from concurrent.futures import ThreadPoolExecutor
from requests_futures.sessions import FuturesSession
import pickle
from os import environ
from urllib.parse import urlparse, quote
import pycountry

def generate_search_url(linkedin_api, company_leads,
                        title, linkedin_geo_codes_data,
                        get_companies=True,
                        has_sn=None,
                        countries_codes=None,
                        max_workers=5,
                        maximum_companies=30,
                        type='leadfeeder'):
    """
    :param linkedin_api: linkedin api method
    :param company_leads: raw data from leadfeeder service
    :param title: title to generate search url
    :param linkedin_geo_codes_data: Linkedin Countries ids
    :param get_companies: search companies
    :param has_sn: has sales nav access
    :param countries_codes: country codes, which overwrite lead country code
    :return: generates LN search url
    """



    parsed_leads = {}

    if type == 'leadfeeder':
        data = company_leads.get('data')
        included_data = company_leads.get('included')
        logger.debug('Found %d company_leads. Predefined country codes: %s',
                     len(data), countries_codes)

        if not data or not included_data:
            return None, None, None

        for lead in data:
            company_id = None

            # Location field
            country_code = None
            country = None
            region = None
            city = None

            lead_linkedin_url = lead.get('attributes', {}).get('linkedin_url')
            lead_company_name = lead.get('attributes', {}).get('name')
            if lead_linkedin_url:
                public_id = urlparse(lead_linkedin_url).path.rpartition('/')[-1]
                leadfeeder_location_id = lead.get('relationships', {}).get(
                    'location', {}).get('data', {}).get('id')

                if leadfeeder_location_id:
                    location = next(iter([location for location in included_data if
                                          location['id'] == leadfeeder_location_id]), {})

                    country_code = location.get('attributes', {}).get('country_code')
                    country = location.get('attributes', {}).get('country')
                    region = location.get('attributes', {}).get('region')
                    city = location.get('attributes', {}).get('city')

                if public_id.isnumeric():
                    company_id = int(public_id)

                if public_id:
                    parsed_leads[public_id] = {
                        'name': lead_company_name,
                        'company_id': company_id,
                        'country_code': country_code.lower(),
                        'valid': company_id is not None,
                        'country': country,
                        'region': region,
                        'city': city
                    }

            if len(parsed_leads) > maximum_companies:
                logger.debug('Raised maximum companies - %s, stop.',
                             maximum_companies)
                break
    elif type == 'visitorqueue':
        data = company_leads
        if not data:
            return None, None, None

        for lead in data:
            company_name = lead.get('name')
            company_country_code = None
            company_id = None
            public_id = None

            if lead.get('country'):
                company_country = pycountry.countries.get(name=lead.get('country'))
                if company_country:
                    company_country_code = company_country.alpha_2.lower()

            for url in lead.get('social_urls', []):
                if 'linkedin.com' in url:
                    public_id = urlparse(url).path.rpartition('/')[-1]
                    break

            if all([company_name, public_id, company_country_code]):
                parsed_leads[public_id] = {
                    'name': company_name,
                    'company_id': company_id,
                    'country_code': company_country_code,
                    'valid': company_id is not None
                }

            if len(parsed_leads) > maximum_companies:
                logger.debug('Raised maximum companies - %s, stop.',
                             maximum_companies)
                break
    else:
        raise Exception('Unknown leads type ')

    return generate_search_url_leads(linkedin_api, parsed_leads, title,
                                     linkedin_geo_codes_data, get_companies=get_companies,
                                     has_sn=has_sn, countries_codes=countries_codes,
                                     max_workers=max_workers)


def generate_search_url_leads(linkedin_api, parsed_leads, title, linkedin_geo_codes_data,
                              get_companies=True, has_sn=None, countries_codes=None, max_workers=5):
    DEFAULT_SEARCH_PARAMS = {
        "facetCurrentCompany": None,
        "facetGeoRegion": None,
        "origin": "FACETED_SEARCH",
        "title": None,
        "geoUrn": None,
    }

    SALES_SEARCH_DEFAULT_PARAMS = {
        'companyIncluded': None,  # 'Microsoft:1035',
        'companyTimeScope': 'CURRENT',  # 'CURRENT',
        'doFetchHeroCard': 'false',  # 'false',
        'geoIncluded': None,  # '103644278',
        # 'keywords': None,           # 'css',
        'logHistory': 'true',  # 'true',
        'page': '1',  # '1',
        'titleIncluded': None,
        'titleTimeScope': 'CURRENT'
    }

    linkedin_api_cookies = pickle.loads(linkedin_api.api_cookies)
    linkedin_api_headers = pickle.loads(linkedin_api.api_headers)
    linkedin_api_proxies = linkedin_api.api_proxies

    with FuturesSession(executor=ThreadPoolExecutor(max_workers=max_workers)) as session:
        search_timeout = int(environ['LINKEDIN_API_SEARCH_TIMEOUT'])

        # check sales support
        if has_sn is None:
            users_data = session.get(f"https://www.linkedin.com/voyager/api/me",
                                     cookies=linkedin_api_cookies,
                                     headers=linkedin_api_headers,
                                     proxies=linkedin_api_proxies,
                                     timeout=search_timeout)

        logger.debug('Getting companies data with %d workers', max_workers)
        futures = []
        for company_name, company_data in parsed_leads.items():
            if not company_data.get('company_id'):
                fast_evade()

                params = {
                    "decorationId": "com.linkedin.voyager.deco.organization.web.WebFullCompanyMain-12",
                    "q": "universalName",
                    "universalName": company_name,
                }

                res = session.get(f"https://www.linkedin.com/voyager/api/organization/companies",
                                  params=params,
                                  cookies=linkedin_api_cookies,
                                  headers=linkedin_api_headers,
                                  proxies=linkedin_api_proxies,
                                  timeout=search_timeout)

                futures.append((company_name, res))
                logger.debug('Added %s company name to futures', company_name)

        if has_sn is None:
            current_user_profile = users_data.result()
            current_user_profile_data = current_user_profile.json()
            assert isinstance(current_user_profile_data['premiumSubscriber'], bool)
            has_sn = current_user_profile_data['premiumSubscriber']

        for company_name, future in futures:
            try:
                future_data = future.result()
                data = future_data.json()
                company_linkedin_data = data["elements"][0]
                company_id = get_id_from_urn(company_linkedin_data.get('entityUrn'))
                logger.debug('Found %s company id', company_id)

                # TODO do something if company_id not found!
                if company_id and company_id.isnumeric():
                    parsed_leads[company_name]['company_id'] = int(company_id)
                    parsed_leads[company_name]['valid'] = True
            except Exception as e:
                logger.warning('Failed get company! %s', company_name, exc_info=e)

    if parsed_leads:
        search_urls_list = []
        companies_ids = []

        sales_companies_ids = []

        url_title = quote(title)

        if countries_codes:
            logger.debug('Use predefined country codes: %d',
                         len(countries_codes))
            regions = [f"{country_code}:0" for country_code in countries_codes]
            sales_regions = [linkedin_geo_codes_data.get(country_code.upper(), {}).get('id')
                             for country_code in countries_codes]
        else:
            regions = []
            sales_regions = []

        # generate sub search urls
        for company_name, lead in parsed_leads.items():
            company_id = lead.get('company_id')
            if not company_id:
                logger.warning('no company id found, parsing error? %s', lead)
                continue

            # default search params
            company_id = str(company_id)
            companies_ids.append(company_id)
            sub_search_url = None

            # sales search params
            if has_sn:
                # SALES NAV URL LOGIC
                sales_companies_ids.append(f'{company_name}:{company_id}')

                if lead.get('country_code') or countries_codes:
                    # generate single url
                    url_default_params = SALES_SEARCH_DEFAULT_PARAMS

                    if countries_codes:
                        # regions exist, overwrite leads locations
                        url_default_params['geoIncluded'] = quote_query_param(sales_regions,
                                                                              is_sales=True)
                    else:
                        # use lead location, append location to generate all regions
                        country_code_id = linkedin_geo_codes_data.get(
                            lead.get('country_code').upper(), {}).get('id')

                        if country_code_id:
                            sales_regions.append(country_code_id)
                        else:
                            logger.warning('Unknown code - %s', country_code_id)

                        url_default_params['geoIncluded'] = quote_query_param(country_code_id,
                                                                              is_sales=True)

                    url_default_params['companyIncluded'] = quote_query_param(f'{company_name}:{company_id}',
                                                                              is_sales=True)
                    url_default_params['titleIncluded'] = url_title
                    query_data = '&'.join(["{}={}".format(k, v) for k, v in url_default_params.items()])
                    sub_search_url = f'https://www.linkedin.com/sales/search/people/?{query_data}'
            else:
                # DEFAULT URL LOGIC
                sub_url_default_params = DEFAULT_SEARCH_PARAMS
                sub_url_default_params["facetCurrentCompany"] = quote_query_param(company_id)

                if countries_codes:
                    # regions exist, overwrite leads locations
                    sub_url_default_params["facetGeoRegion"] = quote_query_param(regions)
                else:
                    # use lead location, append location to generate all regions
                    regions.append(f"{lead.get('country_code')}:0")
                    sub_url_default_params["facetGeoRegion"] = quote_query_param(f"{lead.get('country_code')}:0")

                geo_urns = [
                    linkedin_geo_codes_data.get(region.replace(':0', '').upper(), {}).get('id')
                    for region in regions if region]

                sub_url_default_params["geoUrn"] = quote_query_param(geo_urns)

                sub_url_default_params["title"] = url_title
                query_data = '&'.join(["{}={}".format(k, v) for k, v in sub_url_default_params.items()])
                sub_search_url = f'https://www.linkedin.com/search/results/people/?{query_data}'

            if sub_search_url:
                if get_companies:
                    search_urls_list.append((sub_search_url,
                                             lead.get('name')))
                else:
                    search_urls_list.append(sub_search_url)

        # generate merged url
        if has_sn:
            url_default_params = SALES_SEARCH_DEFAULT_PARAMS
            url_default_params['companyIncluded'] = quote_query_param(sales_companies_ids, is_sales=True)
            url_default_params['geoIncluded'] = quote_query_param(sales_regions, is_sales=True)
            url_default_params['titleIncluded'] = url_title
            query_data = '&'.join(["{}={}".format(k, v) for k, v in url_default_params.items()])
            search_url = f'https://www.linkedin.com/sales/search/people/?{query_data}'
        else:
            url_default_params = DEFAULT_SEARCH_PARAMS
            url_default_params["facetCurrentCompany"] = quote_query_param(companies_ids)
            url_default_params["facetGeoRegion"] = quote_query_param(regions)

            geo_urns = [linkedin_geo_codes_data.get(region.replace(':0', '').upper(), {}).get('id')
                        for region in regions if region]
            url_default_params["geoUrn"] = quote_query_param(geo_urns)

            url_default_params["title"] = url_title
            query_data = '&'.join(["{}={}".format(k, v) for k, v in url_default_params.items()])
            search_url = f'https://www.linkedin.com/search/results/people/?{query_data}'

    else:
        logger.error('No parsed leads failed!')
        raise Exception('No parsed leads failed!')

    return parsed_leads, search_url, search_urls_list
