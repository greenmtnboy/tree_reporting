"""Explicit bulk actions; individual annotation writes always need an owner city."""


def publish_cities(contexts, publish):
    results = []
    for city, context in contexts.items():
        try:
            publish(context)
            results.append({'city': city, 'ok': True})
        except (OSError, ValueError, KeyError) as error:
            results.append({'city': city, 'ok': False, 'error': str(error)})
    return {'ok': bool(results) and all(row['ok'] for row in results), 'cities': results}
