from __future__ import print_function
from __future__ import absolute_import
import sys
import json
import logging
import logging.handlers
import os
import time
import six

from .utilities import (generate_machine_id,
                        write_to_disk,
                        write_registered_file,
                        write_unregistered_file,
                        delete_registered_file,
                        delete_unregistered_file,
                        delete_cache_files,
                        determine_hostname)
from .collection_rules import InsightsUploadConf
from .data_collector import DataCollector
from .core_collector import CoreCollector
from .connection import (InsightsConnection,
                         TimeoutException,
                         PayloadTooLargeException,
                         UnregisteredException,
                         InvalidContentTypeException)
from .archive import InsightsArchive
from .support import registration_check
from .constants import InsightsConstants as constants
from .schedule import get_scheduler

NETWORK = constants.custom_network_log_level
LOG_FORMAT = ("%(asctime)s %(levelname)8s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def do_log_rotation():
    handler = get_file_handler()
    return handler.doRollover()


def get_file_handler(config):
    log_file = config.logging_file
    log_dir = os.path.dirname(log_file)
    if not log_dir:
        log_dir = os.getcwd()
    elif not os.path.exists(log_dir):
        os.makedirs(log_dir, 0o700)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, backupCount=3)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return file_handler


def get_console_handler(config):
    if config.silent:
        target_level = logging.FATAL
    elif config.verbose:
        target_level = logging.DEBUG
    elif config.net_debug:
        target_level = NETWORK
    elif config.quiet:
        target_level = logging.ERROR
    else:
        target_level = logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(target_level)

    log_format = LOG_FORMAT if config.verbose else "%(message)s"
    handler.setFormatter(logging.Formatter(log_format))

    return handler


def configure_level(config):
    config_level = 'NETWORK' if config.net_debug else config.loglevel
    config_level = 'DEBUG' if config.verbose else config.loglevel

    init_log_level = logging.getLevelName(config_level)
    if type(init_log_level) in six.string_types:
        print("Invalid log level %s, defaulting to DEBUG" % config_level)
        init_log_level = logging.DEBUG

    logger.setLevel(init_log_level)
    logging.root.setLevel(init_log_level)

    if not config.verbose:
        logging.getLogger('insights.core.dr').setLevel(logging.WARNING)


def set_up_logging(config):
    logging.addLevelName(NETWORK, "NETWORK")
    if len(logging.root.handlers) == 0:
        logging.root.addHandler(get_console_handler(config))
        logging.root.addHandler(get_file_handler(config))
        configure_level(config)
        logger.debug("Logging initialized")


# -LEGACY-
def _legacy_handle_registration(config, pconn):
    '''
    Handle the registration process
    Returns:
        True - machine is registered
        False - machine is unregistered
    '''
    logger.debug('Trying registration.')
    # force-reregister -- remove machine-id files and registration files
    # before trying to register again
    if config.reregister:
        delete_registered_file()
        delete_unregistered_file()
        write_to_disk(constants.machine_id_file, delete=True)
        logger.debug('Re-register set, forcing registration.')

    logger.debug('Machine-id: %s', generate_machine_id(new=config.reregister))

    # check registration with API
    check = registration_check(pconn)

    if check:
        # registered in API
        if config.register:
            logger.info('This host has already been registered.')
        return True

    if config.register:
        # register if specified
        if config.authmethod == 'BASIC' and not (config.username and config.password):
            logger.info('Username and password must be defined in configuration file with BASIC authentication method.')
            return False
        pconn.register()
        write_registered_file()
        return True

    else:
        logger.info('This machine has not been registered.'
                    'Use --register to register this machine.')
        return False


def handle_registration(config, pconn):
    '''
    Does nothing on the platform. Will be deleted eventually.
    '''
    if config.legacy_upload:
        return _legacy_handle_registration(config, pconn)


# -LEGACY-
def _legacy_handle_unregistration(config, pconn):
    """
        returns (bool): True success, False failure
    """
    def __cleanup_local_files():
        write_unregistered_file()
        get_scheduler(config).remove_scheduling()
        delete_cache_files()

    try:
        check = registration_check(pconn)

    except RuntimeError as e:
        if config.force:
            __cleanup_local_files()
            return True
        raise e

    if check:
        unreg = pconn.unregister()
    else:
        unreg = True
        logger.info('This system is already unregistered.')
    if unreg:
        # only set if unreg was successful
        __cleanup_local_files()
    return unreg


def handle_unregistration(config, pconn):
    """
    Returns:
        True - machine was successfully unregistered
        False - machine could not be unregistered
    """
    if config.legacy_upload:
        return _legacy_handle_unregistration(config, pconn)

    unreg = pconn.unregister()
    if unreg or config.force:
        # only set if unreg was successful or --force was set
        write_unregistered_file()
        delete_cache_files()
    return unreg


def update_rules(config, pconn):
    if not pconn:
        raise ValueError('ERROR: Cannot update rules in --offline mode. '
                         'Disable auto_update in config file.')

    pc = InsightsUploadConf(config, conn=pconn)
    return pc.get_conf_update()


def get_branch_info(config):
    """
    Get branch info for a system
    returns (dict): {'remote_branch': -1, 'remote_leaf': -1}
    """
    # in the case we are running on offline mode
    # or we are analyzing a running container/image
    # or tar file, mountpoint, simply return the default branch info
    if config.offline:
        return constants.default_branch_info
    return config.branch_info


def collect(config, pconn):
    """
    All the heavy lifting done here
    """
    branch_info = get_branch_info(config)
    pc = InsightsUploadConf(config)
    output = None

    rm_conf = pc.get_rm_conf()
    blacklist_report = pc.create_report()
    if rm_conf:
        logger.warn("WARNING: Excluding data from files")

    archive = InsightsArchive(config)

    msg_name = determine_hostname(config.display_name)
    if config.core_collect:
        collection_rules = None
        dc = CoreCollector(config, archive)
    else:
        collection_rules = pc.get_conf_file()
        dc = DataCollector(config, archive)
    logger.info('Starting to collect Insights data for %s', msg_name)
    dc.run_collection(collection_rules, rm_conf, branch_info, blacklist_report)
    output = dc.done(collection_rules, rm_conf)
    return output


def get_connection(config):
    return InsightsConnection(config)


def _legacy_upload(config, pconn, tar_file, content_type, collection_duration=None):
    logger.info('Uploading Insights data.')
    api_response = None
    for tries in range(config.retries):
        try:
            upload = pconn.upload_archive(tar_file, '', collection_duration)
        except TimeoutException:
            # allow timeouts here because we want to retry the upload
            upload = None
        except (PayloadTooLargeException, UnregisteredException):
            raise RuntimeError('Upload failed.')

        if not upload:
            logger.error("Upload attempt %d of %d failed! Status Code: %s",
                         tries + 1, config.retries, upload.status_code)
            if tries + 1 != config.retries:
                logger.info("Waiting %d seconds then retrying",
                            constants.sleep_time)
                time.sleep(constants.sleep_time)
            else:
                logger.error("All attempts to upload have failed!")
                logger.error("Please see %s for additional information", config.logging_file)
                raise RuntimeError('Upload failed.')

        api_response = json.loads(upload.text)

        # Write to last upload file
        with open(constants.last_upload_results_file, 'w') as handler:
            if six.PY3:
                handler.write(upload.text)
            else:
                handler.write(upload.text.encode('utf-8'))
        write_to_disk(constants.lastupload_file)

        msg_name = determine_hostname(config.display_name)
        account_number = config.account_number
        if account_number:
            logger.info("Successfully uploaded report from %s to account %s.",
                        msg_name, account_number)
        else:
            logger.info("Successfully uploaded report for %s.", msg_name)
        if config.register:
            # direct to console after register + upload
            logger.info('View the Red Hat Insights console at https://cloud.redhat.com/insights/')
        break

    return api_response


def upload(config, pconn, tar_file, content_type, collection_duration=None):
    if config.legacy_upload:
        return _legacy_upload(config, pconn, tar_file, content_type, collection_duration)
    logger.info('Uploading Insights data.')
    for tries in range(config.retries):
        try:
            upload = pconn.upload_archive(tar_file, content_type, collection_duration)
        except TimeoutException:
            # allow timeouts here because we want to retry the upload
            upload = None
        except (PayloadTooLargeException, InvalidContentTypeException):
            raise RuntimeError('Upload failed.')

        if not upload:
            logger.error("Upload attempt %d of %d failed! Status code: %s",
                         tries + 1, config.retries, upload.status_code)
            if tries + 1 != config.retries:
                logger.info("Waiting %d seconds then retrying",
                            constants.sleep_time)
                time.sleep(constants.sleep_time)
            else:
                logger.error("All attempts to upload have failed!")
                logger.error("Please see %s for additional information", config.logging_file)
                raise RuntimeError('Upload failed.')

        write_to_disk(constants.lastupload_file)
        msg_name = determine_hostname(config.display_name)
        logger.info("Successfully uploaded report for %s.", msg_name)
        if config.register:
            # direct to console after register + upload
            logger.info('View the Red Hat Insights console at https://cloud.redhat.com/insights/')
        return
