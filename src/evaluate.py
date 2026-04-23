import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from google import genai
from google.api_core import exceptions as google_exceptions
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

# Pydantic models
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

# Backoff settings for transient errors (timeouts, network blips)
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 3
JITTER_RANGE = 0.5
MAX_RETRIES = 3

# Backoff settings for 429 quota errors
QUOTA_INITIAL_BACKOFF = 30.0
QUOTA_BACKOFF_MULTIPLIER = 2
QUOTA_MAX_RETRIES = 5


def _backoff_seconds(attempt: int) -> float:
    delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
    jitter = random.uniform(-JITTER_RANGE, JITTER_RANGE)
    return max(0.0, delay + jitter)


def _quota_backoff_seconds(attempt: int) -> float:
    # 30s, 60s, 120s, 240s, 480s — no jitter, predictable spacing
    return QUOTA_INITIAL_BACKOFF * (QUOTA_BACKOFF_MULTIPLIER ** attempt)


@dataclass
class FailedTweet:
    url: str
    id_str: str
    error: str


@dataclass
class EvaluationSummary:
    results: list[TweetEvaluation] = field(default_factory=list)
    failed: list[FailedTweet] = field(default_factory=list)


# Checkpoint helpers

def _load_checkpoint(checkpoint_path: Path) -> set[str]:
    if not checkpoint_path.exists():
        return set()
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        return set(data.get("evaluated_ids", []))
    except Exception as exc:
        logger.warning("Could not read checkpoint file, starting fresh. Error: %s", exc)
        return set()


def _save_checkpoint(checkpoint_path: Path, evaluated_ids: set[str]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps({"evaluated_ids": list(evaluated_ids)}, indent=2),
        encoding="utf-8",
    )


def _call_gemini(client: genai.Client, tweets: list[dict]) -> list[TweetEvaluation]:
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
    last_exc: Exception | None = None
    quota_attempt = 0

    for attempt in range(retries):
        try:
            return _call_gemini(client, tweets)
        except google_exceptions.ResourceExhausted as exc:
            # 429 — use longer backoff and don't count against normal retries
            last_exc = exc
            wait = _quota_backoff_seconds(quota_attempt)
            quota_attempt += 1
            logger.warning(
                "Quota exceeded (429) for batch of %d tweets. "
                "Waiting %.0fs before retrying. Error: %s",
                len(tweets),
                wait,
                exc,
            )
            time.sleep(wait)
            if quota_attempt >= QUOTA_MAX_RETRIES:
                raise
            # Don't increment attempt — quota errors don't burn normal retries
            continue
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
    if not tweets:
        return

    # Attempt 1: full batch
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

    # Attempt 2: halves
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

        tweets = still_failing

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


BATCH_SIZE = 100


def evaluate_tweets(
    tweets: list[dict],
    api_key: str,
    checkpoint_path: Path,
    flagged_path: Path,
    failed_path: Path,
    batch_size: int = BATCH_SIZE,
) -> EvaluationSummary:
    from output import append_flagged, append_failed

    client = genai.Client(api_key=api_key)
    summary = EvaluationSummary()

    # Skip tweets already evaluated in a previous run
    evaluated_ids = _load_checkpoint(checkpoint_path)
    remaining = [t for t in tweets if t["id_str"] not in evaluated_ids]

    if evaluated_ids:
        logger.info(
            "Resuming from checkpoint: %d already evaluated, %d remaining.",
            len(evaluated_ids),
            len(remaining),
        )

    batches = [remaining[i : i + batch_size] for i in range(0, len(remaining), batch_size)]
    total = len(batches)

    logger.info("Starting evaluation: %d tweets across %d batches.", len(remaining), total)

    for idx, batch in enumerate(batches, start=1):
        logger.info("Processing batch %d/%d (%d tweets)...", idx, total, len(batch))

        batch_summary = EvaluationSummary()
        _evaluate_batch(client, batch, batch_summary)

        # Write this batch's results to CSV immediately
        append_flagged(batch_summary, flagged_path)
        append_failed(batch_summary, failed_path)

        # Accumulate into the overall summary
        summary.results.extend(batch_summary.results)
        summary.failed.extend(batch_summary.failed)

        # Save checkpoint
        evaluated_ids.update(t["id_str"] for t in batch)
        _save_checkpoint(checkpoint_path, evaluated_ids)

    logger.info(
        "Evaluation complete. %d evaluated, %d failed.",
        len(summary.results),
        len(summary.failed),
    )

    return summary