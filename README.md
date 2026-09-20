# LoopLabsBar

All your AI coding limits in the macOS menu bar — **Claude Code, OpenAI Codex, Cursor, Grok Bot, Grok, and GitHub Copilot** — with zero credential prompts, ever.

```
🟢CC67│19🔴 Cx│39🟠 🟠Cu75│0☠️ Gb100🟢    ← one segment per provider, one dot per limit
```

`CC` Claude Code session│weekly · `Cx` Codex session│weekly · `Cu` Cursor total│API (monthly cycle) · `Gb` Grok Bot weekly · `Gk` Grok credits. Cursor, Grok Bot and Grok appear only once that login has produced data, so a tool you don't use never takes up menu-bar space. Choose which providers get a menu-bar segment (and in what order) with the `menubar` config key below — every provider keeps its dropdown section regardless.

All numbers are **% remaining** (how much you have left, not how much you used). Session/weekly meters (Claude's 5h/7d, Codex's own windows) are colored by **pace** — are you spending faster than a straight line to the next reset — not just the raw %, so "92% left" can still show yellow if you burned a chunk right after a reset: **🟢** on pace or better, **🟡** a little behind, **🟠** well behind (or ≤15% left regardless of pace), **🔴** way behind (or ≤5% left regardless of pace), **☠️** actually at 0% (a distinct glyph, not just another dot, so it can't be misread as healthy at a glance). If weekly dies, session shows ☠️ too — a dead weekly blocks you regardless of session headroom. Cursor is paced against its monthly billing cycle and Grok Bot against its weekly one, the same way. Meters with no reset line (Copilot, extra usage, model-scoped weekly caps) use a plain remaining-% scale instead: **🟢** ≥60%, **🟠** 20–59%, **🔴** <20%. A window the API doesn't report at all (e.g. Codex has no session window on some plans) drops its value and dot rather than showing a ball that would read as "all good"; a **–** means that provider has never returned data at all (first run, or a persistent failure with nothing cached yet) — a provider that's merely erroring right now but already has a cached payload (e.g. after a restart, while waiting out a rate-limit backoff) keeps showing its last-known numbers in the title too, exactly like the dropdown does. (SwiftBar allows only one text color on the menu bar title, so independent per-limit signaling uses emoji dots; the dropdown rows tint their numbers too, and an "ℹ️ How the colors work" row at the bottom of the dropdown explains all of this in the app itself.) Click it for details:

```
Claude Code
Session  49% left  ·  resets 15:20
Weekly   88% left  ·  resets Sun 03:00
Weekly   58% left  ·  Fable only
Extra    $12.34 of $300.00 left  (4%)

Codex (pro)
Session  78% left  ·  resets 19:03
Weekly   23% left  ·  resets Sat 10:20

Cursor (Pro)
Total    75% left  ·  resets Oct 19 10:48
Auto     82% left  ·  resets Oct 19 10:48
API       0% left  ·  resets Oct 19 10:48
         incl. $106.10 bonus usage on top of the $20 plan

Grok Bot (Grok Bot Plan)
Weekly   100% left  ·  resets Sat 17:18

Grok (SuperGrok)
Weekly   64% left  ·  resets Tue 09:00

Copilot (individual)
Premium  92% left  ·  1382 of 1500  ·  resets Aug 1
```

## Why

I built this after using [CodexBar](https://github.com/steipete/CodexBar), which is impressive in scope but didn't fit how I want to glance at limits:

- **It felt convoluted.** I want one fixed-width string and one dropdown, not a multi-provider app with modes and settings.
- **The menu bar only shows the active model.** I want *all* my session and weekly meters visible at once, side by side.
- **Inconsistent meter direction.** Limits count down toward 0 while extra usage counts up from $0. Here **everything reads as "how much is left"** — percentages and extra-usage dollars alike.
- **Worst of all: the recurring Keychain prompts.** Every CodexBar update re-signs the app, which invalidates its "Always Allow" Keychain approval, so the credential prompt keeps coming back. This plugin reads the Keychain through Apple's own `/usr/bin/security` — a binary that never changes — so you approve **once** and never see the prompt again. Codex and Copilot tokens live in plain config files; no Keychain involved at all. (This convenience has a real trade-off — see [Security](#security).)

## Install

```sh
brew install --cask swiftbar
mkdir -p ~/.swiftbar
curl -o ~/.swiftbar/looplabsbar.1m.py https://raw.githubusercontent.com/RichStone/LoopLabsBar/main/looplabsbar.1m.py
chmod +x ~/.swiftbar/looplabsbar.1m.py
defaults write com.ameba.SwiftBar PluginDirectory "$HOME/.swiftbar"
open -a SwiftBar
```

On the first refresh macOS asks for Keychain access — click **Always Allow**. That's the last prompt you'll ever see (understand what you're approving: [Security](#security)).

You're curl-ing a script that will run every minute — it's one file of dependency-free Python, so read it first.

Upgrading from the pre-rename `ai-usage.1m.py`? Delete that file from `~/.swiftbar`; the script moves the old `~/.cache/ai-usage-bar` and `~/.config/ai-usage-bar` directories to their `looplabsbar` names on first run.

Requirements: macOS with python3 (ships with the Xcode Command Line Tools) and whichever tools you use logged in — `claude`, `codex`, Cursor (IDE or `agent` CLI; Grok Bot rides on the same login), the Grok Build CLI (`grok login`), and/or Copilot in any editor. Providers you don't use just show a warning row; everything else keeps working.

## How it works

Every minute SwiftBar runs the script, which calls the same usage APIs the official `/usage` screens use:

| Provider | Endpoint | Auth source |
|---|---|---|
| Claude Code | `api.anthropic.com/api/oauth/usage` | Keychain item `Claude Code-credentials` via `/usr/bin/security` |
| Codex | `chatgpt.com/backend-api/wham/usage` | `~/.codex/auth.json` |
| Cursor | `api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage` (Connect-RPC, JSON) | `cursorAuth/accessToken` in Cursor's `state.vscdb` (plain sqlite, no prompt); falls back to the `agent` CLI's Keychain item `cursor-access-token` |
| Grok Bot | `api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus` | same Cursor token — Grok Bot is Cursor's desktop agent for xAI (bundle id `com.anysphere.sand`) and signs in with a Cursor account |
| Grok | `cli-chat-proxy.grok.com/v1/billing?format=credits` | `~/.grok/auth.json` written by `grok login` (`GROK_HOME` honored) |
| Copilot | `api.github.com/copilot_internal/user` | `~/.config/github-copilot/apps.json` (tries all entries, remembers the working one) |

Details worth knowing:

- **Claude is polled at most every 5 minutes** (the endpoint rate-limits under minutely polling); everything else every minute. On HTTP 429 the script backs off and serves cached numbers, marked "showing cached from …".
- **Codex windows are identified by duration** (5h vs 7d), not by their position in the response — the API nulls out unreported windows and promotes whatever remains, so position lies. A missing window means the API isn't reporting that limit at all (some plans currently get only a weekly window) and renders as "–  ·  not reported by the API".
- **Tokens are never refreshed by the script** (that would invalidate your CLI's session). If a token expires, the dropdown says so — running the CLI once fixes it.
- **Cursor reports three buckets** for its monthly cycle — total (everything, including the bonus usage Cursor grants beyond your plan), auto (auto-selected models) and API (named models). The title shows total│API because API is the one that runs dry first; the dropdown has all three.
- **Grok Bot's `usagePercent` is a real 0–100 percent** (its own UI divides it by 100 for display), one weekly window.
- **Grok is read through the Grok Build CLI's billing backend**, following [CodexBar's Grok provider](https://github.com/steipete/CodexBar/blob/main/docs/grok.md): `config.creditUsagePercent`, else `onDemandUsed/onDemandCap`, period from `currentPeriod` else `billingPeriod*`. This path was written from that documentation, not verified against a live Grok login — if your dropdown shows something odd there, open an issue with the (redacted) payload.
- **Nothing leaves your machine** except the HTTPS calls to the providers. Last-known-good data is cached in `~/.cache/looplabsbar/state.json`.
- If the script ever crashes, the menu bar shows `CC?│?-Cx?│?` and the dropdown shows the traceback with a retry item.

## Caveats

- **The APIs are undocumented and drift.** When a provider changes a response shape, the widget degrades visibly (a `–`, a warning row with data age, or a traceback in the dropdown) rather than crashing or showing wrong numbers — but it may need a small code fix to catch up.
- **Rate limits cost freshness, not uptime.** On HTTP 429 the widget serves cached data and retries within 15 minutes, ignoring absurd `Retry-After` values (14 hours has been observed).
- **Expired tokens aren't auto-refreshed** (deliberately — see Security). The dropdown will tell you to run the CLI once.
- **The icon-visibility fix runs at boot.** If SwiftBar is quit and reopened mid-session (e.g. by its updater), the icon can come back hidden until the next boot — see Troubleshooting.

## Troubleshooting

**Icon vanished after a reboot?** SwiftBar records the status item as removed every time it quits, so it can come back hidden. Fix (find your key name with `defaults read com.ameba.SwiftBar | grep Visible`):

```sh
killall SwiftBar
defaults write com.ameba.SwiftBar "NSStatusItem VisibleCC Item-1" -bool true
defaults write com.ameba.SwiftBar "NSStatusItem Preferred PositionCC Item-1" -float 1
open -a SwiftBar
```

`Preferred Position 1` also pins the icon to the rightmost slot macOS allows third-party items. For a permanent fix, wrap those lines in a launchd agent (or login script) that runs them before starting SwiftBar.

**Monthly plan renewal date.** Neither usage API exposes your billing-cycle renewal, so it's a manual setting that adds a "Renews <date>" row to each provider's dropdown (the widget computes the next occurrence and clamps to short months). Configure it in `~/.config/looplabsbar/config.json` — this survives re-downloading the script (Cursor, Grok Bot and Grok report their own cycle end, so they need no setting):

```json
{ "claude_renewal_day": 1, "codex_renewal_day": 10, "codex_count_reset_credits": true }
```

Omit a key (or the whole file) to hide that row.

**Menu-bar segments (`menubar`).** Which providers appear in the title, in that order — `claude`, `codex`, `cursor`, `grokbot`, `grok`, `copilot`. Default is all but Copilot (`Cp` premium-requests % left, opt-in):

```json
{ "menubar": ["claude", "codex", "grokbot"] }
```

Providers left out keep their dropdown section; only the title segment goes. As a fallback you can instead edit the `CLAUDE_RENEWAL_DAY` / `CODEX_RENEWAL_DAY` constants at the top of the script, but the config file is preferred since a re-download overwrites the script.

**Codex reset credits (`codex_count_reset_credits`, default `true`).** Each banked Codex rate-limit reset credit buys back a full window, so it's genuine weekly headroom. By default the widget folds them into the Codex weekly figure — e.g. `64% now + 1 reset` shows as **164% left**, with the breakdown in the dropdown. Set this to `false` to display the raw weekly number and a separate `Reset credits available: N` row instead.

Reset credits expire ~30 days after they're granted, but the API only ever returns a *count* — no per-credit dates. So the widget keeps a small local ledger (in the state cache) that stamps each credit when it first appears and estimates its expiry, shown as `expires ~Sep 2` and turning orange within 3 days. The estimate can only start when the widget first sees a credit, so a credit banked before you installed reads as expiring later than it truly does.

**Everything easy to tweak** at the top of the script: poll interval, flat color thresholds in `color_for()`, and the pace-based `PACE_YELLOW`/`PACE_ORANGE`/`PACE_RED`/`ABS_ORANGE`/`ABS_RED` constants that grade session/weekly meters. Renewal days live in the config file above.

## Security

An honest assessment — including the parts that should give you pause.

**Credit where it's due.** Of these tools, Claude Code has the most secure token storage by default: it puts its OAuth token in the macOS Keychain instead of a plain file on disk (Cursor's `agent` CLI does too, but the Cursor IDE also keeps the same token in a plain sqlite file, which is what we read first). That's exactly why Claude is the only provider with a permission prompt to deal with at all — and why this tool's central trade-off (below) exists: for convenience, we partially flatten Claude's stronger default down to the level the others chose from the start.

**What happens to your tokens.** On each refresh the script reads a token, holds it in process memory for a single HTTPS request to the provider that issued it, and exits. Tokens are never written to disk, never logged, and never sent anywhere except hardcoded endpoints (`api.anthropic.com`, `chatgpt.com`, `api2.cursor.sh`, `cli-chat-proxy.grok.com`, `api.github.com`) over TLS via the system trust store. The script is one file of stdlib-only Python — no pip dependencies, so the supply chain you need to trust is this file plus SwiftBar. Nothing auto-updates and the script cannot fetch or execute remote code; updates only happen when you pull them.

**The real trade-off: "Always Allow" on the Keychain.** This is the one place the tool *weakens* your default security posture. Claude Code stores its OAuth token in the Keychain precisely so that each app must be individually approved to read it. Approving `/usr/bin/security` permanently removes that tripwire: afterwards, **any process running as your user can read the Claude Code token silently** with the same one-liner this script uses — the prompt that would have flagged it is exactly what you disabled. Two things put that in perspective rather than excuse it: (1) your Codex, Cursor IDE, Grok and Copilot tokens already sit in plain files (`~/.codex/auth.json`, Cursor's `state.vscdb`, `~/.grok/auth.json`, `~/.config/github-copilot/apps.json`) that any user-land process can read — that is those tools' default token storage on every machine, not something this plugin sets up — after Always Allow, the Claude token is simply as exposed as the others already are; (2) malware running as your user has many comparable options anyway. But if an attacker-shaped process on your Mac is in your threat model, don't grant Always Allow — click Allow per prompt or remove the Claude section from the script. A stolen token here doesn't expose your password, but it does let someone use these AI services as you and burn your quota — treat it as sensitive.

**How CodexBar handles the same problem.** It can't use this trick, because its Keychain access is tied to its own app signature: it offers a prompt-policy setting (controls *when* it asks, not the cause), a documented workaround of adding CodexBar.app to the Keychain item's Access Control list — narrower than our approach (only that one app can read the token) but fragile, since macOS drops the grant every time the app updates and re-signs — and a last-resort toggle to skip the Keychain entirely in favor of browser cookies or CLI probing. Granting `/usr/bin/security` is the opposite point on the same trade-off curve: broader access, but durable. Nothing gets you both narrow and permanent.

**What's cached on disk.** `~/.cache/looplabsbar/state.json` (file mode 600, directory mode 700, written via a `O_NOFOLLOW` temp file so it can't be redirected through a planted symlink) keeps the last successful API responses so the widget survives network hiccups. It contains no tokens, but the provider responses include account metadata — plan names, account IDs, and (for Codex) your email. Delete it anytime.

**Provider text is treated as untrusted input.** SwiftBar plugin lines are `text | param=value ...`, split on the first `|`; an unterminated quote in a value consumes the rest of the line. Any field a provider API (or a tampered cache file) controls — plan names, model names, error text — is passed through a small sanitizer before being printed, stripping `|` and newlines so it can't smuggle extra params (like a hidden click-to-run action) or fabricate new rows.

**Unofficial APIs.** All of these endpoints are internal/undocumented — the same read-only calls the official `/usage` screens make, but not a supported integration. They can change shape or disappear without notice (one already changed shape once during development), and strictly speaking, internal endpoints may not be covered by the providers' terms of service. Read-only usage polling is a mild case, but you should know it's not blessed.

**Residual gaps, stated plainly:** the Always Allow exposure above is permanent until you delete the Keychain item's ACL entry (or the item itself); the script trusts whatever is in the local auth files without verifying what process put it there; and SwiftBar executes any script in your plugin folder, so that folder's write permissions are part of your attack surface.

## Credits

Claude, Codex, Copilot and Grok endpoints discovered by reading the source of [steipete/CodexBar](https://github.com/steipete/CodexBar); the Cursor and Grok Bot RPCs by reading Grok Bot's own bundle. Built with [SwiftBar](https://github.com/swiftbar/SwiftBar).

## License

[MIT](LICENSE)
