"""Builds the Classroom-style HTML report from a checker Result."""

import html
from datetime import datetime

COLORS = ["#f4a6a6", "#f7c97e", "#a8d8a0", "#9cc8f0", "#d2b3ef", "#f5b8d6",
          "#9fe0d8", "#e8d27a", "#c9c0b0", "#b5c7ff"]


def pct(n, d):
    return 0 if not d else round(100 * n / d)


def build_report(res, doc_name):
    e = html.escape
    sim = pct(res.flagged_words + res.cited_words, res.total_words)
    flagged = pct(res.flagged_words, res.total_words)
    cited = pct(res.cited_words, res.total_words)
    n_flag = sum(1 for s in res.sentences if s.source >= 0 and not s.cited)
    n_cite = sum(1 for s in res.sentences if s.source >= 0 and s.cited)

    if flagged <= 10:
        verdict, vcls = "Looks good. Low uncited similarity.", "ok"
    elif flagged <= 25:
        verdict, vcls = "Review the flagged passages and add citations or reword.", "warn"
    else:
        verdict, vcls = "High uncited similarity. Fix these before submitting.", "bad"

    # sources list
    src_rows = []
    for i, s in enumerate(res.sources):
        col = COLORS[i % len(COLORS)]
        link = (f'<a href="{e(s.url)}" target="_blank" rel="noopener">{e(s.title or s.url)}</a>'
                if s.url.startswith("http") else e(s.title))
        where = "" if not s.url.startswith("http") else f'<div class="url">{e(s.url[:90])}</div>'
        src_rows.append(
            f'<li data-src="{i}"><span class="sw" style="background:{col}"></span>'
            f'<div class="st">{link}{where}<div class="kind">{e(s.kind)}</div></div>'
            f'<span class="sp">{pct(s.words_matched, res.total_words)}%</span></li>')
    if not src_rows:
        src_rows.append('<li class="none">No matching sources found.</li>')

    # body
    paras, cur, cur_p = [], [], None
    for s in res.sentences:
        if cur_p is not None and s.para != cur_p:
            paras.append(cur)
            cur = []
        cur_p = s.para
        t = e(s.text)
        if s.source >= 0:
            src = res.sources[s.source]
            col = COLORS[s.source % len(COLORS)]
            tip = e(f"{'Cited/quoted · ' if s.cited else 'Flagged · '}{round(s.score*100)}% match · {src.title}")
            cls = "hl cited" if s.cited else "hl"
            t = (f'<span class="{cls}" data-src="{s.source}" style="--c:{col}" title="{tip}">{t}'
                 f'<sup>{s.source + 1}</sup></span>')
        cur.append(t)
    if cur:
        paras.append(cur)
    body = "\n".join(f"<p>{' '.join(p)}</p>" for p in paras)

    notes = "".join(f"<li>{e(n)}</li>" for n in res.notes)
    refs = ""
    if res.excluded_refs:
        refs = (f'<details class="refs"><summary>Excluded: References section</summary>'
                f'<pre>{e(res.excluded_refs[:20000])}</pre></details>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Originality Report</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1f2328;--mute:#616a75;--line:#e3e6ea;--ok:#1e7d43;--warn:#a06100;--bad:#b42318;}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#16181b;--card:#1f2226;--ink:#e8eaed;--mute:#9aa3ad;--line:#33373c;--ok:#5cc489;--warn:#e7a94a;--bad:#f2817a;}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px 60px;display:grid;grid-template-columns:1fr 340px;gap:20px}}
header{{grid-column:1/-1}}
h1{{font-size:20px;margin:0 0 2px}} .sub{{color:var(--mute);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}}
.doc p{{margin:0 0 14px}}
.hl{{background:color-mix(in srgb,var(--c) 70%,transparent);border-radius:3px;padding:1px 2px;cursor:pointer;color:#111}}
.hl.cited{{background:transparent;border-bottom:2px dashed var(--c);color:inherit}}
.hl sup{{font-size:10px;font-weight:700;margin-left:2px}}
.hl.dim{{opacity:.25}}
aside{{position:sticky;top:16px;align-self:start;display:flex;flex-direction:column;gap:16px}}
.big{{font-size:44px;font-weight:700;line-height:1}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}}
.stat{{border:1px solid var(--line);border-radius:8px;padding:8px 10px}}
.stat b{{font-size:20px;display:block}} .stat span{{font-size:12px;color:var(--mute)}}
.verdict{{margin-top:12px;font-weight:600;font-size:14px}} .verdict.ok{{color:var(--ok)}} .verdict.warn{{color:var(--warn)}} .verdict.bad{{color:var(--bad)}}
h2{{font-size:14px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}}
ul.src{{list-style:none;margin:0;padding:0}}
ul.src li{{display:flex;gap:10px;align-items:flex-start;padding:8px 6px;border-radius:8px;cursor:pointer}}
ul.src li:hover,ul.src li.on{{background:color-mix(in srgb,var(--line) 60%,transparent)}}
.sw{{width:14px;height:14px;border-radius:4px;flex:none;margin-top:4px}}
.st{{flex:1;min-width:0;font-size:13px}} .st a{{color:var(--ink);font-weight:600;word-break:break-word}}
.url,.kind{{color:var(--mute);font-size:11px;word-break:break-all}}
.sp{{font-weight:700;font-size:13px}}
.legend{{font-size:12px;color:var(--mute);display:flex;gap:14px;flex-wrap:wrap;margin-bottom:12px}}
.legend i{{display:inline-block;width:22px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}}
.notes{{font-size:12px;color:var(--mute);padding-left:18px;margin:0}}
.refs summary{{cursor:pointer;color:var(--mute);font-size:13px}} .refs pre{{white-space:pre-wrap;font-size:12px;color:var(--mute)}}
@media (max-width:860px){{.wrap{{grid-template-columns:1fr}} aside{{position:static;order:-1}}}}
@media print{{aside{{position:static}} .wrap{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="wrap">
<header><h1>Originality report</h1>
<div class="sub">{e(doc_name)} · {datetime.now().strftime("%b %d, %Y %I:%M %p")} · {res.total_words:,} words checked</div></header>

<main class="card doc">
<div class="legend"><span><i style="background:#f4a6a6"></i>Flagged (no citation)</span>
<span><i style="border-bottom:2px dashed #888;height:6px"></i>Cited or quoted</span>
<span>Click a passage or source to focus it</span></div>
{body}
{refs}
</main>

<aside>
<div class="card">
<div class="big">{flagged}%</div>
<div class="sub">uncited similarity: the number to worry about</div>
<div class="stats">
<div class="stat"><b>{n_flag}</b><span>flagged passages</span></div>
<div class="stat"><b>{n_cite}</b><span>cited / quoted</span></div>
<div class="stat"><b>{sim}%</b><span>total similarity</span></div>
<div class="stat"><b>{len(res.sources)}</b><span>matching sources</span></div>
</div>
<div class="verdict {vcls}">{verdict}</div>
</div>
<div class="card"><h2>Sources</h2><ul class="src">{''.join(src_rows)}</ul></div>
{f'<div class="card"><h2>Notes</h2><ul class="notes">{notes}</ul></div>' if notes else ''}
</aside>
</div>
<script>
(function(){{
  let active=null;
  function focus(id){{
    active = (active===id)?null:id;
    document.querySelectorAll('.hl').forEach(h=>h.classList.toggle('dim', active!==null && h.dataset.src!==active));
    document.querySelectorAll('ul.src li').forEach(l=>l.classList.toggle('on', l.dataset.src===active));
    if(active!==null){{const f=document.querySelector('.hl[data-src="'+active+'"]'); if(f) f.scrollIntoView({{behavior:'smooth',block:'center'}});}}
  }}
  document.querySelectorAll('.hl').forEach(h=>h.addEventListener('click',()=>focus(h.dataset.src)));
  document.querySelectorAll('ul.src li[data-src]').forEach(l=>l.addEventListener('click',ev=>{{if(ev.target.tagName!=='A')focus(l.dataset.src)}}));
}})();
</script>
</body></html>"""
