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
TITLE_SECONDS = 1.7  # this project's measured p50 on Decision Bench v4, checked in comparison()
TITLE = f'LLM decision model: near-Jev accuracy, no per-request bill, ~{TITLE_SECONDS} s per call'
SERIES = {'jev': 'jev', 'luna_decisions': 'dec', 'ours': 'luna'}  # data key -> CSS class
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
    v4 = data['decision_bench_v4']
    ov, gr = v4['overall'], v4['groups']
    rates = data['pricing']['rates']['product_subscription']
    # Title claims: near-Jev accuracy (<= 2 pp overall on Decision Bench v4), the stated seconds per call (measured p50 of this project), no per-request price.
    if (abs(ov['ours']['accuracy'] - ov['jev']['accuracy']) > 0.02 or abs(ov['ours']['p50_ms'] / 1000 - TITLE_SECONDS) > 0.25
            or rates['input_per_million'] is not None or rates['output_per_million'] is not None):
        sys.exit('comparison-data.json no longer supports the chart title; rewrite the title')
    if any(ov[k]['rows'] != v4['rows'] for k in SERIES) or sum(g['rows'] for g in gr.values()) != v4['rows'] or len(v4['tasks']) != v4['task_count']:
        sys.exit('Decision Bench v4 needs identical rows for all three series and groups that add up to the corpus')
    xa, xb, xl, row_h, bw = 150, 355, 515, 56, 12
    brier_max = max(s['brier'] for g in [ov] + list(gr.values()) for s in (g[k] for k in SERIES))
    p50_max = max(s['p50_ms'] for g in [ov] + list(gr.values()) for s in (g[k] for k in SERIES))

    def bar(x, y, w, cls):
        return f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{bw}" rx="2" class="{cls}"/>'

    def row(y, name, subs, g, wide):
        out = [f'<line x1="16" y1="{y - 6}" x2="744" y2="{y - 6}" class="ln"/>', text(16, y + 11, name, 'b')]
        out += [text(16, y + 29 + 16 * i, sub, 'm s') for i, sub in enumerate(subs)]
        for j, (key, cls) in enumerate(SERIES.items()):
            d, by = g[key], y + 16 * j
            ty = by + 11
            wa, wb, wl = 76 * d['accuracy'], 70 * d['brier'] / brier_max, 90 * d['p50_ms'] / p50_max
            acc = f'{100 * d["accuracy"]:.1f}%' + (f' · {d["correct"]}/{d["rows"]}' if wide else '')
            lat = f'{d["p50_ms"] / 1000:.2f} s' + (f' · p95 {d["p95_ms"] / 1000:.2f} s' if wide else '')
            out += [bar(xa, by, wa, cls), text(f'{xa + wa + 6:.1f}', ty, acc, 's'), bar(xb, by, wb, cls), text(f'{xb + wb + 6:.1f}', ty, f'{d["brier"]:.3f}', 's'),
                    bar(xl, by, wl, cls), text(f'{xl + wl + 6:.1f}', ty, lat, 's')]
        return out

    g = [text(16, 28, escape(TITLE), 'h')]
    for x, (key, cls) in zip((16, 156, 368), SERIES.items()):
        g += [f'<rect x="{x}" y="46" width="12" height="12" rx="2" class="{cls}"/>', text(x + 18, 57, v4['series'][key]['label'].replace(', ', ' · '), 's')]
    g += [text(xa, 86, 'Correct answers', 'm s b'), text(xb, 86, 'Brier (lower is better)', 'm s b'), text(xl, 86, 'Median latency (p50)', 'm s b')]
    y0 = 98
    rows = [('All %s rows' % f'{v4["rows"]:,}', [f'{v4["task_count"]} tasks', f'{v4["by_modality"]["image_rows"]["rows"]} image rows'], ov, True)]
    for pre, grp in gr.items():
        rows.append((grp['label'], [f'{grp["rows"]} rows'] + ([f'{grp["image_rows"]} image rows'] if grp['image_rows'] else []), grp, False))
    for i, (name, subs, d, wide) in enumerate(rows):
        g += row(y0 + row_h * i, name, subs, d, wide)
    yb = y0 + row_h * len(rows)
    for x in (xa, xb, xl):
        g.append(f'<line x1="{x}" y1="{y0 - 4}" x2="{x}" y2="{yb - 6}" class="ln"/>')
    ncorp = v4['run']
    g += [f'<line x1="16" y1="{yb - 6}" x2="744" y2="{yb - 6}" class="ln"/>',
          text(16, yb + 14, f'Decision Bench v4: {v4["rows"]:,} rows, the same rows for all three series; ours run {ncorp["date_utc"]}, build {ncorp["build_commit"]}.', 'm s'),
          text(16, yb + 34, (lambda m: f'Jev scored the {m["image_rows"]["rows"]} image rows from text renderings; on the {m["text_rows"]["rows"]} text rows: Jev {100*m["text_rows"]["jev"]["accuracy"]:.1f}%, this project {100*m["text_rows"]["ours"]["accuracy"]:.1f}%.')(v4['by_modality']), 'm s'),
          text(16, yb + 54, 'Decisions API latency was measured by a third party via OpenRouter; ours on one machine through Codex.', 'm s')]
    return svg(760, yb + 66, TITLE,
               'Accuracy, Brier score and median latency of Jev, GPT-6 Luna through the Decisions API and through Luna Decision Model on Decision Bench v4.',
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
