# app/audit_manager.py

from datetime import datetime

AUDIT_LOG = []

def log_event(
    event_type,
    source,
    details
):

    entry = {

        "event_type":
            event_type,

        "source":
            source,

        "details":
            details,

        "timestamp":
            datetime.utcnow().isoformat()
    }

    AUDIT_LOG.append(
        entry
    )

    return entry


def get_audit_log():

    return AUDIT_LOG


def get_events_by_type(
    event_type
):

    return [

        event

        for event in AUDIT_LOG

        if event[
            "event_type"
        ] == event_type
    ]