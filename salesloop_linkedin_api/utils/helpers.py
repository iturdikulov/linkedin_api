from time import sleep
import lxml.html as LH
import re
from re import finditer
from traceback import print_exc
import logging
from urllib.parse import urlparse, quote
import json
from time import sleep
import random
import string
import base64


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('application')


def get_random_base64(length=16):
    letters_and_digits = string.ascii_letters + string.digits
    message_bytes = ''.join((random.choice(letters_and_digits) for i in range(length))).encode('ascii')
    base64_bytes = base64.b64encode(message_bytes)
    base64_message = base64_bytes.decode('ascii')
    return base64_message


def default_evade():
    """
    A catch-all method to try and evade suspension from Linkedin.
    Currently, just delays the request by a random (bounded) time
    """
    sleep(random.uniform(2, 5))  # sleep a random duration to try and evade suspention


def fast_evade():
    """
    A catch-all method to try and evade suspension from Linkedin.
    Currently, just delays the request by a random (bounded) time
    """
    sleep(random.uniform(0.5, 2))


def quote_query_param(data, is_sales=False, has_companies_names=False):
    if isinstance(data, str):
        data = [data]
    elif isinstance(data, int):
        data = [str(data)]

    if has_companies_names:
        data = [f'{company}:{item}' for item, company in data]

    if is_sales:
        return quote(','.join(str(item) for item in data))
    else:
        return quote(json.dumps([item for item in data]))


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
    if logger is None:
        logger = logging.getLogger('application')

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

                        logger.debug(f'Found user with event_body, User public_id: {public_id}, Picture: {display_picture_url}')

            # Step 2. Users to whom we wrote message

            for participant in element.get('participants', []):
                current_participant = participant.get(
                    'com.linkedin.voyager.messaging.MessagingMember',
                    {}).get('miniProfile', {})

                if skip_participant:
                    public_id = current_participant.get('publicIdentifier')
                    if public_id and public_id not in public_ids_found:
                        public_ids_found.append(public_id)
                else:
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


def parse_default_search_data(elements):
    """
    :param elements: JSON decoded data with linkedin profiles
    :return: valid LN profile data, unknown LN profiles data, limitation info
    """
    users = {}
    unknown_profiles = []
    users_order = []

    for sub_element in elements:
        if not users_order and sub_element.get('*results') and isinstance(sub_element.get('*results'), list):
            users_order = sub_element['*results']

        # Search limit detector
        extended_elements = sub_element.get('extendedElements', [])
        if extended_elements and isinstance(extended_elements, list):
            for extended_element in extended_elements:
                if extended_element.get('searchTieIn') == 'FREE_UPSELL':
                    logger.info('Detected known search limit - %s', extended_element)

        elif not isinstance(extended_elements, list):
            logger.warning('Extend elements wrong type!')

        sub_elements = sub_element.get('elements')
        if sub_elements and isinstance(sub_elements, list):
            for item in sub_elements:
                user_public_id = item.get('publicIdentifier')
                if item.get('$type') == 'com.linkedin.voyager.search.SearchHitV2':
                    users.setdefault(user_public_id, {})
                    users[user_public_id].update(item)

                    if item.get('targetUrn') and not item.get('publicIdentifier'):
                        unknown_profiles.append(item)

    return users, unknown_profiles, {}, users_order


def parse_search_hits(search_hits, is_sales=False):
    search_type = 'SALES_SEARCH' if is_sales else 'DEFAULT_SEARCH'
    search_hit_data = None

    users_data = None
    users = {}
    parsed_users = []
    unknown_profiles = []
    pagination = {}
    results_length = 0
    logged_in = False
    limit_data = {}
    parsed_search_hits = search_hits
    users_order = None

    if not is_sales:
        for data in parsed_search_hits:
            try:
                data_type = data.get('data', {}).get('$type')

                if data_type == 'com.linkedin.restli.common.CollectionResponse' and not users_data:
                    users_data = data
                else:
                    logger.debug('Data type: %s', data_type)

                if data_type == 'com.linkedin.voyager.common.Me':
                    logged_in = True

            except Exception as e:
                logger.warning(f'Failed parse item... {data}. {repr(e)}')

        if users_data:
            paging = users_data.get('data', {}).get('paging')
            if paging and not pagination:
                pagination = paging
                results_length = pagination.get('total', 0)

            total_results_count = users_data.get('data', {}).get('metadata', {}).get('totalResultCount', 0)
            elements = users_data.get('data', {}).get('elements', [])
            logger.debug('Found %d elements, total result count: %d', len(elements),
                         total_results_count)

            if elements and isinstance(elements, list):
                users, unknown_profiles, limit_data, users_order = parse_default_search_data(elements)

            mini_profiles = users_data.get('included', {})
            if mini_profiles and isinstance(mini_profiles, list):
                for item in mini_profiles:
                    item_type = item.get('$type')

                    if item.get('$recipeTypes') and 'com.linkedin.voyager.dash.deco.relationships.ProfileWithIweWarned' in item.get('$recipeTypes'):
                        continue

                    if item_type in ['com.linkedin.voyager.identity.shared.MiniProfile',
                                'com.linkedin.voyager.dash.identity.profile.Profile']:
                        user_public_id = item.get('publicIdentifier')

                        if not user_public_id:
                            logger.debug('No public id found: %s', item)
                            continue

                        if item_type == 'com.linkedin.voyager.dash.identity.profile.Profile':
                            if user_public_id in users:
                                users[user_public_id].update(item)
                            else:
                                users[user_public_id] = item
                        elif user_public_id in users:
                            users[user_public_id].update(item)
                    elif item_type not in ['com.linkedin.voyager.identity.profile.MemberBadges']:
                        logger.warning('Unknown profile type: %s', item_type)

                # fallback parser, if not users found
                fallback_profiles_images = {}
                if not users:
                    logger.warning('No found elements with default parser, use fallback users parser')
                    for item in mini_profiles:
                        item_type = item.get('$type')
                        user_public_id = None

                        if item_type == 'com.linkedin.voyager.dash.search.EntityResultViewModel':
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

                        elif item_type == 'com.linkedin.voyager.dash.identity.profile.Profile':
                            if item.get('profilePicture') and item.get('entityUrn'):
                                fallback_profiles_images[item.get('entityUrn')] = item.get('profilePicture', {}).get('displayImageReference', {}).get('vectorImage')

                # fallback parser - elements
                for key, lead in users.items():
                    try:
                        for item in mini_profiles:
                            item_type = item.get('$type')

                            if item_type in ['com.linkedin.voyager.dash.search.EntityResultViewModel']:
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
        if users_order:
            logger.info('Found users order, sort %d users', len(users))
            try:
                sorted_users = {}
                for entity_urn in users_order:
                    for user_key, user_data in users.items():
                        if user_data.get('entityUrn') == entity_urn:
                            sorted_users[user_key] = user_data

                if len(sorted_users) == len(users):
                    logger.info('Replace users with sorted_users')
                    users = sorted_users
                else:
                    logger.warning('Failed to sort: sorted users %s, \n non-sorted users %s',
                                   sorted_users, users)
            except Exception as e:
                logger.warning('Failed to sort', exc_info=e)

        for key, lead in users.items():
            fullname = None

            if xstr(lead.get('firstName')) and xstr(lead.get('lastName')):
                if lead['firstName'].lower().strip() == 'linkedin' \
                   and lead['lastName'].lower().strip() == 'member':
                    logger.info('Reset lead fullname,'
                                ' detected default fullname combination %s', lead)
                    lead['firstName'] = ''
                    lead['lastName'] = ''
                else:
                    fullname = f"{lead.get('firstName')} {lead.get('lastName')}"
            else:
                if isinstance(lead.get('title'), dict) and lead.get('title', {}).get('text'):
                    fullname = lead.get('title', {}).get('text')
                elif lead.get('title'):
                    fullname = lead.get('title')

                if xstr(fullname).lower().strip() == 'linkedin member':
                    fullname = None
                    logger.info('Reset lead fullname,'
                                ' detected default fullname combination %s', lead)

            if (not lead.get('firstName') or not lead.get('lastName')) and fullname:
                fullname_parts = fullname.split(' ')
                if len(fullname_parts) == 2:
                    lead['firstName'], lead['lastName'] = fullname_parts

            headline = lead.get('headline', {})

            if headline and isinstance(headline, dict):
                headline = headline.get('text')
            elif lead.get('headline'):
                headline = lead.get('headline')
            elif lead.get('primarySubtitle') and isinstance(lead.get('primarySubtitle'), dict):
                headline = lead.get('primarySubtitle', {}).get('text')

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
            lead_subline = lead.get('subline')
            lead_secondary_subtitle = lead.get('secondarySubtitle')

            if isinstance(lead_subline, dict):
                i['location'] = lead_subline.get('text')
            elif isinstance(lead_secondary_subtitle, dict):
                i['location'] = lead_secondary_subtitle.get('text')

            i['inCrm'] = -1
            i['tags'] = ""
            entityUrn = lead.get('entityUrn')

            if entityUrn and isinstance(entityUrn, str):
                if 'urn:li:fs_miniProfile' in entityUrn:
                    # this usually default format
                    entityUrn = ''.join(re.findall(r'urn:li:fs_miniProfile:(.*)', entityUrn))
                elif 'urn:li:fsd_profile' in entityUrn:
                    # fallback format
                    entityUrn = ''.join(re.findall(r'urn:li:fsd_profile:(.*)', entityUrn))

                # get data before comma in entity (remove some like ,SEARCH_SRP)
                if ',' in entityUrn:
                    entityUrn = ''.join(re.findall(r'^(.+?),', entityUrn))

                # check entity valid after all operations & fill variables
                if entityUrn:
                    i['profileLinkSN'] = 'https://www.linkedin.com/sales/people/%s' % entityUrn
                    i['entityUrn'] = entityUrn

            if isinstance(lead.get('headline'), dict):
                i['position'] = lead.get('headline', {}).get('text')
            elif lead.get('headline'):
                i['position'] = lead.get('headline')
            elif isinstance(lead.get('primarySubtitle', {}), dict):
                i['position'] = lead.get('primarySubtitle', {}).get('text')

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



            if i.get('profileLink') and i.get('profileLinkSN')\
                and ((i.get('firstname') and i.get('lastname'))
                     or i.get('fullname')):
                parsed_users.append(i)

                logger.debug('Added user to parsed_users list: %s', {
                    'entityUrn': i.get('entityUrn'),
                    'profileLink': i.get('profileLink'),
                    'profileLinkSN': i.get('profileLinkSN'),
                    'firstname': i.get('firstname'),
                    'lastname': i.get('lastname'),
                    'fullname': i.get('fullname')
                })
            else:
                logger.warning('Not enough data to add user: %s', i)

    else:
        parsed_items = []
        for data in parsed_search_hits:
            try:
                if not pagination and data.get('paging'):
                    pagination = data.get('paging')
                    results_length = pagination.get('count', 0)

                if data.get('elements'):
                    for element in data.get('elements'):
                        parsed_items.append(element)

                current_entity_run = data.get('memberResolutionResult', {}).get('entityUrn')

                if current_entity_run and 'urn:li:fs_salesProfile' in current_entity_run:
                    logged_in = True
            except Exception as e:
                logger.warning('Unknown sales search parse error', exc_info=e)

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
                            break

                if 'profilePictureDisplayImage' in lead:
                    for image in lead['profilePictureDisplayImage']['artifacts']:
                        if image['width'] == 400:
                            i['picture'] = '%s%s' % (lead['profilePictureDisplayImage']['rootUrl'], image['fileIdentifyingUrlPathSegment'])
                            break

                parsed_users.append(i)

                logger.debug('Added user to parsed_users list: %s', {
                    'entityUrn': i.get('entityUrn'),
                    'profileLink': i.get('profileLink'),
                    'profileLinkSN': i.get('profileLinkSN'),
                    'firstname': i.get('firstname'),
                    'lastname': i.get('lastname')
                })

    pagination['results_length'] = results_length
    pagination['logged_in'] = logged_in

    return parsed_users, pagination, unknown_profiles, limit_data


def get_leads_from_html(html, is_sales=False):
    tree = LH.document_fromstring(html)
    search_hits = tree.xpath("//code//text()[string-length() > 0]")
    logger.info('Found %d search hits', len(search_hits))
    search_hits_list = []
    search_type = 'SALES_SEARCH' if is_sales else 'DEFAULT_SEARCH'

    for search_hit in search_hits:
        # base string validation
        if search_type == 'DEFAULT_SEARCH' and 'publicIdentifier' not in search_hit:
            logger.info('Skip search hit, with %d length, no needed data found', len(search_hit))
            continue

        try:
            search_hit_data = json.loads(search_hit)
        except json.JSONDecodeError:
            logger.warning('Failed load %s search hit', search_hit)
        else:
            # Validator
            if search_type == 'DEFAULT_SEARCH' and not search_hit_data.get('data'):
                logger.info('Skip search_hit_data, with %d length, no data key found',
                            len(search_hit_data))
                continue

            search_hits_list.append(json.loads(search_hit))

    return search_hits_list

def xstr(s):
    return str(s or '')
