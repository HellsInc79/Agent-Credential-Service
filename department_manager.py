# app/department_manager.py

DEPARTMENTS = {}


def create_department(
    name,
    manager
):

    DEPARTMENTS[name] = {

        "name": name,

        "manager": manager,

        "members": []
    }

    return DEPARTMENTS[name]


def add_member(
    department,
    agent
):

    if department not in DEPARTMENTS:

        return None

    DEPARTMENTS[
        department
    ]["members"].append(
        agent
    )

    return DEPARTMENTS[
        department
    ]


def get_department(
    department
):

    return DEPARTMENTS.get(
        department
    )


def list_departments():

    return DEPARTMENTS