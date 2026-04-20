import csv
import logging
from pathlib import Path

from evaluate import EvaluationSummary, FailedTweet, TweetEvaluation

logger = logging.getLogger(__name__)

# CSV writers

FLAGGED_FIELDNAMES = ["tweet_url", "deleted"]
FAILED_FIELDNAMES = ["tweet_url", "id_str", "error"]


def write_flagged(summary: EvaluationSummary, output_path: Path) -> int:
    """
    Write flagged tweets to a CSV file for manual review and deletion.

    Columns:
        tweet_url — direct link to the tweet on X
        deleted   — manual tracking flag, always initialised to 'false'

    Returns the number of flagged tweets written.
    """
    flagged: list[TweetEvaluation] = [r for r in summary.results if r.flagged]

    if not flagged:
        logger.info("No tweets were flagged. No output file written.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FLAGGED_FIELDNAMES)
        writer.writeheader()
        writer.writerows({"tweet_url": r.url, "deleted": "false"} for r in flagged)

    logger.info("Wrote %d flagged tweet(s) to %s.", len(flagged), output_path)
    return len(flagged)


def write_failed(summary: EvaluationSummary, output_path: Path) -> int:
    """
    Write tweets that could not be evaluated to a separate CSV for inspection.

    Columns:
        tweet_url — direct link to the tweet on X
        id_str    — tweet ID
        error     — the last error message from the Gemini API

    Returns the number of failed tweets written. Writes nothing if there are none.
    """
    if not summary.failed:
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILED_FIELDNAMES)
        writer.writeheader()
        writer.writerows(
            {"tweet_url": ft.url, "id_str": ft.id_str, "error": ft.error}
            for ft in summary.failed
        )

    logger.warning(
        "%d tweet(s) could not be evaluated. Written to %s for inspection.",
        len(summary.failed),
        output_path,
    )
    return len(summary.failed)