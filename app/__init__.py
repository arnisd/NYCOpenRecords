import atexit
from datetime import date

import os
import uuid
import redis
import logging
from logging import Formatter
from logging.handlers import TimedRotatingFileHandler, SMTPHandler
from business_calendar import Calendar, MO, TU, WE, TH, FR
from celery import Celery
from flask import (
    Flask,
    render_template,
    request as flask_request,
)
from flask_apscheduler import APScheduler
from flask_bootstrap import Bootstrap
from flask_elasticsearch import FlaskElasticsearch
from flask_tracy import Tracy
from flask_kvsession import KVSessionExtension
from flask_login import LoginManager, current_user
from flask_mail import Mail
from flask_moment import Moment
from flask_recaptcha import ReCaptcha
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CsrfProtect
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from simplekv.decorator import PrefixDecorator
from simplekv.memory.redisstore import RedisStore
from app.lib import NYCHolidays, jinja_filters
from app.constants import OPENRECORDS_DL_EMAIL

from config import config, Config

recaptcha = ReCaptcha()
bootstrap = Bootstrap()
es = FlaskElasticsearch()
db = SQLAlchemy()
csrf = CsrfProtect()
moment = Moment()
mail = Mail()
tracy = Tracy()
login_manager = LoginManager()
scheduler = APScheduler()
store = RedisStore(redis.StrictRedis(db=Config.SESSION_REDIS_DB, host=Config.REDIS_HOST, port=Config.REDIS_PORT))
prefixed_store = PrefixDecorator('session_', store)
celery = Celery(__name__, broker=Config.CELERY_BROKER_URL)

upload_redis = redis.StrictRedis(db=Config.UPLOAD_REDIS_DB, host=Config.REDIS_HOST, port=Config.REDIS_PORT)
email_redis = redis.StrictRedis(db=Config.EMAIL_REDIS_DB, host=Config.REDIS_HOST, port=Config.REDIS_PORT)

holidays = NYCHolidays(years=[year for year in range(date.today().year, date.today().year + 5)])
calendar = Calendar(
    workdays=[MO, TU, WE, TH, FR],
    holidays=[str(key) for key in holidays.keys()]
)


def create_app(config_name, jobs_enabled=True):
    """
    Set up the Flask Application context.

    :param config_name: Configuration for specific application context.

    :return: Flask application
    """

    app = Flask(__name__)
    app.config.from_object(config[config_name])
    config[config_name].init_app(app)

    # TODO: handler_info, handler_debug, handler_warn
    mail_handler = SMTPHandler(mailhost=(app.config['MAIL_SERVER'], app.config['MAIL_PORT']),
                               fromaddr=app.config['MAIL_SENDER'],
                               toaddrs=OPENRECORDS_DL_EMAIL, subject='OpenRecords Error')
    mail_handler.setLevel(logging.ERROR)
    mail_handler.setFormatter(Formatter('''
    Message Type:       %(levelname)s
    Location:           %(pathname)s:%(lineno)d
    Module:             %(module)s
    Function:           %(funcName)s
    Time:               %(asctime)s
    
    Message:
    %(message)s
    '''))
    app.logger.addHandler(mail_handler)

    handler_error = TimedRotatingFileHandler(
        os.path.join(app.config['LOGFILE_DIRECTORY'],
                     'error',
                     'openrecords_{}_error.log'.format(app.config['APP_VERSION_STRING'])),
        when='midnight', interval=1, backupCount=60)
    handler_error.setLevel(logging.ERROR)
    handler_error.setFormatter(Formatter(
        '------------------------------------------------------------------------------- \n'
        '%(asctime)s %(levelname)s: %(message)s '
        '[in %(pathname)s:%(lineno)d]\n'
    ))
    app.logger.addHandler(handler_error)

    app.jinja_env.filters['format_event_type'] = jinja_filters.format_event_type
    app.jinja_env.filters['format_response_type'] = jinja_filters.format_response_type
    app.jinja_env.filters['format_response_privacy'] = jinja_filters.format_response_privacy
    app.jinja_env.filters['format_ultimate_determination_reason'] = jinja_filters.format_ultimate_determination_reason

    recaptcha.init_app(app)
    bootstrap.init_app(app)
    es.init_app(app,
                use_ssl=app.config['ELASTICSEARCH_USE_SSL'],
                verify_certs=app.config['ELASTICSEARCH_VERIFY_CERTS'])
    db.init_app(app)
    csrf.init_app(app)
    moment.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    celery.conf.update(app.config)
    if jobs_enabled:
        scheduler.init_app(app)

    with app.app_context():
        from app.models import Anonymous
        login_manager.login_view = 'auth.login'
        login_manager.anonymous_user = Anonymous
        KVSessionExtension(prefixed_store, app)

    # schedule jobs
    if jobs_enabled:
        # NOTE: if running with reloader, jobs will execute twice
        import jobs
        scheduler.add_job(
            'update_request_statuses',
            jobs.update_request_statuses,
            name="Update requests statuses every day at 3 AM.",
            trigger=CronTrigger(hour=3),
        )
        scheduler.add_job(
            'check_sanity',
            jobs.check_sanity,
            name="Check if scheduler is running every morning at 8 AM.",
            # trigger=IntervalTrigger(minutes=1)  # TODO: switch to cron below after testing
            trigger=CronTrigger(hour=8)
        )

        scheduler.start()

    # Error Handlers
    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error/generic.html", status_code=400,
                               message=e.description or None)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error/generic.html", status_code=403)

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("error/generic.html", status_code=404)

    @app.errorhandler(500)
    def internal_server_error(e):
        error_id = str(uuid.uuid4())
        app.logger.error("""Request:   {method} {path}
    IP:        {ip}
    User:      {user}
    Agent:     {agent_platform} | {agent_browser} {agent_browser_version}
    Raw Agent: {agent}
    Error ID:  {error_id}
            """.format(
                method=flask_request.method,
                path=flask_request.path,
                ip=flask_request.remote_addr,
                agent_platform=flask_request.user_agent.platform,
                agent_browser=flask_request.user_agent.browser,
                agent_browser_version=flask_request.user_agent.version,
                agent=flask_request.user_agent.string,
                user=current_user,
                error_id=error_id
            ), exc_info=e
        )
        return render_template("error/generic.html",
                               status_code=500,
                               error_id=error_id)

    @app.context_processor
    def add_session_config():
        """Add current_app.permanent_session_lifetime converted to milliseconds
        to context. The config variable PERMANENT_SESSION_LIFETIME is not
        used because it could be either a timedelta object or an integer
        representing seconds.
        """
        return {
            'PERMANENT_SESSION_LIFETIME_MS': (
                app.permanent_session_lifetime.seconds * 1000),
        }

    # Register Blueprints
    from .main import main
    app.register_blueprint(main)

    from .auth import auth
    app.register_blueprint(auth, url_prefix="/auth")

    from .request import request
    app.register_blueprint(request, url_prefix="/request")

    from .request.api import request_api_blueprint
    app.register_blueprint(request_api_blueprint, url_prefix="/request/api/v1.0")

    from .report import report
    app.register_blueprint(report, url_prefix="/report")

    from .response import response
    app.register_blueprint(response, url_prefix="/response")

    from .upload import upload
    app.register_blueprint(upload, url_prefix="/upload")

    from .user import user
    app.register_blueprint(user, url_prefix="/user")

    from .agency import agency
    app.register_blueprint(agency, url_prefix="/agency")

    from .search import search
    app.register_blueprint(search, url_prefix="/search")

    from .admin import admin
    app.register_blueprint(admin, url_prefix="/admin")

    from .user_request import user_request
    app.register_blueprint(user_request, url_prefix="/user_request")

    from .permissions import permissions
    app.register_blueprint(permissions, url_prefix="/permissions/api/v1.0")

    # exit handling
    if jobs_enabled:
        atexit.register(lambda: scheduler.shutdown())

    return app
