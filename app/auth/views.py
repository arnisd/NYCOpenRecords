"""
.. module:: auth.views.

   :synopsis: Handles OAUTH and LDAP authentication endpoints for NYC OpenRecords

"""
from datetime import datetime
from urllib.parse import urljoin

from requests_oauthlib import OAuth2Session
from oauthlib.oauth2 import MobileApplicationClient

from flask import (
    request,
    redirect,
    session,
    render_template,
    url_for,
    abort,
    flash,
)
from flask_login import (
    login_user,
    logout_user,
    current_user,
    current_app,
    login_required,
)
from app.auth import auth
from app.auth.constants.error_msg import UNSAFE_NEXT_URL
from app.auth.forms import ManageUserAccountForm, LDAPLoginForm
from app.auth.utils import (
    revoke_and_remove_access_token,
    ldap_authentication,
    find_user_by_email,
    handle_user_data,
    fetch_user_json,
    is_safe_url,
    update_openrecords_user,
)
from app.constants.web_services import AUTH_ENDPOINT


@auth.route('/login', methods=['GET'])
def login():
    """
    Based off of: https://flask-login.readthedocs.io/en/latest/#login-example

    If using LDAP, see ldap_login().

    If using SAML/OAuth, check for the presence of an access token
    in the session, which is used to fetch user information for processing.
    If no token exists, send the user to the authorization url
    (first leg of the OAuth 2 workflow).

    * NOTE *
    Since we are using OAuth Mobile Application Flow to fetch the information
    normally retrieved in a SAML Assertion, the url resulting from authorization
    is in the format 'redirect_uri#access_token=123guid=ABC...'. Notice the fragment
    identifier ('#') in place of what would normally be the '?' separator.
    Since Flask drops everything after the identifier, we must extract these values
    client-side in order to forward them to the server. Therefore, the redirect uri
    we are using is our home page (main.index, along with the 'next' url if present)
    which contains a script that detects the presence of an access token
    and redirects to the intended OAuth callback (auth.authorize).

    https://tools.ietf.org/html/rfc3986#section-3.5

    """
    next_url = request.args.get('next')

    if current_app.config['USE_LDAP']:
        return redirect(url_for('auth.ldap_login', next=next_url))

    elif current_app.config['USE_OAUTH']:
        if session.get('token') is not None:
            status, user_json = fetch_user_json()
            if status == 200:
                return handle_user_data(
                    user_json['id'],
                    user_json['userType'],
                    user_json['email'],
                    user_json.get('firstName'),
                    user_json.get('middleInitial'),
                    user_json.get('lastName'),
                    user_json.get('termsOfUse'),
                    user_json.get('validated'),
                    next_url)

        redirect_uri = urljoin(request.host_url, url_for('main.index', next=next_url))

        oauth = OAuth2Session(
            client=MobileApplicationClient(client_id=current_app.config['NYC_ID_USERNAME']),
            redirect_uri=redirect_uri
        )
        auth_url, _ = oauth.authorization_url(
            urljoin(current_app.config['WEB_SERVICES_URL'], AUTH_ENDPOINT)
        )
        return redirect(auth_url)
    return abort(404)


@auth.route('/authorize', methods=['GET'])
def authorize():
    """
    Store access token and a UTC timestamp for its
    expiration date and time in the session, then
    process SAML Assertion information.

    See: https://nyc4d.nycnet/nycidauthentication.shtml
    """
    session['token'] = {
        'access_token': request.args['access_token'],
        'token_type': request.args['token_type']
    }
    session['token_expires_at'] = datetime.utcnow().timestamp() + int(request.args['expires_in'])

    return handle_user_data(
        request.args['GUID'],
        request.args['userType'],
        request.args['mail'],
        request.args.get('givenName'),
        request.args.get('middleName'),
        request.args.get('sn'),
        request.args.get('nycExtTOUVersion'),
        request.args.get('nycExtEmailValidationFlag'),
        request.args.get('next'))


@auth.route('/logout', methods=['GET'])
def logout():
    timed_out = request.args.get('timeout')

    if current_app.config['USE_LDAP']:
        return redirect(url_for('auth.ldap_logout', timed_out=timed_out))

    elif current_app.config['USE_OAUTH']:
        if 'token' in session:
            revoke_and_remove_access_token()
        if timed_out is not None:
            flash("Your session timed out. Please login again", category='info')
        logout_user()
        session.regenerate()
        return redirect(url_for("main.index"))

    return abort(404)


@auth.route('/manage', methods=['GET', 'POST'])
@login_required
def manage():
    form = ManageUserAccountForm(user=current_user)

    if request.method == 'POST':
        if form.validate_on_submit():
            print(form.title.data)
            update_openrecords_user(form)
            redirect(url_for('auth.manage'))
        else:
            flash("Account cannot be updated.", category="danger")
            return render_template('auth/manage_account.html', form=form)
    else:
        form.autofill()

    return render_template('auth/manage_account.html', form=form)


# LDAP -----------------------------------------------------------------------------------------------------------------

@auth.route('/ldap_login', methods=['GET', 'POST'])
def ldap_login():
    login_form = LDAPLoginForm()
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = find_user_by_email(email)

        if user is not None:
            authenticated = ldap_authentication(email, password)

            if authenticated:
                login_user(user)
                session.regenerate()
                session['user_id'] = current_user.get_id()

                next_url = request.form.get('next')
                if not is_safe_url(next_url):
                    return abort(400, UNSAFE_NEXT_URL)

                return redirect(next_url or url_for('main.index'))

            flash("Invalid username/password combination.", category="danger")
            return render_template('auth/ldap_login_form.html', login_form=login_form)
        else:
            flash("User not found. Please contact your agency FOIL Officer to gain access to the system.",
                  category="warning")
            return render_template('auth/ldap_login_form.html', login_form=login_form)

    elif request.method == 'GET':
        return render_template(
            'auth/ldap_login_form.html',
            login_form=login_form,
            next_url=request.args.get('next', ''))


@auth.route('/ldap_logout', methods=['GET'])
def ldap_logout(timed_out=None):
    logout_user()
    session.regenerate()
    if timed_out is not None:
        flash("Your session timed out. Please login again", category='info')
    return redirect(url_for('main.index'))
