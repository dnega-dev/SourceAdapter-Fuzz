# AGENTS.md: working rules for AI agents and contributors

These rules apply to every repository owned by **dnega-dev**, whichever tool is doing the work: Codex, Claude Code, Cursor, Copilot, Hyperagent or a person.

- **Canonical source:** `dnega-dev/app-foundry`, the repo that scaffolds new apps. See `standards/STANDARD.md` for the full method, `standards/case-studies/` for worked examples, and `standards/ADOPTION.md` for which repos carry this file.
- This file is self-contained, so it still works without access to that repo.
- **Which tools read what:** Codex, Cursor, Windsurf, Cline, Jules, Amp, VS Code agents and Hyperagent read this file directly. The companion files below point here:
  - `CLAUDE.md` for Claude Code;
  - `GEMINI.md` for Gemini CLI;
  - `.github/copilot-instructions.md` for GitHub Copilot.
- **Project specifics** are at the bottom of this file. Where they conflict with the general rules, the project rules win.

---

## 1. Non-negotiables

1. **Evidence before claims.**
   - Never say something works, is fixed, is deployed or is live without evidence from *this* session that someone else can re-run: command output, a test result, a commit SHA, a measurement link or a URL.
   - "Should work" is not a status.
2. **Verify from the owner's vantage point.**
   - The owner is in Perth, Western Australia. Checks from US or EU servers alone don't prove what the owner or their customers see.
   - Use Australian probes or resolvers for anything about reachability, DNS or deploys (see §4), and name the locations you tested.
3. **Reproduce before fixing.**
   - Prove the root cause with the real toolchain: the real compiler, the real host behaviour, the real file bytes.
   - Don't fix what you have only reasoned about.
4. **Every fix gets a test that fails without it.**
   - Write the test, then break the fix on purpose and confirm the test goes red. Restore the fix and confirm green.
   - A test that passes either way proves nothing. Run new tests several times to catch flakiness.
5. **Smallest correct change, one concern per PR.**
   - Keep copy and content verbatim unless the owner approved new wording.
   - No drive-by refactors.
6. **Missing data is never a pass.**
   - A check that couldn't run reports WARN or UNKNOWN, never PASS.
   - In product code, missing data renders as unavailable, never as zero, green or "compliant".
7. **Nothing fabricated.** No invented clients, figures, results, prices, citations or test outcomes. If it isn't verified, say so.
8. **Correct yourself loudly.** When an earlier claim or diagnosis turns out wrong, say so first, record the correction in the repo's docs, and add the lesson to this file or the project docs so it can't recur.

## 2. The change loop (every change)

```
reproduce → root cause → minimal fix → test → mutation check (fix off = red) → full local suite
→ branch + push → verify the pushed bytes match what you tested → PR (what / why / evidence / rollback)
→ CI + deploy preview verified on the exact head commit → address every review finding
→ merge with the head SHA pinned → verify production → update docs and state
```

- **Merging to the production branch:** only when the owner asked you to complete the work end to end, or approved the merge. Otherwise open the PR with the evidence and stop.
- **Wait for automated reviewers to finish.**
  - A stalled review (still "running", nothing posted) is **not** a clean review. Re-trigger it with an `@codex review` comment.
  - Treat each finding as a hypothesis: reproduce it, fix it with a test, and reply on the thread with the commit and the evidence.

## 3. Verification standard

- **Know exactly which version you tested:** a commit SHA, a build hash, or a version endpoint (for example `/version.json` emitted at build time).
- **Previews:** wait until the preview serves the head commit, then run the checks against it.
- **Production:** wait until production serves the merged commit, then run the full suite or health checks against the real URL.
- **CI green is necessary, not sufficient.** Functional checks beat presence checks. Look for:
  - broken links and anchors;
  - dead buttons;
  - blank states;
  - unhandled errors;
  - a 404 that shows the homepage;
  - missing chunks after a deploy.
- **Report what you didn't test,** and keep these separate: implemented, verified locally, verified on a preview, verified in production.

## 4. Diagnosis

- **Separate the layers** and test each one: registry → nameservers → zone → resolver (ISP, filters) → network → TLS → host → app.
- **Identify an unexpected IP by what it serves:** the TLS certificate issuer and SAN, and the reverse DNS. Don't go by who owns the IP block.
  - Example: an IP in Fly.io's range was a NextDNS block page, not an old host.
- **Interception test:** ask an authoritative nameserver for a name it doesn't host. An answer means DNS is being intercepted.
- **Measure timing before sizing retries or timeouts.**
  - Example: a cold start assumed to be under a second actually lasted 18–60 seconds.
- **Useful tools:**
  - Globalping (`api.globalping.io`, 250 free tests an hour; Perth and AU probes for DNS, HTTP and TLS);
  - DNS over HTTPS with a client subnet (`dns.google/resolve?...&edns_client_subnet=<AU prefix>`);
  - RDAP for domains and IPs;
  - OSV (`api.osv.dev`) for vulnerabilities.

## 5. Git and GitHub

- **Branches:** one branch per change, with a descriptive name. Don't force-push shared history.
- **Commits:** small and meaningful. **PRs** say what, why, the evidence, and how to roll back.
- **Never push binaries through text-only channels or APIs.**
  - They can be truncated silently. One migration cut every image to exactly 500,000 bytes.
  - Store small binaries as base64 text and decode them at build time, or use git or LFS directly.
  - After any migration, check binary sizes and structure (watch for suspicious round numbers).
- **When pushing through an API**, compare each file's git blob SHA (`sha1("blob <len>\0" + bytes)`) with what the remote returns.
- **Integration limits seen with API-based GitHub access:**
  - writes under `.github/workflows/` can return 403;
  - multi-file pushes (git trees) can return 403;
  - reading check runs can return 403.
  - Instead: write one file at a time, and read the host bot's PR comment for preview status.
- **If CI results can't be read, report CI as UNKNOWN.** `mergeable_state: "clean"` only means no *required* check is blocking. Optional, skipped and neutral jobs still pass that test, so it doesn't prove the tests ran or passed. Get real evidence: run the suite yourself on the head commit (locally or against the deploy preview) and cite that.
- **Never commit secrets.** Run a secret scan on changes before pushing.

## 6. Security and reliability baseline (web projects)

- **Headers:** CSP, X-Frame-Options or frame-ancestors, nosniff, Referrer-Policy, Permissions-Policy, HSTS. Keep a test that loads every route under the real CSP.
- **Build output:** no source maps in production, and no secrets in the client bundle.
- **Caching:** immutable caching only for content-hashed files. Missing hashed files must return **404**, not the SPA shell.
  - A tab opened before a deploy must recover: reload once on a chunk-load error, plus an error boundary.
- **Routing:** unknown routes render a real 404 with noindex.
- **Serverless or AI endpoints:**
  - same-origin check, rate limiting, input-size and role validation, capped output;
  - never log message content;
  - streamed responses must signal interruption, not just close.
- **Email domain:** MX, SPF, DKIM (know who controls each key), DMARC (`quarantine` or `reject` once mail flows). Contact addresses must actually receive mail.
- **Dependencies:** lockfile committed; reproducible install (`npm ci`, `yarn install --immutable`, `pip` with hashes); weekly automated minor/patch updates; plan major upgrades deliberately.
- **Accessibility:** every control has an accessible name at every breakpoint. Use visually-hidden text, never `display:none`, for the only label.

## 7. Working with the owner (Dan Nega)

- Be direct and concise. No apologies, no filler, no stacked questions. Ask at most one question, and only when genuinely blocked.
- If the request is "complete it", execute end to end. If it's analysis only, report and stop.
- Lead with the outcome, then what the owner must do, then the evidence.
- Collect owner-only actions (dashboards, DNS, accounts, legal, payments) into **one prioritised list** (P1 this week, P2 this month, P3 when convenient). Give each item a direct clickable URL and two or three steps. The owner can't type into an agent's browser session.
- Before handing off a long session, leave a resume note: state, tested SHAs, what failed, open items and the exact next step.
- **Copy rules for any AXUM or mining-sector product:**
  - describe outcomes, not implementation;
  - frame automation as **worker safety**, never as reducing headcount;
  - never claim unverified capabilities, deployments or customers.

## 8. Environment gotchas seen before

- **Sandboxes may block registry.npmjs.org.** Rebuild `node_modules` from `package-lock.json` through jsDelivr (`data.jsdelivr.com/v1/packages/npm/<name>@<version>?structure=flat` plus `cdn.jsdelivr.net/npm/...`).
- **Sandboxes may intercept TLS and ignore `curl --resolve`.** Read certificates from external probes.
- **Netlify:**
  - `netlify.toml` overrides the UI, but empty strings (`base = ""`) are stripped. Use explicit values (`base = "/"`).
  - Deploy previews inject a feedback drawer iframe, which can break CSP tests and cover bottom-right UI. Production doesn't inject it.
  - AI Gateway credentials can be missing for 18–60 seconds or more on cold function instances. Retry from the client; log which variable *names* exist.
- **Node's test reporter output differs between versions.** Pin it (`--test-reporter=tap`) before parsing.
- **Don't kill processes with `pkill -f`.** Kill by PID instead; `pkill -f` can match and kill your own shell.

---

## Project specifics

<!--
PROJECT-SPECIFIC SECTION: keep it current. Record only facts you have verified. Fill in:
- What this repo is, who uses it, and its status (production / prototype / archive)
- How to install, build, test and run it (exact commands)
- Deploy target, production URL and branch, preview URLs
- Key files and directories
- Decisions that need the owner, and standing constraints
- Known pitfalls and past incidents (date, symptom, root cause, fix)
-->

_Not documented yet._ The first agent to do substantive work here should fill this in from verified facts, in the same PR as that work.
