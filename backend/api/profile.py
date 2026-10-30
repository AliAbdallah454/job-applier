"""Read and replace the current user's profile."""

from fastapi import APIRouter, HTTPException

from backend.api.deps import AppContext, CurrentUser
from backend.profiles.models import CandidateProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])


def load_profile(context, user):
    item = context.profiles.get_item(Key={"user_id": user}, ConsistentRead=True).get("Item")
    return None if item is None else CandidateProfile.model_validate(item)


@router.get("")
def get_profile(context: AppContext, user: CurrentUser) -> CandidateProfile:
    profile = load_profile(context, user)
    if profile is None:
        raise HTTPException(404, "You have no profile yet")
    return profile


@router.put("")
def put_profile(
    profile: CandidateProfile, context: AppContext, user: CurrentUser
) -> CandidateProfile:
    if profile.user_id != user:
        raise HTTPException(403, "You can only edit your own profile")

    table = context.profiles
    conflict = table.meta.client.exceptions.ConditionalCheckFailedException
    current = load_profile(context, user)
    saved = profile.model_copy(update={"version": current.version + 1 if current else 1})
    if current and current.version != profile.version:
        raise HTTPException(409, "This profile changed since you opened it; reload and try again")
    try:
        table.put_item(
            Item=saved.model_dump(mode="json"),
            **(
                {
                    "ConditionExpression": "attribute_not_exists(#v) OR #v = :seen",
                    "ExpressionAttributeNames": {"#v": "version"},
                    "ExpressionAttributeValues": {":seen": current.version},
                }
                if current
                else {"ConditionExpression": "attribute_not_exists(user_id)"}
            ),
        )
    except conflict:
        raise HTTPException(
            409, "This profile changed since you opened it; reload and try again"
        ) from None
    return saved
