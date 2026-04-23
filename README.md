# tweet-audit

Audit your X (Twitter) archive for tweets that no longer align with your values. This tool parses your archive, evaluates each tweet against a set of alignment criteria using the Gemini API, and generates a CSV of flagged tweets for manual review and deletion.

**Why use this?** Your old tweets may contain outdated opinions, harmful language, low-effort content, or things you'd rather not be associated with. This tool helps you identify and remove them at scale.

## Requirements

- Python 3.11+
- A downloaded X archive (Settings → Your Account → Download an archive of your data)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com)
  - **Note:** The Gemini API is free for basic usage, but costs apply for high-volume requests. Check [pricing](https://ai.google.dev/pricing) before processing a large archive.

## Setup

**1. Create and activate a virtual environment** (recommended):

```bash
# On Windows
python -m venv venv
venv\Scripts\activate

# On macOS/Linux
python -m venv venv
source venv/bin/activate
```

**2. Install dependencies:**

```bash
pip install -r requirements.txt
```

**3. Create your config file:**

```bash
cp config.example.json config.json
```

**4. Add your Gemini API key:**

Open `config.json` and replace the placeholder with your real key:

```json
{
  "gemini_api_key": "YOUR_KEY_HERE"
}
```

**5. Verify your setup:**

```bash
python -m pytest -v
```

All tests should pass. If they don't, check that your API key is valid.

## Alignment Criteria

Tweets are flagged if they violate one or more of these criteria:

1. **Unprofessional or offensive language** — Insults, slurs, or crude humour
2. **Crypto/NFT promotion** — Endorsements of crypto, NFTs, or get-rich-quick schemes
3. **Factually wrong or harmful opinions** — Opinions that are factually incorrect, harmful, or embarrassing in hindsight
4. **Aggressive or disrespectful language** — Dismissive or disrespectful speech toward any person or group
5. **Low-effort noise** — Meaningless filler, spam-like repetition, or content with no substance
6. **Partisan political statements** — Explicit endorsements, opposition, or strong alignment with political parties, politicians, or public figures

**The model is conservative:** It only flags tweets that *clearly* violate a criterion. When in doubt, it does not flag.

## Usage

### Basic usage

```bash
python src/main.py --archive /path/to/twitter-archive/data/ --config config.json
```

The `data/` directory should contain `tweets.js` and `account.js`. These are inside the ZIP file X sends you — extract the archive first.

### With custom output directory

```bash
python src/main.py --archive /path/to/twitter-archive/data/ --config config.json --output ./my-output
```

### Example run

```
$ python src/main.py --archive ~/Downloads/twitter-archive/data --config config.json
INFO: Loading tweets from ~/Downloads/twitter-archive/data/tweets.js
INFO: Loaded 2,543 tweets
INFO: Evaluating batch 1/26... (flagged: 3, failed: 0)
INFO: Evaluating batch 2/26... (flagged: 7, failed: 0)
...
INFO: Done! 847 tweets flagged, 12 failed.
INFO: Results written to ./output/
```

## Output

All output files are written to `./output/` by default:

- **`flagged.csv`** — tweets flagged for review
  - Columns: `tweet_url`, `reason`, `deleted`
  - The `deleted` column starts as `false` and you can update it manually as you delete tweets
  - Example row: `https://twitter.com/user/status/1234567890,Crypto promotion,false`

- **`failed.csv`** — tweets that could not be evaluated after all retries
  - Columns: `tweet_url`, `id_str`, `error`
  - Use this to debug or manually review tweets that failed

- **`checkpoint.json`** — internal progress tracking (do not edit)
  - Tracks which tweets have been evaluated to enable resuming

## Resuming a run

Progress is saved to `output/checkpoint.json` after every batch. If the script is interrupted — by a rate limit, a crash, or manually — just rerun the same command:

```bash
python src/main.py --archive /path/to/twitter-archive/data/ --config config.json
```

It will automatically skip already-evaluated tweets and continue from where it stopped. No additional flags needed.

## Troubleshooting

### Authentication error

**Problem:** `Error: Invalid API key`

**Solution:** Check that your Gemini API key in `config.json` is correct and active. Generate a new one at [Google AI Studio](https://aistudio.google.com) if needed.

### Rate limiting

**Problem:** Script pauses with `429 Too Many Requests`

**Solution:** This is normal on the free Gemini tier. The script automatically retries with exponential backoff:
- 30s → 60s → 120s → 240s → 480s

On a large archive, processing may span multiple days. Your progress is saved — simply rerun the command to resume.

### CSV duplicates

**Problem:** Running the script twice created duplicate rows in `flagged.csv`

**Solution:** The checkpoint and CSV are kept in sync. If you delete both `checkpoint.json` and `flagged.csv` before rerunning, you start fresh. If you delete only one, duplicates can occur. Always delete both if starting over.

### Tests fail

**Problem:** `pytest` fails after setup

**Solution:** Check that:
- Your Gemini API key in `config.json` is valid (tests require it)
- Python 3.11+ is installed (`python --version`)
- All packages installed: `pip install -r requirements.txt`