# app/knowledge_manager.py

KNOWLEDGE = []

def add_knowledge(
    title,
    category,
    content
):

    record = {

        "title":
            title,

        "category":
            category,

        "content":
            content
    }

    KNOWLEDGE.append(
        record
    )

    return record

def search_knowledge(
    query
):

    query = query.lower()

    results = []

    for item in KNOWLEDGE:

        if (
            query in item["title"].lower()
            or
            query in item["content"].lower()
        ):

            results.append(item)

    return results

def category_search(
    category
):

    return [

        item

        for item in KNOWLEDGE

        if item["category"]
        ==
        category
    ]

def all_knowledge():

    return KNOWLEDGE