import importlib.util
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_docs_charts', ROOT / 'scripts/render_docs_charts.py')
charts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(charts)


class DocsChartsTest(unittest.TestCase):
    def test_committed_svgs_are_current(self):
        done = subprocess.run([sys.executable, str(ROOT / 'scripts/render_docs_charts.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_svgs_are_valid_adaptive_and_described(self):
        for name, content in charts.render().items():
            with self.subTest(name=name):
                root = ET.fromstring(content)
                ns = {'s': 'http://www.w3.org/2000/svg'}
                self.assertTrue(root.find('s:title', ns).text.strip())
                self.assertLessEqual(len(root.find('s:desc', ns).text.split()), 25)
                self.assertIsNone(root.get('width'))
                self.assertIn('@media (prefers-color-scheme:dark)', content)
                self.assertNotIn('<rect width="100%"', content)

    def test_comparison_numbers_come_from_data(self):
        data = charts.json.loads(charts.DATA.read_text())
        v4 = data['decision_bench_v4']
        svg = charts.render()['public-comparison.svg']
        self.assertEqual(v4['merge_commit'], '8d1563c8740600367172f4bffd2858f400bdd32d')
        self.assertEqual((v4['rows'], v4['task_count'], len(v4['tasks'])), (1071, 35, 35))
        self.assertEqual(v4['run']['wire_rows'], {'systemone': 949, 'decisions': 122})
        self.assertEqual((v4['overall']['ours']['correct'], v4['overall']['luna_decisions']['correct'], v4['overall']['jev']['correct']), (973, 969, 990))
        self.assertEqual(sum(g['rows'] for g in v4['groups'].values()), v4['rows'])
        self.assertEqual(sum(g['image_rows'] for g in v4['groups'].values()), v4['by_modality']['image_rows']['rows'])
        for key in charts.SERIES:
            o = v4['overall'][key]
            self.assertEqual(o['rows'], v4['rows'])
            self.assertAlmostEqual(o['accuracy'], o['correct'] / o['rows'])
            self.assertIn(f'{100 * o["accuracy"]:.1f}% \u00b7 {o["correct"]}/{o["rows"]}', svg)
            self.assertIn(f'p95 {o["p95_ms"] / 1000:.2f} s', svg)
            for g in v4['groups'].values():
                d = g[key]
                self.assertEqual(d['rows'], g['rows'])
                self.assertAlmostEqual(d['accuracy'], d['correct'] / d['rows'])
                for x in (f'{100 * d["accuracy"]:.1f}%', f'{d["brier"]:.3f}', f'{d["p50_ms"] / 1000:.2f} s'):
                    self.assertIn(x, svg)
            self.assertEqual(sum(t[key]['correct'] for t in v4['tasks'].values()), o['correct'])
            self.assertEqual(sum(t['rows'] for t in v4['tasks'].values()), v4['rows'])
        for g in v4['groups'].values():
            self.assertIn(f'{g["label"]}</text>', svg)
            self.assertIn(f'{g["rows"]} rows', svg)
        for s in (charts.TITLE, 'All 1,071 rows', 'Jev native', 'GPT-6 Luna \u00b7 Decisions API', 'GPT-6 Luna \u00b7 this project', 'Correct answers',
                  'Brier (lower is better)', 'Median latency (p50)', 'Jev scored the 122 image rows from text renderings; on the 949 text rows: Jev 93.2%, this project 89.8%.', 'via OpenRouter', 'build 3a86e9e'):
            self.assertIn(s, svg)

    def test_comparison_is_one_table_without_cost_or_panels(self):
        svg = charts.render()['public-comparison.svg']
        self.assertEqual(svg.count('Correct answers'), 1)
        self.assertEqual(svg.count('Median latency (p50)'), 1)
        for s in ('Panel', 'panel', 'A \u00b7', 'B \u00b7', 'Per 1,000 requests', 'plan credits', 'rate card', 'API rate', 'slower', '\u00d7', '$'):
            self.assertNotIn(s, svg)

    def test_title_claims_hold_in_data(self):
        data = charts.json.loads(charts.DATA.read_text())
        o = data['decision_bench_v4']['overall']
        self.assertLessEqual(abs(o['ours']['accuracy'] - o['jev']['accuracy']), 0.02)
        self.assertLessEqual(abs(o['ours']['p50_ms'] / 1000 - charts.TITLE_SECONDS), 0.25)
        sub = data['pricing']['rates']['product_subscription']
        self.assertIsNone(sub['input_per_million'])
        self.assertIsNone(sub['output_per_million'])
        self.assertEqual(charts.TITLE, 'LLM decision model: near-Jev accuracy, no per-request bill, ~1.7 s per call')

    def test_guards_reject_unsupported_title(self):
        def broken(edit):
            data = charts.json.loads(charts.DATA.read_text())
            edit(data)
            with self.assertRaises(SystemExit):
                charts.comparison(data)
        broken(lambda d: d['decision_bench_v4']['overall']['ours'].update(accuracy=d['decision_bench_v4']['overall']['jev']['accuracy'] - 0.03))
        broken(lambda d: d['decision_bench_v4']['overall']['ours'].update(p50_ms=2100))
        broken(lambda d: d['pricing']['rates']['product_subscription'].update(input_per_million=0.1))
        broken(lambda d: d['decision_bench_v4']['groups']['AGT'].update(rows=1))

    def test_cost_numbers_match_computation(self):
        data = charts.json.loads(charts.DATA.read_text())
        n = sum(data['datasets'][k]['luna']['n'] for k in data['datasets'])
        self.assertEqual(n, 460)
        tin, tout = data['observed_luna_usage']['input_tokens'] / n, data['observed_luna_usage']['output_tokens'] / n
        rates = data['pricing']['rates']
        usd = {k: (tin * rates[k]['input_per_million'] + tout * rates[k]['output_per_million']) / 1000
               for k in ('typesafe_jev', 'openai_decisions', 'ordinary_luna_api_reference')}
        cr = data['subscription_quota']['official']['standard_paid_credits_per_million']
        credits = (tin * cr['uncached_input'] + tout * cr['output']) / 1000
        svg = charts.render()['cost.svg']
        for v in usd.values():
            self.assertIn(f'${v:.3f}', svg)
        for s in ('$0.026', '$0.062', '$0.075', "≈1.9 credits at Luna's credit rate", 'Codex plan, credit-rate equivalent', 'on a Codex plan', 'Same token mix for every row: 618 input / 26 output tokens per request (460 public tasks).'):
            self.assertIn(s, svg)
        self.assertIn(f'≈{credits:.1f} credits', svg)

    def test_removed_route_chart_stays_removed(self):
        self.assertFalse((ROOT / 'docs/assets/api-routes.svg').exists())
        self.assertNotIn('api-routes.svg', charts.render())

    def test_removed_baseline_chart_stays_removed(self):
        self.assertFalse((ROOT / 'docs/assets/baseline-development.svg').exists())


if __name__ == '__main__':
    unittest.main()
