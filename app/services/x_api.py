from __future__ import annotations
import httpx
from app.config import get_settings
import structlog

logger   = structlog.get_logger()
settings = get_settings()

BASE_URL = settings.socialdata_base_url
HEADERS  = {"Authorization": f"Bearer {settings.socialdata_api_key}", "Accept": "application/json"}
CLIP_QUALITY_THRESHOLD = 0.3


class XAPIError(Exception):
    pass


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{BASE_URL}{path}", headers=HEADERS, params=params or {})
        if resp.status_code == 429:
            raise XAPIError("Rate limited")
        if resp.status_code >= 400:
            raise XAPIError(f"API error {resp.status_code}: {resp.text}")
        return resp.json()


async def get_tweet(tweet_id: str) -> dict | None:
    try:
        return await _get("/twitter/statuses/show", {"id": tweet_id})
    except XAPIError as e:
        logger.warning("get_tweet failed", tweet_id=tweet_id, error=str(e))
        return None


async def get_user_tweets(x_handle: str, count: int = 20) -> list[dict]:
    try:
        data = await _get(f"/twitter/user/{x_handle}/tweets", {"count": count})
        return data.get("tweets", [])
    except XAPIError as e:
        logger.warning("get_user_tweets failed", handle=x_handle, error=str(e))
        return []


async def verify_post_mission(x_handle: str, tweet_id: str,
                               keyword: str | None = None, min_length: int = 0) -> tuple[bool, str]:
    tweet = await get_tweet(tweet_id)
    if not tweet:
        return False, "Tweet not found"
    author = tweet.get("user", {}).get("screen_name", "").lower()
    if author != x_handle.lower().lstrip("@"):
        return False, f"Tweet author @{author} does not match @{x_handle}"
    text = tweet.get("full_text", tweet.get("text", ""))
    if keyword and keyword.lower() not in text.lower():
        return False, f"Tweet missing keyword: {keyword}"
    if len(text) < min_length:
        return False, f"Tweet too short ({len(text)} < {min_length})"
    return True, "Verified"


async def verify_retweet_mission(x_handle: str, original_tweet_id: str) -> tuple[bool, str]:
    try:
        tweets = await get_user_tweets(x_handle, count=50)
        for tweet in tweets:
            rt = tweet.get("retweeted_status", {})
            if rt and str(rt.get("id_str", "")) == str(original_tweet_id):
                return True, "Retweet verified"
        return False, "Retweet not found in recent activity"
    except Exception as e:
        return False, str(e)


async def calculate_clip_quality(tweet_id: str) -> tuple[float, dict]:
    tweet = await get_tweet(tweet_id)
    if not tweet:
        return 0.0, {}
    likes    = tweet.get("favorite_count", 0)
    retweets = tweet.get("retweet_count",  0)
    replies  = tweet.get("reply_count",    0)
    views    = tweet.get("view_count",     0) or tweet.get("impression_count", 0) or 1
    score    = min(((likes * 3 + retweets * 5 + replies * 2) / max(views, 100)) * 50, 1.0)
    return score, {"likes": likes, "retweets": retweets, "replies": replies,
                   "views": views, "quality_score": round(score, 3)}


async def verify_clip_mission(x_handle: str, tweet_id: str,
                               quality_threshold: float = CLIP_QUALITY_THRESHOLD) -> tuple[bool, str, dict]:
    tweet = await get_tweet(tweet_id)
    if not tweet:
        return False, "Tweet not found", {}
    author = tweet.get("user", {}).get("screen_name", "").lower()
    if author != x_handle.lower().lstrip("@"):
        return False, f"Tweet not authored by @{x_handle}", {}
    score, metrics = await calculate_clip_quality(tweet_id)
    if score < quality_threshold:
        return False, f"Quality score {score:.3f} below threshold {quality_threshold}", metrics
    return True, f"Verified. Quality: {score:.3f}", metrics
