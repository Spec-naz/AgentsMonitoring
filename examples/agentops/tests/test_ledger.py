import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agentops.ledger import Conflict, Ledger
from agentops.observe import snapshot


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = Ledger(Path(self.directory.name) / 'tasks.sqlite')
        self.addCleanup(self.ledger.close)
        self.task = self.ledger.create('Review the release', 'kodak', 'operator')

    def change(self, action, who='kodak', **kwargs):
        return self.ledger.change(self.task['id'], who, action, **kwargs)

    def test_handoff_is_not_ownership_until_explicit_acceptance(self):
        self.change('start')
        self.change('offer', target='klodak', purpose='review', artifact='git:abc123')
        pending = self.ledger.get(self.task['id'])
        self.assertEqual(pending['owner'], 'kodak')
        with self.assertRaises(Conflict):
            self.change('accept', who='hermik')
        accepted = self.change('accept', who='klodak')
        self.assertEqual((accepted['owner'], accepted['state']), ('klodak', 'review'))
        done = self.change('done', who='klodak', artifact='report:review-passed')
        self.assertEqual(done['state'], 'done')

    def test_done_needs_review_and_other_actor(self):
        self.change('start')
        with self.assertRaises(Conflict):
            self.change('done', artifact='git:abc')
        with self.assertRaises(ValueError):
            self.change('offer', target='kodak', purpose='review', artifact='git:abc')

    def test_review_forwarding_keeps_original_producer(self):
        self.change('start')
        self.change('offer', target='klodak', purpose='review', artifact='git:abc')
        self.change('accept', who='klodak')
        with self.assertRaises(Conflict):
            self.change('offer', who='klodak', target='kodak', purpose='review', artifact='git:abc')
        self.change('offer', who='klodak', target='hermik', purpose='review', artifact='git:abc')
        forwarded = self.change('accept', who='hermik')
        self.assertEqual(forwarded['producer'], 'kodak')
        self.assertEqual(self.change('done', who='hermik', artifact='review:passed')['state'], 'done')

    def test_idempotency_does_not_duplicate_and_conflicting_reuse_fails(self):
        first = self.ledger.create('One task', 'kodak', 'operator', 'message-123')
        second = self.ledger.create('One task', 'kodak', 'operator', 'message-123')
        self.assertEqual(first, second)
        with self.assertRaises(Conflict):
            self.ledger.create('Different task', 'kodak', 'operator', 'message-123')
        self.assertEqual(len(self.ledger.list()), 2)

    def test_revision_prevents_stale_writer_and_single_handoff_claim(self):
        self.change('start', expected=1)
        with self.assertRaises(Conflict):
            self.change('progress', note='stale writer', expected=1)
        self.change('offer', target='hermik', purpose='work', artifact='doc:requirements')
        other = Ledger(self.ledger.path)
        try:
            self.change('accept', who='hermik')
            with self.assertRaises(Conflict):
                other.change(self.task['id'], 'hermik', 'accept')
        finally:
            other.close()

    def test_audit_is_append_only_and_contains_transitions(self):
        self.change('start')
        event = self.ledger.events(self.task['id'])[-1]
        details = json.loads(event['details'])
        self.assertEqual((details['from_state'], details['to_state']), ('queued', 'running'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute('DELETE FROM events')

    def test_costs_keep_estimates_currencies_and_actuals_separate(self):
        for amount, currency, kind in [(1000, 'USD', 'actual'), (9000, 'USD', 'estimate'), (2000, 'EUR', 'actual')]:
            self.ledger.cost(self.task['id'], 'kodak', amount, currency, kind, 'manual:test')
        totals = {(entry['currency'], entry['kind']): entry['micros'] for entry in self.ledger.cost_totals()}
        self.assertEqual(totals, {('USD', 'actual'): 1000, ('USD', 'estimate'): 9000, ('EUR', 'actual'): 2000})

    def test_stale_progress_is_hint_not_state_change(self):
        with patch('agentops.ledger.time.time', return_value=100):
            self.change('start')
        state = snapshot(self.ledger, {}, now=1100, signals={})
        task = state['tasks'][0]
        self.assertEqual(task['attention'], 'progress_overdue_hint')
        self.assertEqual(self.ledger.get(self.task['id'])['state'], 'running')
        self.assertFalse(state['access']['actor_names_authenticated'])
        self.assertIn('unknown', state['cost_coverage'])

    def test_handoff_reminder_does_not_reassign(self):
        self.change('start')
        with patch('agentops.ledger.time.time', return_value=100):
            self.change('offer', target='hermik', purpose='work', artifact='doc:requirements')
        task = snapshot(self.ledger, {}, now=1000, signals={})['tasks'][0]
        self.assertEqual(task['attention'], 'handoff_unacknowledged')
        self.assertEqual(task['owner'], 'kodak')


if __name__ == '__main__':
    unittest.main()
