"""
Tests for parse.py

No mocks needed — all logic is pure file parsing.
Fixtures create minimal but realistic X archive files.
"""
import json
import pytest
from pathlib import Path

from parse import _load_js_file, parse_username, parse_tweets

# Helpers

def make_tweets_js(path: Path, entries: list[dict]) -> Path:
    """Write a tweets.js file with the X archive JS assignment wrapper."""
    content = "window.YTD.tweets.part0 = " + json.dumps(entries)
    path.write_text(content, encoding="utf-8")
    return path


def make_account_js(path: Path, username: str) -> Path:
    """Write an account.js file with the X archive JS assignment wrapper."""
    data = [{"account": {"username": username, "accountId": "123"}}]
    content = "window.YTD.account.part0 = " + json.dumps(data)
    path.write_text(content, encoding="utf-8")
    return path


def tweet_entry(id_str: str, full_text: str) -> dict:
    """Minimal tweet entry matching the X archive structure."""
    return {"tweet": {"id_str": id_str, "full_text": full_text}}

# _load_js_file

class TestLoadJsFile:
    def test_strips_array_assignment(self, tmp_path):
        f = tmp_path / "tweets.js"
        f.write_text('window.YTD.tweets.part0 = [{"a": 1}]')
        result = _load_js_file(f)
        assert result == [{"a": 1}]

    def test_strips_object_assignment(self, tmp_path):
        f = tmp_path / "account.js"
        f.write_text('window.YTD.account.part0 = {"key": "value"}')
        result = _load_js_file(f)
        assert result == {"key": "value"}

    def test_raises_on_no_json(self, tmp_path):
        f = tmp_path / "bad.js"
        f.write_text("this is not json at all")
        with pytest.raises(ValueError, match="No JSON"):
            _load_js_file(f)

    def test_handles_empty_array(self, tmp_path):
        f = tmp_path / "tweets.js"
        f.write_text("window.YTD.tweets.part0 = []")
        assert _load_js_file(f) == []


# parse_username

class TestParseUsername:
    def test_extracts_username(self, tmp_path):
        f = make_account_js(tmp_path / "account.js", "chimaobi")
        assert parse_username(f) == "chimaobi"

    def test_raises_on_missing_key(self, tmp_path):
        f = tmp_path / "account.js"
        # Valid JS/JSON but wrong structure
        f.write_text("window.YTD.account.part0 = [{}]")
        with pytest.raises(ValueError, match="Could not extract username"):
            parse_username(f)

    def test_raises_on_empty_array(self, tmp_path):
        f = tmp_path / "account.js"
        f.write_text("window.YTD.account.part0 = []")
        with pytest.raises(ValueError, match="Could not extract username"):
            parse_username(f)

# parse_tweets

class TestParseTweets:
    def test_returns_correct_shape(self, tmp_path):
        f = make_tweets_js(tmp_path / "tweets.js", [
            tweet_entry("111", "Hello world"),
        ])
        result = parse_tweets(f, "chimaobi")
        assert len(result) == 1
        assert result[0] == {
            "id_str": "111",
            "full_text": "Hello world",
            "url": "https://x.com/chimaobi/status/111",
        }

    def test_constructs_url_correctly(self, tmp_path):
        f = make_tweets_js(tmp_path / "tweets.js", [
            tweet_entry("999", "Test tweet"),
        ])
        result = parse_tweets(f, "testuser")
        assert result[0]["url"] == "https://x.com/testuser/status/999"

    def test_parses_multiple_tweets(self, tmp_path):
        entries = [tweet_entry(str(i), f"Tweet {i}") for i in range(5)]
        f = make_tweets_js(tmp_path / "tweets.js", entries)
        result = parse_tweets(f, "chimaobi")
        assert len(result) == 5
        assert [r["id_str"] for r in result] == [str(i) for i in range(5)]

    def test_skips_malformed_entries(self, tmp_path):
        entries = [
            tweet_entry("111", "Good tweet"),
            {"not_a_tweet": {}},           # missing "tweet" key
            {"tweet": {"no_id": "oops"}},  # missing id_str
            tweet_entry("222", "Another good one"),
        ]
        f = make_tweets_js(tmp_path / "tweets.js", entries)
        result = parse_tweets(f, "chimaobi")
        # Only the two well-formed entries should come through
        assert len(result) == 2
        assert result[0]["id_str"] == "111"
        assert result[1]["id_str"] == "222"

    def test_returns_empty_list_for_empty_archive(self, tmp_path):
        f = make_tweets_js(tmp_path / "tweets.js", [])
        assert parse_tweets(f, "chimaobi") == []

    def test_preserves_full_text(self, tmp_path):
        text = "This tweet has emoji 🎉, newlines\nand 'quotes'"
        f = make_tweets_js(tmp_path / "tweets.js", [tweet_entry("1", text)])
        result = parse_tweets(f, "chimaobi")
        assert result[0]["full_text"] == text