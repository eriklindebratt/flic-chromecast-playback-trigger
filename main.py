#!/usr/bin/env python3

import fliclib
import caster
import logging
import sys

BLACK_BUTTON_ADDRESS = '80:e4:da:70:32:3b'
TURQUOISE_BUTTON_ADDRESS = '80:e4:da:73:70:72'

flicClient = fliclib.FlicClient('localhost')
castDevice = None

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger(__name__)

def onButtonClick(channel, clickType, wasQueued, timeDiff):
    global castDevice

    if clickType != fliclib.ClickType.ButtonClick:
        return

    if channel.bd_addr == BLACK_BUTTON_ADDRESS:
        logger.info('black button clicked')

        if castDevice is not None and caster.isPlaying(castDevice):
            logger.info('currently playing - stopping')
            caster.stop(castDevice)
            caster.quit(castDevice)
        else:
            castDevice = caster.play({
                'deviceName': 'Home Group',
                'volume': 0.2,
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
    elif channel.bd_addr == TURQUOISE_BUTTON_ADDRESS:
        logger.info('turqouise button clicked')

        if castDevice is not None and caster.isPlaying(castDevice):
            logger.info('currently playing - stopping')
            caster.stop(castDevice)
            caster.quit(castDevice)
        else:
            castDevice = caster.play({
                'deviceName': 'Home Group',
                'volume': 0.2,
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

def onConnectionStatusChanged(channel, connection_status, disconnect_reason):
    logger.debug(channel.bd_addr + " " + str(connection_status) + (" " + str(disconnect_reason) if connection_status == fliclib.ConnectionStatus.Disconnected else ""))

def gotButton(bdAddr):
    cc = fliclib.ButtonConnectionChannel(bdAddr)

    cc.on_button_click_or_hold = onButtonClick
    cc.on_connection_status_changed = onConnectionStatusChanged

    flicClient.add_connection_channel(cc)

def gotInfo(items):
    logger.debug('gotInfo - items: {}'.format(items))
    for bdAddr in items['bd_addr_of_verified_buttons']:
        gotButton(bdAddr)

logger.info('Waiting for button clicks...')

flicClient.get_info(gotInfo)
flicClient.on_new_verified_button = gotButton

flicClient.handle_events()

