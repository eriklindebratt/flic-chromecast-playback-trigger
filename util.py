import socket

def formatTimeDelta(delta):
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return '{}h{}m{}s'.format(
        int(hours),
        int(minutes),
        int(seconds)
    )

def getLocalIpAddress():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))

    ipAddress = s.getsockname()[0]

    s.shutdown(socket.SHUT_RDWR)
    s.close()

    return ipAddress

