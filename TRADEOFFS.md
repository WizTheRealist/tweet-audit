# Design Tradeoffs

This document explains key architectural decisions and their tradeoffs.

## Architecture — CLI script vs web app/service

**Decision:** Single-user CLI script with file-in/file-out workflow.

**Rationale:** This is a personal tool for auditing and deleting your own tweets. A web framework, authentication layer, or service adds complexity with zero practical benefit.

**Tradeoff:** Less convenient than a web UI, but simpler to deploy and maintain.

---

## Language — Python

**Decision:** Python with Gemini SDK, Pydantic, and CSV.

**Rationale:** All three are first-class citizens in Python. No language would be meaningfully better for this task.

**Tradeoff:** None worth noting. This is the obvious fit.

---

## Batching — 100 tweets per request vs one at a time

**Decision:** Batch 100 tweets per API call, with fallback to smaller batches on error.

**Rationale:** Fewer API calls means faster processing and lower costs. 100 is conservative enough to stay under token limits.

**Tradeoff:** Larger batches risk schema drift — the model might reorder results or skip items under load. Processing one at a time is safer but hits rate limits faster and costs more. Batching 100 is a middle ground.

---

## Fallback strategy — full batch → halves → individual

**Decision:** On batch failure, try half the batch, then quarter, then one at a time.

**Rationale:** Recovers as many tweets as possible from a partially-broken batch. A single bad tweet shouldn't block all 100.

**Tradeoff:** A pathological case (e.g., a tweet with invalid JSON) can trigger many retries: full batch fails → 2 halves → 4 quarters → up to 100 individual requests. This wastes API quota but maximizes recovery. The alternative (fail the whole batch) would lose data.

---

## Error handling — retry and continue vs fail fast

**Decision:** Retry with backoff, log failures to `failed.csv`, and continue processing.

**Rationale:** This is a long-running pipeline. A single flaky network call shouldn't block the entire job. Users can review failed tweets manually later.

**Tradeoff:** You might silently accumulate failures and not notice until the end. If the API is down for an hour, you won't know until you finish batch 50 of 100. The alternative (fail on error) is safer but worse for resilience — you'd lose all progress.

---

## Checkpointing — JSON file

**Decision:** Save the list of processed tweet IDs to `checkpoint.json` after every batch.

**Rationale:** Simple, human-readable, and works for typical archive sizes (under 10k tweets). Easy to inspect and debug.

**Tradeoff:** Writing the full ID list on every batch gets slower as the list grows. At 4,500 tweets it's negligible. At 100k tweets it would be noticeable (takes ~500ms per batch). A SQLite database would scale better but adds a dependency and debugging complexity. For the expected use case, the JSON file is the right choice.

---

## Incremental CSV writes — append mode

**Decision:** Write flagged tweets to `flagged.csv` in append mode, even during a run.

**Rationale:** You get partial output even if interrupted. If the script crashes mid-run, you still have all flagged tweets up to that point.

**Tradeoff:** If you rerun the script without clearing the CSV and checkpoint, the checkpoint prevents re-evaluation, but old CSV rows are already there — you'll get duplicates. Users must manually delete both files if starting fresh, or duplicates will appear. The checkpoint file prevents duplicate *processing* but not duplicate *output*.

---

## Rate limiting — wait 30s–480s on 429 vs fail

**Decision:** On rate limit (HTTP 429), retry with exponential backoff: 30s → 60s → 120s → 240s → 480s.

**Rationale:** Resilient. The script never gets stuck in a permanent failure state. On the free Gemini tier, this is necessary — users might need to process a large archive across multiple days.

**Tradeoff:** A quota error can stall the script for several minutes (up to 8 minutes total across retries). The alternative (fail immediately) is faster but loses progress. For a background job, waiting is acceptable.

---

## Summary

Most tradeoffs prioritize **resilience and data recovery** over **speed and simplicity**. The tool is optimized for:
- Never losing progress (checkpoint + append CSV)
- Recovering from partial failures (batch fallback)
- Handling rate limits gracefully (exponential backoff)

The cost is occasional redundant API calls and the possibility of silent failures. For a personal audit tool that might run unattended for hours, this is the right set of priorities.
