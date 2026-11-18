import copy
import json
import tempfile
import unittest
from pathlib import Path

from pilotcheck.observations import attach_observations
from pilotcheck.core import load_dataset
from pilotcheck.report import export


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = {'baseline_sha256': 'abc', 'summary': {'rows': 2}, 'records': [
            {'id': rid, 'suggested_c': 'unknown', 'consequence_tier': 'unknown',
             'confirmation_tier': 'unknown', 'reachability': 'unknown',
             'danger_status': 'unresolved', 'probe_allowed': False}
            for rid in ('R1', 'R2')]}
        self.ledger = {'schema': 'pilotcheck-scoped-observations-v1', 'baseline_sha256': 'abc',
                       'cases': [{'id': 'O1', 'row_ids': ['R1', 'R2'], 'association': 'ambiguous-rows',
                                  'conditions': [{'focus': 'child checkbox', 'input': 'Space', 'result': 'changed'}],
                                  'limit': 'No one-to-one row mapping', 'repeat': 'unspecified'}]}

    def attach(self, ledger):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'observations.json'
            p.write_text(json.dumps(ledger), encoding='utf-8')
            attach_observations(self.dataset, p)

    def test_associations_preserve_all_classification_fields(self):
        before = copy.deepcopy(self.dataset)
        self.attach(self.ledger)
        for old, new in zip(before['records'], self.dataset['records']):
            self.assertEqual({k: new[k] for k in old}, old)
            self.assertEqual(new['observation_refs'], ['O1'])
        self.assertEqual(self.dataset['summary'], before['summary'])
        self.assertEqual(self.dataset['observation_ledger'], self.ledger)

    def test_bad_hash_duplicate_unknown_row_and_missing_conditions_are_atomic(self):
        variants = []
        bad = copy.deepcopy(self.ledger); bad['baseline_sha256'] = 'changed'; variants.append(bad)
        bad = copy.deepcopy(self.ledger); bad['cases'] *= 2; variants.append(bad)
        bad = copy.deepcopy(self.ledger); bad['cases'][0]['row_ids'] = ['missing']; variants.append(bad)
        bad = copy.deepcopy(self.ledger); bad['cases'][0]['conditions'] = []; variants.append(bad)
        variants.append({'cases': self.ledger['cases']})
        for ledger in variants:
            with self.subTest(ledger=ledger):
                before = copy.deepcopy(self.dataset)
                with self.assertRaises(ValueError): self.attach(ledger)
                self.assertEqual(self.dataset, before)

    def test_summary_cannot_be_passed_as_review_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'records').mkdir()
            (root / 'records/records.jsonl').write_text('', encoding='utf-8')
            p = root / 'summary.json'; p.write_text('{"cases": []}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'results/records'):
                load_dataset(root, None, p)

    def test_no_html_export_preserves_observation_associations(self):
        self.attach(self.ledger)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            export(self.dataset, {'jobs': []}, out, include_html=False)
            self.assertFalse((out / 'report.html').exists())
            saved = json.loads((out / 'analysis.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['observation_ledger'], self.ledger)
            self.assertIn('observation_refs', (out / 'classification.csv').read_text(encoding='utf-8-sig'))
