import os

from dotenv import load_dotenv
from scalekit import ScalekitClient

load_dotenv()


def get_client() -> ScalekitClient:
    environment_url = os.environ["SCALEKIT_ENVIRONMENT_URL"]
    client_id = os.environ["SCALEKIT_CLIENT_ID"]
    client_secret = os.environ["SCALEKIT_CLIENT_SECRET"]
    # scalekit-sdk-python 2.15.0's actual constructor takes `env_url`, not
    # `environment_url` (verified via inspect.signature against the
    # installed package — main's README example is stale on this point).
    return ScalekitClient(
        env_url=environment_url,
        client_id=client_id,
        client_secret=client_secret,
    )


GITHUB_CONNECTION_NAME = os.environ.get("SCALEKIT_GITHUB_CONNECTION_NAME", "github")
