# app/plugin_manager.py

import json
import math


def calculator(expression):

    try:

        result = eval(
            expression,
            {
                "__builtins__": {},
                "math": math
            }
        )

        return str(result)

    except Exception as e:

        return str(e)


def get_tool(name):

    tools = {

        "calculator":
            calculator

    }

    return tools.get(name)


def run_tool(
    tool_name,
    argument
):

    tool = get_tool(
        tool_name
    )

    if not tool:

        return "Tool not found"

    return tool(argument)