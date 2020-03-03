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
deviceHosts = []
deviceHostScanTimer = None

DEVICE_HOST_SCAN_TIMEOUT = 15
CONTINUOUS_DEVICE_HOST_SCAN_INTERVAL = 900.0  # in seconds

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
    #devices = pychromecast.get_chromecasts(tries=DEVICE_SCAN_ATTEMPTS_PER_SCAN)
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
    deviceHostScanTimer.start()

    return gotAcceptableSetOfHosts

def cancelDeviceHostScanner():
    global deviceHostScanTimer

    if deviceHostScanTimer is not None and deviceHostScanTimer.is_alive():
        deviceHostScanTimer.cancel()
        deviceHostScanTimer = None

def getDevice(deviceName, calledFromSelf=False):
    if not calledFromSelf:
        logger.debug('Getting device "{}"'.format(deviceName))

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

            return None

    # start worker thread and wait for cast device to be ready
    logger.debug('Device "{}" found, connecting...'.format(deviceName))

    device.wait()

    logger.debug('Connected to "{}"'.format(deviceName))

    return device

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

