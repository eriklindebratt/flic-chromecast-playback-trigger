#!/usr/bin/env python3

import fliclib
import caster
import logging
import sys
import os
import signal

BLACK_BUTTON_ADDRESS = '80:e4:da:70:32:3b'
TURQUOISE_BUTTON_ADDRESS = '80:e4:da:73:70:72'

logger = None
flicClient = None
flicButtonConnectionChannels = None
castDevice = None

def playOrStop():
    global castDevice

    if deviceToCastTo is None:
        logger.error(
            'Can\'t play or stop - no target device configured in env vars'
        )
        return

    if castDevice is not None and caster.isPlaying(castDevice):
        logger.info('currently playing - stopping')
        caster.stop(castDevice)
        caster.quit(castDevice)
    else:
        if devicesToSetVolumeFor is not None:
            [caster.setVolume(
                i['device'],
                i['volume']
            ) for i in devicesToSetVolumeFor]

        castDevice = caster.play({
            'deviceName': deviceToCastTo,
            'media': {
                'url': 'https://sverigesradio.se/topsy/direkt/srapi/132.mp3',
                'args': {
                    'stream_type': 'LIVE',
                    'autoplay': True,
                    'title': 'P1',
                    'thumb': 'https://static-cdn.sr.se/sida/images/132/2186745_512_512.jpg?preset=api-default-square'
                }
            }
        }, castDevice)


def onFlicButtonClickOrHold(channel, clickType, wasQueued, timeDiff):
    global castDevice

    if clickType != fliclib.ClickType.ButtonClick:
        return

    if channel.bd_addr == BLACK_BUTTON_ADDRESS:
        logger.info('black button clicked')
        playOrStop()
    elif channel.bd_addr == TURQUOISE_BUTTON_ADDRESS:
        logger.info('turqouise button clicked')
        playOrStop()

def onFlicButtonConnectionStatusChanged(channel, connectionStatus, disconnectReason):
    logger.info('Button "{}" changed connection status to: {}{}'.format(
        channel.bd_addr,
        connectionStatus,
        ' ({})'.format(disconnectReason) if connectionStatus == fliclib.ConnectionStatus.Disconnected else ''
    ))

def onFlicButtonCreateConnectionChannelResponse(channel, error, connectionStatus):
    if error and error is not fliclib.CreateConnectionChannelError.NoError:
        logger.error(
            'Button "{}" got error in create connection channel \
                response: {}. Connection status: {}'.format(
            channel.bd_addr,
            error,
            connectionStatus
        ))
    else:
        logger.debug('Button "{}" got create connection channel response'
            .format(channel.bd_addr))

        flicButtonConnectionChannels.append(channel)


def onFlicButtonConnectionChannelRemoved(channel, removedReason=None):
    global flicButtonConnectionChannels

    flicButtonConnectionChannels = [i for i in flicButtonConnectionChannels \
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
    cc.on_create_connection_channel_response = onFlicButtonCreateConnectionChannelResponse
    cc.on_removed = onFlicButtonConnectionChannelRemoved

    flicClient.add_connection_channel(cc)

def onFlicGetInfo(items):
    logger.debug('onFlicGetInfo - items: {}'.format(items))

    for bdAddr in items['bd_addr_of_verified_buttons']:
        onFlicNewVerifiedButton(bdAddr)

def onFlicBluetoothControllerStateChange(state):
    logger.info('onFlicBluetoothControllerStateChange - state: {}'.format(state))

    if state == fliclib.ConnectionStatus.Disconnected:
        logger.info(
            'Flic Bluetooth controller got disconnected state - exiting...'
        )

        exit(1)

def onCasterError(error=None):
    logger.error('Caster got error: {}'.format(error))
    exit(1)

def exit(exitCode=0):
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

    caster.cancelDeviceScanner()

    if castDevice is not None:
        caster.stop(castDevice)
        caster.quit(castDevice)

    logger.info('Exiting with code {}'.format(exitCode))

    sys.exit(exitCode)

def onSIGINT(*args):
    logger.info('Received SIGINT')
    exit(0)

def onSIGTERM(*args):
    logger.info('Received SIGTERM')
    exit(0)

if __name__ == '__main__':
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    devicesToSetVolumeFor = os.environ.get('DEVICES_TO_SET_VOLUME_FOR')
    deviceToCastTo = os.environ.get('DEVICE_TO_CAST_TO')

    if devicesToSetVolumeFor is not None:
        try:
            devicesToSetVolumeFor = [
                {
                    'device': caster.getDevice(a[0]),
                    'volume': float(a[1])
                } for a in [
                    [
                        n.strip() for n in i.strip().split('=')
                    ] for i in devicesToSetVolumeFor.split(',')
                ]
            ]
        except Exception as e:
            logger.error(
                'Failed to configure devices to set volume for from environment: {}'
                    .format(e)
            )
            raise e

    caster.setup(errorHandler=onCasterError)

    try:
        logger.info('setting up Flic client')

        flicButtonConnectionChannels = []

        flicClient = fliclib.FlicClient('localhost')
        flicClient.get_info(onFlicGetInfo)
        flicClient.on_new_verified_button = onFlicNewVerifiedButton
        flicClient.on_bluetooth_controller_state_change = onFlicBluetoothControllerStateChange
    except Exception as e:
        logger.error('Failed to start Flic client: {}'.format(e))
        exit(1)


    signal.signal(signal.SIGINT, onSIGINT)
    signal.signal(signal.SIGTERM, onSIGTERM)

    logger.info('Waiting for button clicks...')

    # note that this method is blocking!
    flicClient.handle_events()
