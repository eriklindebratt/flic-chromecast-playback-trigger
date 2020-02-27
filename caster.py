import time
import pychromecast
from mimetypes import MimeTypes
import logging
import sys
import threading
from datetime import datetime, timedelta
from util import formatTimeDelta

logging.getLogger('pychromecast').setLevel(logging.WARN)

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger(__name__)

devices = []
deviceScanTimer = None

CONTINUOUS_DEVICE_SCAN_INTERVAL = 900.0  # in seconds

def scanForDevices():
    global devices, deviceScanTimer

    if deviceScanTimer is not None and deviceScanTimer.is_alive():
        cancelDeviceScanner()

    logger.debug('Scanning for devices...')

    startTime = datetime.utcnow()
    devices = pychromecast.get_chromecasts(tries=1)

    logger.info(
        'Device scan completed after {}. Scheduling next scan for {}.'.format(
            formatTimeDelta(datetime.utcnow() - startTime),
            (
                datetime.utcnow() +
                timedelta(seconds=CONTINUOUS_DEVICE_SCAN_INTERVAL)
            ).strftime('%Y-%m-%d %H:%M:%S')
        )
    )

    # continue to scan every N seconds
    deviceScanTimer = threading.Timer(
        CONTINUOUS_DEVICE_SCAN_INTERVAL,
        scanForDevices
    )
    deviceScanTimer.start()

def cancelDeviceScanner():
    global deviceScanTimer

    deviceScanTimer.cancel()
    deviceScanTimer = None

def setup():
    logger.info('Setting up...')

    scanForDevices()

    logger.info('Setup completed\n---')

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
    logger.debug('Device found'.format(deviceName))
    cast.wait()

    return cast

def stop(device):
    if not device:
        return

    logger.info('Stopping playback on "{}"'.format(device.name))

    device.media_controller.stop()

def quit(device):
    if not device:
        return

    logger.info('Closing Chromecast application "{}"'.format(device.name))

    device.quit_app()

def setVolume(device, volume):
    if not device:
        return

    logger.info(
        'Setting volume to {}% on "{}"'.format(
            volume * 100,
            device.name
        )
    )
    device.set_volume(volume)

def isPlaying(device):
    return device.media_controller.is_playing

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

setup()

