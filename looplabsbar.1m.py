#!/usr/bin/env python3
# <xbar.title>LoopLabsBar</xbar.title>
# <xbar.version>v2.0</xbar.version>
# <xbar.author>Rich Steinmetz</xbar.author>
# <xbar.author.github>RichStone</xbar.author.github>
# <xbar.desc>Claude Code, Codex, Cursor, Grok Bot, Grok and GitHub Copilot usage limits (% remaining) from the official usage APIs. No credential prompts: Codex/Cursor/Grok/Copilot auth comes from plain local files, Claude's from the Keychain via /usr/bin/security (one-time Always Allow).</xbar.desc>
# <xbar.abouturl>https://github.com/RichStone/LoopLabsBar</xbar.abouturl>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>

import calendar
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

FIVE_HOUR_SECONDS = 5 * 3600
SEVEN_DAY_SECONDS = 7 * 86400

CACHE_FILE = os.path.expanduser("~/.cache/looplabsbar/state.json")
CLAUDE_POLL = 5 * 60  # Anthropic's usage endpoint 429s under 1-minute polling; ask it less often

# Monthly billing-cycle renewal day (1-31) per provider, shown in the dropdown as
# the next occurrence (neither usage API reports it). Configure without editing this
# file — a re-download would overwrite it — by creating the JSON config below with
# {"claude_renewal_day": 1, "codex_renewal_day": 10}. The constants are the fallback
# when a key is absent; None hides the row. Days past a month's length clamp to its
# last day (e.g. 31 -> Feb 28).
CONFIG_FILE = os.path.expanduser("~/.config/looplabsbar/config.json")
CLAUDE_RENEWAL_DAY = None
CODEX_RENEWAL_DAY = None
# Which providers get a segment in the menu-bar title, in this order. Override with
# "menubar": [...] in the config file (every provider still gets its dropdown section).
MENUBAR_DEFAULT = ["claude", "codex", "cursor", "grokbot", "grok"]   # "copilot" is opt-in

# Cursor's Connect-RPC backend. The same bearer token the Cursor IDE/CLI hold
# answers both Cursor's own plan usage and Grok Bot's weekly usage (Grok Bot is
# built by Cursor — bundle id com.anysphere.sand — and signs in with a Cursor account).
CURSOR_RPC = "https://api2.cursor.sh/aiserver.v1.DashboardService/"
CURSOR_STATE_DB = os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage/state.vscdb")
# Grok Build CLI (`grok login`) token file, and the CLI's billing backend.
GROK_AUTH_FILE = os.path.expanduser(os.environ.get("GROK_HOME") or "~/.grok") + "/auth.json"
GROK_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def migrate_legacy_dirs():
    """One-time move of the pre-rename (ai-usage-bar) cache/config dirs into place."""
    for new, old in ((CACHE_FILE, "~/.cache/ai-usage-bar"), (CONFIG_FILE, "~/.config/ai-usage-bar")):
        new_dir, old_dir = os.path.dirname(new), os.path.expanduser(old)
        if os.path.isdir(old_dir) and not os.path.exists(new_dir):
            try:
                shutil.move(old_dir, new_dir)
            except OSError:
                pass


def load_state():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    cache_dir = os.path.dirname(CACHE_FILE)
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    os.chmod(cache_dir, 0o700)  # tighten if the dir already existed looser
    tmp = CACHE_FILE + ".tmp"
    # O_NOFOLLOW + mode 0o600 up front: cached responses hold account metadata
    # (email, plan, ids), so the tmp file must never be briefly world-readable
    # (default open() mode is 0644 pre-umask) or follow a planted symlink.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, CACHE_FILE)


def http_json(url, headers, data=None, method=None):
    req = urllib.request.Request(url, headers={"User-Agent": "LoopLabsBar", **headers}, data=data, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def fetch_claude(state):
    """Returns (data, error). Token read via Apple's security tool — silent after one Always Allow."""
    if time.time() - state.get("claude", {}).get("ts", 0) < CLAUDE_POLL:
        return None, None  # cache is recent enough; main() falls back to it silently
    # Honor the 429 backoff. Do NOT poke the endpoint every tick while stale — this
    # endpoint 429s hard under sub-minute polling and hammering it only lengthens
    # the ban. Staleness is instead papered over by rolling windows forward locally
    # (see _roll) so the display stays sane until the backoff lets a real fetch land.
    if state.get("claude_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-s", "Claude Code-credentials", "-a", os.environ.get("USER", ""), "-w"],
            capture_output=True, text=True, timeout=25)
        if out.returncode != 0:
            return None, "keychain read denied"
        token = json.loads(out.stdout.strip())["claudeAiOauth"]["accessToken"]
        return http_json("https://api.anthropic.com/api/oauth/usage",
                         {"Authorization": f"Bearer {token}",
                          "anthropic-beta": "oauth-2025-04-20"}), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = e.headers.get("Retry-After", "")
            delay = int(retry) if retry.isdigit() else 300
            # Anthropic has been seen sending Retry-After: ~14 hours. Honoring that
            # literally starves the widget all night; a 15-min cap is polite enough.
            state["claude_backoff_until"] = time.time() + min(delay, 900)
            return None, "rate-limited by Anthropic"
        if e.code == 401:
            return None, "token expired — run Claude Code once to refresh"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)[:100]


def fetch_codex(state):
    if state.get("codex_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        with open(os.path.expanduser("~/.codex/auth.json")) as f:
            tokens = json.load(f)["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        return http_json("https://chatgpt.com/backend-api/wham/usage", headers), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            state["codex_backoff_until"] = time.time() + 300
            return None, "rate-limited by OpenAI"
        if e.code == 401:
            return None, "token expired — run codex once to refresh"
        return None, f"HTTP {e.code}"
    except FileNotFoundError:
        return None, "~/.codex/auth.json not found"
    except Exception as e:
        return None, str(e)[:100]


def cursor_token():
    """Cursor's bearer token, from the IDE's plain sqlite state DB (no prompt), else
    the CLI's Keychain item (one-time Always Allow, like Claude). Returns (token, err)."""
    for uri in (f"file:{CURSOR_STATE_DB}?mode=ro", f"file:{CURSOR_STATE_DB}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2)
            row = con.execute("select value from ItemTable where key='cursorAuth/accessToken'").fetchone()
            con.close()
            if row and row[0]:
                return row[0], None
        except sqlite3.Error:
            continue
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password",
                              "-s", "cursor-access-token", "-a", "cursor-user", "-w"],
                             capture_output=True, text=True, timeout=25)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip(), None
    except Exception:
        pass
    return None, "no Cursor login found — sign in to Cursor or run the agent CLI once"


def cursor_rpc(state, key, method):
    """POST an empty Connect-RPC JSON request to Cursor's DashboardService. Both the
    Cursor and Grok Bot providers go through here; `key` names the backoff slot."""
    if state.get(f"{key}_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    token, err = cursor_token()
    if not token:
        return None, err
    try:
        return http_json(CURSOR_RPC + method,
                         {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                          "connect-protocol-version": "1"}, data=b"{}", method="POST"), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            state[f"{key}_backoff_until"] = time.time() + 300
            return None, "rate-limited by Cursor"
        if e.code == 401:
            return None, "token rejected — sign in to Cursor again"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)[:100]


def fetch_cursor(state):
    """Cursor plan usage for the current monthly billing cycle (auto / API / total)."""
    return cursor_rpc(state, "cursor", "GetCurrentPeriodUsage")


def fetch_grokbot(state):
    """Grok Bot weekly usage (usagePercent 0-100, resets nextResetTimestampUtc)."""
    return cursor_rpc(state, "grokbot", "GetSandUsageStatus")


def fetch_grok(state):
    """Grok (xAI) subscription credits via the Grok Build CLI's billing backend, using
    the token `grok login` writes to ~/.grok/auth.json (a map keyed by scope URL, each
    entry carrying `key`). Endpoint + fields per steipete/CodexBar's Grok provider."""
    if state.get("grok_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        with open(GROK_AUTH_FILE) as f:
            root = json.load(f)
    except FileNotFoundError:
        return None, "no Grok CLI login found — run `grok login`"
    except Exception as e:
        return None, str(e)[:100]
    entries = [v for v in (root.values() if isinstance(root, dict) else []) if isinstance(v, dict) and v.get("key")]
    if not entries:
        return None, "auth.json has no token — run `grok login`"
    entry = entries[0]
    exp = reset_epoch(entry.get("expires_at"))
    if exp and exp <= time.time():
        return None, "token expired — run `grok login`"
    try:
        return http_json(GROK_BILLING_URL, {"Authorization": f"Bearer {entry['key']}",
                                            "x-xai-token-auth": "xai-grok-cli",
                                            "Accept": "application/json"}), None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            state["grok_backoff_until"] = time.time() + 300
            return None, "rate-limited by xAI"
        if e.code == 401:
            return None, "token rejected — run `grok login`"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)[:100]


def grok_usage(grok):
    """(used_percent, period_start_epoch, period_end_epoch) from the credits payload,
    any of them None when absent. Percent comes from config.creditUsagePercent, else
    onDemandUsed/onDemandCap; the period from currentPeriod, else billingPeriod*."""
    cfg = (grok or {}).get("config") or {}
    used = cfg.get("creditUsagePercent")
    if used is None:
        cap = ((cfg.get("onDemandCap") or {}).get("val")) or 0
        spent = (cfg.get("onDemandUsed") or {}).get("val")
        used = spent / cap * 100 if cap and spent is not None else None
    period = cfg.get("currentPeriod") or {}
    end = reset_epoch(period.get("end")) or reset_epoch(cfg.get("billingPeriodEnd"))
    start = reset_epoch(period.get("start")) if period.get("end") else reset_epoch(cfg.get("billingPeriodStart"))
    return used, start, end


def fetch_copilot(state):
    """Dropdown-only provider. Tries every oauth_token in Copilot's apps.json (editor logins
    accumulate there and stale ones 401), remembering which one worked."""
    if state.get("copilot_backoff_until", 0) > time.time():
        return None, "rate-limited, backing off"
    try:
        with open(os.path.expanduser("~/.config/github-copilot/apps.json")) as f:
            apps = json.load(f)
    except FileNotFoundError:
        return None, "no Copilot login found"
    except Exception as e:
        return None, str(e)[:100]
    tokens = [(k, v["oauth_token"]) for k, v in apps.items()
              if isinstance(v, dict) and v.get("oauth_token")]
    tokens.sort(key=lambda kv: kv[0] != state.get("copilot_token_key"))
    err = "no oauth tokens in apps.json"
    for key, tok in tokens:
        try:
            data = http_json("https://api.github.com/copilot_internal/user",
                             {"Authorization": f"token {tok}", "Accept": "application/json"})
            state["copilot_token_key"] = key
            return data, None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                err = "tokens rejected — sign in to Copilot in an editor"
                continue
            if e.code == 429:
                state["copilot_backoff_until"] = time.time() + 300
                return None, "rate-limited by GitHub"
            return None, f"HTTP {e.code}"
        except Exception as e:
            return None, str(e)[:100]
    return None, err


def pct(v):
    return "–" if v is None else str(round(v))


def age_text(seconds):
    return f"{seconds / 3600:.1f}h" if seconds >= 3600 else f"{max(seconds, 60) / 60:.0f}m"


def read_config():
    """User settings from CONFIG_FILE (renewal days etc.); {} if absent/unreadable."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def valid_day(val):
    """Coerce a config/constant value to a 1-31 day-of-month, or None if unusable."""
    try:
        d = int(val)
    except (TypeError, ValueError):
        return None
    return d if 1 <= d <= 31 else None


def next_renewal(day):
    """Next date on/after today falling on `day` of the month (clamped to the
    month's length), formatted 'Mon D'. None when the day isn't configured."""
    if not day:
        return None
    today = date.today()
    def on(y, m):
        return date(y, m, min(day, calendar.monthrange(y, m)[1]))
    d = on(today.year, today.month)
    if d < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        d = on(y, m)
    return d.strftime("%b %-d")


def left(used):
    """Convert a used-% into a remaining-%. Everything displayed is 'how much is left'."""
    return None if used is None else max(0.0, 100.0 - used)


def codex_windows(rate_limit):
    """Returns (session, weekly) windows identified by duration, either may be None.
    The API nulls out idle windows and promotes whatever remains to primary_window,
    so primary/secondary position says nothing about which window it is."""
    session = weekly = None
    for w in (rate_limit.get("primary_window"), rate_limit.get("secondary_window")):
        if not isinstance(w, dict):
            continue
        if (w.get("limit_window_seconds") or 0) >= 100_000:
            weekly = w
        else:
            session = w
    return session, weekly


def win_left(window):
    """Remaining % for a codex window; None when the API doesn't report that window
    (some plans only get a weekly limit — no session window exists to report)."""
    return left(window.get("used_percent")) if window else None


RESET_CREDIT_TTL = 30 * 86400  # Codex reset credits expire ~30 days after granting


def track_reset_credits(state, codex, now):
    """Maintain a local ledger of Codex reset-credit grant times. The API reports
    only a count, never per-credit expiry, so we stamp each newly-appeared credit
    with the time we first saw it and reconcile the list to the live count every
    fetch: extras are stamped `now` (their real grant may predate our first sight —
    the estimate can only start when we do), a shrunk count drops oldest-first, and
    anything past its TTL is pruned. No-op when Codex data is missing this cycle."""
    if not codex:
        return
    current = int(((codex.get("rate_limit_reset_credits") or {}).get("available_count")) or 0)
    led = state.setdefault("codex_reset_credits", {})
    grants = sorted(t for t in (led.get("grants") or []) if now - t < RESET_CREDIT_TTL)
    if len(grants) < current:
        grants += [now] * (current - len(grants))
    elif len(grants) > current:
        grants = grants[len(grants) - current:]  # assume the oldest was used or expired
    led["grants"] = sorted(grants)
    led["count"] = current


def reset_credit_expiry(state, now):
    """(date_str, days_until) for the soonest-expiring tracked reset credit, or
    (None, None). Estimated ~30 days after each credit was first observed locally."""
    grants = (state.get("codex_reset_credits") or {}).get("grants") or []
    if not grants:
        return None, None
    soonest = min(grants) + RESET_CREDIT_TTL
    return datetime.fromtimestamp(soonest).strftime("%b %-d"), (soonest - now) / 86400


# Everything is "% remaining". These thresholds and the palette are shared by the
# dropdown row text and the menu-bar status dots.
LOW, MID = 20, 60          # absolute remaining-% cutoffs for NON-windowed meters
                           # (Copilot quotas, credit balances) that have no reset line
COLOR_RED = "#e06c75"      # calm reddish
COLOR_ORANGE = "#d9902b"   # orangeish
COLOR_YELLOW = "#c9a227"   # muted gold

# --- pace-based severity for rolling windows ---------------------------------
# A rolling limit (Claude 5h/7d, Codex session/weekly) refills fully at reset, so
# the useful question isn't "how much is left" but "am I burning faster than it
# refills?" The even-burn line is 100% * (fraction of the window still ahead):
# right after a reset you should still have ~100%; at reset, ~0%. Sitting AT or
# ABOVE that line is green; how many percentage points you've fallen BELOW it
# grades yellow -> orange -> red. A hard absolute floor keeps a nearly empty meter
# hot even when it is minutes from resetting.
#
# Example (weekly = 100% over 7 days => ~14.3%/day of even burn): on day 1 you can
# spend up to ~14% and stay on the line (green); spend more and you dip below it
# (yellow), and the further below, the hotter — because at that rate you run dry
# before the week is out.
PACE_YELLOW = 6            # <=6pp behind the line still reads green (burst tolerance)
PACE_ORANGE = 9            # >9pp behind -> orange (was 12; catches "well behind" sooner)
PACE_RED = 30              # >30pp behind -> red
ABS_ORANGE = 15            # <=15% left is orange no matter the pace
ABS_RED = 5                # <=5% left is red no matter the pace

_SEV_ORDER = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
SEV_COLOR = {"green": None, "yellow": COLOR_YELLOW, "orange": COLOR_ORANGE, "red": COLOR_RED}
SEV_DOT = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}

# Plain-language explanation shown at the bottom of the dropdown (see main()).
# Keep this in sync by hand if the thresholds above change.
HOW_COLORS_WORK = [
    "Every number is % LEFT, not used — a bigger number is always better.",
    "Session/Weekly meters color by PACE: are you spending faster than a",
    "  straight line to the next reset, not just 'is the number big?'",
    "🟢 on pace or better   🟡 a bit behind   🟠 well behind   🔴 way behind",
    "Example: weekly just reset, but you already burned 8% in the first hour —",
    "  your 'even' pace is ~0% used so far, so you're already behind → 🟡,",
    "  even though '92% left' sounds great on its own.",
    "Regardless of pace: ≤15% left is always 🟠, ≤5% left is always 🔴 —",
    "  you're close to empty and pace stops mattering.",
    "0% left shows ☠️ instead of 🔴 — you're not just critical, you're out.",
    "If weekly hits ☠️, session shows ☠️ too — a dead weekly blocks you either way.",
    "Codex weekly folds in banked reset credits (each buys back a full window):",
    "  '164% left' = 64% now + one reset (+100%). Set codex_count_reset_credits",
    "  to false in the config to show the raw number instead.",
    "  Each credit's ~expiry is estimated locally (the API gives only a count), so",
    "  164% can drop back toward 64% when an unused credit lapses (~30 days).",
    "Cursor paces against its monthly billing cycle: 'Cu75│0' = 75% of total",
    "  usage left │ 0% of the named-model (API) bucket left. Grok Bot (Gb) is a",
    "  weekly meter; Grok (Gk) follows its billing period. These three only join",
    "  the menu bar once their login has produced data. Pick which providers sit",
    "  in the menu bar with \"menubar\": [\"claude\",\"codex\",\"cursor\",\"grokbot\",\"grok\",\"copilot\"]",
    "  in the config file (that order; Copilot is off unless listed).",
    "A dash (–) means that number failed to load this cycle — NOT that it's full.",
    "Other rows (Copilot, extra $, model-scoped weekly) use plain % left:",
    "  🟢 ≥60%   🟠 20-59%   🔴 <20%.",
]


def reset_epoch(value):
    """A reset time as epoch seconds (accepts epoch or ISO string), or None."""
    try:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = float(value)
        if isinstance(value, (int, float)):
            return float(value) / 1000 if value > 1e11 else float(value)  # epoch millis vs seconds
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _roll(window, used_key, reset_key, window_seconds, now):
    """Advance one rolling-window dict IN PLACE if its reset time has already passed.
    A passed reset means the window has refilled, so used%->0 and the reset jumps to
    the next period boundary; the dict is tagged _rolled so the UI can flag the value
    as an unconfirmed estimate. No-op when the reset is still ahead — i.e. whenever the
    data is current (a fresh fetch always reports a future reset). This is what keeps a
    cache that straddles a reset (API unreachable across the boundary) from showing
    stale pre-reset numbers, e.g. '1% left' hours after the weekly actually reset."""
    if not isinstance(window, dict) or not window_seconds:
        return
    reset = reset_epoch(window.get(reset_key))
    if reset is None or reset > now:
        return
    periods = int((now - reset) // window_seconds) + 1
    window[used_key] = 0.0
    window[reset_key] = datetime.fromtimestamp(reset + periods * window_seconds, tz=timezone.utc).isoformat()
    window["_rolled"] = True


def severity(remaining, reset_at=None, window_seconds=None, now=None):
    """'green'|'yellow'|'orange'|'red' for a meter, or None if remaining is unknown.
    With reset_at + window_seconds it grades by PACE (burn vs. the even-burn line)
    and returns the hotter of that and an absolute floor; without them it falls back
    to the absolute floor alone."""
    if remaining is None:
        return None
    abs_sev = "red" if remaining <= ABS_RED else "orange" if remaining <= ABS_ORANGE else "green"
    pace_sev = "green"
    if reset_at and window_seconds and now is not None:
        left_frac = max(0.0, min(1.0, (reset_at - now) / window_seconds))
        behind = 100.0 * left_frac - remaining      # >0 => spending ahead of the refill
        pace_sev = ("red" if behind > PACE_RED else "orange" if behind > PACE_ORANGE
                    else "yellow" if behind > PACE_YELLOW else "green")
    return max(abs_sev, pace_sev, key=_SEV_ORDER.get)


def color_for(remaining):
    """Absolute-only text color for non-windowed meters (no even-burn line)."""
    if remaining is None:
        return None
    if remaining < LOW:
        return COLOR_RED
    if remaining < MID:
        return COLOR_ORANGE
    return None


def cell(remaining, unknown, sev="green"):
    """(value, dot) for one limit in the menu bar title.
    - unknown (no data ever cached for this provider): a plain '–', never a
      healthy ball. Merely stale/erroring data with something cached does NOT
      count as unknown — it still shows its last-known number, same as the
      dropdown (which pairs it with a separate "stale" warning row instead).
    - reported-absent (remaining is None but the provider answered): this window
      isn't reported at all (e.g. Codex session on some plans) — omit the value
      and dot entirely rather than implying "all good" with a green ball.
    - zero (rounds to 0% left): ☠️ instead of the usual red dot — you're not just
      critical, you're actually out. A distinct glyph, not just another dot,
      so it can't be misread as a healthy ball at a glance.
    - a real value: the number plus its pace-colored dot (🟢/🟡/🟠/🔴)."""
    if unknown:
        return "–", ""
    if remaining is None:
        return "", ""
    if round(remaining) <= 0:
        return "0", "☠️"
    return str(round(remaining)), SEV_DOT.get(sev, "")


def fmt_reset(value):
    """Accepts ISO string, epoch seconds or epoch millis; returns local 'HH:MM' today,
    'Wed 14:00' within a week, 'Oct 19 10:48' beyond that."""
    try:
        epoch = reset_epoch(value)
        if epoch is None:
            return "?"
        dt = datetime.fromtimestamp(epoch).astimezone()
        today = datetime.now().astimezone().date()
        if dt.date() == today:
            return dt.strftime("%H:%M")
        if (dt.hour, dt.minute) == (0, 0):  # date-only values like Copilot's quota_reset_date
            return dt.strftime("%b %-d")
        if (dt.date() - today).days > 6:  # beyond a week a weekday name is ambiguous (monthly cycles)
            return dt.strftime("%b %-d %H:%M")
        return dt.strftime("%a %H:%M")
    except Exception:
        return "?"


def clean(text):
    """Strip SwiftBar's own line/param separators from a string we're about to
    print. SwiftBar splits a plugin line on its FIRST '|' and, on an unterminated
    quote, consumes the rest of the line as that param's value — so any untrusted
    text reaching a printed line unescaped (a provider API field, a poisoned cache
    entry) could smuggle extra params (e.g. a hidden bash= action) or fabricate
    whole new rows via an embedded newline. Every provider-derived value ends up
    in one of these lines, so sanitize at the print choke point instead of chasing
    every call site."""
    return str(text).replace("|", "¦").replace("\n", " ").replace("\r", " ")


def line(text, **params):
    # AppKit dims action-less (disabled) menu rows even when they have an explicit
    # color, so every row gets an action: real ones keep theirs, the rest get a
    # no-op click target to stay enabled and solid. Color pair = light,dark menus.
    parts = [p for p in [
        f"color={params.get('color') or '#000000,#ffffff'}",
        "font=Menlo size=12" if params.get("mono") else None,
        f"href={params['href']}" if params.get("href") else None,
        "refresh=true" if params.get("refresh") else None,
    ] if p]
    if not (params.get("href") or params.get("refresh")):
        parts.append("bash=/usr/bin/true terminal=false")
    return f"{clean(text)} | {' '.join(parts)}"


def main():
    migrate_legacy_dirs()
    state = load_state()
    now = time.time()

    cfg = read_config()
    claude_day = valid_day(cfg.get("claude_renewal_day", CLAUDE_RENEWAL_DAY))
    codex_day = valid_day(cfg.get("codex_renewal_day", CODEX_RENEWAL_DAY))

    claude, claude_err = fetch_claude(state)
    codex, codex_err = fetch_codex(state)
    cursor, cursor_err = fetch_cursor(state)
    grokbot, grokbot_err = fetch_grokbot(state)
    grok, grok_err = fetch_grok(state)
    copilot, copilot_err = fetch_copilot(state)

    for key, data in (("claude", claude), ("codex", codex), ("cursor", cursor),
                      ("grokbot", grokbot), ("grok", grok), ("copilot", copilot)):
        if data is not None:
            state[key] = {"data": data, "ts": now}
    save_state(state)

    claude = claude or state.get("claude", {}).get("data")
    codex = codex or state.get("codex", {}).get("data")
    cursor = cursor or state.get("cursor", {}).get("data")
    grokbot = grokbot or state.get("grokbot", {}).get("data")
    grok = grok or state.get("grok", {}).get("data")
    copilot = copilot or state.get("copilot", {}).get("data")

    # Ledger the Codex reset-credit count over time (persisted), so we can estimate
    # each credit's ~30-day expiry the API never exposes. Saved now — before windows
    # are rolled below — since rolled estimates are deliberately not persisted.
    track_reset_credits(state, codex, now)
    save_state(state)

    # Roll any window whose reset has passed forward to the current period, so a cache
    # that straddled a reset boundary stops showing stale pre-reset numbers. Applied
    # after save_state so the estimate is never persisted — the next successful fetch
    # replaces it with real data.
    if claude:
        _roll(claude.get("five_hour"), "utilization", "resets_at", FIVE_HOUR_SECONDS, now)
        _roll(claude.get("seven_day"), "utilization", "resets_at", SEVEN_DAY_SECONDS, now)
    if codex:
        for _rl in [(codex.get("rate_limit") or {})] + [
                (x.get("rate_limit") or {}) for x in (codex.get("additional_rate_limits") or [])]:
            for _w in (_rl.get("primary_window"), _rl.get("secondary_window")):
                if isinstance(_w, dict):
                    _roll(_w, "used_percent", "reset_at", _w.get("limit_window_seconds"), now)

    cc_s = left((claude.get("five_hour") or {}).get("utilization")) if claude else None
    cc_w = left((claude.get("seven_day") or {}).get("utilization")) if claude else None
    cx = (codex or {}).get("rate_limit") or {}
    cx_session, cx_weekly = codex_windows(cx)
    cx_s = win_left(cx_session) if codex else None
    cx_w = win_left(cx_weekly) if codex else None

    # Banked Codex rate-limit reset credits each buy back a full window (~+100%),
    # so they're real weekly headroom, not a footnote: fold them into the weekly
    # figure (64% now + 1 reset => 164% effective). Applied to the WEEKLY window —
    # it's the slow-to-refill one and the only one some plans report. Opt out with
    # "codex_count_reset_credits": false in the config file.
    count_resets = cfg.get("codex_count_reset_credits", True)
    cx_resets = int((state.get("codex_reset_credits") or {}).get("count") or 0)
    fold_resets = count_resets and cx_w is not None and cx_resets > 0
    cx_w_eff = cx_w + 100 * cx_resets if fold_resets else cx_w

    # Pace-based severity per window: how far the meter has fallen below its
    # even-burn line, hotter the further behind (see severity()). Claude's window
    # lengths are fixed (5h / 7d); Codex reports its own limit_window_seconds.
    cc_s_sev = severity(cc_s, reset_epoch((claude.get("five_hour") or {}).get("resets_at")) if claude else None,
                        FIVE_HOUR_SECONDS, now)
    cc_w_sev = severity(cc_w, reset_epoch((claude.get("seven_day") or {}).get("resets_at")) if claude else None,
                        SEVEN_DAY_SECONDS, now)
    cx_s_sev = severity(cx_s, reset_epoch((cx_session or {}).get("reset_at")),
                        (cx_session or {}).get("limit_window_seconds"), now)
    cx_w_sev = severity(cx_w_eff, reset_epoch((cx_weekly or {}).get("reset_at")),
                        (cx_weekly or {}).get("limit_window_seconds"), now)

    # Cursor: one monthly billing cycle, three buckets — "total" (everything incl.
    # bonus usage), "auto" (auto-picked models) and "API" (named models). Paced
    # against the cycle end, whose length the API reports directly.
    cu = (cursor or {}).get("planUsage") or {}
    cu_start, cu_end = reset_epoch((cursor or {}).get("billingCycleStart")), reset_epoch((cursor or {}).get("billingCycleEnd"))
    cu_window = (cu_end - cu_start) if cu_start and cu_end and cu_end > cu_start else None
    cu_total, cu_auto, cu_api = left(cu.get("totalPercentUsed")), left(cu.get("autoPercentUsed")), left(cu.get("apiPercentUsed"))
    cu_total_sev = severity(cu_total, cu_end, cu_window, now)
    cu_api_sev = severity(cu_api, cu_end, cu_window, now)

    # Grok Bot: a single weekly meter (usagePercent is 0-100, like its own UI shows it).
    gb_left = left((grokbot or {}).get("usagePercent")) if grokbot else None
    gb_start, gb_end = reset_epoch((grokbot or {}).get("currentPeriodStart")), reset_epoch((grokbot or {}).get("nextResetTimestampUtc"))
    gb_window = (gb_end - gb_start) if gb_start and gb_end and gb_end > gb_start else SEVEN_DAY_SECONDS
    gb_sev = severity(gb_left, gb_end, gb_window, now)

    # Grok (xAI subscription credits via the Grok CLI's billing backend).
    gk_used, gk_start, gk_end = grok_usage(grok)
    gk_left = left(gk_used) if grok else None
    gk_window = (gk_end - gk_start) if gk_start and gk_end and gk_end > gk_start else None
    gk_sev = severity(gk_left, gk_end, gk_window, now)

    # One status dot per limit, bookending each provider's two numbers: session's
    # dot on the left, weekly's on the right (│ is U+2502, not a literal pipe —
    # SwiftBar treats "|" as its parameter separator). SwiftBar allows only one
    # text color on the title, but emoji keep their own, so every meter signals
    # its pace independently: 🟢 on-track / 🟡 slipping / 🟠 behind / 🔴 way behind /
    # ☠️ out, nothing for a window the API doesn't report, and "–" when data is
    # unavailable.
    # "Unknown" (–) is reserved for genuinely no data ever cached — NOT for merely
    # stale/erroring data. A provider that's erroring but still has a cached payload
    # keeps showing its last-known numbers in the title, exactly like the dropdown
    # does (which shows the same numbers plus a separate "⚠ ... / showing data from
    # X ago" row) — the title used to blank to "–" whenever stale, which read as
    # "no idea" even though the dropdown one click away proved the number was known.
    cc_unknown = not claude
    cx_unknown = not codex
    cc_sv, cc_sd = cell(cc_s, cc_unknown, cc_s_sev)
    cc_wv, cc_wd = cell(cc_w, cc_unknown, cc_w_sev)
    cx_sv, cx_sd = cell(cx_s, cx_unknown, cx_s_sev)
    cx_wv, cx_wd = cell(cx_w_eff, cx_unknown, cx_w_sev)
    # A dead weekly limit blocks you regardless of session headroom — flag session
    # ☠️ too (only when it has a dot to override; a blank/absent session stays blank).
    if cc_wd == "☠️" and cc_sd:
        cc_sd = "☠️"
    if cx_wd == "☠️" and cx_sd:
        cx_sd = "☠️"
    # One segment per provider listed in the config's "menubar" (default: all but
    # Copilot). Claude/Codex show "–" while they have never had data; Cursor / Grok
    # Bot / Grok / Copilot stay out of the title until their login has produced data —
    # a permanent "–" for a tool you may never log into would just be menu-bar noise.
    # Cursor shows total│API (API = named models, the bucket that runs dry first);
    # Grok Bot, Grok and Copilot are single meters, dot on the right.
    segments = {"claude": f"{cc_sd}CC{cc_sv}│{cc_wv}{cc_wd}",
                "codex": f"{cx_sd}Cx{cx_sv}│{cx_wv}{cx_wd}"}
    if cursor:
        cu_tv, cu_td = cell(cu_total, False, cu_total_sev)
        cu_av, cu_ad = cell(cu_api, False, cu_api_sev)
        segments["cursor"] = f"{cu_td}Cu{cu_tv}│{cu_av}{cu_ad}"
    if grokbot:
        segments["grokbot"] = "Gb%s%s" % cell(gb_left, False, gb_sev)
    if grok:
        segments["grok"] = "Gk%s%s" % cell(gk_left, False, gk_sev)
    cp_prem = ((copilot or {}).get("quota_snapshots") or {}).get("premium_interactions") or {}
    if copilot and not cp_prem.get("unlimited") and cp_prem.get("percent_remaining") is not None:
        segments["copilot"] = "Cp%s%s" % cell(cp_prem["percent_remaining"], False,
                                             severity(cp_prem["percent_remaining"]))
    wanted = cfg.get("menubar", MENUBAR_DEFAULT)
    wanted = [w for w in wanted if isinstance(w, str)] if isinstance(wanted, list) else MENUBAR_DEFAULT
    title = " ".join(segments[k] for k in wanted if k in segments) or "LL"
    print(f"{clean(title)} | font=Menlo size=12")
    print("---")

    # --- Claude Code section ---
    print("Claude Code | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if claude:
        fh, sd = claude.get("five_hour") or {}, claude.get("seven_day") or {}
        fh_left, sd_left = left(fh.get("utilization")), left(sd.get("utilization"))
        fh_m = "~" if fh.get("_rolled") else ""   # ~ = estimated: window reset locally, awaiting a fresh fetch
        sd_m = "~" if sd.get("_rolled") else ""
        print(line(f"Session  {fh_m}{pct(fh_left)}% left  ·  resets {fmt_reset(fh.get('resets_at'))}",
                   color=SEV_COLOR.get(severity(fh_left, reset_epoch(fh.get('resets_at')), FIVE_HOUR_SECONDS, now)), mono=True))
        print(line(f"Weekly   {sd_m}{pct(sd_left)}% left  ·  resets {fmt_reset(sd.get('resets_at'))}",
                   color=SEV_COLOR.get(severity(sd_left, reset_epoch(sd.get('resets_at')), SEVEN_DAY_SECONDS, now)), mono=True))
        for lim in claude.get("limits") or []:
            if lim.get("kind") == "weekly_scoped":
                name = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
                lim_left = left(lim.get("percent"))
                print(line(f"Weekly   {pct(lim_left)}% left  ·  {name} only",
                           color=color_for(lim_left), mono=True))
        extra = claude.get("extra_usage") or {}
        if extra.get("is_enabled"):
            dp = extra.get("decimal_places", 2)
            used = (extra.get("used_credits") or 0) / (10 ** dp)
            limit = (extra.get("monthly_limit") or 0) / (10 ** dp)
            u_left = left(extra.get("utilization"))
            remaining = max(0.0, limit - used)
            print(line(f"Extra    ${remaining:,.2f} of ${limit:,.2f} left  ({pct(u_left)}%)",
                       color=color_for(u_left), mono=True))
        elif extra.get("credits_ever_enabled"):
            # Anthropic nulls the dollar fields and flips is_enabled off once extra
            # usage is exhausted/disabled — keep the row so it doesn't just vanish.
            reason = (extra.get("disabled_reason") or "off").replace("_", " ")
            spent = extra.get("disabled_reason") == "out_of_credits" or extra.get("spend_limit_reached")
            print(line(f"Extra    {reason}", color=(COLOR_RED if spent else None), mono=True))
    renewal = next_renewal(claude_day)
    if renewal:
        print(line(f"{'Renews':<8} {renewal}  ·  monthly plan", mono=True))
    if claude_err:
        print(line(f"⚠ {claude_err}", color="#febc2e"))
        if claude:
            print(line(f"showing data from {age_text(now - state.get('claude', {}).get('ts', now))} ago", color="gray"))
    print("---")

    # --- Codex section ---
    plan = f" ({codex.get('plan_type')})" if codex and codex.get("plan_type") else ""
    print(f"{clean(f'Codex{plan}')} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if codex:
        for label, w in (("Session", cx_session), ("Weekly", cx_weekly)):
            if w:
                wl = left(w.get("used_percent"))
                m = "~" if w.get("_rolled") else ""
                folded = w is cx_weekly and fold_resets
                shown = cx_w_eff if folded else wl
                sev = severity(shown, reset_epoch(w.get("reset_at")), w.get("limit_window_seconds"), now)
                print(line(f"{label:<8} {m}{pct(shown)}% left  ·  resets {fmt_reset(w.get('reset_at'))}",
                           color=SEV_COLOR.get(sev), mono=True))
                if folded:
                    plural = "s" if cx_resets > 1 else ""
                    exp_date, exp_days = reset_credit_expiry(state, now)
                    exp_txt = f"  ·  {'next ' if cx_resets > 1 else ''}expires ~{exp_date}" if exp_date else ""
                    sub_color = COLOR_ORANGE if (exp_days is not None and exp_days <= 3) else "gray"
                    print(line(f"{'':<8} {pct(wl)}% now + {cx_resets} reset credit{plural} (+{100 * cx_resets}%){exp_txt}",
                               color=sub_color, mono=True))
            else:
                print(line(f"{label:<8} –  ·  not reported by the API", mono=True))
        for extra_lim in codex.get("additional_rate_limits") or []:
            rl = extra_lim.get("rate_limit") or {}
            es, ew = codex_windows(rl)
            s, w = win_left(es), win_left(ew)
            s_txt = "–" if s is None else f"{pct(s)}%"
            w_txt = "–" if w is None else f"{pct(w)}%"
            print(line(f"{extra_lim.get('limit_name', 'other')}: session {s_txt} · weekly {w_txt} left",
                       color=color_for(min((v for v in (s, w) if v is not None), default=None)), mono=True))
        credits = codex.get("credits") or {}
        if credits.get("has_credits"):
            print(line(f"Credits  {credits.get('balance')}", mono=True))
        resets = (codex.get("rate_limit_reset_credits") or {}).get("available_count")
        if resets and not fold_resets:  # when folded, the weekly breakdown already shows it
            print(line(f"Reset credits available: {resets}", mono=True))
    renewal = next_renewal(codex_day)
    if renewal:
        print(line(f"{'Renews':<8} {renewal}  ·  monthly plan", mono=True))
    if codex_err:
        print(line(f"⚠ {codex_err}", color="#febc2e"))
        if codex:
            print(line(f"showing data from {age_text(now - state.get('codex', {}).get('ts', now))} ago", color="gray"))
    print("---")

    # --- Cursor section ---
    cu_plan = f" ({grokbot.get('cursorPlanName')})" if grokbot and grokbot.get("cursorPlanName") else ""
    print(f"{clean(f'Cursor{cu_plan}')} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if cursor:
        for label, val in (("Total", cu_total), ("Auto", cu_auto), ("API", cu_api)):
            print(line(f"{label:<8} {pct(val)}% left  ·  resets {fmt_reset(cu_end)}",
                       color=SEV_COLOR.get(severity(val, cu_end, cu_window, now)), mono=True))
        bd = cu.get("bonusSpend")
        if bd:
            print(line(f"{'':<8} incl. ${bd / 100:,.2f} bonus usage on top of the ${(cu.get('limit') or 0) / 100:,.0f} plan",
                       color="gray", mono=True))
    if cursor_err:
        print(line(f"⚠ {cursor_err}", color="#febc2e"))
        if cursor:
            print(line(f"showing data from {age_text(now - state.get('cursor', {}).get('ts', now))} ago", color="gray"))
    print("---")

    # --- Grok Bot section (Cursor's desktop agent for xAI; same Cursor login) ---
    gb_plan = f" ({grokbot.get('grokPlanLabel')})" if grokbot and grokbot.get("grokPlanLabel") else ""
    print(f"{clean(f'Grok Bot{gb_plan}')} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if grokbot:
        print(line(f"{'Weekly':<8} {pct(gb_left)}% left  ·  resets {fmt_reset(gb_end)}",
                   color=SEV_COLOR.get(gb_sev), mono=True))
        if grokbot.get("hasAvailableUsage") is False:
            print(line(f"{'':<8} no usage available on this plan", color=COLOR_RED, mono=True))
    if grokbot_err:
        print(line(f"⚠ {grokbot_err}", color="#febc2e"))
        if grokbot:
            print(line(f"showing data from {age_text(now - state.get('grokbot', {}).get('ts', now))} ago", color="gray"))
    print("---")

    # --- Grok section (xAI subscription credits via the Grok Build CLI login) ---
    gk_cfg = (grok or {}).get("config") or {}
    gk_tier = gk_cfg.get("subscriptionTier") or (grok or {}).get("subscriptionTier")
    gk_plan = f" ({gk_tier})" if gk_tier else ""
    print(f"{clean(f'Grok{gk_plan}')} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if grok:
        if gk_left is not None:
            label = "Weekly" if gk_window and gk_window <= 8 * 86400 else "Credits"
            print(line(f"{label:<8} {pct(gk_left)}% left  ·  resets {fmt_reset(gk_end)}",
                       color=SEV_COLOR.get(gk_sev), mono=True))
        else:
            print(line(f"{'Credits':<8} –  ·  usage not reported" + (f"  ·  resets {fmt_reset(gk_end)}" if gk_end else ""), mono=True))
    if grok_err:
        print(line(f"⚠ {grok_err}", color="#febc2e"))
        if grok:
            print(line(f"showing data from {age_text(now - state.get('grok', {}).get('ts', now))} ago", color="gray"))
    print("---")

    # --- Copilot section (dropdown only, deliberately not in the menu bar title) ---
    cp_plan = f" ({copilot.get('copilot_plan')})" if copilot and copilot.get("copilot_plan") else ""
    print(f"{clean(f'Copilot{cp_plan}')} | size=13 color=#000000,#ffffff bash=/usr/bin/true terminal=false")
    if copilot:
        names = {"premium_interactions": "Premium", "chat": "Chat", "completions": "Complete"}
        unlimited = []
        for qid, snap in (copilot.get("quota_snapshots") or {}).items():
            if not isinstance(snap, dict):
                continue
            label = names.get(qid, qid)
            if snap.get("unlimited"):
                unlimited.append(label)
                continue
            rem = snap.get("percent_remaining")
            print(line(f"{label:<8} {pct(rem)}% left  ·  {snap.get('remaining')} of {snap.get('entitlement')}"
                       f"  ·  resets {fmt_reset(copilot.get('quota_reset_date'))}",
                       color=color_for(rem), mono=True))
        if unlimited:
            print(line(f"{' & '.join(unlimited)}: unlimited", color="gray", mono=True))
    if copilot_err:
        print(line(f"⚠ {copilot_err}", color="#febc2e"))
    print("---")

    print(line(f"Updated {datetime.now().strftime('%H:%M:%S')} · refresh", color="gray", refresh=True))
    print(line("claude.ai usage settings", href="https://claude.ai/settings/usage", color="gray"))
    print("---")
    print(line("ℹ️  How the colors work", color="gray"))
    for tip in HOW_COLORS_WORK:
        print(line(f"--{tip}", color="gray", mono=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never exit non-zero: SwiftBar would replace the whole widget with a "?" icon.
        # Keep a placeholder title and put the traceback in the dropdown instead.
        import traceback
        print("CC?│? Cx?│? | font=Menlo size=12")
        print("---")
        print("plugin crashed | color=#ff5f57")
        for tb_line in traceback.format_exc().strip().splitlines():
            print(line(tb_line, color="gray", mono=True))
        print("---")
        print(line("retry now · refresh", refresh=True))
