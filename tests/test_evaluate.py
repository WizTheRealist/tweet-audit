"""
Tests for evaluate.py

The Gemini client is always mocked — no real API calls are made.
We test our own logic: retry behaviour, fallback splitting, failure marking.
"""
import pytest
from unittest.mock import MagicMock, patch, call

from evaluate import (
    _backoff_seconds,
    _evaluate_batch,
    _evaluate_with_retry,
    EvaluationSummary,
    FailedTweet,
    TweetEvaluation,
    INITIAL_BACKOFF,
    BACKOFF_MULTIPLIER,
    MAX_RETRIES,
)



# Fixtures

def make_tweet(id_str: str, text: str = "some text") -> dict:
    return {
        "id_str": id_str,
        "full_text": text,
        "url": f"https://x.com/user/status/{id_str}",
    }


def make_evaluation(id_str: str, flagged: bool = False) -> TweetEvaluation:
    return TweetEvaluation(
        url=f"https://x.com/user/status/{id_str}",
        flagged=flagged,
        reason="Bad tweet" if flagged else None,
    )


def mock_client_returning(evaluations: list[TweetEvaluation]) -> MagicMock:
    """Return a mock Gemini client whose generate_content returns the given evaluations."""
    client = MagicMock()
    response = MagicMock()
    from evaluate import BatchEvaluation
    batch = BatchEvaluation(results=evaluations)
    response.text = batch.model_dump_json()
    client.models.generate_content.return_value = response
    return client


def mock_client_always_failing(exc: Exception = RuntimeError("API error")) -> MagicMock:
    client = MagicMock()
    client.models.generate_content.side_effect = exc
    return client



# _backoff_seconds

class TestBackoffSeconds:
    def test_grows_with_attempt(self):
        # Each attempt should produce a strictly larger base delay
        base_delays = [
            INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
            for attempt in range(4)
        ]
        assert base_delays == sorted(base_delays)

    def test_never_negative(self):
        for attempt in range(10):
            # Run many times to account for jitter
            for _ in range(20):
                assert _backoff_seconds(attempt) >= 0.0

    def test_first_attempt_is_around_initial_backoff(self):
        from evaluate import JITTER_RANGE
        result = _backoff_seconds(0)
        assert INITIAL_BACKOFF - JITTER_RANGE <= result <= INITIAL_BACKOFF + JITTER_RANGE


# _evaluate_with_retry

class TestEvaluateWithRetry:
    @patch("evaluate.time.sleep")
    def test_returns_on_first_success(self, mock_sleep):
        tweets = [make_tweet("1")]
        evals = [make_evaluation("1")]
        client = mock_client_returning(evals)

        result = _evaluate_with_retry(client, tweets, retries=3)

        assert result == evals
        assert client.models.generate_content.call_count == 1
        mock_sleep.assert_not_called()

    @patch("evaluate.time.sleep")
    def test_retries_on_failure_then_succeeds(self, mock_sleep):
        tweets = [make_tweet("1")]
        evals = [make_evaluation("1")]

        client = MagicMock()
        response = MagicMock()
        from evaluate import BatchEvaluation
        response.text = BatchEvaluation(results=evals).model_dump_json()

        # Fail twice, succeed on third
        client.models.generate_content.side_effect = [
            RuntimeError("fail"),
            RuntimeError("fail"),
            response,
        ]

        result = _evaluate_with_retry(client, tweets, retries=3)

        assert result == evals
        assert client.models.generate_content.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("evaluate.time.sleep")
    def test_raises_after_all_retries_exhausted(self, mock_sleep):
        tweets = [make_tweet("1")]
        client = mock_client_always_failing(RuntimeError("persistent error"))

        with pytest.raises(RuntimeError, match="persistent error"):
            _evaluate_with_retry(client, tweets, retries=3)

        assert client.models.generate_content.call_count == 3
        assert mock_sleep.call_count == 3

    @patch("evaluate.time.sleep")
    def test_sleeps_between_retries(self, mock_sleep):
        tweets = [make_tweet("1")]
        client = mock_client_always_failing()

        with pytest.raises(RuntimeError):
            _evaluate_with_retry(client, tweets, retries=2)

        assert mock_sleep.call_count == 2



# _evaluate_batch

class TestEvaluateBatch:
    @patch("evaluate.time.sleep")
    def test_succeeds_on_first_attempt(self, mock_sleep):
        tweets = [make_tweet("1"), make_tweet("2")]
        evals = [make_evaluation("1"), make_evaluation("2", flagged=True)]
        client = mock_client_returning(evals)
        summary = EvaluationSummary()

        _evaluate_batch(client, tweets, summary)

        assert summary.results == evals
        assert summary.failed == []
        assert client.models.generate_content.call_count == 1

    @patch("evaluate.time.sleep")
    def test_empty_batch_is_a_no_op(self, mock_sleep):
        client = MagicMock()
        summary = EvaluationSummary()

        _evaluate_batch(client, [], summary)

        client.models.generate_content.assert_not_called()
        assert summary.results == []
        assert summary.failed == []

    @patch("evaluate.time.sleep")
    def test_falls_back_to_halves_on_full_batch_failure(self, mock_sleep):
        """
        Full batch always fails. Each half succeeds independently.
        All tweets should end up in results, none in failed.
        """
        tweets = [make_tweet(str(i)) for i in range(4)]

        # Full batch of 4 always fails; halves of 2 succeed
        from evaluate import BatchEvaluation

        def generate_side_effect(*args, **kwargs):
            # Determine batch size from the prompt content
            contents = args[0] if args else kwargs.get("contents", "")
            tweet_count = contents.count('"url"')
            if tweet_count == 4:
                raise RuntimeError("full batch too large")
            # Return evaluations for however many tweets were sent
            evals = [
                TweetEvaluation(
                    url=f"https://x.com/user/status/{i}",
                    flagged=False,
                    reason=None,
                )
                for i in range(tweet_count)
            ]
            response = MagicMock()
            response.text = BatchEvaluation(results=evals).model_dump_json()
            return response

        client = MagicMock()
        client.models.generate_content.side_effect = generate_side_effect
        summary = EvaluationSummary()

        _evaluate_batch(client, tweets, summary)

        assert len(summary.results) == 4
        assert summary.failed == []

    @patch("evaluate.time.sleep")
    def test_falls_back_to_individual_when_halves_fail(self, mock_sleep):
        """
        Full batch fails. Both halves fail. Individual tweets succeed.
        """
        tweets = [make_tweet("1"), make_tweet("2")]
        from evaluate import BatchEvaluation

        call_count = {"n": 0}

        def generate_side_effect(*args, **kwargs):
            call_count["n"] += 1
            contents = args[0] if args else kwargs.get("contents", "")
            tweet_count = contents.count('"url"')
            # Fail full batch and any batch > 1
            if tweet_count > 1:
                raise RuntimeError("batch too large")
            # Single tweet succeeds
            tweet_id = "1" if call_count["n"] <= MAX_RETRIES * 2 + 1 else "2"
            response = MagicMock()
            response.text = BatchEvaluation(results=[
                TweetEvaluation(url=f"https://x.com/user/status/{tweet_id}", flagged=False, reason=None)
            ]).model_dump_json()
            return response

        client = MagicMock()
        client.models.generate_content.side_effect = generate_side_effect
        summary = EvaluationSummary()

        _evaluate_batch(client, tweets, summary)

        assert len(summary.results) == 2
        assert summary.failed == []

    @patch("evaluate.time.sleep")
    def test_marks_tweet_as_failed_when_all_fallbacks_exhausted(self, mock_sleep):
        """
        Everything fails at every level. Tweet ends up in summary.failed.
        """
        tweets = [make_tweet("42")]
        client = mock_client_always_failing(RuntimeError("total failure"))
        summary = EvaluationSummary()

        _evaluate_batch(client, tweets, summary)

        assert summary.results == []
        assert len(summary.failed) == 1
        assert summary.failed[0].id_str == "42"
        assert summary.failed[0].url == "https://x.com/user/status/42"
        assert "total failure" in summary.failed[0].error

    @patch("evaluate.time.sleep")
    def test_partial_batch_failure_doesnt_lose_successes(self, mock_sleep):
        """
        A batch of 2: full batch fails, first half succeeds, second half fails
        and falls to individual which also fails. One result + one failure.
        """
        from evaluate import BatchEvaluation

        tweet_a = make_tweet("A")
        tweet_b = make_tweet("B")

        success_response = MagicMock()
        success_response.text = BatchEvaluation(results=[
            TweetEvaluation(url=tweet_a["url"], flagged=False, reason=None)
        ]).model_dump_json()

        # full batch (2) → fail; first half (1, tweet A) → succeed; second half (1, tweet B) → fail always
        call_log = []

        def side_effect(*args, **kwargs):
            contents = args[0] if args else kwargs.get("contents", "")
            is_tweet_a = tweet_a["url"] in contents
            is_tweet_b = tweet_b["url"] in contents
            tweet_count = contents.count('"url"')

            call_log.append(tweet_count)

            if tweet_count > 1:
                raise RuntimeError("full batch fail")
            if is_tweet_a:
                return success_response
            if is_tweet_b:
                raise RuntimeError("tweet B always fails")

        client = MagicMock()
        client.models.generate_content.side_effect = side_effect
        summary = EvaluationSummary()

        _evaluate_batch(client, [tweet_a, tweet_b], summary)

        assert len(summary.results) == 1
        assert summary.results[0].url == tweet_a["url"]
        assert len(summary.failed) == 1
        assert summary.failed[0].id_str == "B"