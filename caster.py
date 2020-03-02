import time
import pychromecast
from mimetypes import MimeTypes
import logging
import sys
import threading
from datetime import datetime, timedelta
from util import formatTimeDelta

logging.getLogger('pychromecast').setLevel(logging.WARN)
logger = logging.getLogger(__name__)

onError = lambda error: None
devices = []
deviceScanTimer = None

DEVICE_SCAN_ATTEMPTS_PER_SCAN = 2
CONTINUOUS_DEVICE_SCAN_INTERVAL = 900.0  # in seconds

def setup(errorHandler=None):
    global onError

    logger.info('Setting up...')

    if errorHandler is not None:
        onError = errorHandler

    if not scanForDevices():
        logger.error('Setup completed with failing scanner\n---')
    else:
        logger.info('Setup completed\n---')


def scanForDevices():
    '''
    Returns: Bool Whether scanner returned any devices or not
    '''

    global devices, deviceScanTimer

    # cancel currently running scanner, if any
    cancelDeviceScanner()

    logger.debug('Scanning for devices at {}...'.format(datetime.utcnow()))

    startTime = datetime.utcnow()
    devices = pychromecast.get_chromecasts(tries=DEVICE_SCAN_ATTEMPTS_PER_SCAN)

    formattedScanTime = formatTimeDelta(datetime.utcnow() - startTime)
    formattedNextScanTimestamp = (
        datetime.utcnow() +
        timedelta(seconds=CONTINUOUS_DEVICE_SCAN_INTERVAL)
    ).isoformat()

    gotAcceptableSetOfDevices = devices is not None and len(devices) > 0

    if not gotAcceptableSetOfDevices:
        logger.error(
            'Device scan completed with no device(s) found after {} '
            '(max {} attempt(s)). Scheduling next scan for {}.'.format(
                formattedScanTime,
                DEVICE_SCAN_ATTEMPTS_PER_SCAN,
                formattedNextScanTimestamp
            )
        )

        cancelDeviceScanner()
        onError(
            Exception(
                'Device scan completed with no device(s) found '
                '(max {} attempt(s))'.format(DEVICE_SCAN_ATTEMPTS_PER_SCAN)
            )
        )
    else:
        logger.info(
            'Device scan completed with {} device(s) found after {} '
            '(max {} attempt(s)). Scheduling next scan for {}.'.format(
                len(devices),
                formattedScanTime,
                DEVICE_SCAN_ATTEMPTS_PER_SCAN,
                formattedNextScanTimestamp
            )
        )

    # continue to scan every N seconds
    deviceScanTimer = threading.Timer(
        CONTINUOUS_DEVICE_SCAN_INTERVAL,
        scanForDevices
    )
    deviceScanTimer.start()

    return gotAcceptableSetOfDevices

def cancelDeviceScanner():
    global deviceScanTimer

    if deviceScanTimer is not None and deviceScanTimer.is_alive():
        deviceScanTimer.cancel()
        deviceScanTimer = None

def getDevice(deviceName, calledFromSelf=False):
    if not calledFromSelf:
        logger.debug('Getting device "{}"'.format(deviceName))

    try:
        cast = next(cc for cc in devices if cc.device.friendly_name == deviceName)
    except StopIteration:
        if not calledFromSelf:
            logger.warn(
                'Device "{}" not found - trigger new device scan'.format(
                    deviceName
                )
            )

            scanForDevices()

            return getDevice(deviceName, calledFromSelf=True)
        else:
            logger.warn(
                'Device "{}" not found (tried scanning anew)'.format(
                    deviceName
                )
            )

            return None

    # start worker thread and wait for cast device to be ready
    logger.debug('Device "{}" found, connecting...'.format(deviceName))
    cast.wait()
    logger.debug('Connected to "{}"'.format(deviceName))

    return cast

def stop(device):
    if not device:
        return

    logger.info('Stopping playback on "{}"'.format(device.name))

    try:
        device.media_controller.stop()
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to stop: {}'.format(e))
        onError(e)

def quit(device):
    if not device:
        return

    logger.info('Closing Chromecast application "{}"'.format(device.name))

    try:
        device.quit_app()
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to quit: {}'.format(e))
        onError(e)

def setVolume(device, volume):
    logger.info('setVolume - device: {}'.format(device))

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

def isPlaying(device):
    try:
        return device.media_controller.is_playing
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to get `isPlaying`: {}'.format(e))
        onError(e)
        return False

def play(data, device=None):
    if data is None:
        data = {}

    if not device and data.get('deviceName') is None:
        raise Exception('Missing `data[\'deviceName\']`')

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

    if not device:
        device = getDevice(data['deviceName'])
        if not device:
            raise Exception('Failed to get device "{}"'.format(data['deviceName']))

    if data.get('volume') is not None:
        setVolume(device, data['volume'])

    logger.info('Starting playback on "{}"'.format(device.name))
    logger.debug('Playing:\n  - url: {}\n  - args: {}'.format(data['media']['url'], mediaArgs))

    mc = device.media_controller

    mc.play_media(data['media']['url'], **mediaArgs)
    mc.block_until_active()

    return device

