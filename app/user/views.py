from datetime import datetime

from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound

from flask import request, jsonify
from flask_login import current_user

from app.user import user
from app.user_request.utils import create_user_request_event
from app.models import Users, Events, Roles, UserRequests
from app.constants import (
    USER_ID_DELIMITER,
    event_type,
    permission,
    role_name,
    user_type_request,
)
from app.lib.db_utils import (
    update_object,
    create_object,
    delete_object,
)
from app.lib.utils import eval_request_bool


@user.route('/<user_id>', methods=['PATCH'])
def patch(user_id):
    """
    Request Parameters:
    - title
    - organization
    - email
    - phone_number
    - fax_number
    - mailing_address
    - is_super
    - is_agency_active
    - is_agency_admin
    (Mailing Address)
    - zip
    - city
    - state
    - address_one
    - address_two

    Restrictions:
    - Anonymous Users
        - cannot access this endpoint
    - Agency Administrators
        - cannot change their agency status
        - can only update the agency status of users within their agency
        - cannot change any super user status
    - Super Users
        - cannot change their super user status
    - Agency Users
        - cannot change any user except for themselves or
          *anonymous* requesters for requests they are assigned to
        - cannot change super user or agency status
    - Public Users
        - can only update themselves
        - cannot change super user or agency status

    """
    if not current_user.is_anonymous:
        # attempt to parse user_id and find user
        try:
            guid, auth_type = user_id.split(USER_ID_DELIMITER)
            user_ = Users.query.filter_by(guid=guid,
                                          auth_user_type=auth_type).one()
        except (ValueError, NoResultFound, MultipleResultsFound):
            return jsonify({}), 404

        updating_self = current_user == user_
        current_user_is_agency_user = (current_user.is_agency
                                       and not current_user.is_super
                                       and not current_user.is_agency_admin
                                       and current_user.is_agency_active)
        current_user_is_agency_admin = (current_user.is_agency
                                        and not current_user.is_super
                                        and current_user.is_agency_admin
                                        and current_user.is_agency_active)
        same_agency = current_user.agency is user_.agency
        associated_anonymous_requester = (user_.is_anonymous_requester
                                          and current_user.user_requests.filter_by(
                                            request_id=user_.anonymous_request.id
                                          ).first() is None)

        is_agency_admin = request.form.get('is_agency_admin')
        is_agency_active = request.form.get('is_agency_active')
        is_super = request.form.get('is_super')

        changing_status = any((is_agency_active, is_agency_admin, is_super))

        rform_copy = dict(request.form)
        try:
            rform_copy.pop('is_agency_admin')
            rform_copy.pop('is_agency_active')
            changing_more_than_agency_status = len(rform_copy) != 0
        except KeyError:
            changing_more_than_agency_status = False

        # VALIDATE
        if ((updating_self and (
                # super user attempting to change their own super status
                (current_user.is_super and is_super is not None)
            or
                # agency admin or public user attempting to change their own agency/super status
                (changing_status and (current_user_is_agency_admin or current_user.is_public)))) or
           (not updating_self and (
                # public user attempting to change another user
                current_user.is_public
            or
                # agency user attempting to change a agency/super status
                (current_user_is_agency_user and changing_status)
            or
                # agency user attempting to change a user that is not an anonymous requester
                # for a request they are assigned to
                (current_user_is_agency_user and (
                            not user_.is_anonymous_requester or not associated_anonymous_requester))
            or
                # agency admin attempting to change another user that is not in the same agency or
                # attempting to change more than just the agency status of a user
                (current_user_is_agency_admin
                 and not (associated_anonymous_requester or user_.is_anonymous_requester)
                 and (not same_agency or changing_more_than_agency_status))
            or
                # agency admin attempting to change an anonymous requester for a request
                # they are not assigned to
                (current_user_is_agency_admin and associated_anonymous_requester)))):
            return jsonify({}), 403

        # UPDATE
        user_fields = [
            'email',
            'phone_number',
            'fax_number',
            'title',
            'organization'
        ]
        status_fields = [
            'is_agency_admin',
            'is_agency_active',
            'is_super'
        ]
        address_fields = [
            'zip',
            'city',
            'state',
            'address_one',
            'address_two'
        ]

        user_field_val = {
            'email': request.form.get('email'),
            'phone_number': request.form.get('phone'),
            'fax_number': request.form.get('fax'),
            'title': request.form.get('title'),
            'organization': request.form.get('organization'),
        }
        status_field_val = {
            'is_agency_admin': request.form.get('is_agency_admin'),
            'is_agency_active': request.form.get('is_agency_active'),
            'is_super': request.form.get('is_super')
        }
        address_field_val = {
            'address_one': request.form.get('address_one'),
            'address_two': request.form.get('address_two'),
            'zip': request.form.get('zipcode'),
            'city': request.form.get('city'),
            'state': request.form.get('state')
        }

        # check if missing contact information
        if (user_field_val['email'] == ''
            and user_field_val['phone_number'] == ''
            and user_field_val['fax_number'] == ''
            and (address_field_val['city'] == ''
                 or address_field_val['zip'] == ''
                 or address_field_val['state'] == ''
                 or address_field_val['address_one'] == '')):
            return jsonify({"error": "Missing contact information."}), 400

        old = {}
        old_address = {}
        new = {}
        new_address = {}

        for field in status_fields:
            if status_field_val[field] is not None:
                cur_val = getattr(user_, field)
                new_val = eval_request_bool(status_field_val[field])
                if cur_val != new_val:
                    old[field] = cur_val
                    new[field] = new_val

        for field in user_fields:
            val = user_field_val[field]
            if val is not None:
                if val == '':
                    user_field_val[field] = None  # null in db, not empty string
                cur_val = getattr(user_, field)
                new_val = user_field_val[field]
                if cur_val != new_val:
                    old[field] = cur_val
                    new[field] = new_val

        for field in address_fields:
            val = address_field_val[field]
            if val is not None:
                if val == '':
                    address_field_val[field] = None
                cur_val = (user_.mailing_address.get(field)
                           if user_.mailing_address else None)
                new_val = address_field_val[field]
                if cur_val != new_val:
                    old_address[field] = cur_val
                    new_address[field] = new_val

        if new or new_address:
            # in spite of not changing, the guid and auth type of
            # the user being updated is added to Events.new_value
            # in order to identify this user
            new['user_guid'] = user_.guid
            new['auth_user_type'] = user_.auth_user_type

            if new_address:
                new['mailing_address'] = new_address
            if old_address:
                old['mailing_address'] = old_address

            # update object
            update_object(
                new,
                Users,
                (guid, auth_type)
            )

            # create event(s)
            event_kwargs = {
                'request_id': user_.anonymous_request.id if user_.is_anonymous_requester else None,
                'response_id': None,
                'user_guid': current_user.guid,
                'auth_user_type': current_user.auth_user_type,
                'timestamp': datetime.utcnow()
            }

            if changing_status:
                new_statuses = {}
                old_statuses = {}
                for field in status_fields:
                    if new.get(field) is not None:
                        new_statuses[field] = new.pop(field)
                        old_statuses[field] = old.pop(field)

                # TODO: a better way to store user identifiers (than in the value columns)
                new_statuses['user_guid'] = user_.guid
                new_statuses['auth_user_type'] = user_.auth_user_type

                is_agency_active = new_statuses.get('is_agency_active')
                is_agency_admin = new_statuses.get('is_agency_admin')

                if is_agency_active is not None and not is_agency_active:
                    # remove ALL UserRequests
                    for user_request in user_.user_requests.all():
                        create_user_request_event(event_type.USER_REMOVED, user_request)
                        delete_object(user_request)
                elif is_agency_admin is not None:

                    def set_permissions_and_create_event(user_req, perms):
                        """
                        Set permissions for a user request and create a
                        'user_permissions_changed' Event.

                        :param user_req: user request
                        :param perms: permissions to set for user request
                        """
                        old_permissions = user_req.permissions
                        user_request.set_permissions(perms)
                        create_user_request_event(event_type.USER_PERM_CHANGED,
                                                  user_req,
                                                  old_permissions)
                    if is_agency_admin:
                        permissions = Roles.query.filter_by(name=role_name.AGENCY_ADMIN).one().permissions
                        # create UserRequests for ALL existing requests under user's agency where user is not assigned
                        # for where the user *is* assigned, only change the permissions
                        for req in user_.agency.requests:
                            user_request = UserRequests.query.filter_by(
                                request_id=req.id,
                                user_guid=user_.guid,
                                auth_user_type=user_.auth_user_type
                            ).first()
                            if user_request is None:
                                user_request = UserRequests(
                                    user_guid=user_.guid,
                                    auth_user_type=user_.auth_user_type,
                                    request_id=req.id,
                                    request_user_type=user_type_request.AGENCY,
                                    permissions=permissions
                                )
                                create_object(user_request)
                                create_user_request_event(event_type.USER_ADDED,
                                                          user_request)
                            else:
                                set_permissions_and_create_event(user_request, permissions)

                    else:
                        # update ALL UserRequests (strip user of permissions)
                        for user_request in user_.user_requests.all():
                            set_permissions_and_create_event(user_request, permission.NONE)

                # TODO: single email detailing user changes?

                create_object(Events(
                    type_=event_type.USER_STATUS_CHANGED,
                    previous_value=old_statuses,
                    new_value=new_statuses,
                    **event_kwargs
                ))

            if old:  # something besides status changed ('new' holds user guid and auth type)
                create_object(Events(
                    type_=(event_type.REQUESTER_INFO_EDITED
                           if user_.is_anonymous_requester
                           else event_type.USER_INFO_EDITED),
                    previous_value=old,
                    new_value=new,
                    **event_kwargs
                ))
            return jsonify({}), 200
        else:
            return jsonify({"message": "No changes detected."}), 200

    return jsonify({}), 403
