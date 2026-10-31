"""Unchanged scientific publication checks, plus V5's literal figure references."""
from .reuse import load

base = load('_publication_base', 'publication.py')
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)


def publish(toolkit, args, deadline):
    from .consulted import graphic_urls
    original = toolkit.state.get('web_sources')
    assets = graphic_urls(toolkit, args.evidence_ids)
    if not assets:
        return base.publish(toolkit, args, deadline)
    # The existing validator's allowlist is reused, not weakened. The transient
    # view is never persisted as a Web discovery or a scientific reading.
    toolkit.state['web_sources'] = [*(original or []),
        *({'url': url, 'title': 'Référence graphique extraite'} for url in sorted(assets))]
    try:
        return base.publish(toolkit, args, deadline)
    finally:
        if original is None:
            toolkit.state.pop('web_sources', None)
        else:
            toolkit.state['web_sources'] = original
