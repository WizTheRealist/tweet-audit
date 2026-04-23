import csv
import logging
from pathlib import Path

from evaluate_tweets import EvaluationSummary, FailedTweet, TweetEvaluation

logger = logging.getLogger(__name__)

FLAGGED_FIELDNAMES = ["tweet_url", "deleted"]
FAILED_FIELDNAMES = ["tweet_url", "id_str", "error"]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """Write rows to a CSV, creating with header if new, appending if it already exists."""
    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def append_flagged(summary: EvaluationSummary, output_path: Path) -> int:
    """Append flagged tweets from this batch to the CSV."""
    flagged = [r for r in summary.results if r.flagged]
    if not flagged:
        return 0

    _write_csv(
        output_path,
        FLAGGED_FIELDNAMES,
        [{"tweet_url": r.url, "deleted": "false"} for r in flagged],
    )
    logger.info("Appended %d flagged tweet(s) to %s.", len(flagged), output_path)
    return len(flagged)


def append_failed(summary: EvaluationSummary, output_path: Path) -> int:
    """Append failed tweets from this batch to the CSV."""
    if not summary.failed:
        return 0

    _write_csv(
        output_path,
        FAILED_FIELDNAMES,
        [{"tweet_url": ft.url, "id_str": ft.id_str, "error": ft.error} for ft in summary.failed],
    )
    logger.warning(
        "Appended %d failed tweet(s) to %s.",
        len(summary.failed),
        output_path,
    )
    return len(summary.failed)


def write_flagged(summary: EvaluationSummary, output_path: Path) -> int:
    """Write all flagged tweets to CSV from scratch. Used for end-of-run summary reporting."""
    flagged = [r for r in summary.results if r.flagged]
    if not flagged:
        logger.info("No tweets were flagged.")
        return 0
    logger.info("%d flagged tweet(s) written to %s.", len(flagged), output_path)
    return len(flagged)


def write_failed(summary: EvaluationSummary, output_path: Path) -> int:
    """Report failed tweet count. Used for end-of-run summary reporting."""
    if not summary.failed:
        return 0
    logger.warning(
        "%d tweet(s) could not be evaluated. See %s for details.",
        len(summary.failed),
        output_path,
    )
    return len(summary.failed)