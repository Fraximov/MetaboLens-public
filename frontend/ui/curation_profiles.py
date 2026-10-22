"""Present package-owned rule definitions without evaluating scientific rules."""
from dash import html


RULE_LABELS = {
    'has_annotation': 'Annotation present',
    'has_ms2': 'MS2 spectrum present',
    'minimum_sample_detection_pct': 'Sample detection',
    'minimum_group_detection_pct': 'Detection in the best group',
    'maximum_qc_cv_pct': 'QC variability (CV)',
    'blank_to_sample_median_ratio': 'Blank / sample signal ratio',
}


def _rule_limits(rule: dict) -> str:
    params = rule['params']
    percent = rule['rule_id'].endswith('_pct')
    parts = []
    for key, value in params.items():
        if key == 'on_false':
            parts.append(f'{str(value).capitalize()} when absent')
        else:
            label = key.replace('_', ' ').capitalize()
            number = f'{value:g}' if isinstance(value, (int, float)) else str(value)
            parts.append(f'{label} {number}{"%" if percent else ""}')
    return '; '.join(parts)


def curation_rule_options(profile: dict, domain: str) -> list[dict]:
    return [{'value': rule['rule_id'], 'label': html.Span([
        html.Span(RULE_LABELS.get(rule['rule_id'], rule['rule_id'].replace('_', ' ').capitalize()),
                  className='fw-semibold'),
        html.Span(_rule_limits(rule), className='d-block text-muted small mb-2'),
    ])} for rule in profile[domain]]


def curation_rule_list(profile: dict | None):
    if profile is None:
        return []
    sections = []
    for domain, label in (('statistics', 'Statistics'), ('annotation', 'Annotation')):
        rules = profile[domain]
        sections.append(html.Div([
            html.H6(label, className='mb-2'),
            html.Ul([
                html.Li([
                    html.Span(RULE_LABELS.get(rule['rule_id'], rule['rule_id'].replace('_', ' ').capitalize()),
                              className='fw-semibold'),
                    html.Div(_rule_limits(rule), className='text-muted small'),
                ], className='mb-2') for rule in rules
            ], className='list-unstyled mb-0') if rules else html.P('No automatic filtering rules.', className='text-muted small'),
        ], className='col-md-6'))
    return html.Div(sections, className='row g-3')
