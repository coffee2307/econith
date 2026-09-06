import os


def running_in_docker() -> bool:
    """
    Check if we are running in a docker container
    """
    return os.environ.get("ECONITH_APP_ENV") == "docker"
