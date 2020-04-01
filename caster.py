import pychromecast
from pychromecast.controllers.media import (  # noqa: F401
    MEDIA_PLAYER_STATE_PLAYING,
    MEDIA_PLAYER_STATE_PAUSED,
    MEDIA_PLAYER_STATE_BUFFERING,
    MEDIA_PLAYER_STATE_IDLE,
    MEDIA_PLAYER_STATE_UNKNOWN,
    STREAM_TYPE_UNKNOWN,
    STREAM_TYPE_BUFFERED,
    STREAM_TYPE_LIVE
)

from pychromecast.controllers.spotify import SpotifyController
from mimetypes import MimeTypes
import logging
import threading
from datetime import datetime, timedelta
from util import formatTimeDelta
import os
import spotipy
import spotify_token
from time import time

logging.getLogger('pychromecast').setLevel(logging.WARN)

logger = logging.getLogger(__name__)
onError = lambda error: None  # noqa: E731
deviceHosts = []
deviceHostScanTimer = None
_spotifyClient = None

DEVICE_HOST_SCAN_TIMEOUT = 15.0  # in seconds
CONTINUOUS_DEVICE_HOST_SCAN_INTERVAL = 900.0  # in seconds
WAIT_FOR_PLAYBACK_TIMEOUT = 10.0  # in seconds
SPOTIFY_OAUTH_TOKENS_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), '.spotify-tokens')
SPOTIFY_OAUTH_SCOPE = ','.join((
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing',
))
SPOTIFY_OAUTH_REDIRECT_SERVER_PORT = 5000


class DeviceNotFoundError(Exception):
    pass


class SpotifyOAuthCredentialsError(Exception):
    pass


class SpotifyPlaybackError(Exception):
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

        if self.lastPlayerState and \
                self.lastPlayerState == status.player_state:
            return

        self.lastPlayerState = status.player_state

        self.callback(self.device, status)


def setup(logLevel=None, errorHandler=None):
    global onError

    if logLevel:
        logger.setLevel(logLevel)

    logger.info('Setting up...')

    if errorHandler:
        onError = errorHandler

    _setupSpotifyClient()

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
            Exception('Device host scan completed with no device(s) found.')
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
        logger.debug('Getting device "{}"'.format(deviceName))

    try:
        host = next(i for i in deviceHosts if i[-1] == deviceName)
        device = pychromecast.Chromecast(host[0], host[1])
    except StopIteration:
        if not calledFromSelf:
            logger.warning(
                'Device "{}" not found - trigger new device scan'.format(
                    deviceName
                )
            )

            scanForDeviceHosts()

            return getDevice(deviceName, calledFromSelf=True)
        else:
            logger.warning(
                'Device "{}" not found (tried scanning anew)'.format(
                    deviceName
                )
            )

            raise DeviceNotFoundError(
                'Device "{}" not found'.format(deviceName)
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
        _pauseSpotify(device)
    except (Exception, SpotifyPlaybackError):
        logger.exception('Failed to pause Spotify playback')

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
        return isSpotifyPlaying(device) or \
            device.media_controller.status.player_is_playing
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to get `isPlaying`: {}'.format(e))
        onError(e)
        return False


def isSpotifyPlaying(device):
    if not device:
        return False

    if not _spotifyClient:
        return False

    try:
        playbackStatus = _spotifyClient.current_playback()
    except spotipy.client.SpotifyException:
        logger.exception(
            'Error: Failed to get current Spotify playback status'
            ' - got Spotify error'
        )
        return False

    if not playbackStatus:
        return False

    if playbackStatus.get('device', {}).get('name') != device.name:
        return False

    return playbackStatus.get('is_playing', False)


def isPaused(device):
    if not device:
        return False

    try:
        return device.media_controller.status.player_is_paused
    except pychromecast.error.ControllerNotRegistered as e:
        logger.error('Failed to get `isPaused`: {}'.format(e))
        onError(e)
        return False


def isSpotifyUri(uri):
    return uri.startswith('spotify:')


def _getSpotifyAvailableDevices(calledFromSelf=False):
    if not _spotifyClient:
        raise Exception('Spotify client is not set up')

    try:
        devices = _spotifyClient.devices().get('devices', [])
    except spotipy.client.SpotifyException:
        if calledFromSelf:
            logger.exception(
                'Error: Failed to get Spotify devices (second attempt)'
                ' - got Spotify error'
            )
        else:
            logger.exception(
                'Error: Failed to get Spotify devices'
                ' - got Spotify error'
            )
        devices = []

    if not devices:
        if not calledFromSelf:
            logger.warning(
                'No available Spotify devices found '
                '- trying once more'
            )

            return _getSpotifyAvailableDevices(calledFromSelf=True)
        else:
            logger.warning(
                'No available Spotify devices found after second attempt'
            )

    return devices


def _getSpotifyDeviceIdFromDevice(deviceId=None,
                                  deviceName=None,
                                  filters=None):
    '''
    :param deviceId: str Optional if `deviceName` is passed
    :param deviceName: str Optional if `deviceId` is passed
    '''

    if not deviceId and not deviceName:
        raise Exception(
            'Either `deviceId` or `deviceName` are required params')

    availableSpotifyDevices = _getSpotifyAvailableDevices()

    logger.debug('Available Spotify devices: {}'.format(
        availableSpotifyDevices))

    if filters:
        filteredSpotifyDevices = []
        for device in availableSpotifyDevices:
            passedFilters = True
            for key in filters.keys():
                if not (key in device.keys()):
                    passedFilters = False
                    break

                if device[key] != filters[key]:
                    passedFilters = False
                    break
            if passedFilters:
                filteredSpotifyDevices.append(device)
    else:
        filteredSpotifyDevices = availableSpotifyDevices

    for spotifyDevice in filteredSpotifyDevices:
        if deviceId and spotifyDevice['id'] == deviceId:
            return spotifyDevice['id'], availableSpotifyDevices
        elif deviceName and spotifyDevice['name'] == deviceName:
            return spotifyDevice['id'], availableSpotifyDevices

    return None, availableSpotifyDevices


def _setupSpotifyClient():
    global _spotifyClient

    logger.info('Setting up Spotify client...')

    try:
        oAuthClientId = os.environ['SPOTIFY_OAUTH_CLIENT_ID']
        oAuthClientSecret = os.environ['SPOTIFY_OAUTH_CLIENT_SECRET']
    except KeyError:
        raise SpotifyOAuthCredentialsError(
            'Missing Spotify OAuth app credentials in env vars '
            '`SPOTIFY_OAUTH_CLIENT_ID` and/or `SPOTIFY_OAUTH_CLIENT_ID`')

    _spotifyClient = spotipy.Spotify(
        auth_manager=spotipy.oauth2.SpotifyOAuth(
            client_id=oAuthClientId,
            client_secret=oAuthClientSecret,
            scope=SPOTIFY_OAUTH_SCOPE,
            redirect_uri='http://localhost:{}/redirect'.format(
                SPOTIFY_OAUTH_REDIRECT_SERVER_PORT),
            cache_path=SPOTIFY_OAUTH_TOKENS_CACHE_PATH  # ,
            # show_dialog=True
        )
    )

    try:
        _spotifyClient.auth_manager.get_access_token()
        logger.info('Spotify client successfully set up')
    except Exception:
        raise SpotifyOAuthCredentialsError(
            'Failed to get initial Spotify access token')

    if logger.level == logging.DEBUG:
        spotipy.trace = True
        spotipy.trace_out = True

    return _spotifyClient


def _getSpotifyChromecastController():
    try:
        spotifyUserUsername = os.environ['SPOTIFY_USER_USERNAME']
        spotifyUserPassword = os.environ['SPOTIFY_USER_PASSWORD']
    except KeyError:
        raise SpotifyPlaybackError(
            'Missing Spotify user credentials in env vars '
            '`SPOTIFY_USER_USERNAME` and/or `SPOTIFY_USER_PASSWORD`')
    (spotifyControllerAccessToken,
        spotifyControllerExpiresAt) = spotify_token.start_session(
            spotifyUserUsername, spotifyUserPassword)
    spotifyControllerExpiresIn = spotifyControllerExpiresAt - int(time())

    return SpotifyController(
        spotifyControllerAccessToken,
        spotifyControllerExpiresIn
    )


def _playSpotifyUri(device=None, uri=None):
    logger.debug('Playing Spotify URI...')

    # launch the Spotify app on the device we want to cast to
    controller = _getSpotifyChromecastController()
    device.register_handler(controller)
    controller.launch_app()

    if not controller.is_launched and not controller.credential_error:
        raise SpotifyPlaybackError(
            'Failed to launch Spotify controller due to timeout'
        )

    if not controller.is_launched and controller.credential_error:
        raise SpotifyPlaybackError(
            'Failed to launch Spotify controller due to credential error'
        )

    spotifyDeviceId, availableSpotifyDevices = _getSpotifyDeviceIdFromDevice(
        deviceId=controller.device)

    if not spotifyDeviceId:
        logger.error(
            'Device with ID "{}" is unknown to Spotify. '
            'Available devices: {}'.format(
                controller.device,
                availableSpotifyDevices
            )
        )
        raise SpotifyPlaybackError(
            'Device with ID "{}" is unknown to Spotify'.format(
                controller.device
            )
        )

    # offset = {'position': 0}
    # if uri.startswith('spotify:playlist:') and randomizedPlaylistStart:
        # playlistId = uri.split('spotify:playlist:', False)[0]
        # playlistItemCount = len(_spotifyClient.user_playlist_tracks(
        # playlist_id=playlistId)['items'])

        # offset = {'position': randint(0, playlistItemCount-1)}

        # # need to enable repeat to ensure items after
        # # the offset position will get played
        # _spotifyClient.repeat('context', device_id=spotifyDeviceId)

    # start playback
    try:
        _spotifyClient.start_playback(
            device_id=spotifyDeviceId,
            context_uri=uri  # ,
            # offset=offset
        )
    except spotipy.client.SpotifyException:
        logger.exception(
            'Error: Failed to start Spotify playback'
            ' - got Spotify error'
        )

        raise SpotifyPlaybackError('Could not start playback')


def _pauseSpotify(device):
    if not _spotifyClient:
        raise Exception('Spotify client is not set up')

    spotifyDeviceId, availableSpotifyDevices = _getSpotifyDeviceIdFromDevice(
        deviceName=device.name, filters={'is_active': True})

    if not spotifyDeviceId:
        logger.error(
            'Device with name "{}" is unknown to Spotify. '
            'Available devices: {}'.format(
                device.name,
                availableSpotifyDevices
            )
        )
        raise SpotifyPlaybackError(
            'Device with name "{}" is unknown to Spotify'.format(
                device.name
            )
        )

    try:
        _spotifyClient.pause_playback(device_id=spotifyDeviceId)
    except spotipy.client.SpotifyException:
        logger.exception(
            'Error: Failed to pause Spotify playback'
            ' - got Spotify error'
        )


def play(data, device=None):
    '''
    :param data: dict
    :param device
    '''

    if data is None:
        data = {}

    if not data['media'].get('args'):
        data['media']['args'] = {}

    ########################
    # set up media data structure
    mediaArgs = dict(data['media']['args'])

    if not isSpotifyUri(data['media']['uri']):
        try:
            mimeType = MimeTypes().guess_type(data['media']['uri'])[0]
            mediaArgs['content_type'] = mimeType
        except IndexError:
            raise Exception(
                'Failed to look up mime type for media uri "{}"'.format(
                    data['media']['uri']
                )
            )
        if not mediaArgs.get('content_type'):
            raise Exception(
                'Failed to look up mime type for media uri "{}"'.format(
                    data['media']['uri']
                )
            )
    ########################

    if data.get('volume') is not None:
        setVolume(device, data['volume'])

    logger.info('Starting playback on "{}"'.format(device.name))
    logger.debug('Playing:\n  - uri: {}\n  - args: {}'.format(
        data['media']['uri'], mediaArgs))

    mc = device.media_controller

    if isSpotifyUri(data['media']['uri']):
        _playSpotifyUri(
            device=device,
            uri=data['media']['uri']
        )
    else:
        mc.play_media(data['media']['uri'], **mediaArgs)

    mc.block_until_active()

    return device


def addDeviceStatusListener(device, callback):
    device.media_controller.register_status_listener(
        DeviceStatusListener(device, callback)
    )


def addDevicePlayerStatusListener(device, callback):
    device.media_controller.register_status_listener(
        DeviceMediaStatusListener(device, callback)
    )
