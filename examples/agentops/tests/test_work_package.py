"""LC-04: packages from real files through the real CLI, and format errors a person would really make."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import uuid

from agentops import work_package as wp
from agentops.ledger import Ledger

ROOT = Path(__file__).parents[1]
TASK = '00000000-0000-4000-8000-000000000001'


def draft(path, **change):
    return {'schema': wp.DRAFT_SCHEMA, 'package_id': 'balicek', 'version': 1, 'task': {'task_id': TASK}, 'goal': 'Cil.',
            'roles': {'owner': 'autor', 'reviewer': 'kontrolor'},
            'financial': {'scope': 'Predplatne.', 'new_paid_usage': False, 'on_limit': 'stop_and_report'},
            'constraints': [{'id': 'c1', 'text': 'Nenasazovat.'}],
            'sources': [{'id': 's1', 'path': str(path), 'origin': {'kind': 'file', 'ref': 'x'}, 'why': 'Duvod.', 'load': 'required'}],
            'output': {'description': 'Zprava.', 'location': 'ukol'},
            'done_when': [{'id': 'd1', 'text': 'Hotovo.', 'evidence': 'log'}], 'out_of_scope': [], 'open_questions': [],
            'budget_bytes': 100000, **change}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / 'podklad.md'
        self.source.write_text('radek jedna\nradek dva\nradek tri\n')

    def cli(self, *args, db=None):
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        return subprocess.run([sys.executable, '-m', 'agentops', '--db', str(db or self.root / 'neni.sqlite'), 'package', *args],
                              env=env, capture_output=True, text=True, timeout=60, cwd=self.root)

    def built(self, **change):
        return wp.build(draft(self.source, **change))

    def write(self, package, name='p.json'):
        path = self.root / name
        path.write_text(json.dumps(package))
        return path

    def codes(self, package, ledger=None):
        return [row['code'] for row in wp.check(package, ledger)['problems']]


class Build(Base):
    def test_hash_and_size_come_from_the_file_not_from_the_draft(self):
        row = self.built()['sources'][0]
        self.assertEqual(row['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(row['bytes'], self.source.stat().st_size)

    def test_draft_cannot_state_its_own_hash(self):
        source = draft(self.source)
        source['sources'][0]['sha256'] = 'a' * 64
        with self.assertRaises(wp.Problem):
            wp.build(source)

    def test_line_selection_hands_over_only_those_lines(self):
        source = draft(self.source)
        source['sources'][0]['lines'] = [2, 2]
        row = wp.build(source)['sources'][0]
        self.assertEqual(row['bytes'], len('radek dva\n'))
        self.assertEqual(row['sha256'], hashlib.sha256(b'radek dva\n').hexdigest())

    def test_missing_source_stays_visible_and_blocks(self):
        package = wp.build(draft(self.root / 'neni.md'))
        self.assertIsNone(package['sources'][0]['sha256'])
        self.assertEqual(package['sources'][0]['missing_reason'], 'source_missing')
        result = wp.check(package)
        self.assertFalse(result['ready'])
        self.assertIn('CHYBÍ: source_missing', wp.render(package))

    def test_cli_build_does_not_overwrite_and_is_private(self):
        draft_path = self.write(draft(self.source), 'd.json')
        output = self.root / 'p.json'
        run = self.cli('build', '--draft', str(draft_path), '--output', str(output))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(output.stat().st_mode & 0o077, 0)
        before = output.read_bytes()
        self.assertEqual(self.cli('build', '--draft', str(draft_path), '--output', str(output)).returncode, 1)
        self.assertEqual(output.read_bytes(), before)

    def test_relative_paths_need_a_base(self):
        relative = draft('podklad.md')
        with self.assertRaises(wp.Problem):
            wp.build(relative)
        self.assertEqual(wp.build(relative, self.root)['sources'][0]['path'], str(self.source))
        escaping = draft('../../etc/passwd')
        self.assertNotEqual(wp.build(escaping, self.root)['sources'][0]['path'], str(self.source))

    def test_included_example_is_usable(self):
        examples = ROOT / 'examples/work-packages'
        output = self.root / 'ukazka.json'
        run = self.cli('build', '--draft', str(examples / 'synthetic.draft.json'), '--output', str(output), '--base', str(examples))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Prahová', self.cli('render', str(output)).stdout + (examples / 'README-source.md').read_text())
        self.assertEqual(self.cli('check', str(output)).returncode, 0)


class FormatErrors(Base):
    def test_every_missing_mandatory_part_is_refused(self):
        for field in ('goal', 'roles', 'financial', 'constraints', 'sources', 'output', 'done_when', 'task', 'version', 'package_id',
                      'out_of_scope', 'open_questions', 'budget_bytes'):
            broken = draft(self.source)
            del broken[field]
            with self.subTest(field), self.assertRaises(wp.Problem):
                wp.build(broken)

    def test_invalid_content_is_refused(self):
        cases = {'empty goal': {'goal': '   '}, 'no constraint': {'constraints': []}, 'no end condition': {'done_when': []},
                 'own review': {'roles': {'owner': 'autor', 'reviewer': 'autor'}}, 'bad role': {'roles': {'owner': 'A B', 'reviewer': 'k'}},
                 'version zero': {'version': 0}, 'version text': {'version': '1'}, 'version bool': {'version': True},
                 'bad task': {'task': {'task_id': 'LC-04'}}, 'unknown top field': {'history': 'cela'},
                 'silent paid limit': {'financial': {'scope': 'x', 'new_paid_usage': False, 'on_limit': 'continue_paid'}},
                 'no paid statement': {'financial': {'scope': 'x', 'on_limit': 'stop_and_report'}},
                 'paid without approval': {'financial': {'scope': 'x', 'new_paid_usage': True, 'on_limit': 'stop_and_report'}},
                 'evidence missing': {'done_when': [{'id': 'd', 'text': 't'}]},
                 'nested constraint': {'constraints': [{'id': 'c', 'text': {'a': 'b'}}]},
                 'duplicate ids': {'constraints': [{'id': 'c', 'text': 'a'}, {'id': 'c', 'text': 'b'}]},
                 'control character': {'goal': 'a\x07b'}, 'long goal': {'goal': 'x' * 2001}, 'bad budget': {'budget_bytes': 0},
                 'question without flag': {'open_questions': [{'id': 'q', 'question': 'x'}]}}
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(wp.Problem):
                wp.build(draft(self.source, **change))

    def test_invalid_sources_are_refused(self):
        changes = {'origin missing': {'origin': None}, 'origin kind': {'origin': {'kind': 'tusim', 'ref': 'x'}}, 'why empty': {'why': ''},
                   'load': {'load': 'vzdy'}, 'lines reversed': {'lines': [3, 1]}, 'lines zero': {'lines': [0, 1]}, 'lines text': {'lines': 'vse'},
                   'unknown field': {'content': 'cely text'}}
        for name, change in changes.items():
            broken = draft(self.source)
            broken['sources'][0].update(change)
            with self.subTest(name), self.assertRaises(wp.Problem):
                wp.build(broken)
        for bad in ('/a/../b', '//a', '/home/x/DANĚ/a', '/home/x/.ssh/id', '/x/.credentials.json', '/x/auth.json',
                    '/home/x/.local/state/datahub-context-cache/a', '/home/x/OneDrive/a', '/home/x/DATA HUB/a'):
            with self.subTest(bad), self.assertRaises(wp.Problem):
                wp.build(draft(bad))

    def test_package_without_required_source_must_say_why(self):
        with self.assertRaises(wp.Problem):
            wp.build(draft(self.source, sources=[]))
        package = wp.build(draft(self.source, sources=[], open_questions=[{'id': 'q', 'question': 'Jaky podklad?', 'blocking': True}]))
        self.assertIn('open_question', self.codes(package))

    def test_tampered_package_is_invalid(self):
        good = self.built()
        for mutate in (lambda p: p['sources'][0].update(sha256='neni-hash'), lambda p: p['sources'][0].update(bytes=-1),
                       lambda p: p['sources'][0].pop('sha256'), lambda p: p.update(schema='jine'),
                       lambda p: p['sources'][0].update(sha256=None, bytes=None)):
            broken = copy.deepcopy(good)
            mutate(broken)
            with self.assertRaises(wp.Problem):
                wp.check(broken)

    def test_cli_invalid_format_is_exit_one_and_short(self):
        run = self.cli('check', str(self.write({'schema': wp.SCHEMA})))
        self.assertEqual(run.returncode, 1)
        self.assertNotIn('Traceback', run.stderr)

    def test_file_errors_in_every_step_do_not_repeat_the_path(self):
        marker = 'TAJNE_V_CESTE'
        parent = self.root / marker
        parent.write_text('neni slozka')
        good_package = self.write(self.built())
        good_draft = self.write(draft(self.source), 'd.json')
        hidden = str(parent / 'x.json')
        for args in (('build', '--draft', str(good_draft), '--output', hidden), ('build', '--draft', hidden, '--output', str(self.root / 'o.json')),
                     ('check', hidden), ('render', hidden), ('compare', hidden, '--broad', str(self.source)),
                     ('compare', str(good_package), '--broad', str(self.source), '--facts', hidden)):
            with self.subTest(step=args[0], position=args.index(hidden)):
                run = self.cli(*args)
                self.assertEqual((run.returncode, run.stderr.strip()), (1, 'AgentOps: A file for this command could not be read or written'))
                self.assertNotIn(marker, run.stdout)

    def test_broken_ledger_error_does_not_repeat_the_path(self):
        db = self.root / 'TAJNE_LEDGER.sqlite'
        db.write_bytes(b'neni databaze ' * 400)
        db.chmod(0o600)
        run = self.cli('check', str(self.write(self.built())), '--with-task', db=db)
        self.assertEqual((run.returncode, run.stderr.strip()), (1, 'AgentOps: A file for this command could not be read or written'))
        self.assertNotIn('TAJNE_LEDGER', run.stdout)

    def test_error_does_not_repeat_the_text(self):
        with self.assertRaises(wp.Problem) as caught:
            wp.build(draft(self.source, goal='TAJNE\x07'))
        self.assertNotIn('TAJNE', str(caught.exception))

    def test_oversized_package_is_refused(self):
        rows = [{'id': f'c{n}', 'text': 'x' * 1000} for n in range(64)] * 1
        extra = [{'id': f'd{n}', 'text': 'x' * 1000, 'evidence': 'y' * 500} for n in range(64)]
        with self.assertRaises(wp.Problem):
            wp.build(draft(self.source, constraints=rows, done_when=extra))


class Check(Base):
    def test_ready_package(self):
        result = wp.check(self.built())
        self.assertTrue(result['ready'])
        self.assertEqual(result['volume']['required_source_bytes'], self.source.stat().st_size)
        self.assertIn('not billed tokens', result['volume']['unit_note'])

    def test_changed_source_is_not_ready_and_says_which(self):
        package = self.built()
        self.source.write_text('jiny obsah')
        result = wp.check(package)
        self.assertFalse(result['ready'])
        self.assertEqual(result['problems'], [{'code': 'source_changed', 'source': 's1', 'blocking': True}])

    def test_removed_replaced_or_unsuitable_source_is_not_ready(self):
        package = self.built()
        self.source.unlink()
        self.assertEqual(self.codes(package), ['source_missing'])
        self.source.symlink_to(self.root)
        self.assertEqual(self.codes(package), ['source_unreadable'])
        self.source.unlink()
        self.source.mkdir()
        self.assertEqual(self.codes(package), ['source_not_a_file'])

    def test_fifo_does_not_hang(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        self.assertEqual(wp.build(draft(fifo))['sources'][0]['missing_reason'], 'source_not_a_file')

    def test_lines_past_the_end_are_a_problem(self):
        source = draft(self.source)
        source['sources'][0]['lines'] = [2, 99]
        self.assertEqual(wp.build(source)['sources'][0]['missing_reason'], 'source_lines_out_of_range')

    def test_change_outside_selected_lines_keeps_it_ready(self):
        source = draft(self.source)
        source['sources'][0]['lines'] = [1, 1]
        package = wp.build(source)
        self.source.write_text('radek jedna\nzmeneno\n')
        self.assertTrue(wp.check(package)['ready'])

    def test_on_demand_problem_is_reported_but_does_not_block(self):
        source = draft(self.source)
        source['sources'].append({'id': 's2', 'path': str(self.root / 'neni.md'), 'origin': {'kind': 'file', 'ref': 'x'},
                                  'why': 'Az bude.', 'load': 'on_demand'})
        result = wp.check(wp.build(source))
        self.assertTrue(result['ready'])
        self.assertEqual(result['problems'], [{'code': 'source_missing', 'source': 's2', 'blocking': False}])

    def test_on_demand_is_not_counted_as_handed_over(self):
        other = self.root / 'dlouhy.md'
        other.write_text('x' * 5000)
        source = draft(self.source)
        source['sources'].append({'id': 's2', 'path': str(other), 'origin': {'kind': 'file', 'ref': 'x'}, 'why': 'Kdyz.', 'load': 'on_demand'})
        volume = wp.check(wp.build(source))['volume']
        self.assertEqual(volume['on_demand_source_bytes'], 5000)
        self.assertLess(volume['handed_over_bytes'], 5000)
        self.assertNotIn('xxxxx', wp.render(wp.build(source)))

    def test_over_budget_is_not_ready(self):
        self.assertIn('over_budget', self.codes(self.built(budget_bytes=10)))

    def test_blocking_question_is_not_ready_but_a_note_is(self):
        blocked = self.built(open_questions=[{'id': 'q', 'question': 'Kdo rozhodne?', 'blocking': True}])
        self.assertFalse(wp.check(blocked)['ready'])
        self.assertIn('BLOKUJE: Kdo rozhodne?', wp.render(blocked))
        self.assertTrue(wp.check(self.built(open_questions=[{'id': 'q', 'question': 'Poznamka', 'blocking': False}]))['ready'])

    def test_paid_usage_is_never_ready_by_itself(self):
        package = self.built(financial={'scope': 'x', 'new_paid_usage': True, 'on_limit': 'stop_and_report', 'approval_ref': 'zaznam'})
        self.assertIn('paid_usage_needs_person', self.codes(package))

    def test_cli_exit_codes(self):
        path = self.write(self.built())
        self.assertEqual(self.cli('check', str(path)).returncode, 0)
        self.source.write_text('zmena')
        run = self.cli('check', str(path))
        self.assertEqual(run.returncode, 2)
        self.assertIn('source_changed', run.stdout)

    def test_check_changes_nothing(self):
        package = self.built()
        path = self.write(package)
        before = (path.read_bytes(), self.source.read_bytes())
        self.cli('check', str(path))
        self.assertEqual(before, (path.read_bytes(), self.source.read_bytes()))


class TaskLink(Base):
    def setUp(self):
        super().setUp()
        self.db = self.root / 'tasks.sqlite'
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

    def package_for(self, task):
        return self.built(task={'task_id': task})

    def test_matching_task_is_reported(self):
        task = self.ledger.create('t', 'autor', 'autor')['id']
        result = wp.check(self.package_for(task), self.db)
        self.assertTrue(result['ready'])
        self.assertEqual(result['task']['owner'], 'autor')

    def test_a_pending_handover_to_the_owner_counts(self):
        task = self.ledger.create('t', 'jiny', 'jiny')['id']
        self.ledger.change(task, 'jiny', 'start')
        self.ledger.change(task, 'jiny', 'offer', target='autor', purpose='work', artifact='/p.json')
        self.assertTrue(wp.check(self.package_for(task), self.db)['ready'])

    def test_other_owner_closed_or_unknown_task_is_not_ready(self):
        task = self.ledger.create('t', 'jiny', 'jiny')['id']
        self.assertIn('owner_differs_from_task', self.codes(self.package_for(task), self.db))
        self.ledger.change(task, 'jiny', 'cancel', note='konec')
        self.assertIn('task_closed', self.codes(self.package_for(task), self.db))
        self.assertIn('task_not_found', self.codes(self.package_for(str(uuid.uuid4())), self.db))

    def test_missing_ledger_is_not_created(self):
        missing = self.root / 'neni/tasks.sqlite'
        self.assertIn('ledger_unavailable', self.codes(self.built(), missing))
        self.assertFalse(missing.parent.exists())

    def test_checking_does_not_change_the_task(self):
        task = self.ledger.create('t', 'autor', 'autor')['id']
        before = (self.ledger.get(task), self.ledger.events(task))
        wp.check(self.package_for(task), self.db)
        path = self.write(self.package_for(task))
        run = self.cli('check', str(path), '--with-task', db=self.db)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(before, (self.ledger.get(task), self.ledger.events(task)))


class ExcludedLocations(Base):
    """Synthetic folders with excluded names, and a marker that must never be read."""

    def setUp(self):
        super().setUp()
        self.private = self.root / '.ssh'
        self.private.mkdir()
        self.secret = self.private / 'fixture.txt'
        self.secret.write_text('VYLOUCENY_OBSAH\n')
        self.alias = self.root / 'alias'
        self.alias.symlink_to(self.private, target_is_directory=True)

    def test_folder_alias_is_not_read_and_blocks_a_required_source(self):
        package = wp.build(draft(self.alias / 'fixture.txt'))
        row = package['sources'][0]
        self.assertEqual((row['sha256'], row['bytes'], row['missing_reason']), (None, None, 'source_excluded'))
        self.assertFalse(wp.check(package)['ready'])

    def test_nothing_is_opened(self):
        real = os.open
        def watching(path, *args, **kwargs):
            self.assertNotIn('fixture.txt', str(path))
            return real(path, *args, **kwargs)
        with unittest.mock.patch.object(wp.os, 'open', watching):
            wp.build(draft(self.alias / 'fixture.txt'))

    def test_alias_chain_and_excluded_folder_further_up(self):
        second = self.root / 'druhy'
        second.symlink_to(self.alias, target_is_directory=True)
        nested = self.private / 'hloubeji'
        nested.mkdir()
        (nested / 'f.txt').write_text('x')
        self.assertEqual(wp.build(draft(second / 'fixture.txt'))['sources'][0]['missing_reason'], 'source_excluded')
        self.assertEqual(wp.build(draft(self.alias / 'hloubeji/f.txt'))['sources'][0]['missing_reason'], 'source_excluded')

    def test_alias_to_each_excluded_kind(self):
        for name in ('DANĚ', 'Dane', '.gnupg', 'OneDrive', 'datahub-context-cache', 'DATA HUB', 'DANE\u030c'):
            with self.subTest(name):
                folder = self.root / 'mista' / name
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'a.txt').write_text('x')
                link = self.root / ('odkaz-' + str(abs(hash(name))))
                link.symlink_to(folder, target_is_directory=True)
                self.assertEqual(wp.build(draft(link / 'a.txt'))['sources'][0]['missing_reason'], 'source_excluded')

    def test_alias_on_demand_is_reported_and_not_read(self):
        source = draft(self.source)
        source['sources'].append({'id': 's2', 'path': str(self.alias / 'fixture.txt'), 'origin': {'kind': 'file', 'ref': 'x'}, 'why': 'Kdyz.', 'load': 'on_demand'})
        result = wp.check(wp.build(source))
        self.assertTrue(result['ready'])
        self.assertEqual(result['problems'], [{'code': 'source_excluded', 'source': 's2', 'blocking': False}])
        self.assertEqual(result['volume']['on_demand_source_bytes'], 0)

    def test_link_changed_after_build_is_caught_at_check(self):
        folder = self.root / 'bezna'
        folder.mkdir()
        (folder / 'fixture.txt').write_text('VYLOUCENY_OBSAH\n')
        link = self.root / 'prepnuty'
        link.symlink_to(folder, target_is_directory=True)
        package = wp.build(draft(link / 'fixture.txt'))
        self.assertTrue(wp.check(package)['ready'])
        link.unlink()
        link.symlink_to(self.private, target_is_directory=True)
        self.assertEqual([row['code'] for row in wp.check(package)['problems']], ['source_excluded'])

    def test_ordinary_alias_outside_excluded_places_still_works(self):
        folder = self.root / 'bezna'
        folder.mkdir()
        (folder / 'f.txt').write_text('verejny')
        link = self.root / 'odkaz'
        link.symlink_to(folder, target_is_directory=True)
        self.assertEqual(wp.build(draft(link / 'f.txt'))['sources'][0]['bytes'], 7)

    def test_ordinary_missing_source_keeps_its_own_reason(self):
        self.assertEqual(wp.build(draft(self.root / 'neni.md'))['sources'][0]['missing_reason'], 'source_missing')

    def test_compare_never_reads_excluded_broad_files(self):
        facts = [{'id': 'f', 'text': 'VYLOUCENY_OBSAH'}]
        for broad in (self.secret, self.alias / 'fixture.txt'):
            with self.subTest(broad=str(broad)):
                result = wp.compare(self.built(), [broad], facts)
                self.assertEqual((result['broad']['files'], result['broad']['bytes'], result['broad']['excluded']), (0, 0, 1))
                self.assertEqual(result['facts']['missing_from_broad'], ['f'])
                self.assertNotIn(str(broad), json.dumps(result))

    def test_compare_cli_with_relative_and_mixed_broad_paths(self):
        other = self.root / 'siroky.md'
        other.write_text('verejny text')
        run = self.cli('compare', str(self.write(self.built())), '--broad', 'siroky.md', '.ssh/fixture.txt')
        self.assertEqual(run.returncode, 0, run.stderr)
        broad = json.loads(run.stdout)['broad']
        self.assertEqual((broad['files'], broad['excluded'], broad['bytes']), (1, 1, other.stat().st_size))

    def test_path_written_through_an_excluded_name_is_refused_even_if_it_leads_out(self):
        public = self.root / 'bezna'
        public.mkdir()
        (public / 'f.txt').write_text('VEREJNY')
        (self.private / 'ven').symlink_to(public, target_is_directory=True)
        result = wp.compare(self.built(), [self.private / 'ven/f.txt'], [{'id': 'f', 'text': 'VEREJNY'}])
        self.assertEqual((result['broad']['files'], result['broad']['excluded']), (0, 1))

    def test_package_side_of_compare_is_guarded_too(self):
        package = wp.build(draft(self.alias / 'fixture.txt'))
        result = wp.compare(package, [self.source], [{'id': 'f', 'text': 'VYLOUCENY_OBSAH'}])
        self.assertEqual(result['package']['unreadable'], ['s1'])
        self.assertEqual(result['facts']['missing_from_package'], ['f'])


class Render(Base):
    def test_everything_a_worker_needs_is_in_the_text(self):
        text = wp.render(self.built(out_of_scope=['Base44.']))
        for part in ('Cil.', 'Nenasazovat.', 'Predplatne.', 'Nové placené využití: ne', 'zastav a ozvi se', str(self.source), 'Duvod.',
                     'Zprava.', 'Hotovo.', 'Důkaz: log', 'Base44.', 'autor', 'kontrolor', TASK):
            self.assertIn(part, text)

    def test_on_demand_source_is_listed_only_as_on_demand(self):
        source = draft(self.source)
        source['sources'].append({'id': 'poptavka', 'path': str(self.root / 'neni.md'), 'origin': {'kind': 'file', 'ref': 'x'}, 'why': 'Kdyz.', 'load': 'on_demand'})
        required, on_demand = wp.render(wp.build(source)).split('## Zdroje na vyžádání')
        self.assertIn('s1:', required)
        self.assertNotIn('poptavka:', required)
        self.assertIn('poptavka:', on_demand)
        self.assertNotIn('s1:', on_demand)

    def test_source_content_is_referenced_not_copied(self):
        self.assertNotIn('radek dva', wp.render(self.built()))


class Compare(Base):
    def test_volume_is_measured_from_what_is_really_handed_over(self):
        broad = self.root / 'siroky.md'
        broad.write_text('radek dva\n' + 'y' * 100000)
        result = wp.compare(self.built(), [broad])
        self.assertEqual(result['broad']['bytes'], broad.stat().st_size)
        self.assertEqual(result['package']['bytes'], len(wp.render(self.built()).encode()) + self.source.stat().st_size)
        self.assertLess(result['ratio_bytes'], 0.1)
        self.assertIn('billed tokens', result['not_measured'])

    def test_a_fact_missing_from_the_package_is_shown_not_hidden(self):
        broad = self.root / 'siroky.md'
        broad.write_text('radek dva a pravidlo X je dulezite\n')
        facts = [{'id': 'ma', 'text': 'radek dva'}, {'id': 'jen-sirsi', 'text': 'pravidlo X'}, {'id': 'nikde', 'text': 'pravidlo Y'}]
        result = wp.compare(self.built(), [broad], facts)['facts']
        self.assertEqual(result['missing_from_package'], ['jen-sirsi', 'nikde'])
        self.assertEqual(result['missing_from_broad'], ['nikde'])

    def test_a_smaller_package_with_gaps_does_not_look_clean(self):
        source = draft(self.source)
        source['sources'][0]['lines'] = [1, 1]
        broad = self.root / 'siroky.md'
        broad.write_text(self.source.read_text())
        result = wp.compare(wp.build(source), [broad], [{'id': 'dva', 'text': 'radek dva'}])
        self.assertEqual(result['facts']['missing_from_package'], ['dva'])

    def test_unreadable_broad_file_is_named(self):
        result = wp.compare(self.built(), [self.root / 'neni.md'])
        self.assertEqual(result['broad']['unreadable'], [str(self.root / 'neni.md')])
        self.assertIsNone(result['ratio_bytes'])


class Boundaries(unittest.TestCase):
    def test_module_runs_nothing_and_reaches_no_network(self):
        import ast
        tree = ast.parse((ROOT / 'agentops/work_package.py').read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module.split('.')[0])
        self.assertFalse(imported & {'subprocess', 'socket', 'urllib', 'http', 'importlib', 'ctypes'})

    def test_docs_state_the_limits(self):
        text = (ROOT / 'docs/work-packages.md').read_text()
        for phrase in ('not isolation', 'Bytes are not tokens', 'not an evaluation'):
            self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
