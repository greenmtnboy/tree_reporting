"""Shared first/second-pass review choices for chip and registration galleries."""
import re

REVIEW_STATUSES = (
    ('all', 'All statuses'), ('pending', 'Unreviewed'),
    ('not-final', 'Not final reviewed'), ('second-pending', 'First pass only'),
    ('done', 'Done (any pass)'), ('more-done', 'Final reviewed'),
)


def inject_review_status_options(html):
    # Coverage's separate workflow status (including not-in-review) is not a
    # first/second-pass completion selector and intentionally does not match.
    for identifier, all_value in [('review-status', 'all'), ('status', '')]:
        options = ''.join(f'<option value="{all_value if value == "all" else value}">{label}</option>'
                          for value, label in REVIEW_STATUSES)
        html = re.sub(f'(<select id="{identifier}">).*?(</select>)',
                      lambda match: match[1] + options + match[2], html, flags=re.S)
    return html
