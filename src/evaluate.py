import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional
 
from google import genai
from pydantic import BaseModel, Field
 
logger = logging.getLogger(__name__)

ALIGNMENT_PROMPT = """
You are reviewing tweets from a personal X (Twitter) archive.
Flag any tweet that violates one or more of the following criteria:
 
1. Contains unprofessional or offensive language (insults, slurs, crude humour).
2. Promotes or endorses crypto, NFTs, or get-rich-quick schemes.
3. Expresses opinions that are factually wrong, harmful, or embarrassing in hindsight.
4. Contains aggressive, dismissive, or disrespectful language toward any person or group.
5. Is low-effort noise: meaningless filler, spam-like repetition, or content with no substance.
6. Expresses explicit partisan support or opposition toward a political party, politician, or public figure (e.g., endorsements, campaigning, or strong political alignment).
 
Be conservative: only flag tweets that clearly violate a criterion.
When in doubt, do NOT flag.
 
Tweets to evaluate (JSON array):
{tweets_json}
 
Return a JSON object matching the schema exactly. One result per tweet, in the same order.
""".strip()

# Pydantic models to validate structure
class TweetEvaluation(BaseModel):
    url: str = Field(description="The tweet URL, copied exactly from the input.")
    flagged: bool = Field(description="True if the tweet violates any alignment criterion.")
    reason: Optional[str] = Field(
        default=None,
        description="Brief explanation of why the tweet was flagged. Null if not flagged.",
    )
 
class BatchEvaluation(BaseModel):
    results: list[TweetEvaluation] = Field(
        description="One evaluation per tweet, in the same order as the input."
    )


# Retry / Backoff helpers
INITIAL_BACKOFF = 1.0   # seconds
BACKOFF_MULTIPLIER = 3
JITTER_RANGE = 0.5      # ± seconds
MAX_RETRIES = 3

def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter. attempt is 0-indexed."""
    delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
    jitter = random.uniform(-JITTER_RANGE, JITTER_RANGE)
    return max(0.0, delay + jitter)

# Core Evaluation Logic
@dataclass
class FailedTweet:
    url: str
    id_str: str
    error: str
 
 
@dataclass
class EvaluationSummary:
    results: list[TweetEvaluation] = field(default_factory=list)
    failed: list[FailedTweet] = field(default_factory=list)
 
 
def _call_gemini(client: genai.Client, tweets: list[dict]) -> list[TweetEvaluation]:
    """
    Single Gemini API call for a list of tweets.
    Returns a list of TweetEvaluation objects.
    Raises on any API or validation error.
    """
    import json
 
    tweets_json = json.dumps(
        [{"url": t["url"], "text": t["full_text"]} for t in tweets],
        ensure_ascii=False,
        indent=2,
    )
    prompt = ALIGNMENT_PROMPT.format(tweets_json=tweets_json)
 
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": BatchEvaluation.model_json_schema(),
        },
    )
 
    batch = BatchEvaluation.model_validate_json(response.text)
    return batch.results
 
 
def _evaluate_with_retry(
    client: genai.Client,
    tweets: list[dict],
    retries: int = MAX_RETRIES,
) -> list[TweetEvaluation]:
    """
    Attempt to evaluate a batch, retrying with exponential backoff on failure.
    Raises the last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
 
    for attempt in range(retries):
        try:
            return _call_gemini(client, tweets)
        except Exception as exc:
            last_exc = exc
            wait = _backoff_seconds(attempt)
            logger.warning(
                "Gemini call failed (attempt %d/%d) for batch of %d tweets. "
                "Retrying in %.1fs. Error: %s",
                attempt + 1,
                retries,
                len(tweets),
                wait,
                exc,
            )
            time.sleep(wait)
 
    raise last_exc  # type: ignore[misc]
 
 
def _evaluate_batch(
    client: genai.Client,
    tweets: list[dict],
    summary: EvaluationSummary,
) -> None:
    """
    Evaluate a batch with progressive splitting on repeated failure:
      full batch → halved chunks → individual tweets
 
    Results and failures are appended to `summary` in place.
    """
    if not tweets:
        return
 
    # Attempt 1: full batch with retries
    try:
        results = _evaluate_with_retry(client, tweets)
        summary.results.extend(results)
        logger.info("Batch of %d tweets evaluated successfully.", len(tweets))
        return
    except Exception as exc:
        logger.warning(
            "Batch of %d tweets failed after all retries. Splitting. Error: %s",
            len(tweets),
            exc,
        )
 
    # Attempt 2: split into halves
    if len(tweets) > 1:
        mid = len(tweets) // 2
        halves = [tweets[:mid], tweets[mid:]]
        still_failing: list[dict] = []
 
        for half in halves:
            try:
                results = _evaluate_with_retry(client, half)
                summary.results.extend(results)
                logger.info("Half-batch of %d tweets evaluated successfully.", len(half))
            except Exception as exc:
                logger.warning(
                    "Half-batch of %d tweets failed. Falling back to one-by-one. Error: %s",
                    len(half),
                    exc,
                )
                still_failing.extend(half)
 
        tweets = still_failing  # only the truly stubborn ones go to individual pass
 
    # Attempt 3: one by one
    for tweet in tweets:
        try:
            results = _evaluate_with_retry(client, [tweet], retries=2)
            summary.results.extend(results)
            logger.info("Individual tweet %s evaluated successfully.", tweet["id_str"])
        except Exception as exc:
            logger.error(
                "Tweet %s could not be evaluated after all fallbacks. Marking as failed. Error: %s",
                tweet["id_str"],
                exc,
            )
            summary.failed.append(
                FailedTweet(
                    url=tweet["url"],
                    id_str=tweet["id_str"],
                    error=str(exc),
                )
            )
 
 
# Public interface
 
BATCH_SIZE = 50
 
 
def evaluate_tweets(
    tweets: list[dict],
    api_key: str,
    batch_size: int = BATCH_SIZE,
) -> EvaluationSummary:
    """
    Evaluate all tweets against the alignment criteria using the Gemini API.
 
    Args:
        tweets:     Output of parse_tweets.parse_tweets() — list of dicts
                    with keys id_str, full_text, url.
        api_key:    Gemini API key.
        batch_size: How many tweets to send per API call (default 50).
 
    Returns:
        EvaluationSummary with:
            .results — list of TweetEvaluation (flagged and unflagged)
            .failed  — list of FailedTweet for any tweets that couldn't be evaluated
    """
    client = genai.Client(api_key=api_key)
    summary = EvaluationSummary()
 
    batches = [tweets[i : i + batch_size] for i in range(0, len(tweets), batch_size)]
    total = len(batches)
 
    logger.info("Starting evaluation: %d tweets across %d batches.", len(tweets), total)
 
    for idx, batch in enumerate(batches, start=1):
        logger.info("Processing batch %d/%d (%d tweets)...", idx, total, len(batch))
        _evaluate_batch(client, batch, summary)
 
    logger.info(
        "Evaluation complete. %d evaluated, %d failed.",
        len(summary.results),
        len(summary.failed),
    )
 
    return summary