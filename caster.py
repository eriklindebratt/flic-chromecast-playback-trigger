import pychromecast
from pychromecast.controllers.media import (
    MEDIA_PLAYER_STATE_PLAYING,
    MEDIA_PLAYER_STATE_PAUSED,
    MEDIA_PLAYER_STATE_BUFFERING,
    MEDIA_PLAYER_STATE_IDLE,
    MEDIA_PLAYER_STATE_UNKNOWN
)
from mimetypes import MimeTypes
import logging
import sys
import threading
from datetime import datetime, timedelta
from util import formatTimeDelta
from time import time, sleep

logging.getLogger('pychromecast').setLevel(logging.WARN)
logger = logging.getLogger(__name__)

onError = lambda error: None
deviceHosts = []
deviceHostScanTimer = None

DEVICE_HOST_SCAN_TIMEOUT = 15.0  # in seconds
CONTINUOUS_DEVICE_HOST_SCAN_INTERVAL = 900.0  # in seconds
WAIT_FOR_PLAYBACK_TIMEOUT = 3.0  # in seconds

class DeviceNotFoundError(Exception):
    pass

class PlaybackStartTimeoutError(Exception):
    pass

class DeviceStatusListener:
    def __init__(self, device, callback):
        self.device = device
        self.callback = callback

    def new_cast_status(self, status):
        logger.debug('Device "{}" got new device status: {}'.format(
            self.device.name,
            status
        ))

        self.callback(self.device, status)

class DeviceMediaStatusListener:
    def __init__(self, device, callback):
        self.device = device
        self.callback = callback
        self.lastPlayerState = None

    def new_media_status(self, status):
        logger.debug('Device "{}" got new media status: {}'.format(
            self.device.name,
            status
        ))

        if self.lastPlayerState and self.lastPlayerState == status.player_state:
            return

        self.lastPlayerState = status.player_state

        self.callback(self.device, status)

def setup(errorHandler=None):
    global onError

    logger.info('Setting up...')

    if errorHandler is not None:
        onError = errorHandler

    if not scanForDeviceHosts():
        logger.error('Setup completed with failing scanner')
    else:
        logger.info('Setup completed')


def scanForDeviceHosts():
    '''
    Returns: Bool Whether scanner returned any device hosts or not
    '''

    global deviceHosts, deviceHostScanTimer

    # cancel currently running scanner, if any
    cancelDeviceHostScanner()

    logger.debug('Scanning for device hosts...')

    startTime = datetime.utcnow()
    deviceHosts = pychromecast.discover_chromecasts(
        timeout=DEVICE_HOST_SCAN_TIMEOUT
    )

    formattedScanTime = formatTimeDelta(datetime.utcnow() - startTime)
    formattedNextScanTimestamp = (
        datetime.utcnow() +
        timedelta(seconds=CONTINUOUS_DEVICE_HOST_SCAN_INTERVAL)
    ).isoformat()

    gotAcceptableSetOfHosts = deviceHosts is not None and len(deviceHosts) > 0

    if not gotAcceptableSetOfHosts:
        logger.error(
            'Device host scan completed with no hosts found after {}. '
            'Scheduling next scan for {}.'.format(
                formattedScanTime,
                formattedNextScanTimestamp
            )
        )

        cancelDeviceHostScanner()
        onError(
            Exception(
                'Device host scan completed with no device(s) found.'.format(
                    DEVICE_SCAN_ATTEMPTS_PER_SCAN
                )
            )
        )
    else:
        logger.info(
            'Device scan completed with {} device(s) found after {}. '
            'Scheduling next scan for {}.'.format(
                len(deviceHosts),
                formattedScanTime,
                formattedNextScanTimestamp
            )
        )

    # continue to scan every N seconds
    deviceHostScanTimer = threading.Timer(
        CONTINUOUS_DEVICE_HOST_SCAN_INTERVAL,
        scanForDeviceHosts
    )
    deviceHostScanTimer.daemon = True
    deviceHostScanTimer.start()

    return gotAcceptableSetOfHosts

def cancelDeviceHostScanner():
    global deviceHostScanTimer

    if deviceHostScanTimer is not None and deviceHostScanTimer.is_alive():
        logger.debug('Canceling device host scanner')
        deviceHostScanTimer.cancel()
        deviceHostScanTimer = None

def getDevice(deviceName, calledFromSelf=False):
    if not calledFromSelf:
        logger.info('Getting device "{}"'.format(deviceName))

    try:
        host = next(i for i in deviceHosts if i[-1] == deviceName)
        device = pychromecast.Chromecast(host[0], host[1])
    except StopIteration:
        if not calledFromSelf:
            logger.warn(
                'Device "{}" not found - trigger new device scan'.format(
                    deviceName
                )
            )

            scanForDeviceHosts()

            return getDevice(deviceName, calledFromSelf=True)
        else:
            logger.warn(
                'Device "{}" not found (tried scanning anew)'.format(
                    deviceName
                )
            )

            raise(
                DeviceNotFoundError('Device "{}" not found'.format(deviceName))
            )

    # start worker thread and wait for cast device to be ready
    logger.debug('Device "{}" found, connecting...'.format(deviceName))

    device.wait()

    logger.debug('Connected to "{}"'.format(deviceName))

    return device

def stop(device, disconnectFromDevice=False):
    if not device:
        return

    logger.info('Stopping playback on "{}"'.format(device.name))

    try:
        device.media_controller.stop()
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to stop: {}'.format(e))
        onError(e)
        return

    if disconnectFromDevice:
        logger.info(
            'Playback stopped on "{}" - disconnecting..'.format(device.name)
        )
        device.disconnect(blocking=False)

def quit(device, disconnectFromDevice=False):
    if not device:
        return

    logger.info('Closing Chromecast application on "{}"'.format(device.name))

    try:
        if device.app_id:
            device.quit_app()
        else:
            logger.info(
                ' - no Chromecast application active '
                'on "{}"!'.format(device.name)
            )
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to quit: {}'.format(e))
        onError(e)
        return

    if disconnectFromDevice:
        logger.info('Disconnecting from "{}"'.format(device.name))
        device.disconnect(blocking=False)

def setVolume(device, volume, callback=None, disconnectFromDevice=False):
    if not device:
        return

    logger.info(
        'Setting volume to {}% on "{}"'.format(
            volume * 100,
            device.name
        )
    )

    try:
        device.set_volume(volume)
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to set volume: {}'.format(e))
        onError(e)
        return

    if callback is not None:
        logger.info(
            'Volume set on "{}" - disconnecting...'.format(device.name)
        )
        device.disconnect()

    if disconnectFromDevice:
        logger.info(
            'Volume set on "{}" - disconnecting...'.format(device.name)
        )
        device.disconnect()

def isPlaying(device):
    if not device:
        return False

    try:
        return device.media_controller.status.player_is_playing
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to get `isPlaying`: {}'.format(e))
        onError(e)
        return False

def isPaused(device):
    if not device:
        return False

    try:
        return device.media_controller.status.player_is_paused
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to get `isPaused`: {}'.format(e))
        onError(e)
        return False

def play(data, device=None):
    if data is None:
        data = {}

    ########################
    # set up media data structure
    mediaArgs = dict(data['media']['args'])

    try:
        mimeType = MimeTypes().guess_type(data['media']['url'])[0]
        mediaArgs['content_type'] = mimeType
    except IndexError:
        raise Exception(
            'Failed to look up mime type for media url "{}"'.format(
                data['media']['url']
            )
        )
    if not mediaArgs.get('content_type'):
        raise Exception(
            'Failed to look up mime type for media url "{}"'.format(
                data['media']['url']
            )
        )
    ########################

    if data.get('volume') is not None:
        setVolume(device, data['volume'])

    logger.info('Starting playback on "{}"'.format(device.name))
    logger.debug('Playing:\n  - url: {}\n  - args: {}'.format(data['media']['url'], mediaArgs))

    mc = device.media_controller

    mc.play_media(data['media']['url'], **mediaArgs)

    start = time()
    mc.block_until_active(timeout=WAIT_FOR_PLAYBACK_TIMEOUT)
    # `block_until_active` might return before `WAIT_FOR_PLAYBACK_TIMEOUT`
    # ensure we wait the whole time until checking status
    sleep(max(WAIT_FOR_PLAYBACK_TIMEOUT - (time() - start), 0))

    if not mc.status.player_state in (
            MEDIA_PLAYER_STATE_PLAYING,
            MEDIA_PLAYER_STATE_BUFFERING):
        msg = 'Failed to start playback within {} seconds'.format(
            WAIT_FOR_PLAYBACK_TIMEOUT
        )
        logger.warning(msg)
        raise PlaybackStartTimeoutError(msg)

    return device

def addDeviceStatusListener(device, callback):
    device.media_controller.register_status_listener(
        DeviceStatusListener(device, callback)
    )

def addDevicePlayerStatusListener(device, callback):
    device.media_controller.register_status_listener(
        DeviceMediaStatusListener(device, callback)
    )
