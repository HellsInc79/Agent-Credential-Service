# app/model_registry.py

from .ollama_client import list_models

MODEL_REGISTRY = {}


def refresh_models():

    try:

        data = list_models()

        models = data.get(
            "models",
            []
        )

        for model in models:

            MODEL_REGISTRY[
                model["name"]
            ] = {

                "name":
                    model["name"],

                "enabled":
                    True
            }

        return MODEL_REGISTRY

    except Exception as e:

        return {
            "error":
                str(e)
        }


def get_models():

    return MODEL_REGISTRY


def disable_model(
    model_name
):

    if model_name in MODEL_REGISTRY:

        MODEL_REGISTRY[
            model_name
        ]["enabled"] = False

    return MODEL_REGISTRY.get(
        model_name
    )


def enable_model(
    model_name
):

    if model_name in MODEL_REGISTRY:

        MODEL_REGISTRY[
            model_name
        ]["enabled"] = True

    return MODEL_REGISTRY.get(
        model_name
    )