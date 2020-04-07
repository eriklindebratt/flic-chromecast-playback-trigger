#!/usr/bin/env python3

import fliclib
import caster
import logging
import sys
import os
import signal
import json

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(asctime)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S %z'
)
logging.getLogger('urllib3').setLevel(logging.INFO)

BLACK_BUTTON_ADDRESS = '80:e4:da:70:32:3b'
TURQUOISE_BUTTON_ADDRESS = '80:e4:da:73:70:72'

logger = None
flicClient = None
flicButtonConnectionChannels = None
castDevice = None
deviceNamesToSetVolumeFor = None
deviceToCastTo = None
hasDevicePlayerStatusListener = False


def getFlicButtonName(buttonId):
    if buttonId == BLACK_BUTTON_ADDRESS:
        return 'Black'
    if buttonId == TURQUOISE_BUTTON_ADDRESS:
        return 'Turqouise'
    else:
        return 'UNKNOWN'


def stopAndQuitCasting(device, forceQuit=False):
    global castDevice, hasDevicePlayerStatusListener

    castDevice = None
    hasDevicePlayerStatusListener = False

    if not forceQuit:
        if caster.isPlaying(device) or caster.isPaused(device):
            caster.stop(device)

        caster.quit(device, disconnectFromDevice=True)


def playOrStop(data):
    '''
    :param data: dict
    '''

    global castDevice, hasDevicePlayerStatusListener

    if castDevice is not None and caster.isPlaying(castDevice):
        logger.info('Currently playing - stopping')
        stopAndQuitCasting(castDevice)
    else:
        try:
            castDevice = caster.play(data, caster.getDevice(deviceToCastTo))
        except (caster.DeviceNotFoundError,
                caster.SpotifyPlaybackError) as e:
            logger.error('Failed to start playback: {}'.format(e))
            exit(1)
        else:
            if not castDevice:
                return

        def onDevicePlayerStatus(device, status):
            global castDevice

            if not castDevice:
                logger.debug(
                    'Got device media player state "{}" while `castDevice` '
                    'was `None`'.format(status.player_state)
                )
                return

            logger.info(
                'Got device media player state "{}"'.format(
                    status.player_state
                )
            )

            if not caster.isPlaying(device) and status.player_state in (
                    caster.MEDIA_PLAYER_STATE_IDLE,
                    caster.MEDIA_PLAYER_STATE_UNKNOWN):
                logger.debug('Player state is valid for exit')
                stopAndQuitCasting(device)
                return

            if status.stream_type == caster.STREAM_TYPE_LIVE and \
                    status.player_state == caster.MEDIA_PLAYER_STATE_PAUSED:
                logger.info(
                    'Player state is valid for exit (paused live stream)')
                stopAndQuitCasting(device)
                return

        if not hasDevicePlayerStatusListener:
            caster.addDevicePlayerStatusListener(
                castDevice,
                onDevicePlayerStatus
            )

            hasDevicePlayerStatusListener = True

        ########################
        # setting device volumes
        if deviceNamesToSetVolumeFor is not None:
            devicesToSetVolumeFor = None

            try:
                devicesToSetVolumeFor = [
                    {
                        'device': caster.getDevice(a[0]),
                        'volume': float(a[1])
                    } for a in [
                        [
                            n.strip() for n in i.strip().split('=')
                        ] for i in deviceNamesToSetVolumeFor.split(',')
                    ]
                ]
            except caster.DeviceNotFoundError:
                pass
            else:
                if devicesToSetVolumeFor is not None:
                    [caster.setVolume(
                        i['device'],
                        i['volume']
                    ) for i in devicesToSetVolumeFor]

                    [i['device'].disconnect(
                        blocking=False
                    ) for i in devicesToSetVolumeFor]
        ########################


def getFlicButtonCasterMediaData(buttonAddress):
    buttonCasterMediaData = None

    try:
        buttonCasterMediaData = json.loads(
            os.environ['CASTER_MEDIA_DATA']
        )[buttonAddress]
    except json.decoder.JSONDecodeError:
        logger.exception(
            'Error: Failed to parse caster media config'
            ' for Flic button {}'.format(getFlicButtonName(buttonAddress))
        )
    except KeyError:
        logger.warning(
            'No caster media config found for Flic button {}'.format(
                getFlicButtonName(buttonAddress)
            )
        )

    return buttonCasterMediaData


def onFlicButtonClickOrHold(channel, clickType, wasQueued, timeDiff):
    if clickType != fliclib.ClickType.ButtonClick:
        return

    if wasQueued and timeDiff > 2:
        logger.debug(
            'Discarding previously queued click for {} button '
            '(was {} seconds ago)'.format(
                getFlicButtonName(channel.bd_addr),
                timeDiff
            )
        )
        return

    logger.info(
        '{} button clicked'.format(
            getFlicButtonName(channel.bd_addr)
        )
    )

    buttonCasterMediaData = getFlicButtonCasterMediaData(channel.bd_addr)

    if buttonCasterMediaData:
        playOrStop({'media': buttonCasterMediaData})
    else:
        logger.info(
            'Not playing nor stopping - got no caster'
            ' media data for Flic button {}'.format(
                getFlicButtonName(channel.bd_addr)
            )
        )


def onFlicButtonConnectionStatusChanged(channel,
                                        connectionStatus,
                                        disconnectReason):
    logger.debug('Button "{}" changed connection status to: {}{}'.format(
        channel.bd_addr,
        connectionStatus,
        ' ({})'.format(
            disconnectReason) if connectionStatus ==
        fliclib.ConnectionStatus.Disconnected else ''
    ))


def onFlicButtonCreateConnectionChannelResponse(channel,
                                                error,
                                                connectionStatus):
    if error and error is not fliclib.CreateConnectionChannelError.NoError:
        logger.error(
            'Button "{}" got error in create connection channel '
            'response: {}. Connection status: {}'.format(
                channel.bd_addr,
                error,
                connectionStatus
            )
        )
    else:
        logger.debug('Button "{}" got create connection channel response'
                     .format(channel.bd_addr))

        flicButtonConnectionChannels.append(channel)


def onFlicButtonConnectionChannelRemoved(channel, removedReason=None):
    global flicButtonConnectionChannels

    flicButtonConnectionChannels = [i for i in flicButtonConnectionChannels
                                    if i != channel
                                    ]

    logger.debug(
        'Button connection channel for button "{}" was removed'.format(
            channel.bd_addr
        )
    )


def onFlicNewVerifiedButton(bdAddr):
    cc = fliclib.ButtonConnectionChannel(bdAddr)

    cc.on_button_click_or_hold = onFlicButtonClickOrHold
    cc.on_connection_status_changed = onFlicButtonConnectionStatusChanged
    cc.on_create_connection_channel_response = \
        onFlicButtonCreateConnectionChannelResponse
    cc.on_removed = onFlicButtonConnectionChannelRemoved

    flicClient.add_connection_channel(cc)


def onFlicGetInfo(items):
    logger.debug('onFlicGetInfo - items: {}'.format(items))

    for bdAddr in items['bd_addr_of_verified_buttons']:
        onFlicNewVerifiedButton(bdAddr)


def onFlicBluetoothControllerStateChange(state):
    logger.info(
        'onFlicBluetoothControllerStateChange - state: {}'.format(state)
    )

    if state == fliclib.ConnectionStatus.Disconnected:
        logger.info(
            'Flic Bluetooth controller got disconnected state - exiting...'
        )

        exit(1)


def onCasterError(error=None):
    logger.error('Caster got error: {}'.format(error))
    exit(1, forceQuitCaster=True)


def exit(exitCode=0, forceQuitCaster=False):
    global castDevice

    logger.info('Stopping subprocesses...')

    if flicClient is not None:
        logger.debug(
            'Waiting for all Flic button connection channels to get removed...'
        )
        for i in flicButtonConnectionChannels:
            flicClient.remove_connection_channel(i)

            # should not have to call this manually
            # - this should get called when `channel.on_removed` gets
            # triggered, but it never seems to get triggered
            onFlicButtonConnectionChannelRemoved(i)

        while len(flicButtonConnectionChannels) != 0:
            pass

        flicClient.close()

    caster.cancelDeviceHostScanner()

    if not forceQuitCaster:
        stopAndQuitCasting(castDevice, forceQuit=forceQuitCaster)
    else:
        logger.info(
            'Exit was called with caster force quit requested - '
            'not calling caster’s stop+quit'
        )

    logger.info('Exiting with code {}'.format(exitCode))

    sys.exit(exitCode)


def onSIGINT(*args):
    logger.info('Received SIGINT')
    exit(0)


def onSIGTERM(*args):
    logger.info('Received SIGTERM')
    exit(0)


if __name__ == '__main__':
    logger = logging.getLogger(__name__)

    deviceNamesToSetVolumeFor = os.environ.get('DEVICES_TO_SET_VOLUME_FOR')
    deviceToCastTo = os.environ.get('DEVICE_TO_CAST_TO')

    logLevel = os.environ.get('LOG_LEVEL')
    if logLevel == 'CRITICAL':
        logger.setLevel(logging.CRITICAL)
    elif logLevel == 'ERROR':
        logger.setLevel(logging.ERROR)
    elif logLevel == 'WARNING':
        logger.setLevel(logging.WARNING)
    elif logLevel == 'INFO':
        logger.setLevel(logging.INFO)
    elif logLevel == 'DEBUG':
        logger.setLevel(logging.DEBUG)

    if not deviceToCastTo:
        logger.error('No target device specified in env vars')
        sys.exit(1)

    signal.signal(signal.SIGINT, onSIGINT)
    signal.signal(signal.SIGTERM, onSIGTERM)

    # caster.setup(
    #     logLevel=logger.level,
    #     errorHandler=onCasterError,
    # )
    # logger.info('ready')
    # input('press key...')
    # onFlicButtonClickOrHold(
    #     fliclib.ButtonConnectionChannel(BLACK_BUTTON_ADDRESS),
    #     fliclib.ClickType.ButtonClick,
    #     False,
    #     0
    # )
    # while True:
    #     pass

    try:
        logger.info('Setting up Flic client...')

        flicButtonConnectionChannels = []

        flicClient = fliclib.FlicClient('localhost')
        flicClient.get_info(onFlicGetInfo)
        flicClient.on_new_verified_button = onFlicNewVerifiedButton
        flicClient.on_bluetooth_controller_state_change = \
            onFlicBluetoothControllerStateChange
    except Exception as e:
        logger.error('Failed to start Flic client: {}'.format(e))
        exit(1, forceQuitCaster=True)
    else:
        caster.setup(
            logLevel=logger.level,
            errorHandler=onCasterError
        )

    logger.info('Ready - waiting for button clicks...\n---')

    # note that this method is blocking!
    flicClient.handle_events()
