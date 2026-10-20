# ECONITH — Bounded OpenAI Responses five-analyst research-only integration (20/10/2026)

**Draft, fully opt-in, not production.** This is a first genuinely callable multi-LLM adapter, NOT a benchmark of model performance, TradingAgents framework integration, portfolio profit, or broker execution. Strictly downstream of two-layer independently audited original Spot evidence. Defaults OFF.

## Why

Relative to TradingAgents v0.7.0, ECONITH's initial five-role technical/bull/bear/risk/portfolio layer was a typed contract only (PR #40), and PR #41 separately tested finite after-the-fact first-forward-candle directional quality. This branch adds a real **OpenAI Responses API** provider-specific adapter to those safety contracts, without installing any tools available to the model.

## Actual behavior

- Operators provide an already captured **private** source root with original SHA256 + 3 symbols × 2 intervals OHLCV audit. A fresh **future deadline** (10–180 seconds) conservatively defines the earliest valid research decision; every model response must finish before that same deadline. Source observed vintage must still be within 10 minutes of declared deadline. If the agent response arrives after deadline, the whole session is refused, not backdated into earlier PnL.
- At most **5 sequential requests** to the **operator-chosen** OpenAI model via pinned `openai==1.99.9` SDK's `Responses.create`, with `text.format=json_schema` strict, roles technical / bull / bear / risk / portfolio. Responses are validated locally for role-specific stance and max 1200-char explanation. CLI creates `OpenAI(..., max_retries=0, timeout=20)`, sets `store=False`, no tools and `tool_choice=none`. Each call caps model output at **240 tokens**; total requested maximum is 1200 output tokens for five calls. Actual input/output token usage is recorded and each response must report <=4096 input and <=240 output tokens.
- Prompt includes only certified metadata (symbol, interval, closed close, local source observed timestamps, evidence SHA), **not full private OHLCV archive**, secret keys, orders, amounts, wallet or portfolio credentials. Each role has an independent prompt digest; result journal saves model name, prompt/output SHA, local completion ms and token counts. Provider prompts and generated rationales are untrusted and never allowed to call execution interfaces.
- The **risk role can veto** any proposal, then `adjudicate_advisory(...,source_root=...)` independently verifies source original SHA and exact report timestamps; all five reports share the same source evidence SHA. Only a `RESEARCH_RATING_NON_EXECUTABLE`, `RESEARCH_HOLD_NO_ACTION` or `WITHHELD_RISK_VETO` can result. No API route to a broker, no order sizes, no live trade.
- Any schema, usage, deadline or consent violation fails closed. No output directory is created on failure. If some paid API calls completed before failure, **provider charges may still apply**; this is not a hard dollar-spend guarantee. A real (successful) five-agent run has **not** occurred within CI because no credentials or billable calls were used.

## Running explicitly on an operator-controlled machine

Set `OPENAI_API_KEY` privately in the runtime environment. Never paste the key into PR, CI, screenshot, chat, URL or shell history. Capture and independently qualify a **recent** finite Spot source bundle first. Then, when willing to accept API charges:

```bash
python -m ci.phase1_optin_openai_research_panel \
  --allow-billed-llm-research --acknowledge-usage-charges \
  --source-root /private/capture \
  --output-dir /private/llm-run-new \
  --symbol BTCUSDT --interval 1m \
  --model YOUR_AUTHORIZED_MODEL \
  --decision-window-seconds 120
```

Default: no network and no billable calls. Models, billing access and API key must be configured explicitly by the operator; no assumption any model is enabled. Output `llm_advisory.json` is a local, fsynced, source-rechecked report containing **no key**, usage, prompt hashes, model ID and research conclusions. No external exchange account is needed for this model-only step. If the source capture is too old for the decision deadline, execution stops. Do not use a historical archive whose `source_observed_after_ms` is after the planned research decision.

## Validation and limitations

GitHub Actions uses a fake Responses client **only**: mandatory consent, five strict JSON-schema calls, empty tools/no storage, token accounting, risk veto, Hold, wrong roles, inflated token usage, malformed responses, expired deadline and self-hashed source manipulation. **No model API calls or billing are made in CI**.

Future work: capture real provider latency and price-tariff schedule, enforce monetary budget with usage caps and per-role completion, redact rationales if later forwarded to external systems, audit repeatability with model/prompt revisions, label vendor data source vintage, then compare **actual observed** recommendations with no-AI baseline under a separate walk-forward evaluation. Defer long soak to the agreed unified multi-phase release-candidate campaign. No Phase 1 exit score increase: **81/100 provisional OPEN**, keep Draft/no main merge.
