from time import sleep
import lxml.html as LH
import json
import re
from re import finditer

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

                        skip_participant = True

                        if logger:
                            logger.debug(f'Found user with event_body, User public_id: {public_id}, Picture: {display_picture_url}')

            # Step 2. Users to whom we wrote message
            if not skip_participant:
                for participant in element.get('participants', []):
                    current_participant = participant.get('com.linkedin.voyager.messaging.MessagingMember',
                                                          {}).get('miniProfile', {})
                    public_id = current_participant.get('publicIdentifier')
                    display_picture_url = linkedin_get_display_picture_url(
                        current_participant.get('picture'))

                    if public_id and public_id not in conversations_users_participants:
                        conversations_users_participants[public_id] = {
                            'conversationUrn': element.get('entityUrn'),
                            'first_name': current_participant.get('firstName'),
                            'last_name': current_participant.get('lastName'),
                            'event_body': None,
                            'display_picture_url': display_picture_url
                        }

                        logger.debug(f'Found user without event_body, User public_id: {public_id}, Picture: {display_picture_url}')

    # Step 3. Remove users from conversations_users_participants (if they exist in conversations_users_replies)
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

    return conversations_users_replies, conversations_users_participants, linkedin_users_blacklist

def get_leads_from_html(html, is_sales=False, get_pagination=False):
    users_data = None
    users = {}
    parsed_users = []
    pagination = None

    tree = LH.document_fromstring(html)
    search_hits = tree.xpath("//code//text()")

    if not is_sales:
        for item in search_hits:
            if 'publicIdentifier' in item:

                try:
                    data = json.loads(item)
                    if not data.get('data'):
                        continue

                    data_type = data.get('data', {}).get('$type')

                    if data_type == 'com.linkedin.restli.common.CollectionResponse' and not users_data:
                        users_data = data

                except Exception as e:
                    print(f'Failed parse item... {str(e)}')

        if users_data:
            paging = users_data.get('data', {}).get('paging')
            if paging and not pagination:
                pagination = paging

            elements = users_data.get('data', {}).get('elements')

            if elements and isinstance(elements, list):
                for sub_element in elements:
                    sub_elements = sub_element.get('elements')
                    if sub_elements and isinstance(sub_elements, list):
                        for item in sub_elements:
                            if item.get('$type') == 'com.linkedin.voyager.search.SearchHitV2':
                                user_public_id = item.get('publicIdentifier')
                                users.setdefault(user_public_id, {})
                                users[user_public_id].update(item)

            mini_profiles = users_data.get('included', {})
            if mini_profiles and isinstance(mini_profiles, list):
                for item in mini_profiles:
                    type = item.get('$type')
                    if type == 'com.linkedin.voyager.identity.shared.MiniProfile':
                        user_public_id = item.get('publicIdentifier')
                        if user_public_id in users:
                            users[user_public_id].update(item)

        for key, lead in users.items():
            i = {'publicIdentifier': lead.get('publicIdentifier'),
                 'firstname': lead.get('firstName'),
                 'lastname': lead.get('lastName'),
                 'fullname': lead.get('title', {}).get('text'),
                 'degree': None,
                 'canSendInMail': None,
                 'headline': lead.get('headline', {}).get('text'),
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
            i['location'] = lead.get('subline', {}).get('text')
            i['inCrm'] = -1
            i['tags'] = ""
            entityUrn = None

            if 'entityUrn' in lead:
                entityUrn = ''.join(re.findall(r'urn:li:fs_miniProfile:(.*)', lead['entityUrn']))
                if entityUrn:
                    i['profileLinkSN'] = 'https://www.linkedin.com/sales/people/%s' % entityUrn
                    i['entityUrn'] = entityUrn

            i['position'] = lead.get('headline', {}).get('text')

            snippet_text = lead.get('snippetText', {})
            if snippet_text and snippet_text.get('text'):
                company_name = re.findall(r'at(.*)?', snippet_text.get('text'))
                if len(company_name) == 1:
                    i['companyName'] = company_name[0].strip()

            if lead.get('picture', {}):
                pictures = lead.get('picture', {}).get('artifacts', [])
                if pictures:
                    for image in pictures:
                        if image['width'] == 400:
                            i['picture'] = '%s%s' % (lead['picture']['rootUrl'], image['fileIdentifyingUrlPathSegment'])
                            break

            if i.get('profileLink') and i.get('profileLinkSN') and i.get('firstname') and i.get('lastname'):
                parsed_users.append(i)

    else:
        parsed_items = []
        for item in search_hits:
            try:
                data = json.loads(item)
                print(data)

                if not pagination and data.get('paging'):
                    pagination = data.get('paging')

                if data.get('elements'):
                    for element in data.get('elements'):
                        parsed_items.append(element)
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
        return parsed_users, pagination
    else:
        return parsed_users
