"""Render docs/assets/*.svg deterministically from docs/comparison-data.json (stdlib only)."""
import argparse
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/comparison-data.json'
ASSETS = ROOT / 'docs/assets'
SUITES = [('risk', 'Risk', 'multiclass'), ('toxicity', 'Toxicity', 'binary'), ('tool_select', 'Tool selection', 'multiclass')]  # Brier kind: docs/COMPARISON.md

# Palette: dataviz reference slots 1 (blue) and 2 (orange) plus purple for the Decisions API, validated in light and dark.
TITLE = 'LLM decision model: Jev-level accuracy, no per-request bill, ~1 s per call'
CSS = """:root{color-scheme:light dark;--ink:#1f2328;--mute:#59636e;--line:#d1d9e0;--luna:#2a78d6;--jev:#eb6834;--cloud:#0f6cbd;--api:#6e7781;--dec:#8250df}
@media (prefers-color-scheme:dark){:root{--ink:#e6edf3;--mute:#9198a1;--line:#3d444d;--luna:#3987e5;--jev:#d95926;--cloud:#58a6ff;--api:#8b949e;--dec:#a371f7}}
text{font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;fill:var(--ink)}
.m{fill:var(--mute)}.s{font-size:13px}.b{font-weight:600}.h{font-size:19px;font-weight:700}.c{text-anchor:middle}
.api{fill:var(--api)}.dec{fill:var(--dec)}.pill{fill:none;stroke:var(--luna);stroke-width:2}.luna{fill:var(--luna)}.jev{fill:var(--jev)}.ln{stroke:var(--line);stroke-width:1;fill:none}
.box{fill:none;stroke:var(--line);stroke-width:1.5}.cloud{fill:none;stroke:var(--cloud);stroke-width:2}
.ar{stroke:var(--mute);stroke-width:1.8;fill:none}.arh{fill:var(--mute)}"""


def svg(width, height, title, desc, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">\n'
            f'<title id="t">{escape(title)}</title>\n<desc id="d">{escape(desc)}</desc>\n'
            f'<style>\n{CSS}\n</style>\n{body}\n</svg>\n')


def text(x, y, s, cls='', extra=''):
    return f'<text x="{x}" y="{y}" class="{cls}"{extra}>{s}</text>'


def architecture():
    def node(x, lines, subs, cls):
        out = [f'<rect x="{x}" y="72" width="170" height="116" rx="10" class="{cls}"/>']
        for i, s in enumerate(lines):
            out.append(text(x + 85, 102 + 20 * i, s, 'b c', ' style="font-size:16px"'))
        for i, s in enumerate(subs):
            out.append(text(x + 85, 152 + 18 * i, s, 'm s c'))
        return out

    def arrow(x1, x2, y, right):
        a, b = (x1, x2) if right else (x2, x1)
        tip = f'{b - 7},{y - 4} {b},{y} {b - 7},{y + 4}' if right else f'{b + 7},{y - 4} {b},{y} {b + 7},{y + 4}'
        return [f'<path d="M{a} {y}H{b + (-1 if right else 1)}" class="ar"/>', f'<polygon points="{tip}" class="arh"/>']

    g = ['<rect x="8" y="40" width="472" height="166" rx="14" class="box" stroke-dasharray="6 5"/>',
         text(22, 60, 'YOUR MACHINE', 'm s b', ' letter-spacing="1"')]
    g += node(24, ['Your Jev /', 'TypeSafe app'], ['your existing code'], 'box')
    g += node(304, ['Luna Decision', 'Model'], ['local service on your', 'machine, no model'], 'box')
    g += node(584, ['OpenAI', 'GPT-6 Luna'], ['cloud, your ChatGPT /', 'Codex subscription'], 'cloud')
    for x1, x2, up, down in [(198, 300, 'typed questions', 'typed answers'), (490, 576, 'your text', 'model answer')]:
        g += arrow(x1, x2, 118, True) + arrow(x1, x2, 150, False)
        g += [text((x1 + x2) // 2, 108, up, 'm s c'), text((x1 + x2) // 2, 176, down, 'm s c')]
    return svg(760, 222, 'How it works',
               'Your Jev or TypeSafe app calls a local Luna Decision Model service, which calls OpenAI GPT-6 Luna with your Codex login.',
               '\n'.join(g))


def comparison(data):
    rows = {k: data['datasets'][k] for k, _, _ in SUITES}
    ms = {(k, side): rows[k][side]['p50_ms'] for k, _, _ in SUITES for side in ('luna', 'jev')}
    gaps = [abs(r['luna']['accuracy'] - r['jev']['accuracy']) for r in rows.values()]
    v4 = data['decision_bench_v4']
    jev4, api4 = v4['overall']['jev'], v4['overall']['luna_decisions']
    rates = data['pricing']['rates']['product_subscription']
    # Title claims: Jev-level accuracy (<= 2 pp on our suites and on Decision Bench v4), ~1 s p50 on this project (slower than Jev), no per-request price.
    if (max(gaps) > 0.02 or abs(jev4['accuracy'] - api4['accuracy']) > 0.02 or any(not ms[k, 'jev'] < ms[k, 'luna'] < 1500 for k in rows)
            or rates['input_per_million'] is not None or rates['output_per_million'] is not None):
        sys.exit('comparison-data.json no longer supports the chart title; rewrite the message')
    if jev4['rows'] != api4['rows'] or jev4['rows'] != v4['rows']:
        sys.exit('Decision Bench v4 panel needs identical rows for both models')
    title = TITLE
    c1, c2, c3, aw = 140, 350, 605, 84
    p50 = max(list(ms.values()) + [jev4['p50_ms'], api4['p50_ms']])
    brier_a = max(r[s]['brier'] for r in rows.values() for s in ('luna', 'jev'))
    brier_b = max(jev4['brier'], api4['brier'])
    _, _, usd, credits, _ = cost_per_1000(data)

    def bar(x, y, w, cls):
        return f'<rect x="{x}" y="{y}" width="{w:.1f}" height="14" rx="2" class="{cls}"/>'

    def metrics(y, side, acc, lat, brier, bscale):
        """One series' bars on one row: accuracy, p50 latency and Brier (None leaves the cell empty)."""
        ty = y + 11
        out = [bar(c1, y, aw * acc['accuracy'], side), text(c1 + aw * acc['accuracy'] + 6, ty, f'{100 * acc["accuracy"]:.1f}% · {acc["correct"]}/{acc.get("n", acc.get("rows"))}', 's')]
        if lat is not None:
            out += [bar(c2, y, 90 * lat / p50, side), text(c2 + 90 * lat / p50 + 6, ty, f'{lat / 1000:.2f} s', 's')]
        if brier is not None:
            out += [bar(c3, y, 80 * brier / bscale, side), text(c3 + 80 * brier / bscale + 6, ty, f'{brier:.3f}', 's')]
        return out

    def rules(y, h, cols=(c1, c2, c3)):
        return [f'<line x1="{c}" y1="{y - 4}" x2="{c}" y2="{y + h}" class="ln"/>' for c in cols]

    def heads(y):
        return [text(c1, y, 'Correct answers', 'm s b'), text(c2, y, 'Median latency (p50)', 'm s b'), text(c3, y, 'Brier (lower is better)', 'm s b')]

    sw = [(16, 'jev', 'Jev (native)'), (156, 'dec', 'GPT-6 Luna · Decisions API'), (368, 'luna', 'GPT-6 Luna · this project')]
    g = [text(16, 28, escape(title), 'h')]
    for x, cls, label in sw:
        g += [f'<rect x="{x}" y="46" width="12" height="12" rx="2" class="{cls}"/>', text(x + 18, 57, label, 's')]
    # Panel A: our 460 public tasks, Jev vs this project.
    g += [text(16, 92, 'A · Our 460 public tasks: Jev vs this project', 'b')] + heads(114)
    for i, (key, name, kind) in enumerate(SUITES):
        y = 128 + 60 * i
        r = rows[key]
        g += [f'<line x1="16" y1="{y - 8}" x2="744" y2="{y - 8}" class="ln"/>', text(16, y + 15, name, 'b'), text(16, y + 33, f'{kind} Brier', 'm s')]
        for j, side in enumerate(('jev', 'luna')):
            d = r[side]
            g += metrics(y + 22 * j, side, d, ms[key, side], d['brier'], brier_a)
        g += rules(y, 38)
    # Panel B: same model through the Decisions API, identical rows.
    yb = 128 + 60 * 3
    g += [f'<line x1="16" y1="{yb - 8}" x2="744" y2="{yb - 8}" class="ln"/>',
          text(16, yb + 18, 'B · Same model through the Decisions API: independent run, Decision Bench v4', 'b'),
          text(16, yb + 36, f'Jev vs Luna Decisions API on the same {jev4["rows"]:,} rows.', 'm s')] + heads(yb + 60)
    y = yb + 74
    g += [f'<line x1="16" y1="{y - 8}" x2="744" y2="{y - 8}" class="ln"/>', text(16, y + 15, 'All tasks', 'b'), text(16, y + 33, f'{jev4["rows"]:,} rows', 'm s')]
    for j, (side, d) in enumerate((('jev', jev4), ('dec', api4))):
        g += metrics(y + 22 * j, side, d, d['p50_ms'], d['brier'], brier_b)
    g += rules(y, 38)
    for i, tid in enumerate(('AGT-1', 'SAF-2')):
        y = yb + 74 + 60 + 52 * i
        t = v4['tasks'][tid]
        g += [f'<line x1="16" y1="{y - 8}" x2="744" y2="{y - 8}" class="ln"/>', text(16, y + 15, tid, 'b'), text(16, y + 33, t['name'].replace(' or not', ''), 'm s')]
        for j, (side, key) in enumerate((('jev', 'jev'), ('dec', 'luna_decisions'))):
            g += metrics(y + 22 * j, side, t[key], None, None, 1)
        g += rules(y, 38, (c1,))
    g.append(text(c2, yb + 74 + 60 + 26, 'Per-task latency and Brier are not published.', 'm s'))
    # Cost strip and sources.
    yc = yb + 74 + 60 + 104 - 8
    g += [f'<line x1="16" y1="{yc}" x2="744" y2="{yc}" class="ln"/>', text(16, yc + 24, 'Per 1,000 requests', 'b')]
    for x, cls, label in ((176, 'jev', f'Jev ${usd["typesafe_jev"]:.3f}'), (326, 'dec', f'Decisions API ${usd["openai_decisions"]:.3f}'),
                          (536, 'luna', f'This project ≈{credits:.1f} plan credits')):
        g += [f'<rect x="{x}" y="{yc + 13}" width="12" height="12" rx="2" class="{cls}"/>', text(x + 18, yc + 24, label, 's')]
    ys = yc + 50
    g += [f'<line x1="16" y1="{ys - 14}" x2="744" y2="{ys - 14}" class="ln"/>',
          text(16, ys, 'A: accuracy, Brier and latency on 460 public tasks. Cost: rate cards at 618 input / 26 output tokens per request.', 'm s'),
          text(16, ys + 20, 'B: Decision Bench v4, run 2026-10-08; Luna latency measured via OpenRouter.', 'm s'),
          text(16, ys + 40, 'Brier is comparable within a panel only: A scores binary tasks as one squared error, B sums over all classes.', 'm s')]
    return svg(760, ys + 50, title,
               'Jev, GPT-6 Luna through the Decisions API and through Luna Decision Model: accuracy, median latency and Brier score, plus cost.',
               '\n'.join(g))


API_ROWS = [('typesafe_jev', 'Native Jev API'), ('openai_decisions', 'OpenAI Decisions API'), ('ordinary_luna_api_reference', 'GPT-6 Luna API')]


def cost_per_1000(data):
    """(tokens in/out per request, {rate key: USD per 1,000 requests}, plan credits per 1,000 requests, n requests)."""
    n = sum(data['datasets'][k]['luna']['n'] for k, _, _ in SUITES)
    use = data['observed_luna_usage']
    tin, tout = use['input_tokens'] / n, use['output_tokens'] / n
    usd = {k: (tin * data['pricing']['rates'][k]['input_per_million'] + tout * data['pricing']['rates'][k]['output_per_million']) / 1000 for k, _ in API_ROWS}
    credit = data['subscription_quota']['official']['standard_paid_credits_per_million']
    return tin, tout, usd, (tin * credit['uncached_input'] + tout * credit['output']) / 1000, n


def cost(data):
    tin, tout, usd, credits, n = cost_per_1000(data)
    title = f'Per 1,000 requests: ${min(usd.values()):.3f}–${max(usd.values()):.3f} on APIs, ≈{credits:.1f} credits on a Codex plan'
    x0, scale = 240, 340 / max(usd.values())
    g = [text(16, 28, escape(title), 'h'), text(x0, 62, 'API rate cards, USD', 'm s b')]
    for i, (key, name) in enumerate(API_ROWS):
        y = 76 + 32 * i
        w = usd[key] * scale
        g += [text(16, y + 14, name, ''), f'<rect x="{x0}" y="{y}" width="{w:.1f}" height="18" rx="2" class="api"/>',
              text(f'{x0 + w + 6:.1f}', y + 14, f'${usd[key]:.3f}', 's')]
    g += [f'<line x1="{x0}" y1="70" x2="{x0}" y2="164" class="ln"/>', '<line x1="16" y1="178" x2="744" y2="178" class="ln"/>',
          text(x0, 202, 'Codex plan, credit-rate equivalent', 'm s b'),
          text(16, 226, 'Luna Decision Model', 'b'),
          f'<rect x="{x0}" y="210" width="330" height="32" rx="8" class="pill"/>',
          text(x0 + 165, 231, f'≈{credits:.1f} credits at Luna\'s credit rate', 'b c'),
          '<line x1="16" y1="262" x2="744" y2="262" class="ln"/>',
          text(16, 286, f'Same token mix for every row: {tin:.0f} input / {tout:.0f} output tokens per request ({n} public tasks).', 'm s')]
    return svg(760, 298, title, 'Cost of 1,000 requests on three API rate cards in US dollars, and on Luna Decision Model as credits at Luna\'s credit rate.', '\n'.join(g))


def render():
    data = json.loads(DATA.read_text())
    return {'architecture.svg': architecture(), 'public-comparison.svg': comparison(data), 'cost.svg': cost(data)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true', help='exit 1 if committed SVGs differ from rendered output')
    check = ap.parse_args().check
    stale = []
    for name, content in render().items():
        path = ASSETS / name
        if check:
            if not path.exists() or path.read_text(encoding='utf-8') != content: stale.append(name)
        else:
            path.write_text(content, encoding='utf-8', newline='\n')
    if stale: sys.exit(f'stale: {", ".join(stale)}; run scripts/render_docs_charts.py')


if __name__ == '__main__':
    main()
