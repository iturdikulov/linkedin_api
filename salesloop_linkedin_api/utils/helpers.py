from time import sleep


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
            elements = data.get('elements', [])
            if elements and isinstance(elements, list):
                element = elements[-1]
                events = element.get('events')
                if events and isinstance(events, list):
                    created_at = events[-1].get('createdAt')
                    created_before = created_at

            conversations_data.append(data)

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


def get_conversations_additional_data(conversations_data, logger=None):
    conversations_users_replies = {}  # Users who replied to our message
    conversations_users_participants = {}  # Users to whom only we wrote message and users which need sent follow up message
    linkedin_users_blacklist = {}

    for data in conversations_data:
        user_elements = data.get('elements', [])

        for element in user_elements:
            skip_participant = False

            # Step 1. Users who replied to our message
            for event in element.get('events', []):
                event_body = event.get('eventContent', {}) \
                    .get('com.linkedin.voyager.messaging.event.MessageEvent', {}) \
                    .get('attributedBody') \
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
        linkedin_users_blacklist[public_id] = {
            'latest_reply': user.get('event_body'),
            'display_picture_url': user.get('display_picture_url')
        }
        logger.debug(f'Added user {public_id} with body to linkedin_users_blacklist')

        if public_id in conversations_users_participants:
            del conversations_users_participants[public_id]
            logger.debug(f'Removed {public_id} participant from conversations_users_participants (already replied?')

    return conversations_users_replies, conversations_users_participants, linkedin_users_blacklist