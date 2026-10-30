import copy
import importlib.util
import io
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("looplabsbar", Path(__file__).with_name("looplabsbar.1m.py"))
bar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bar)
NOW = 1_800_000_000


def usage(session=None, weekly=100, balance="58061.3185810000", **credit_fields):
    def window(used, seconds):
        return None if used is None else {"used_percent": used, "limit_window_seconds": seconds,
                                          "reset_at": NOW + seconds // 2}
    return {
        "rate_limit": {"primary_window": window(weekly, bar.SEVEN_DAY_SECONDS),
                       "secondary_window": window(session, bar.FIVE_HOUR_SECONDS)},
        "credits": {"has_credits": True, "balance": balance, **credit_fields},
    }


def render(codex=None, *, grok=None, grokbot=None, cached=False, config=None):
    """Exercise the full SwiftBar output without network, credentials, or disk writes."""
    state = {"codex": {"data": copy.deepcopy(codex), "ts": NOW - 60}} if cached else {}
    output = io.StringIO()
    with ExitStack() as stack:
        for name, value in {
            "migrate_legacy_dirs": None, "load_state": state, "save_state": None,
            "read_config": {"menubar": ["codex"], **(config or {})},
            "fetch_codex_entitlement": None, "fetch_claude_profile": None,
            **{f"fetch_{provider}": (None, None) for provider in
               ("claude", "cursor", "grokbot", "grok", "copilot")},
            "fetch_grok": (copy.deepcopy(grok), None),
            "fetch_grokbot": (copy.deepcopy(grokbot), None),
            "fetch_codex": (None, "offline") if cached else (copy.deepcopy(codex), None),
        }.items():
            stack.enter_context(patch.object(bar, name, return_value=value))
        stack.enter_context(patch.object(bar.time, "time", return_value=NOW))
        stack.enter_context(redirect_stdout(output))
        bar.main()
    return output.getvalue().splitlines()


class CodexCreditsTest(unittest.TestCase):
    def test_exhausted_either_window_shows_neutral_credits(self):
        for session, weekly in ((None, 100), (20, 100), (100, 20), (100, None), (100, 100)):
            with self.subTest(session=session, weekly=weekly):
                rows = render(usage(session, weekly))
                self.assertEqual(rows[0], "Cx58,061.32cr | font=Menlo size=12")
                credit_row = next(r for r in rows if r.startswith("Credits  "))
                self.assertIn("58,061.32 left  ·  using credits", credit_row)
                self.assertIn("color=#000000,#ffffff", credit_row)
                self.assertNotIn("expires", credit_row)
                self.assertTrue(any("0% left" in r for r in rows))

    def test_available_limits_keep_percentage_meters(self):
        self.assertEqual(render(usage(20, 20))[0], "🟢Cx80│80🟢 | font=Menlo size=12")
        self.assertFalse(any("using credits" in r for r in render(usage(20, 20))))

    def test_unspendable_or_unknown_credits_keep_exhausted_meter(self):
        cases = [dict(balance=v) for v in (None, "", "bad", "NaN", "Infinity", "0", "-1")]
        cases += [{"has_credits": False}, {"overage_limit_reached": True}]
        for fields in cases:
            with self.subTest(fields=fields):
                self.assertEqual(render(usage(**fields))[0], "Cx│0☠️ | font=Menlo size=12")

    def test_small_and_unlimited_credit_balances(self):
        for fields, expected in (({"balance": "0.5"}, "0.5"),
                                 ({"balance": "0.001"}, "<0.01"),
                                 ({"unlimited": True, "balance": None}, "∞")):
            with self.subTest(fields=fields):
                self.assertEqual(render(usage(**fields))[0], f"Cx{expected}cr | font=Menlo size=12")

    def test_missing_credit_payload_keeps_exhausted_meter(self):
        data = usage()
        del data["credits"]
        self.assertEqual(render(data)[0], "Cx│0☠️ | font=Menlo size=12")

    def test_explicit_limit_reached_without_window_values(self):
        data = usage(weekly=None)
        data["rate_limit"]["limit_reached"] = True
        self.assertEqual(render(data)[0], "Cx58,061.32cr | font=Menlo size=12")

    def test_reset_restores_percentage_meter_even_with_stale_limit_flag(self):
        data = usage()
        data["rate_limit"]["limit_reached"] = True
        data["rate_limit"]["primary_window"]["reset_at"] = NOW - 1
        self.assertEqual(render(data, cached=True)[0], "Cx│100🟢 | font=Menlo size=12")

    def test_cached_credits_remain_visible_with_warning(self):
        rows = render(usage(), cached=True)
        self.assertEqual(rows[0], "Cx58,061.32cr | font=Menlo size=12")
        self.assertTrue(any("⚠ offline" in r for r in rows))
        self.assertTrue(any("showing data from" in r for r in rows))

    def test_reset_credits_do_not_mask_spendable_credits_after_limit_exhaustion(self):
        data = usage()
        data["rate_limit_reset_credits"] = {"available_count": 1}
        for count_resets in (True, False):
            with self.subTest(count_resets=count_resets):
                self.assertEqual(render(data, config={"codex_count_reset_credits": count_resets})[0],
                                 "Cx58,061.32cr | font=Menlo size=12")

    def test_provider_visibility_still_applies(self):
        self.assertEqual(render(usage(), config={"menubar": []})[0], "LL | font=Menlo size=12")


class GrokUsageTest(unittest.TestCase):
    def test_omitted_zero_percentage_shows_full_period_allowance(self):
        for period_type, seconds, label in (("USAGE_PERIOD_TYPE_WEEKLY", bar.SEVEN_DAY_SECONDS, "Weekly"),
                                            ("USAGE_PERIOD_TYPE_MONTHLY", 30 * 86400, "Credits")):
            with self.subTest(period_type=period_type):
                data = {"config": {"currentPeriod": {"type": period_type,
                                                   "start": NOW - seconds // 2,
                                                   "end": NOW + seconds // 2},
                                   "isUnifiedBillingUser": True,
                                   "onDemandCap": {"val": 1000}, "onDemandUsed": {"val": 800}}}
                rows = render(grok=data, config={"menubar": ["grok"]})
                self.assertEqual(rows[0], "Gk100🟢 | font=Menlo size=12")
                self.assertTrue(any(r.startswith(f"{label:<8} 100% left") for r in rows))

    def test_explicit_percentage_counts_down_remaining_allowance(self):
        data = {"config": {"creditUsagePercent": 1.0}}
        rows = render(grok=data, config={"menubar": ["grok"]})
        self.assertEqual(rows[0], "Gk99🟢 | font=Menlo size=12")

    def test_legacy_included_usage_is_separate_from_extra_spending(self):
        data = {"config": {"monthlyLimit": {"val": 1000}, "used": {"val": 250},
                           "onDemandCap": {"val": 100}, "onDemandUsed": {"val": 80}}}
        self.assertEqual(bar.grok_usage(data)[0], 25)
        self.assertEqual(render(grok=data, config={"menubar": ["grok"]})[0],
                         "Gk75🟢 | font=Menlo size=12")

    def test_extra_spending_alone_does_not_become_included_usage(self):
        data = {"config": {"onDemandCap": {"val": 100}, "onDemandUsed": {"val": 80}}}
        self.assertIsNone(bar.grok_usage(data)[0])
        rows = render(grok=data, config={"menubar": ["grok"]})
        self.assertTrue(any("usage not reported" in r for r in rows))

    def test_missing_legacy_used_scalar_means_zero_used(self):
        self.assertEqual(bar.grok_usage({"config": {"monthlyLimit": {"val": 1000}, "used": {}}})[0], 0)

    def test_untyped_or_null_percentage_remains_unknown(self):
        for cfg in ({}, {"currentPeriod": {"end": NOW + 100}},
                    {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY"}, "creditUsagePercent": None}):
            with self.subTest(cfg=cfg):
                self.assertIsNone(bar.grok_usage({"config": cfg})[0])

    def test_cli_and_cursor_backed_bot_keep_separate_allowances(self):
        rows = render(grok={"config": {"creditUsagePercent": 1}},
                      grokbot={"usagePercent": 4.9}, config={"menubar": ["grokbot", "grok"]})
        self.assertEqual(rows[0], "Gb95🟢 Gk99🟢 | font=Menlo size=12")


if __name__ == "__main__":
    unittest.main()
