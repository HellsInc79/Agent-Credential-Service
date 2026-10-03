# app/learning_manager.py

from datetime import datetime

LESSONS = []

def get_category_lessons(
    category
):

    return [

        lesson

        for lesson in LESSONS

        if lesson[
            "category"
        ] == category
    ]

def search_lessons(
    keyword
):

    results = []

    keyword = keyword.lower()

    for item in LESSONS:

        if keyword in item[
            "lesson"
        ].lower():

            results.append(
                item
            )

    return results

def get_lessons():

    return LESSONS

def store_lesson(
    category,
    lesson,
    outcome
):

    record = {

        "category":
            category,

        "lesson":
            lesson,

        "outcome":
            outcome,

        "created_at":
            datetime.utcnow().isoformat()
    }

    LESSONS.append(
        record
    )

    return record