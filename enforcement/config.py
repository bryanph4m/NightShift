import os

from dotenv import load_dotenv
from scalekit import ScalekitClient

load_dotenv()

# Identifier used for Bob's connected account in ScaleKit. Alice's identifier
# is owned by the perception branch; kept here only for the side-by-side
# identity-verification check, never used to make decisions on her behalf.
BOB_IDENTIFIER = os.environ.get("BOB_IDENTIFIER", "bob")
ALICE_IDENTIFIER = os.environ.get("ALICE_IDENTIFIER", "alice")

# NOTE: main's README documents this as "github" (SCALEKIT_GITHUB_CONNECTION_NAME=github),
# but the actual connection in this ScaleKit environment is named "github-98UjwezY" --
# confirmed empirically, filtering by "github" alone returns NOT_FOUND. Flagged to
# Person 1 to fix on main; use the env var so either side's fix takes effect here
# without a code change.
GITHUB_CONNECTION_NAME = os.environ.get("SCALEKIT_GITHUB_CONNECTION_NAME", "github-98UjwezY")

# Owner of the two demo repos (payments-service, notifications-service).
# Deliberately neither Alice's nor Bob's account -- an owner bypasses
# collaborator permission restrictions, which would break the premise.
GITHUB_ORG_OR_OWNER = os.environ.get("GITHUB_ORG_OR_OWNER", "bryanph4m")


def get_scalekit_client() -> ScalekitClient:
    return ScalekitClient(
        env_url=os.environ["SCALEKIT_ENVIRONMENT_URL"],
        client_id=os.environ["SCALEKIT_CLIENT_ID"],
        client_secret=os.environ["SCALEKIT_CLIENT_SECRET"],
    )
