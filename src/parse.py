import json
from pathlib import Path

def _load_js_file(path: Path) -> list | dict:
    """
    X archive files are JS assignments like:
        window.YTD.tweets.part0 = [ ... ]
    We strip the assignment and parse the raw JSON value.
    """
    raw_tweet = path.read_text(encoding="utf-8")
    # Find the first '[' or '{', everything from there is valid JSON
    start = min(
        (raw_tweet.find(c) for c in ("[", "{") if c in raw_tweet),
        default=-1,
    )
    if start == -1:
        raise ValueError(f"No JSON array or object found in {path}")
    return json.loads(raw_tweet[start:])