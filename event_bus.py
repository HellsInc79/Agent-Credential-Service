# app/event_bus.py

EVENTS = []


def publish_event(
    event_type,
    source,
    payload
):

    event = {

        "event_type":
            event_type,

        "source":
            source,

        "payload":
            payload
    }

    EVENTS.append(
        event
    )

    return event


def get_events():

    return EVENTS


def get_events_by_type(
    event_type
):

    return [

        event

        for event in EVENTS

        if event[
            "event_type"
        ] == event_type
    ]