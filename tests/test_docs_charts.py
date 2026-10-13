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
        svg = charts.render()['public-comparison.svg']
        for suite, _, kind in charts.SUITES:
            for side in ('luna', 'jev'):
                d = data['datasets'][suite][side]
                self.assertIn(f'{d["correct"]}/{d["n"]}', svg)
                self.assertIn(f'{d["brier"]:.3f}', svg)
                self.assertIn(f'{d["p50_ms"] / 1000:.2f} s', svg)
            self.assertIn(f'{kind} Brier', svg)
        for s in ('1.20 s', '1.09 s', '1.12 s', 'A: accuracy, Brier and latency on 460 public tasks.', charts.TITLE,
                  'Jev (native)', 'GPT-6 Luna · Decisions API', 'GPT-6 Luna · this project'):
            self.assertIn(s, svg)
        self.assertNotIn('slower', svg)
        self.assertNotIn('×', svg)

    def test_decision_bench_panel_matches_data(self):
        data = charts.json.loads(charts.DATA.read_text())
        v4 = data['decision_bench_v4']
        svg = charts.render()['public-comparison.svg']
        self.assertEqual(v4['merge_commit'], '8d1563c8740600367172f4bffd2858f400bdd32d')
        self.assertEqual(v4['rows'], 1071)
        for key in ('jev', 'luna_decisions'):
            o = v4['overall'][key]
            self.assertEqual(o['rows'], v4['rows'])
            self.assertAlmostEqual(o['accuracy'], o['correct'] / o['rows'])
            for s in (f'{100 * o["accuracy"]:.1f}% · {o["correct"]}/{o["rows"]}', f'{o["brier"]:.3f}', f'{o["p50_ms"] / 1000:.2f} s'):
                self.assertIn(s, svg)
            for tid in ('AGT-1', 'SAF-2'):
                t = v4['tasks'][tid][key]
                self.assertAlmostEqual(t['accuracy'], t['correct'] / t['rows'])
                self.assertIn(f'{100 * t["accuracy"]:.1f}% · {t["correct"]}/{t["rows"]}', svg)
        self.assertEqual((v4['overall']['luna_decisions']['correct'], v4['overall']['luna_decisions']['rows']), (969, 1071))
        for s in ('B · Same model through the Decisions API', 'Decision Bench v4', 'via OpenRouter', 'AGT-1', 'SAF-2',
                  'Per-task latency and Brier are not published.', 'Brier is comparable within a panel only'):
            self.assertIn(s, svg)

    def test_title_claims_hold_in_data(self):
        data = charts.json.loads(charts.DATA.read_text())
        for r in (data['datasets'][k] for k in data['datasets']):
            self.assertLessEqual(abs(r['luna']['accuracy'] - r['jev']['accuracy']), 0.02)
            self.assertGreater(r['luna']['p50_ms'], r['jev']['p50_ms'])
            self.assertLess(r['luna']['p50_ms'], 1500)
        o = data['decision_bench_v4']['overall']
        self.assertLessEqual(abs(o['luna_decisions']['accuracy'] - o['jev']['accuracy']), 0.02)
        sub = data['pricing']['rates']['product_subscription']
        self.assertIsNone(sub['input_per_million'])
        self.assertIsNone(sub['output_per_million'])
        self.assertIn('Jev-level accuracy', charts.TITLE)
        self.assertIn('no per-request bill', charts.TITLE)
        self.assertIn('~1 s per call', charts.TITLE)

    def test_guard_rejects_unsupported_title(self):
        data = charts.json.loads(charts.DATA.read_text())
        data['datasets']['risk']['luna']['p50_ms'] = data['datasets']['risk']['jev']['p50_ms']
        with self.assertRaises(SystemExit):
            charts.comparison(data)

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
