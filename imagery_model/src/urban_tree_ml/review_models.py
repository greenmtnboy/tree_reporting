"""Lightweight chip-review model selection, independent of score loading."""
from html import escape


def selected_model_review_id(run_id):
    """Repair links emitted when paired-parent audits appeared in the selector."""
    if not run_id or '::' not in run_id:
        return run_id
    family, cohort = run_id.split('::', 1)
    if cohort == 'parent-validation':
        return family
    if cohort in ('parent-validation-usbos', 'parent-validation-usnyc'):
        return family + '::' + cohort.removeprefix('parent-')
    return run_id


def model_choices(records, city=None):
    choices = {}
    # Keep validation-backed models first so adding a review import preserves
    # the existing default selection while making the import navigable.
    for record in sorted(records, key=lambda r: (r['metrics'].get('split') == 'validation'
                                                 and not r['metrics'].get('prediction_only'),
                                                 str(r['created_at'])), reverse=True):
        if city and city != 'all' and record['city'].lower() != city.lower():
            continue
        metrics = record['metrics']
        if metrics.get('split') != 'validation' and not (
                metrics.get('split') == 'train' and metrics.get('prediction_only')):
            continue
        if not (record['evaluation_dir'].parent.parent / 'COMPLETE').exists():
            continue
        choices.setdefault(record['training_run_id'], record)
    return list(choices.values())


def default_review_model(records, city, acquisition=None, preferred_acquisition=None):
    """Choose within the requested city, including cities with only dated imagery."""
    from urban_tree_ml.temporal_studio import acquisition_records
    choices = model_choices(acquisition_records(records, acquisition), city)
    if not choices and not acquisition:
        if preferred_acquisition:
            choices = model_choices(acquisition_records(records, preferred_acquisition), city)
        if not choices:
            choices = model_choices(records, city)
    return choices[0] if choices else None


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
