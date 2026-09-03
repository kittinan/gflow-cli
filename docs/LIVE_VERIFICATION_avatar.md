# Live verification plan — Avatar / likeness generation

> **Status: NOT RUN.** This is a *plan*, not evidence. Every command below was
> deliberately left unexecuted: `gflow video avatar` and `gflow video r2v
> --avatar` spend Veo credits, and the account available during development is
> region-ineligible for Avatar, so a run would have proved nothing except the
> refusal path.
>
> Approve and run these in order. Record the outcome by copying this file to
> `docs/LIVE_VERIFICATION_v<next>.md` with a results column filled in.

## Why this feature needs live verification more than most

Every other selector family in `ui_automation_video.py` carries a live capture
date. `AVATAR_TAB_SELECTORS` does not, and says so in the source: Flow's Avatar
is verified-identity and region gated, `likeness:checkEligibility` answers
`["REGION"]` on this project's accounts, and the tab could therefore never be
opened to read its real DOM. The selector tiers follow the same discipline as
the verified families (structural Radix tab id → icon ligature scoped to the
open picker → bounded localized text), but their *values* are inferred.

**The first live run is the verification.** Treat step 1 as a probe, not as
routine use.

## Environment to record

| | |
|---|---|
| Date | |
| gflow version | |
| Profile | |
| Account locale (Flow UI language) | |
| Region | |
| Transport | `ui_automation` |
| Out dir | `tmp/lv-avatar/` |

## Step 0 — eligibility (FREE, no credits, run this first)

The whole plan is gated on this. If the account is ineligible, steps 1–4 can
only exercise the refusal path, which is what step 5 is for.

```bash
gflow auth status --profile <PROFILE>
```

Then confirm by hand, in a browser signed in as the same account:

1. Open <https://labs.google/fx/tools/flow> and enter a project.
2. Switch the composer to **Video**, then to the **Ingredients / References**
   sub-mode.
3. Click **Add Media** (`+`).
4. **Record what the tabs are**: their visible captions, and — via devtools —
   each tab's `id` attribute and its `<i class="google-symbols">` ligature text.

That fourth item is the single most valuable artefact this plan produces. Paste
it into the results file even if everything else passes; it is what converts
`AVATAR_TAB_SELECTORS` from inferred to verified.

If there is no Avatar tab, stop here and go to step 5.

## Step 1 — image Avatar (cheapest paid-path probe; images are credit-free)

```bash
gflow image avatar "cinematic portrait in Bangkok" \
  --profile <PROFILE> --out tmp/lv-avatar/ --json
```

Expected:

- exit 0, one JSON document on stdout with `"command": "image avatar"`.
- A PNG under `tmp/lv-avatar/` whose subject is the account's Avatar.
- `gflow data list images --profile <PROFILE>` shows the new asset.
- The operations row records mode `avatar` (not `t2i`).

Failure modes to record verbatim: exit 37 (`AvatarUnavailableError`) means the
tab was not found — attach `tmp/lv-avatar/debug_no_avatar_tab.png`. Exit 23
means the picker itself drifted.

## Step 2 — pure Avatar video (SPENDS VEO CREDITS)

Run this only after step 1 succeeds. Keep `--count 1`.

```bash
gflow video avatar "walking through Bangkok at night" \
  --profile <PROFILE> --count 1 --out-dir tmp/lv-avatar/ --json
```

Expected:

- exit 0, one JSON document with `"command": "video avatar"` and
  `"request": {"mode": "avatar"}`.
- An `.mp4` whose subject is the account's Avatar.
- In the structured log: `video_submode_entered sub=references` **before**
  `likeness_attached`, and `likeness_attached` before the submit stage.
- The generate request routes to `batchAsyncGenerateVideoReferenceImages` (the
  ingredients route), not `batchAsyncGenerateVideoText`.

To confirm the wire without a second spend, capture the outgoing request in
devtools during the run and record whether it carries `referenceLikenesses`.

## Step 3 — R2V with a reference image + Avatar (SPENDS VEO CREDITS)

```bash
gflow video r2v "walking with the referenced subjects" \
  --ref tmp/lv-avatar/subject.png \
  --avatar \
  --profile <PROFILE> --count 1 --out-dir tmp/lv-avatar/ --json
```

Expected:

- exit 0; the output shows **both** the reference subject and the Avatar.
- In the log: exactly ONE `video_submode_entered sub=references`, then the
  reference attach, then `likeness_attached` — neither attach replacing the
  other.
- The outgoing request carries `referenceImages` **and** `referenceLikenesses`
  together.

## Step 4 — non-English Flow account locale

Repeat step 1 (image only — it is credit-free) on a profile whose Chrome profile
language is **not** English, e.g. pt-BR. This exercises the locale tiers: the
Avatar tab and the include action are both localized.

Record which tier matched, from the structured log:

```bash
# tier actually used for the include action
grep include_selector_tier <logfile>
# selector that matched for the tab
grep 'selector_matched.*avatar_tab' <logfile>
```

A pass that lands on the **text** tier is a partial result: it means the
locale-free tiers are dead and the next non-covered language will break. Say so
in the results.

## Step 5 — an account or region WITHOUT Avatar

On a profile known to be ineligible (or the development account, which returns
`REGION`):

```bash
gflow video avatar "anything" --profile <INELIGIBLE_PROFILE> --json ; echo "exit=$?"
```

Expected:

- `exit=35`.
- A single JSON document with `"class": "AvatarUnavailableError"`,
  `"retryable": false`, and a remediation hint.
- **No video file**, and — critically — **no credits consumed**. Verify the
  credit balance in Flow's UI before and after.
- `gflow data list errors` (or the operations table) shows a FAILED row under
  mode `avatar`.

## Step 6 — regression smoke on the untouched paths

The avatar work threaded a new predicate through the shared video pipeline and
parameterised `_run_t2i`. These prove nothing else moved:

```bash
# credit-free
gflow image t2i "a quiet forest at dawn" --profile <PROFILE> --out tmp/lv-avatar/
gflow image i2i "make it cinematic" --ref tmp/lv-avatar/<some>.png --profile <PROFILE> --out tmp/lv-avatar/

# SPENDS CREDITS — one clip each, only if the budget allows
gflow video t2v "a golden sunset over mountains" --profile <PROFILE> --count 1 --out-dir tmp/lv-avatar/
gflow video i2v --initial-frame tmp/lv-avatar/<some>.png "slow push in" --profile <PROFILE> --count 1 --out-dir tmp/lv-avatar/
gflow video r2v "a knight in this armor walks forward" --ref tmp/lv-avatar/<some>.png --profile <PROFILE> --count 1 --out-dir tmp/lv-avatar/
```

Expected: unchanged behaviour, and for the three video runs **no**
`likeness_attached` event anywhere in the log.

## Results table (fill in)

| # | Check | Result | Evidence |
|---|---|---|---|
| 0 | Eligibility + real tab DOM captured | | |
| 1 | Image Avatar | | |
| 2 | Pure Avatar video | | |
| 3 | R2V + Avatar | | |
| 4 | Non-English locale | | |
| 5 | Ineligible account fails at exit 37, zero spend | | |
| 6 | t2i / i2i / t2v / i2v / r2v regression | | |
