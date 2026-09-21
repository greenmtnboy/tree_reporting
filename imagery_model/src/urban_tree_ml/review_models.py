"""Lightweight chip-review model selection, independent of score loading."""
from html import escape


def model_choices(records, city=None):
    choices = {}
    for record in sorted(records, key=lambda r: str(r['created_at']), reverse=True):
        if city and city != 'all' and record['city'].lower() != city.lower():
            continue
        if record['metrics'].get('split') != 'validation':
            continue
        if not (record['evaluation_dir'].parent.parent / 'COMPLETE').exists():
            continue
        choices.setdefault(record['training_run_id'], record)
    return list(choices.values())


def model_selector(html, choices, selected):
    options = ''.join(f'<option value="{escape(str(r["run_id"]), quote=True)}"'
                      f'{" selected" if str(selected).split("::")[0] == r["training_run_id"] else ""}>'
                      f'{escape(r["training_run_id"])}</option>' for r in choices)
    control = '<label>Model <select id="review-model">' + options + '</select></label>'
    html = html.replace('<div class="controls">', '<div class="controls">' + control, 1)
    script = """<script>
document.getElementById('review-model').addEventListener('change',event=>{
 const url=new URL(location.href);url.searchParams.set('run',event.target.value);
 for(const key of ['chip','queue','queue_chip'])url.searchParams.delete(key);
 location.href=url.pathname+url.search;
});</script>"""
    return html.replace('</body>', script + '</body>')
