from time import sleep
import lxml.html as LH
import json
import re
from re import finditer
from traceback import print_exc
import logging
from os import environ, path
from urllib.parse import urlparse, quote
import json

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('application')


def quote_query_param(data, is_sales=False):
    if isinstance(data, str):
        data = [data]

    if is_sales:
        return quote(','.join(data))
    else:
        return quote(json.dumps([item for item in data]))


def generate_search_url(linkedin_api, parsed_leads, title, linkedin_geo_codes_data,
                        get_companies=True, has_sn=None):
    DEFAULT_SEARCH_PARAMS = {
        "facetCurrentCompany": None,
        "facetGeoRegion": None,
        "origin": "FACETED_SEARCH",
        "title": None,
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

    # check sales support
    if has_sn is None:
        current_user_profile = linkedin_api.get_user_profile()
        assert isinstance(current_user_profile['premiumSubscriber'], bool)
        has_sn = current_user_profile['premiumSubscriber']

    for company_name, company_data in parsed_leads.items():
        if not company_data.get('company_id'):
            try:
                company_linkedin_data = linkedin_api.get_company(company_name)
                company_id = get_id_from_urn(
                    company_linkedin_data.get('entityUrn'))

                if company_id and company_id.isnumeric():
                    parsed_leads[company_name]['company_id'] = int(company_id)
                    parsed_leads[company_name]['valid'] = True
                # TODO do something if company_id not found!
            except Exception as e:
                logger.warning('Failed get company! %s - %s', company_name, str(e))

    if parsed_leads:
        search_urls_list = []
        companies_ids = []
        regions = []

        sales_companies_ids = []
        sales_regions = []

        url_title = quote(title)

        # generate sub search urls
        for company_name, lead in parsed_leads.items():
            company_id = lead.get('company_id')
            if not company_id:
                logger.warning('no company id found, parsing error? %s', lead)
                continue

            # default search params
            company_id = str(company_id)
            companies_ids.append(company_id)
            regions.append(f"{lead.get('country_code')}:0")
            sub_search_url = None

            # sales search params
            if has_sn:
                sales_companies_ids.append(f'{company_name}:{company_id}')
                if lead.get('country_code'):
                    country_code_id = linkedin_geo_codes_data.get(
                        lead.get('country_code').upper(), {}).get('id')
                    if country_code_id:
                        sales_regions.append(country_code_id)
                    else:
                        logger.warning('Unknown code - %s', country_code_id)

                    # generate single url
                    url_default_params = SALES_SEARCH_DEFAULT_PARAMS
                    url_default_params['companyIncluded'] = quote_query_param(f'{company_name}:{company_id}',
                                                                              is_sales=True)
                    url_default_params['geoIncluded'] = quote_query_param(country_code_id, is_sales=True)
                    url_default_params['titleIncluded'] = url_title
                    query_data = '&'.join(["{}={}".format(k, v) for k, v in url_default_params.items()])
                    sub_search_url = f'https://www.linkedin.com/sales/search/people/?{query_data}'
            else:
                sub_url_default_params = DEFAULT_SEARCH_PARAMS
                sub_url_default_params["facetCurrentCompany"] = quote_query_param(company_id)
                sub_url_default_params["facetGeoRegion"] = quote_query_param(f"{lead.get('country_code')}:0")
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
            url_default_params["title"] = url_title
            query_data = '&'.join(["{}={}".format(k, v) for k, v in url_default_params.items()])
            search_url = f'https://www.linkedin.com/search/results/people/?{query_data}'

    else:
        logger.error('No parsed leads failed!')
        raise Exception('No parsed leads failed!')

    return parsed_leads, search_url, search_urls_list


def get_id_from_urn(urn):
    """
    Return the ID of a given Linkedin URN.

    Example: urn:li:fs_miniProfile:<id>
    """
    return urn.split(":")[3]


def get_converstation_data(api, max_iterations, get_conversation_delay, log=None):
    # Get remote conversations for `login`
    conversations_data = []
    created_before = None

    for i in range(max_iterations):
        if i > 0:
            if log:
                log.debug('{0} sec delay before next get conversation...'.format(get_conversation_delay))

            sleep(get_conversation_delay)

            if log:
                if not created_before:
                    log.warning('NOT FOUND created_before in conversation, check your structure!')
                else:
                    log.debug(f'Found created_before: {created_before}')

        data = api.get_conversations(createdBefore=created_before)

        if data:
            conversations_data.append(data)
            elements = data.get('elements', [])
            if elements and isinstance(elements, list):
                for element in elements:
                    events = element.get('events')
                    if events and isinstance(events, list):
                        created_at = events[-1].get('createdAt')
                        if created_before and created_at < created_before:
                            created_before = created_at
                        else:
                            created_before = created_at

    return conversations_data


def linkedin_get_display_picture_url(picture):
    if not isinstance(picture, dict):
        return None

    display_picture_meta = picture.get('com.linkedin.common.VectorImage', {})
    url_segment = None
    url_root = display_picture_meta.get('rootUrl', None)
    for artifact in display_picture_meta.get('artifacts', []):
        if artifact.get('width') == 100:
            url_segment = artifact.get('fileIdentifyingUrlPathSegment')
            break

    if url_root and url_segment and isinstance(url_root, str) and isinstance(url_segment, str) :
        return url_root + url_segment


def expand_message_variables(current_variables, invite_message):
    if current_variables and isinstance(current_variables, dict):
        string_variables = [x.group() for x in finditer(r'({.*?:\d+})', invite_message)]
        for string_variable in string_variables:
            string_variable_index = string_variable.split(':')[-1].rstrip('}')

            if string_variable_index.isdigit() and string_variable_index in current_variables:
                invite_message = invite_message.replace(string_variable, current_variables.get(string_variable_index))
            else:
                invite_message = invite_message.replace(string_variable, '')

    return invite_message


def get_conversations_additional_data(conversations_data, logger=None):
    conversations_users_replies = {}  # Users who replied to our message
    conversations_users_participants = {}  # Users to whom only we wrote message and users which need sent follow up message
    linkedin_users_blacklist = {}
    public_ids_found = []

    for data in conversations_data:
        if not data:
            logger.warning('No data found! in conversations data')

        user_elements = data.get('elements', [])

        for element in user_elements:
            skip_participant = False

            if not element:
                logger.warning('No element found! in conversations data')

            # Step 1. Users who replied to our message
            for event in element.get('events', []):
                event_body = event.get('eventContent', {}) \
                    .get('com.linkedin.voyager.messaging.event.MessageEvent', {}) \
                    .get('attributedBody', {}) \
                    .get('text')

                if event_body:
                    current_participant = event.get('from', {}).get(
                        'com.linkedin.voyager.messaging.MessagingMember', {}).get('miniProfile', {})
                    public_id = current_participant.get('publicIdentifier')
                    display_picture_url = linkedin_get_display_picture_url(
                        current_participant.get('picture'))

                    if public_id and public_id not in conversations_users_replies:
                        conversations_users_replies[public_id] = {
                            'conversationUrn': element.get('entityUrn'),
                            'first_name': current_participant.get('firstName'),
                            'last_name': current_participant.get('lastName'),
                            'event_body': event_body,
                            'display_picture_url': display_picture_url,
                        }

                        if public_id not in public_ids_found:
                            public_ids_found.append(public_id)

                        skip_participant = True

                        if logger:
                            logger.debug(f'Found user with event_body, User public_id: {public_id}, Picture: {display_picture_url}')

            # Step 2. Users to whom we wrote message
            if not skip_participant:
                for participant in element.get('participants', []):
                    current_participant = participant.get('com.linkedin.voyager.messaging.MessagingMember',
                                                          {}).get('miniProfile', {})

                    logger.debug('Current participants: %s', current_participant)
                    public_id = current_participant.get('publicIdentifier')
                    entity_urn = current_participant.get('entityUrn')
                    if entity_urn:
                        entity_urn = get_id_from_urn(entity_urn)

                    # get entity urn to check conversation events

                    display_picture_url = linkedin_get_display_picture_url(
                        current_participant.get('picture'))

                    if public_id and public_id not in conversations_users_participants:
                        conversations_users_participants[public_id] = {
                            'conversationUrn': element.get('entityUrn'),
                            'first_name': current_participant.get('firstName'),
                            'last_name': current_participant.get('lastName'),
                            'event_body': None,
                            'entity_urn': entity_urn,
                            'display_picture_url': display_picture_url
                        }

                        if public_id not in public_ids_found:
                            public_ids_found.append(public_id)

                        logger.debug(f'Found user without event_body, User public_id: {public_id}, Picture: {display_picture_url}')

    # Step 3. Remove users from conversations_users_participants
    # (if they exist in conversations_users_replies)
    for public_id, user in conversations_users_replies.items():
        if not user or not public_id:
            logger.warning('No user/public_id found in conversations_users')

        linkedin_users_blacklist[public_id] = {
            'latest_reply': user.get('event_body'),
            'display_picture_url': user.get('display_picture_url')
        }
        logger.debug(f'Added user {public_id} with body to linkedin_users_blacklist')

        if public_id in conversations_users_participants:
            del conversations_users_participants[public_id]
            logger.debug(f'Removed {public_id} participant from conversations_users_participants (already replied?')

    return conversations_users_replies, conversations_users_participants, linkedin_users_blacklist, public_ids_found


def get_default_regions(path):
    regions = []
    try:
        with open(path) as f:
            regions = json.load(f)
    except Exception as e:
        print_exc()

    return regions


def get_raw_leads_from_html(html):
    raw_data = {}
    paging = {}

    tree = LH.document_fromstring(html)
    search_hits = tree.xpath("//code//text()")
    for item in search_hits:
        try:
            data = json.loads(item)
            data_type = data.get('data', {}).get('$type')
            if data_type == 'com.linkedin.restli.common.CollectionResponse':
                if data.get('data'):
                    raw_data = data.get('data')
                    paging = raw_data.get('paging')
        except json.JSONDecodeError as e:
            print(f'Failed parse item..., {repr(e)}')

    return raw_data, paging


def get_leads_from_html(html, is_sales=False, get_pagination=False):
    users_data = None
    users = {}
    parsed_users = []
    unknown_profiles = []
    pagination = {}
    results_length = 0
    logged_in = False

    tree = LH.document_fromstring(html)
    search_hits = tree.xpath("//code//text()[string-length() > 0]")
    logger.info('Found %d search hits', len(search_hits))

    if not is_sales:
        for item in search_hits:
            try:
                data = json.loads(item)

                if 'publicIdentifier' in item:
                    if not data.get('data'):
                        continue

                    data_type = data.get('data', {}).get('$type')

                    if data_type == 'com.linkedin.restli.common.CollectionResponse' and not users_data:
                        users_data = data
                    else:
                        logger.debug('Data type: %s', data_type)

                    if data_type == 'com.linkedin.voyager.common.Me':
                        logged_in = True


            except Exception as e:
                logger.info(f'Failed parse item... {item}. {repr(e)}')

        if users_data:
            paging = users_data.get('data', {}).get('paging')
            if paging and not pagination:
                pagination = paging
                results_length = pagination.get('total', 0)

            elements = users_data.get('data', {}).get('elements', [])
            logger.debug('Found %d elements', len(elements))

            if elements and isinstance(elements, list):
                for sub_element in elements:
                    sub_elements = sub_element.get('elements')
                    if sub_elements and isinstance(sub_elements, list):
                        for item in sub_elements:
                            user_public_id = item.get('publicIdentifier')
                            if item.get('$type') == 'com.linkedin.voyager.search.SearchHitV2':
                                users.setdefault(user_public_id, {})
                                users[user_public_id].update(item)

                                if item.get('targetUrn') and not item.get('publicIdentifier'):
                                    unknown_profiles.append(item)

            mini_profiles = users_data.get('included', {})
            if mini_profiles and isinstance(mini_profiles, list):
                for item in mini_profiles:
                    type = item.get('$type')
                    logger.debug('Mini profile type: %s', type)

                    if item.get('$recipeTypes') and 'com.linkedin.voyager.dash.deco.relationships.ProfileWithIweWarned' in item.get('$recipeTypes'):
                        continue

                    if type in ['com.linkedin.voyager.identity.shared.MiniProfile',
                                'com.linkedin.voyager.dash.identity.profile.Profile']:
                        user_public_id = item.get('publicIdentifier')

                        if not user_public_id:
                            logger.debug('No public id found: %s', item)
                            continue

                        if type == 'com.linkedin.voyager.dash.identity.profile.Profile':
                            if user_public_id in users:
                                users[user_public_id].update(item)
                            else:
                                users[user_public_id] = item
                        elif user_public_id in users:
                            users[user_public_id].update(item)

                # fallback parser, if not users found
                fallback_profiles_images = {}
                if not users:
                    logger.warning('No found elements with default parser, use fallback users parser')
                    for item in mini_profiles:
                        type = item.get('$type')
                        user_public_id = None

                        if type == 'com.linkedin.voyager.dash.search.EntityResultViewModel':
                            user_public_id = item.get('publicIdentifier')
                            navigation_url = item.get('navigationUrl')
                            if not user_public_id and navigation_url:
                                logger.debug('Get public_id from navigation URL: %s', navigation_url)
                                try:
                                    url_path = urlparse(navigation_url.strip('/'))
                                    user_public_id = url_path.path.split("/")[-1]

                                    if user_public_id not in users:
                                        logger.debug('Add %s item to users', item)
                                        users[user_public_id] = item
                                        users[user_public_id]['publicIdentifier'] = user_public_id

                                except Exception as e:
                                    logger.warning('Failed parse %s item', item, exc_info=e)
                            else:
                                logger.warning('Unknown parsing case, please check this item: %s', item)

                        elif type == 'com.linkedin.voyager.dash.identity.profile.Profile':
                            if item.get('profilePicture') and item.get('entityUrn'):
                                fallback_profiles_images[item.get('entityUrn')] = item.get('profilePicture', {}).get('displayImageReference', {}).get('vectorImage')


                # fallback parser - elements
                for key, lead in users.items():
                    try:
                        for item in mini_profiles:
                            type = item.get('$type')

                            if type in ['com.linkedin.voyager.dash.search.EntityResultViewModel']:
                                if all([lead.get('publicIdentifier'),
                                        item.get('navigationUrl'),
                                        lead.get('publicIdentifier') in item.get('navigationUrl')]):

                                    logger.debug('Fallback parser - found new user: %s, %s, %s, %s',
                                                 item,
                                                 lead.get('publicIdentifier'),
                                                 item.get('navigationUrl'),
                                                 lead.get('publicIdentifier') in item.get('navigationUrl'))

                                    image_attributes = item.get('image', {}).get('attributes', [])

                                    if image_attributes:
                                        vector_image = image_attributes[0].get('detailDataUnion',
                                                                           {}).get(
                                            'nonEntityProfilePicture', {}).get('vectorImage')

                                        if not vector_image:
                                            logger.debug('Trying get image from fallback images')
                                            profile_picture_urn = image_attributes[0].get(
                                                'detailDataUnion',
                                                {}).get(
                                                'profilePicture')

                                            if profile_picture_urn and profile_picture_urn in fallback_profiles_images:
                                                vector_image = fallback_profiles_images.get(profile_picture_urn)

                                        if vector_image:
                                            item['picture'] = vector_image
                                            users[key].update(item)


                    except Exception as e:
                        logger.warning('Failed pars %s item', lead, exc_info=e)


        logger.debug('Users found %d', len(users))

        for key, lead in users.items():
            fullname = None

            if lead.get('firstName') and lead.get('lastName'):
                fullname = f"{lead.get('firstName')} {lead.get('lastName')}"
            else:
                fullname = lead.get('title') or lead.get('title', {}).get('text')

            headline = lead.get('headline', {})
            if headline and isinstance(headline, dict):
                headline = headline.get('text')
            else:
                headline = lead.get('headline')

            i = {'publicIdentifier': lead.get('publicIdentifier'),
                 'firstname': lead.get('firstName'),
                 'lastname': lead.get('lastName'),
                 'fullname': fullname or '',
                 'degree': None,
                 'canSendInMail': None,
                 'headline': headline,
                 'picture': None,
                 'profileLink': None,
                 'profileLinkSN': None,
                 'location': None,
                 'position': None,
                 'companyId': None,
                 'companyName': None,
                 'companyType': None,
                 'companyIndustry': None,
                 'companyDescription': None,
                 'companyWebsite': None,
                 'companyStaffCount': None,
                 'companyCountry': None,
                 'companyGeographicArea': None,
                 'companyCity': None,
                 'companyPostalCode': None,
                 'companyLine2': None,
                 'companyLine1': None,
                 'companyFounded': None,
                 'companyFollowerCount': None,
                 'companyEmails': None,
                 'companyLink': None,
                 'companyLinkSN': None,
                 'companySlug': None,
                 'extractEmailAddress': False
                 }
            degree = lead.get('secondaryTitle', {}).get('text')
            degree_num = -1
            if degree:
                degree_group = re.search(r'\d+', degree)
                if degree_group:
                    degree_num = int(degree_group.group())

            if i.get('publicIdentifier'):
                i['profileLink'] = 'https://www.linkedin.com/in/' + i.get('publicIdentifier')

            i['degree'] = degree_num
            i['canSendInMail'] = -1
            i['location'] = lead.get('subline', {}).get('text') or lead.get('secondarySubtitle', {}).get('text')
            i['inCrm'] = -1
            i['tags'] = ""
            entityUrn = None

            if 'entityUrn' in lead:
                entityUrn = ''.join(re.findall(r'urn:li:fs_miniProfile:(.*)', lead['entityUrn'])) or ''.join(
                        re.findall(r'urn:li:fsd_profile:(.*)', lead['entityUrn']))
                if entityUrn:
                    i['profileLinkSN'] = 'https://www.linkedin.com/sales/people/%s' % entityUrn
                    i['entityUrn'] = entityUrn

            if isinstance(lead.get('headline'), dict):
                i['position'] = lead.get('headline', {}).get('text')
            else:
                i['position'] = lead.get('headline')

            snippet_text = lead.get('snippetText', {}).get('text') or i['position']
            if snippet_text:
                company_name = re.findall(r'at(.*)?', snippet_text)
                if len(company_name) == 1:
                    i['companyName'] = company_name[0].strip()

            pictures = lead.get('picture', {})
            if pictures:
                pictures = pictures.get('artifacts', [])

            if pictures:
                for image in pictures:
                    if image['width'] == 400:
                        i['picture'] = '%s%s' % (lead['picture']['rootUrl'], image['fileIdentifyingUrlPathSegment'])
                        break

            if i.get('profileLink') and i.get('profileLinkSN') and i.get('firstname') and i.get('lastname'):
                parsed_users.append(i)
            else:
                logger.warning('Not enough data to add user: %s', i)

    else:
        parsed_items = []
        for item in search_hits:
            try:
                data = json.loads(item)

                if not pagination and data.get('paging'):
                    pagination = data.get('paging')
                    results_length = pagination.get('count', 0)

                if data.get('elements'):
                    for element in data.get('elements'):
                        parsed_items.append(element)

                current_entity_run = data.get('memberResolutionResult', {}).get('entityUrn')

                if current_entity_run and 'urn:li:fs_salesProfile' in current_entity_run:
                    logged_in = True
            except Exception:
                pass

        if parsed_items:
            for lead in parsed_items:
                i = {'publicIdentifier': lead.get('publicIdentifier'), 'firstname': lead.get('firstName'), 'lastname': lead.get('lastName'),
                     'fullname': f"{lead.get('firstName')} {lead.get('lastName')}", 'degree': None, 'canSendInMail': None, 'headline': None,
                     'picture': None, 'profileLink': None, 'profileLinkSN': None, 'location': None, 'position': None,
                     'companyId': None, 'companyName': None, 'companyType': None, 'companyIndustry': None,
                     'companyDescription': None, 'companyWebsite': None, 'companyStaffCount': None, 'companyCountry': None,
                     'companyGeographicArea': None, 'companyCity': None, 'companyPostalCode': None, 'companyLine2': None,
                     'companyLine1': None, 'companyFounded': None, 'companyFollowerCount': None, 'companyEmails': None,
                     'companyLink': None, 'companyLinkSN': None, 'companySlug': None, 'extractEmailAddress': False}

                memberId = str(lead['objectUrn'].replace('urn:li:member:', ''))
                i['memberId'] = memberId
                i['fullname'] = lead['fullName']
                degree = -1
                if 'degree' in lead:
                    degree = lead['degree']
                    i['degree'] = degree
                i['canSendInMail'] = 0
                if 'premium' in lead:
                    i['canSendInMail'] = 1 if lead['premium'] else 0
                if 'geoRegion' in lead:
                    i['location'] = lead['geoRegion']
                i['inCrm'] = 0
                if 'crmStatus' in lead and 'imported' in lead['crmStatus']:
                    i['inCrm'] = 1 if lead['crmStatus']['imported'] else 0
                leadTags = []
                if 'tags' in lead:
                    for leadTagId in lead['tags']:
                        leadTags.append(leadTagId)

                i['tags'] = ('\n').join(leadTags)
                entityUrn = None
                if 'entityUrn' in lead:
                    entityUrn = ('').join(re.findall('urn:li:fs_salesProfile:\\((.+?)\\)', lead['entityUrn']))
                    if entityUrn:
                        i['profileLinkSN'] = 'https://www.linkedin.com/sales/people/%s' % entityUrn
                        entityUrns = entityUrn.split(',')
                        if len(entityUrns) == 3:
                            i['profileLink'] = 'https://www.linkedin.com/profile/view/?id=%s' % entityUrns[0]
                            i['entityUrn'] = entityUrns[0]
                if 'currentPositions' in lead:
                    for position in lead['currentPositions']:
                        if position['current'] == True:
                            if 'companyName' in position:
                                i['companyName'] = position['companyName']
                            if 'title' in position:
                                i['position'] = position['title']
                            # if 'companyUrn' in position:
                            #     companyId = str(position['companyUrn'].replace('urn:li:fs_salesCompany:', ''))
                            #     i['companyId'] = companyId
                            break

                if 'profilePictureDisplayImage' in lead:
                    for image in lead['profilePictureDisplayImage']['artifacts']:
                        if image['width'] == 400:
                            i['picture'] = '%s%s' % (lead['profilePictureDisplayImage']['rootUrl'], image['fileIdentifyingUrlPathSegment'])
                            break

                parsed_users.append(i)

    if get_pagination:
        pagination['results_length'] = results_length
        pagination['logged_in'] = logged_in

        return parsed_users, pagination, unknown_profiles
    else:
        return parsed_users, unknown_profiles
