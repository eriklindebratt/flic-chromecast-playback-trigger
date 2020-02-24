import time
import pychromecast
from mimetypes import MimeTypes
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger(__name__)

def getDevice(deviceName):
    logger.info('scanning for chromecast device "{}"...'.format(deviceName))
    chromecasts = pychromecast.get_chromecasts()
    cast = next(cc for cc in chromecasts if cc.device.friendly_name == deviceName)

    # Start worker thread and wait for cast device to be ready
    logger.info('chromecast device found...')
    cast.wait()

    return cast

def stop(device):
    if not device:
        return

    logger.info('stopping playback')

    device.media_controller.stop()

def quit(device):
    if not device:
        return

    logger.info('quitting')

    device.quit_app()

def setVolume(device, volume):
    if not device:
        return

    logger.info('setting device volume to {}%...'.format(volume * 100))
    device.set_volume(volume)

def isPlaying(device):
    logger.debug('isPlaying - status: {}'.format(device.status))
    logger.debug('isPlaying - mc.status: {}'.format(device.media_controller.status))
    logger.debug('isPlaying: {}'.format(device.media_controller.is_playing))

    return device.media_controller.is_playing

def play(data, device):
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
        raise Exception('Failed to look up mime type for media url "{}"'.format(data['media']['url']))
    if not mediaArgs.get('content_type'):
        raise Exception('Failed to look up mime type for media url "{}"'.format(data['media']['url']))
    ########################

    if not device:
        device = getDevice(data['deviceName'])
        if not device:
            raise Exception('Failed to get device "{}"'.format(data['deviceName']))

    if data.get('volume') is not None:
        setVolume(device, data['volume'])

    mc = device.media_controller
    logger.info('starting playback...\n  - url: {}\n  - args: {}'.format(data['media']['url'], mediaArgs))
    mc.play_media(data['media']['url'], **mediaArgs)
    mc.block_until_active()

    return device
