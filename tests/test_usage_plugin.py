import json
from pathlib import Path
import tempfile
import time
import unittest
from agentsmon import usage_plugin as usage


class AccountUsage(unittest.TestCase):
    def test_accounts_never_share_by_vendor_missing_source_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'status.json'
            p.write_text(json.dumps({'rate_limits':{'five_hour':{'used_percentage':20,'resets_at':time.time()+3000}}}))
            result=usage.collect({}, {'sources':{'Alice':{'path':str(p),'format':'claude-statusline','account':'A'},
                                                'Bob':{'path':str(p)+'-missing','format':'claude-statusline','account':'B'}}})
            self.assertEqual(result['patches'][0]['cells']['limits']['bars'][0]['percent'],20)
            self.assertEqual(result['patches'][1]['cells']['limits']['text'],'Unavailable')

    def test_expired_negative_nan_and_percentage_above_100_do_not_fake_a_limit(self):
        for pct in (-1,101,float('nan'),True,'90'):
            self.assertIsNone(usage.window('5h',pct,300,100))
        self.assertIsNone(usage.window('5h',30,99,100))
        self.assertEqual(usage.window('5h',30,'2030-01-01T00:00:00Z',100)['percent'],30)

    def test_rollout_tail_and_real_window_labels(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rollout.jsonl'
            p.write_text(json.dumps({'timestamp':'2026-10-15T12:00:00Z','payload':{'rate_limits':{
                'primary':{'used_percent':42,'window_minutes':180,'resets_at':2_000_000_000}}}})+'\n')
            result=usage.read_source({'path':str(p),'format':'codex-rollout'},1_800_000_000)
            self.assertEqual(result['bars'][0]['label'],'3h')


class QuotaPanelData(unittest.TestCase):
    def test_bars_carry_numeric_reset_for_the_remaining_panel(self):
        b=usage.window('5h',30,2_000_000_000,100)
        self.assertEqual(b['resets_at'],2_000_000_000)
        self.assertNotIn('resets_at',usage.window('5h',30,None,100))

    def test_plugin_cell_validation_accepts_only_finite_nonnegative_reset(self):
        from agentsmon import plugins
        ok=plugins.cell({'text':'','bars':[{'label':'5h','percent':10,'resets_at':2_000_000_000}]})
        self.assertEqual(ok['bars'][0]['resets_at'],2_000_000_000)
        for bad in (-1,float('inf'),'2030',True):
            with self.assertRaises(ValueError):
                plugins.cell({'bars':[{'label':'5h','percent':10,'resets_at':bad}]})


class QuotaPanelPage(unittest.TestCase):
    def test_page_has_hidden_quota_panel_with_czech_hint(self):
        from agentsmon import dashboard
        src=Path(dashboard.__file__).read_text()
        self.assertIn('id="quota" class="quota" aria-label="Subscription limits" hidden',src)
        self.assertIn('"quota":"Kolik ti zbývá',src)
        self.assertIn('renderQuota(d);',src)


class ExplicitQuotaGrouping(unittest.TestCase):
    def test_equal_labels_and_values_do_not_merge_distinct_sources(self):
        from agentsmon import plugins
        with tempfile.TemporaryDirectory() as d:
            first,second=Path(d)/'one.json',Path(d)/'two.json'
            body=json.dumps({'rate_limits':{'five_hour':{'used_percentage':20,'resets_at':2000000000}}})
            first.write_text(body);second.write_text(body)
            spec={'path':str(first),'format':'claude-statusline','account':'Shared declaration'}
            a=usage.read_source(spec,1800000000);b=usage.read_source(dict(spec),1800000000)
            other=usage.read_source({**spec,'path':str(second)},1800000000)
            unnamed=usage.read_source({'path':str(first),'format':'claude-statusline'},1800000000)
            self.assertEqual(a['quota_group'],b['quota_group'])
            self.assertNotEqual(a['quota_group'],other['quota_group'])
            self.assertNotIn('quota_group',unnamed)
            self.assertNotIn(str(first),json.dumps(plugins.cell(a)))
            for bad in ('source-'+('0'*63),'account-one',None,True):
                with self.assertRaises(ValueError):plugins.cell({'quota_group':bad})
