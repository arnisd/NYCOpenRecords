"""
    app.response.utils
    ~~~~~~~~~~~~~~~~

    synopsis: Handles the functions for responses

"""
import json
from datetime import datetime

import os
import re
from abc import ABCMeta, abstractmethod
from flask import current_app

from app.constants import (
    event_type,
    response_type,
    AGENCY_USER,
    PUBLIC_USER_NYC_ID,
    ANONYMOUS_USER
)
from app.lib.db_utils import create_object, update_object
from app.lib.email_utils import (
    send_email,
    store_email
)
from app.lib.file_utils import get_mime_type
from app.models import (
    Responses,
    Events,
    Notes,
    Files,
    Users,
    UserRequests,
    Links,
    Instructions,
)


def add_file(request_id, filename, title, privacy):
    """
    Creates and stores the file object for the specified request.
    Gets the file mimetype from a helper function in lib.file_utils

    :param request_id: Request ID that the file is being added to
    :param filename: The secured_filename of the file.
    :param title: The title of the file which is entered by the uploader.
    :param privacy: The privacy option of the file.

    :return: Stores the file metadata into the Files table.
             Provides parameters for the process_response function to create and store responses and events object.
    """
    size = os.path.getsize(os.path.join(current_app.config['UPLOAD_DIRECTORY'] + request_id, filename))
    mime_type = get_mime_type(request_id, filename)
    files = Files(name=filename, mime_type=mime_type, title=title, size=size)
    files_metadata = json.dumps({'name': filename,
                                 'mime_type': mime_type,
                                 'title': title,
                                 'size': size})
    files_metadata = files_metadata.replace('{', '').replace('}', '')
    create_object(obj=files)
    _process_response(request_id, response_type.FILE, event_type.FILE_ADDED, files.metadata_id, privacy,
                      new_response_value=files_metadata)


def delete_file():
    """
    Will delete a file in the database for the specified request.
    :return:
    """
    # TODO: Implement deleting a file
    print("delete_file function")

    return None


def add_note(request_id, content):
    """
    Creates and stores the note object for the specified request.

    :param request_id: takes in FOIL request ID as an argument for the process_response function
    :param content: content of the note to be created and stored as a note object

    :return: Stores the note content into the Notes table.
             Provides parameters for the process_response function to create and store responses and events object.
    """
    note = Notes(content=content)
    create_object(obj=note)
    content = json.dumps({'content': content})
    _process_response(request_id, response_type.NOTE, event_type.NOTE_ADDED, note.metadata_id,
                      new_response_value=content)


def delete_note():
    """
    Will delete a note in the database for the specified request.
    :return:
    """
    # TODO: Implement deleting a note
    print("delete_note function")


def add_extension():
    """
    Will add an extension to the database for the specified request.
    :return:
    """
    # TODO: Implement adding an extension
    print("add_extension function")


def add_email(request_id, subject, email_content, to=None, cc=None, bcc=None):
    """
    Creates and stores the note object for the specified request.

    :param request_id: takes in FOIL request ID as an argument for the process_response function
    :param subject: subject of the email to be created and stored as a email object
    :param email_content: email body content of the email to be created and stored as a email object
    :param to: list of person(s) email is being sent to
    :param cc: list of person(s) email is being cc'ed to
    :param bcc: list of person(s) email is being bcc'ed
    :return: Stores the email metadata into the Emails table.
             Provides parameters for the process_response function to create and store responses and events object.
    """
    store_email(subject=subject,
                email_content=email_content,
                to=to,
                cc=cc,
                bcc=bcc)
    email_content = json.dumps({'email_content': email_content})
    _process_response(request_id, response_type.EMAIL, event_type.EMAIL_NOTIFICATION_SENT, email.metadata_id,
                      new_response_value=email_content)


def add_sms():
    """
    Will add an SMS to the database for the specified request.
    :return:
    """
    # TODO: Implement adding an SMS
    print("add_sms function")


def add_push():
    """
    Will add a push to the database for the specified request.
    :return:
    """
    # TODO: Implement adding a push
    print("add_push function")


def process_upload_data(form):
    """
    Helper function that processes the uploaded file form data.
    A files dictionary is first created and then populated with keys and their respective values of the form data.

    :return: A files dictionary that contains the uploaded file(s)'s metadata that will be passed as arguments to be
     stored in the database.
    """
    files = {}
    # re_obj is a regular expression that specifies a set of strings and allows you to check if a particular string
    #   matches the regular expression. In this case, we are specifying 'filename_' and checking for it.
    re_obj = re.compile('filename_')
    for key in form.keys():
        if re_obj.match(key):
            files[key.split('filename_')[1]] = {}
    for key in files:
        re_obj = re.compile(key)
        for form_key in form.keys():
            if re_obj.match(form_key):
                files[key][form_key.split(key + '::')[1]] = form[form_key]
    return files


def send_response_email(request_id, privacy, filenames, email_content):
    """
    Function that sends email detailing a file response has been uploaded to a request.
    If the file privacy is private, only agency_ein users are emailed.
    If the file privacy is release, the requester is emailed and the agency_ein users are bcced.

    :param request_id: FOIL request ID
    :param privacy: privacy option of the uploaded file
    :param filenames: list of filenames
    :param email_content: content body of the email notification being sent
    :return: Sends email notification detailing a file response has been uploaded to a request.

    """
    # TODO: make subject constants
    subject = 'Response Added'
    # Get list of agency_ein users on the request
    agency_user_guids = UserRequests.query.filter_by(request_id=request_id, user_type=AGENCY_USER)

    # Query for the agency_ein email information
    agency_emails = []
    for user_guid in agency_user_guids:
        agency_user_email = Users.query.filter_by(guid=user_guid, user_type=AGENCY_USER).first().email
        agency_emails.append(agency_user_email)

    bcc = agency_emails or ['agency_ein@email.com']

    file_to_link = {}
    for filename in filenames:
        file_to_link[filename] = "http://127.0.0.1:5000/request/view/{}".format(filename)

    if privacy == 'release':
        # Query for the requester's email information
        # Query for the requester's guid from UserRequests using first because there can only be one unique requester
        requester_guid = UserRequests.query.filter_by(request_id=request_id).filter(
            UserRequests.user_type.in_([ANONYMOUS_USER, PUBLIC_USER_NYC_ID])).first().user_guid
        requester_email = Users.query.filter_by(guid=requester_guid).first().email

        # Send email with files to requester and bcc agency_ein users as privacy option is release
        to = [requester_email]
        _safely_send_and_add_email(request_id, email_content, subject, "email_templates/email_file_upload",
                                   "Department of Records and Information Services", file_to_link, to=to, bcc=bcc)

    if privacy == 'private':
        # Send email with files to agency_ein users only as privacy option is private
        _safely_send_and_add_email(request_id, email_content, subject, "email_templates/email_file_upload",
                                   "Department of Records and Information Services", file_to_link, bcc=bcc)


def process_privacy_options(files):
    """
    Creates a dictionary, files_privacy_options, containing lists of 'release' and 'private', with values of filenames.

    :param files: list of filenames
    :return: Dictionary with 'release' and 'private' lists
    """
    private_files = []
    release_files = []
    for filename in files:
        if files[filename]['privacy'] == 'private':
            private_files.append(filename)
        else:
            release_files.append(filename)

    files_privacy_options = dict()

    if release_files:
        files_privacy_options['release'] = release_files

    if private_files:
        files_privacy_options['private'] = private_files
    return files_privacy_options


def _safely_send_and_add_email(request_id,
                               email_content,
                               subject,
                               template,
                               department,
                               files_links,
                               to=None,
                               bcc=None):
    """
    Sends email and creates and stores the email object into the Emails table.

    :param request_id: FOIL request ID
    :param email_content: body of the email
    :param subject: subject of the email (current is for TESTING purposes)
    :param template: html template of the email body being rendered
    :param department: department of the request (current is for TESTING purposes)
    :param files_links: url link of files placed in email body to be downloaded (current link is for TESTING purposes)
    :param to: list of person(s) email is being sent to
    :param bcc: list of person(s) email is being bcc'ed
    :return:
    """
    try:
        send_email(subject, template, to=to, bcc=bcc, department=department, files_links=files_links)
        add_email(request_id, subject, email_content, to=to, bcc=bcc)
    except AssertionError:
        print('Must include: To, CC, or BCC')
    except Exception as e:
        print("Error:", e)


def _process_response(request_id, responses_type, events_type, metadata_id, privacy='private', new_response_value='',
                      previous_response_value=''):
    """
    Creates and stores responses and events objects to the database

    :param request_id: FOIL request ID to be stored into the responses and events tables
    :param responses_type: type of response to be stored in the responses table
    :param events_type: type of event to be stored in the events table
    :param metadata_id: metadata_id of the specific response to be stored in the responses table
    :param privacy: privacy of the response (default is 'private') to be stored in the responses table
    :param new_response_value: string content of the new response, to be stored in the responses table
    :param previous_response_value: string content of the previous response, to be stored in the responses table
    :return: Creates and stores response object with given arguments from separate response type functions.
             Creates and stores events object to the database.
    """
    # create response object
    response = Responses(request_id=request_id,
                         type=responses_type,
                         date_modified=datetime.utcnow(),
                         metadata_id=metadata_id,
                         privacy=privacy)
    # store response object
    create_object(obj=response)

    # create event object
    event = Events(request_id=request_id,
                   # user_id and auth_user_type currently commented out for testing
                   # will need in production to store user information in events table
                   # will this be called for anonymous user?
                   # user_id=current_user.id,
                   # auth_user_type=current_user.type,
                   type=events_type,
                   timestamp=datetime.utcnow(),
                   response_id=response.id,
                   previous_response_value=previous_response_value,
                   new_response_value=new_response_value)
    # store event object
    create_object(obj=event)


class ResponseEditor(metaclass=ABCMeta):
    """
    Abstract base class for editing a response and its metadata.
    """

    def __init__(self, user, response, flask_request):
        self.user = user
        self.response = response
        self.flask_request = flask_request
        self.metadata = response.metadata

        self.data_old = {}
        self.data_new = {}
        self.errors = []

        privacy = flask_request.form.get('privacy')
        if privacy and privacy != self.response.privacy:
            self.data_old['privacy'] = self.response.privacy
            self.data_new['privacy'] = privacy

        self.edit_metadata()
        self.add_event_and_update()

    @property
    def event_type(self):
        return {
            Files: event_type.FILE_EDITED,
            Notes: event_type.NOTE_EDITED,
            Links: event_type.LINK_EDITED,
            Instructions: event_type.INSTRUCTIONS_ADDED,
        }[type(self.metadata)]

    @property
    @abstractmethod
    def metadata_fields(self):
        """ List of fields that can be edited directly """
        return list()

    def edit_metadata(self):
        for field in self.metadata_fields:
            value_new = self.flask_request.form.get(field)
            value_orig = getattr(self.metadata, field)
            if value_new and value_new != value_orig:
                self.data_old[field] = value_orig
                self.data_new[field] = value_new

    def add_event_and_update(self):
        timestamp = datetime.utcnow()
        event = Events(
            type=self.event_type,
            request_id=self.response.request_id,
            response_id=self.response.id,
            user_id=self.user.id,
            auth_user_type=self.user.auth_user_type,
            timestamp=timestamp,
            previous_response_value=self.data_old,
            new_reponse_value=self.data_new)
        create_object(event)
        update_object({'date_modified': timestamp}, 'Response', self.response.id)
        # TODO: deal with privacy, commit, merge with master
        update_object(data_new, type(self.metadata).__name__, self.metadata.id)


class RespFileEditor(ResponseEditor):

    def metadata_fields(self):
        return ['title']

    def edit_metadata(self):
        super(RespFileEditor, self).edit_metadata()


class RespNoteEditor(ResponseEditor):

    def metadata_fields(self):
        return ['content']


# TODO: the rest of them (same fashion)
