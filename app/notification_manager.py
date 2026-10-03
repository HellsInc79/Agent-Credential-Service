# app/notification_manager.py

NOTIFICATIONS = []


def create_notification(
    source,
    message
):

    notification = {

        "source":
            source,

        "message":
            message
    }

    NOTIFICATIONS.append(
        notification
    )

    return notification


def get_notifications():

    return NOTIFICATIONS


def clear_notifications():

    NOTIFICATIONS.clear()

    return True