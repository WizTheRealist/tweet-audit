import json
from pathlib import Path

def _load_js_file(path: Path) -> list | dict:
    """
     X archive files are JS assignments like:
        window.YTD.tweets.part0 = [ ... ]
    We strip the assignment and parse the raw JSON value.
    """

    raw_tweet = path.read_text(encoding="utf-8")
    # Find the first '[' or '{' — everything from there is valid JSON
    start = min(
        (raw_tweet.find(c) for c in ("[", "{") if c in raw_tweet), default=-1
    )
    if start == -1:
        raise ValueError(f"No JSON array or object found in {path}")
    return json.loads(raw_tweet[start:])


def parse_username(account_js_path: Path) -> str:
    """
    Extract the account username from account.js.
 
    account.js structure (after stripping the JS assignment):
        [ { "account": { "username": "handle", ... } } ]
    """
    data = _load_js_file(account_js_path)
    try:
        return data[0]["account"]["username"]
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Could not extract username from {account_js_path}: {e}") from e


def parse_tweets(tweets_js_path: Path, username: str) -> list[dict]:
    """
    Parse tweets.js and return a flat list of tweet dicts, each with:
        - id_str  : tweet ID as a string
        - full_text: the raw tweet text
        - url     : direct link to the tweet on X
    
    tweets.js structure (after stripping the JS assignment):
        [ { "tweet": { "id_str": "...", "full_text": "...", ... } }, ... ]
    """
    data = _load_js_file(tweets_js_path)
 
    tweets = []
    for entry in data:
        try:
            tweet = entry["tweet"]
            id_str = tweet["id_str"]
            full_text = tweet["full_text"]
        except (KeyError, TypeError):
            continue
 
        tweets.append({
            "id_str": id_str,
            "full_text": full_text,
            "url": f"https://x.com/{username}/status/{id_str}",
        })
 
    return tweets