def formatTimeDelta(delta):
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return '{}h{}m{}s'.format(
        int(hours),
        int(minutes),
        int(seconds)
    )

