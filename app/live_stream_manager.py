# app/live_stream_manager.py

STREAM_EVENTS = []


def add_stream_event(
    source,
    message
):

    STREAM_EVENTS.append({

        "source":
            source,

        "message":
            message
    })


def get_stream_events():

    return STREAM_EVENTS[-100:]