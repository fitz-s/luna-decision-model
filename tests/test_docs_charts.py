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
            self.assertIn(f'{kind} Brier', svg)
            for side in ('luna', 'jev'):
                self.assertIn(f'{data["datasets"][suite][side]["p50_ms"] / 1000:.2f} s', svg)
        for s in ('1.20 s', '1.09 s', '1.12 s', 'Accuracy, Brier and latency: 460 public tasks.'):
            self.assertIn(s, svg)
        self.assertNotIn('slower', svg)
        self.assertNotIn('×', svg)

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

    def test_api_routes_numbers_match_data(self):
        data = charts.json.loads(charts.DATA.read_text())
        dec = data['api_routes']['decisions']
        self.assertTrue(dec['vendor_claim'])
        self.assertEqual(dec['input_per_million'], data['pricing']['rates']['openai_decisions']['input_per_million'])
        _, _, usd, credits, _ = charts.cost_per_1000(data)
        svg = charts.render()['api-routes.svg']
        for s in (f'${usd["openai_decisions"]:.3f}', f'${usd["ordinary_luna_api_reference"]:.3f}', f'≈{credits:.1f} credits',
                  f'≈{dec["speed_multiplier_vs_responses"]}× faster', dec['status'], 'OpenAI claim', 'Codex ChatGPT',
                  'GPT-6 Luna three ways: metered API key, or your Codex login', "Speed is OpenAI's published claim, not measured here."):
            self.assertIn(s, svg)

    def test_removed_baseline_chart_stays_removed(self):
        self.assertFalse((ROOT / 'docs/assets/baseline-development.svg').exists())


if __name__ == '__main__':
    unittest.main()
