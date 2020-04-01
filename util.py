import socket
import psutil
import os


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


def getProcessesByName(processNames, args=None):
    processes = []

    for proc in psutil.process_iter():
        try:
            if proc.pid == os.getpid():
                continue

            procArgs = ' '.join(proc.cmdline()[1:])

            if proc.name() in processNames:
                if args:
                    if args in procArgs:
                        processes.append(proc)
                else:
                    processes.append(proc)
        except (
            IndexError,
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            pass

    return processes
