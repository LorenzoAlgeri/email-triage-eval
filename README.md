[Italiano](README.it.md)

# email-triage-eval

A command line tool that triages a shared customer inbox with the Claude API. For each email it gives a category, an urgency, a team, a draft reply and a one line reason. It never sends anything: drafts go into a file and a person checks them.

Claude reads the email and pulls out the facts. Then plain Python, under 200 lines of rules, decides what is urgent, who owns it and whether a draft can go out. An eval command checks the whole thing against a gold set I labelled by hand.

## A bike shop's shared inbox

I first built this for a practical test in a real hiring process. This repo is a rewrite for my portfolio. The company, the emails, the gold set and the prompts are new and made up.

The company here is Larkspur Cycle Works, a bike rental and repair shop that doesn't exist. Its inbox mixes rental changes, breakdowns on the road, workshop bookings, group quotes, supplier invoices, complaints, auto-replies and phishing. Whoever opens it in the morning looks for the stranded rider first, then sends the rest to the right team. Every reply has to avoid promising prices, times or refunds, because only a manager can. There is no particular reason for a bike shop: the original was a test of my skills, so the business only had to be invented.

Claude is good at reading messy emails. I didn't want it to be the place where rules like "never answer a supplier from the shared inbox", "no draft until we know which bike it is" or "a second chaser goes up one urgency level" live. Those must hold every time, so they are in code and they have tests.

## One model call per email, then rules in code

```text
data/inbox.json
     |
     v
signals.py   regex and calendar: reply depth, chaser phrases, money, bounces,
     |       "Wednesday 14 May" when 14 May is a Thursday
     v
threads.py   same sender -> RELATED or FOLLOW_UP; a bounce is linked by the subject it quotes
     |
     +-- bounce? --yes--> rules.decide_bounce(): inherit routing from the original, no model call
     |
     no
     v
llm.py       ONE call per email: client.messages.parse(output_format=Extraction)
     |         system prompt (company profile + field definitions), cache_control: ephemeral
     |         disk cache keyed by sha256(model, effort, system, schema, message)
     |         usage and cost logged per call
     v
rules.py     safety and escalation floors, follow-up bump, team matrix,
     |       intake merged from the thread, then the draft gate:
     |       SYSTEM/bounce -> IGNORE -> SUPPLIER -> missing intake -> house-rule validators -> READY
     v
output/<run>/results.json, results.md, costs.md
     |
     v
evaluate.py  compare with eval/gold_dev.json and eval/gold_holdout.json, field by field
```

| File | What it does |
|---|---|
| `triage/models.py` | `Extraction`, the only schema the model fills, plus my own `Signals`, `Thread`, `Result` |
| `triage/llm.py` | The one place that calls Claude: structured output, prompt caching, disk cache, schema retry, refusal handling, cost |
| `triage/rules.py` | House rules and the draft gate |
| `triage/signals.py`, `triage/threads.py` | Pre-processing in plain code, no model |
| `triage/fake.py` | Offline keyword stub with the same `messages.parse` signature as the SDK |
| `triage/evaluate.py` | Gold comparison with "acceptable" alternatives that must match on every field |
| `triage/prompts/` | System prompt, company profile, per-email user turn |

## Decisions I made

1. The schema asks for facts (`safety_risk`, `escalation_threat`, the three intake fields, the sender type) and for a proposed category, urgency, team and draft. `rules.py` then applies the floors and the gate and logs every override in `meta.corrections`. I didn't put the rules in the prompt. In the original project, a second run without temperature control changed 2 of 100 routing values and the wording of most drafts. With the rules in code I get the same decision on every run, and I can test it without a network.

2. I don't let a rule depend on a label the model picks. In the original, the gate let one request type skip the intake check, and that type was a label the model chose. On the first eval pass, one wrong row came from the model picking that label. Here the intake check applies to every REPAIR and RENTAL email, with no exceptions.

3. Some things a model does badly, so code computes them. Thread linking, bounce detection, the typo in a bounced address and weekday/date mismatches are code. A bounce of our own reply never reaches the model: it takes the routing of the email it answered. So there is one model call per email, minus bounces. In this copy a thread is just "same sender".

4. There are two caches. The system prompt is byte-identical on every call and has `cache_control: {"type": "ephemeral"}`, so from the second email on it is read from Anthropic's prompt cache. It is about 1.4k tokens by a characters/4 estimate, above the minimum cacheable length. I haven't counted it with the API yet. On top of that, every full reply is saved on disk under a sha256 of model, effort, system prompt, schema and message, so a repeated run, `--offline` and the tests cost nothing. The disk cache also means anyone can reproduce a result without an API key.

5. The default model is `claude-opus-5-5`, and `TRIAGE_MODEL` changes it. Thinking can't be switched off on this model, so I set the depth with `output_config={"effort": "low"}` (`TRIAGE_EFFORT` changes it). `max_tokens` is 8000 because thinking counts against it. The original used `thinking={"type": "disabled"}` on `claude-sonnet-5`. On `claude-opus-5-5` that returns a 400.

6. I wanted every failure to show up in the output.

   What happens when something goes wrong:

   - The SDK retries transient API errors by itself.
   - A reply that doesn't fit the schema or stops at `max_tokens` gets one retry. If the SDK can still return that reply, I add its tokens to the email's usage. If the SDK rejects the JSON inside `parse()`, the usage can't be read, so the call is counted as "unmetered" and `costs.md` says so.
   - A `refusal` stop reason is not retried.
   - A missing `ANTHROPIC_API_KEY` is an error too: the client is built only from that variable, so the SDK can't fall back to other credentials on the machine.
   - Any failure marks that email "review manually" and the run goes on.

   I tested these paths against the real SDK with a mocked HTTP transport.

One thing I got wrong. In the original I improved the prompt while looking at the same emails I used to measure it. Day 1 went from 23 of 25 rows exact to 25 of 25 after one prompt change, so that 25 of 25 says little. Today I would set aside, from the start, a group of emails I never look at while working on the prompt. This copy does that with the dev and holdout split.

## Try it in 5 minutes

You need Python 3.10 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest                      # 95 tests, all offline
```

### Offline, no key

```powershell
python -m triage demo
```

This runs the whole pipeline on the 23 emails with the keyword stub in place of Claude, then evaluates both gold splits. It writes `output/demo/results.md`, `costs.md`, `eval-dev.md` and `eval-holdout.md`. Every report from this command says `SIMULATED` at the top. The stub never writes to the disk cache, so later nobody can mistake its answers for Claude's.

### With a key

```powershell
copy .env.example .env                # then set ANTHROPIC_API_KEY in .env
python -m triage run --out output/live
python -m triage eval --results output/live/results.json --gold eval/gold_dev.json --out output/live/eval-dev.md
python -m triage eval --results output/live/results.json --gold eval/gold_holdout.json --out output/live/eval-holdout.md
python -m triage run --out output/live --offline   # replay from cache/, no API call
```

The key is read only from the environment. `.env`, `cache/` and `output/` are git-ignored.

## The eval

`data/inbox.json` has 23 synthetic emails over two days, written for this repo. Each sender is stored as a display name and a domain under the reserved `.example` TLD, so the repo contains no email addresses.

I split the gold set before any prompt work. `eval/gold_dev.json` (12 emails) is for improving the prompt, `eval/gold_holdout.json` (11 emails) is for the number you report. In the original project I iterated on the full gold set, and that is the main reason its headline number is optimistic.

Each gold entry has the expected category, urgency, team, draft status and thread link, plus optional `acceptable` alternatives. An alternative counts only if all its fields match. So "REPAIR for WORKSHOP" can be accepted where "COMPLAINT for MANAGEMENT" is expected, but "REPAIR for MANAGEMENT" can't. The report also checks the calendar and bounce flags, and counts READY drafts that contain a price or a time promise (the target is 0).

## Results

### This copy: not run live yet

I haven't made a single call to the Claude API with this code, dataset or prompts. The numbers below come from the offline keyword stub. They show that the pipeline, the rules and the eval run end to end. They say nothing about Claude's accuracy, and they are optimistic, because I wrote both the stub's keywords and the dataset.

| Split | Acceptable | Exact (4 fields) | Source |
|---|---|---|---|
| dev (12) | 11/12 | 10/12 | `output/demo/eval-dev.md`, keyword stub |
| holdout (11) | 9/11 | 8/11 | `output/demo/eval-holdout.md`, keyword stub |

### Original run, different dataset

These come from the original project, which used another made-up company and a 33-email dataset that I haven't published. The model was `claude-sonnet-5`, called through a third-party gateway that speaks the Anthropic protocol, not `claude-opus-5-5` like this copy.

| Measure | Value |
|---|---|
| Day 1, 25 emails, first prompt version | 23/25 rows exact |
| Day 1, after one general prompt change made while looking at the gold set | 25/25 |
| Day 2, 8 emails the prompt had never seen | 8/8 |
| Drafts containing amounts or time references | 0 on both days |
| Cost of the two committed runs | 0.25 USD |
| Total spend including prompt iterations and a variance test | 0.59 USD |
| Cost per email once the system prompt was cached | about 0.005 to 0.008 USD |
| Latency | about 5.5 s per email |
| Second run without the disk cache | 2 of 100 routing values changed |

The number I trust there is the 8/8 on emails the prompt had never seen. And 8 is a small sample.

## Not in this copy

The original dataset (33 emails), its gold set and its prompts are not here. The original also made an extra model call to confirm ambiguous thread pairs, and this copy doesn't.

## Limits

- Synthetic data only. I haven't measured anything on a real inbox.
- There is no CRM, so a known customer and a new one look the same, and a missing booking reference stays missing.
- An email with two requests goes to one team. `secondary_requests` records the second one, but nothing routes it.
- The intake rule and urgency pull in opposite directions: a stranded rider who gives no booking reference gets no written reply, only a "call them" action.
- Threading is "same sender" only. A colleague writing from the same company about the same case starts a new thread.
- Drafts are checked by regex for prices, time promises and refund words. Tone and correctness of a draft still need a person.
- Server-side refusal fallbacks (a beta for `claude-opus-5-5`) are not enabled. A refusal is reported and the email goes to manual review.
- I copied the price table in `triage/config.py` by hand. Check it against Anthropic's pricing page before you trust a cost report.

## License

MIT. See [LICENSE](LICENSE). Author: Lorenzo Algeri.
