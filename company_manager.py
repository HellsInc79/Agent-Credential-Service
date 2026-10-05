# app/company_manager.py

COMPANY = {

    "name":
        "AI Enterprise",

    "mission":
        "",

    "ceo":
        "",

    "departments": [],

    "goals": []
}

def configure_company(
    name,
    mission,
    ceo
):

    COMPANY["name"] = name

    COMPANY["mission"] = mission

    COMPANY["ceo"] = ceo

    return COMPANY

def add_goal(goal):

    COMPANY["goals"].append(
        {
            "goal":
                goal,

            "status":
                "active"
        }
    )

    return COMPANY["goals"]

def complete_goal(index):

    if (
        index <
        len(
            COMPANY["goals"]
        )
    ):

        COMPANY[
            "goals"
        ][index][
            "status"
        ] = "completed"

    return COMPANY[
        "goals"
    ]

def register_department(
    department_name
):

    if department_name not in COMPANY[
        "departments"
    ]:

        COMPANY[
            "departments"
        ].append(
            department_name
        )

def company_status():

    return COMPANY

    