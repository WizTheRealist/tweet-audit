import argparse
import json
import logging
import sys
from pathlib import Path

from evaluate_tweets import evaluate_tweets
from output import write_failed, write_flagged
from parse import parse_tweets, parse_username

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tweet-audit",
        description="Analyse your X archive with Gemini AI and flag tweets for deletion.",
    )
    parser.add_argument(
        "--archive",
        required=True,
        type=Path,
        metavar="DIR",
        help="Path to the extracted X archive data directory (contains tweets.js, account.js).",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="FILE",
        help="Path to config JSON file (must contain a 'gemini_api_key' field).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        default=Path("output"),
        help="Directory to write CSV output files (default: ./output).",
    )
    return parser


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("Config file is not valid JSON: %s", e)
        sys.exit(1)

    if not config.get("gemini_api_key"):
        logger.error("Config file must contain a non-empty 'gemini_api_key' field.")
        sys.exit(1)

    return config


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    archive_dir: Path = args.archive
    output_dir: Path = args.output

    tweets_js = archive_dir / "tweets.js"
    account_js = archive_dir / "account.js"

    for path in (tweets_js, account_js):
        if not path.exists():
            logger.error("Expected archive file not found: %s", path)
            sys.exit(1)

    config = load_config(args.config)
    api_key: str = config["gemini_api_key"]

    # Stage 1: Parse
    print("\n── Stage 1/3: Parsing archive ──────────────────────────")
    try:
        username = parse_username(account_js)
        tweets = parse_tweets(tweets_js, username)
    except ValueError as e:
        logger.error("Failed to parse archive: %s", e)
        sys.exit(1)

    print(f"  ✓ Found {len(tweets):,} tweets for @{username}")

    # Stage 2: Evaluate
    print("\n── Stage 2/3: Evaluating with Gemini ───────────────────")
    print("  Sending tweets in batches of 100 — this may take a while...\n")

    checkpoint_path = output_dir / "checkpoint.json"
    flagged_path = output_dir / "flagged.csv"
    failed_path = output_dir / "failed.csv"

    summary = evaluate_tweets(
        tweets,
        api_key=api_key,
        checkpoint_path=checkpoint_path,
        flagged_path=flagged_path,
        failed_path=failed_path,
    )

    flagged_count = sum(1 for r in summary.results if r.flagged)
    failed_count = len(summary.failed)

    print(f"\n  ✓ Evaluated {len(summary.results):,} tweets")
    print(f"  ✓ Flagged:  {flagged_count:,}")
    if failed_count:
        print(f"  ⚠ Failed:   {failed_count:,} (could not be evaluated)")

    # Stage 3: Summary
    print("\n── Done ─────────────────────────────────────────────────")
    write_flagged(summary, flagged_path)
    write_failed(summary, failed_path)
    if flagged_count:
        print(f"  Flagged tweets → {flagged_path}")
    else:
        print("  No tweets were flagged.")
    if failed_count:
        print(f"  Failed tweets  → {failed_path}")
        print("  Review failed.csv — these tweets were not assessed.")
    print()


if __name__ == "__main__":
    main()