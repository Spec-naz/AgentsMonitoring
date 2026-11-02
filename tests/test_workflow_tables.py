"""An optional workflow presentation for plugin tables; ordinary tables stay untouched."""
import unittest

from agentsmon import dashboard
from agentsmon.plugins import Plugin, validate_result

PRESENTATION = {'type': 'workflow', 'count': '2 úkoly', 'summary': '2 úkoly · 1 k pozornosti', 'details_label': 'Odkaz',
                'headers': ['Úkol', 'Čeká na', 'Odkaz', 'Čeká', 'Stav']}
ROW = {'id': 'one', 'name': 'A', 'alive': True, 'kind': 'workflow-evidence'}


def validated(**extra):
    table = {'id': 't', 'title': 'Fronta', 'rows': [ROW], **extra}
    return validate_result(Plugin('p', '1', {}, {}), {'tables': [table]})['tables'][0]


class Validation(unittest.TestCase):
    def test_table_without_presentation_has_no_new_key(self):
        self.assertNotIn('presentation', validated())

    def test_valid_presentation_is_kept(self):
        self.assertEqual(validated(presentation=PRESENTATION)['presentation'], PRESENTATION)

    def test_row_kind_passes_through(self):
        self.assertEqual(validated(presentation=PRESENTATION)['rows'][0]['kind'], 'workflow-evidence')

    def test_invalid_presentations_are_refused(self):
        for bad in ('workflow', [], {}, {**PRESENTATION, 'type': 'jine'}, {**PRESENTATION, 'html': '<b>'},
                    {**PRESENTATION, 'headers': ['a']}, {**PRESENTATION, 'headers': 'abcde'},
                    {**PRESENTATION, 'headers': ['a', 'b', 'c', 'd', 5]}, {**PRESENTATION, 'count': 'x' * 81},
                    {**PRESENTATION, 'summary': 'radek\nnovy'},
                    {k: v for k, v in PRESENTATION.items() if k != 'count'}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, KeyError)):
                validated(presentation=bad)

    def test_table_error_still_reports(self):
        self.assertFalse(validated(presentation=PRESENTATION, error='Snapshot expired')['up'])


class Page(unittest.TestCase):
    def test_ordinary_wording_is_still_there_for_tables_without_presentation(self):
        for text in ('healthy`', 'Connection details', 'agents`', 'tmux attach'):
            self.assertIn(text, dashboard.PAGE)

    def test_workflow_branch_is_tied_to_the_declared_type(self):
        self.assertIn('summary.presentation.type==="workflow"', dashboard.PAGE)


if __name__ == '__main__':
    unittest.main()
