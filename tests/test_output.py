"""
Tests for output.py

Uses pytest's tmp_path fixture for real filesystem operations — no mocks.
"""
import csv
import pytest
from pathlib import Path

from evaluate import EvaluationSummary, FailedTweet, TweetEvaluation
from output import write_failed, write_flagged

# Fixtures

def make_evaluation(url: str, flagged: bool, reason: str | None = None) -> TweetEvaluation:
    return TweetEvaluation(url=url, flagged=flagged, reason=reason)


def make_summary(
    flagged_urls: list[str] = (),
    unflagged_urls: list[str] = (),
    failed: list[FailedTweet] = (),
) -> EvaluationSummary:
    results = (
        [make_evaluation(url, flagged=True, reason="Bad tweet") for url in flagged_urls]
        + [make_evaluation(url, flagged=False) for url in unflagged_urls]
    )
    return EvaluationSummary(results=results, failed=list(failed))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))

# write_flagged

class TestWriteFlagged:
    def test_writes_flagged_tweets_only(self, tmp_path):
        summary = make_summary(
            flagged_urls=["https://x.com/u/status/1", "https://x.com/u/status/2"],
            unflagged_urls=["https://x.com/u/status/3"],
        )
        out = tmp_path / "output" / "flagged.csv"

        count = write_flagged(summary, out)

        assert count == 2
        rows = read_csv(out)
        assert len(rows) == 2
        assert rows[0] == {"tweet_url": "https://x.com/u/status/1", "deleted": "false"}
        assert rows[1] == {"tweet_url": "https://x.com/u/status/2", "deleted": "false"}

    def test_deleted_column_is_always_false(self, tmp_path):
        summary = make_summary(flagged_urls=["https://x.com/u/status/1"])
        out = tmp_path / "flagged.csv"

        write_flagged(summary, out)

        rows = read_csv(out)
        assert all(r["deleted"] == "false" for r in rows)

    def test_returns_zero_and_no_file_when_nothing_flagged(self, tmp_path):
        summary = make_summary(unflagged_urls=["https://x.com/u/status/1"])
        out = tmp_path / "flagged.csv"

        count = write_flagged(summary, out)

        assert count == 0
        assert not out.exists()

    def test_creates_output_directory_if_missing(self, tmp_path):
        summary = make_summary(flagged_urls=["https://x.com/u/status/1"])
        out = tmp_path / "nested" / "deep" / "flagged.csv"

        write_flagged(summary, out)

        assert out.exists()

    def test_csv_has_correct_headers(self, tmp_path):
        summary = make_summary(flagged_urls=["https://x.com/u/status/1"])
        out = tmp_path / "flagged.csv"

        write_flagged(summary, out)

        with out.open() as f:
            header = f.readline().strip()
        assert header == "tweet_url,deleted"

    def test_returns_correct_count(self, tmp_path):
        urls = [f"https://x.com/u/status/{i}" for i in range(10)]
        summary = make_summary(flagged_urls=urls)
        out = tmp_path / "flagged.csv"

        count = write_flagged(summary, out)

        assert count == 10

# write_failed

class TestWriteFailed:
    def test_writes_failed_tweets(self, tmp_path):
        failed = [
            FailedTweet(url="https://x.com/u/status/99", id_str="99", error="timeout"),
        ]
        summary = EvaluationSummary(failed=failed)
        out = tmp_path / "failed.csv"

        count = write_failed(summary, out)

        assert count == 1
        rows = read_csv(out)
        assert rows[0] == {
            "tweet_url": "https://x.com/u/status/99",
            "id_str": "99",
            "error": "timeout",
        }

    def test_returns_zero_and_no_file_when_no_failures(self, tmp_path):
        summary = EvaluationSummary()
        out = tmp_path / "failed.csv"

        count = write_failed(summary, out)

        assert count == 0
        assert not out.exists()

    def test_creates_output_directory_if_missing(self, tmp_path):
        failed = [FailedTweet(url="https://x.com/u/status/1", id_str="1", error="err")]
        summary = EvaluationSummary(failed=failed)
        out = tmp_path / "deep" / "dir" / "failed.csv"

        write_failed(summary, out)

        assert out.exists()

    def test_csv_has_correct_headers(self, tmp_path):
        failed = [FailedTweet(url="https://x.com/u/status/1", id_str="1", error="err")]
        summary = EvaluationSummary(failed=failed)
        out = tmp_path / "failed.csv"

        write_failed(summary, out)

        with out.open() as f:
            header = f.readline().strip()
        assert header == "tweet_url,id_str,error"

    def test_preserves_full_error_message(self, tmp_path):
        error_msg = "ConnectionError: max retries exceeded with url: /v1/models"
        failed = [FailedTweet(url="https://x.com/u/status/1", id_str="1", error=error_msg)]
        summary = EvaluationSummary(failed=failed)
        out = tmp_path / "failed.csv"

        write_failed(summary, out)

        rows = read_csv(out)
        assert rows[0]["error"] == error_msg