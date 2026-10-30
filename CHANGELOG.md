# Changelog

## 2026-10-30

- Shorten the Codex credit segment to whole credits without a suffix, such as `Cx42,579`, while keeping decimals in the dropdown.
- Show zero usage for Grok's weekly and monthly allowances when the API omits the zero percentage, and use the included budget rather than extra spending for legacy usage.

## 2026-10-24

- Show remaining Codex credits in neutral menu-bar text after a usage limit is exhausted, label the dropdown balance `using credits`, and return to percentage meters after reset.

## 2026-09-23

- Read Codex renewal dates from the subscription entitlement, including cancellation dates and billing periods.
- Learn Claude renewal dates from observed extra-usage resets, with the plan anniversary and configured day as fallbacks.

## 2026-09-19

- Rename the plugin to LoopLabsBar and add Cursor, Grok Bot, and Grok usage providers.
- Make menu-bar providers and their order configurable while retaining every provider's dropdown section.
- Add a settings submenu with provider visibility toggles and shortcuts to the config file and plugin folder.
- Simplify submenu separators and remove the quit action.

## 2026-08-03

- Include banked Codex reset credits in weekly headroom, with an option to show the raw allowance instead.
- Track when Codex reset credits first appear to estimate their expiry and highlight approaching expirations.

## 2026-07-29

- Keep the last known menu-bar values visible during provider errors when cached data is available.
- Sanitize provider text before rendering SwiftBar lines to prevent injected parameters or menu rows.

## 2026-07-28

- Color rolling limits by consumption pace, estimate refills after missed resets, and explain the colors in the dropdown.
- Mark exhausted limits with a skull and propagate weekly exhaustion to the session indicator.

## 2026-07-23

- Keep Claude's extra-usage row visible when credits run out or extra usage is disabled.

## 2026-07-22

- Show separate status dots for each provider's session and weekly limits.
- Distinguish unlimited usage from unavailable data and refine the remaining-usage colors.
- Add optional monthly renewal rows with renewal days stored in the config file.

## 2026-07-15

- Cap rate-limit backoff at 15 minutes and show the age of cached provider data.

## 2026-07-14

- Support Codex plans that report only a weekly usage window.
- Separate provider segments in the menu bar with spaces.

## 2026-07-13

- Introduce a SwiftBar plugin showing Claude Code, Codex, and GitHub Copilot usage limits from existing local credentials.
