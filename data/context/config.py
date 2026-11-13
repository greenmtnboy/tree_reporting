"""City coverage and provider choices, independent of any poster or tree snapshot."""
from datetime import date

BUCKET = 'trilogy_public_models'
PREFIX = 'duckdb/context'
PUBLIC = f'https://storage.googleapis.com/{BUCKET}/{PREFIX}'
CITIES = {
    'USBOS': {
        'bbox': [-71.24541865400175, 42.230243742416064, -70.95905172929986, 42.449156933463016],
        'crs': 'EPSG:32619',
        'season_months': [6, 8],
        'heat_collection': 'landsat-c2-l2',
        'water_service': 'https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Water_Features/FeatureServer/8',
        'water_where': 'POLY_CODE IN (1,6,8)',
        'water_credit': 'USGS / MassDEP hydrography via MassGIS',
        'water_source_url': 'https://www.mass.gov/info-details/massgis-data-massdep-hydrography-125000',
    },
}


def latest_summer(today=None):
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


def filenames(city):
    if city not in CITIES:
        raise ValueError(f'No configured context sources for {city}')
    prefix = city.lower()
    return (f'{prefix}_surface_temperature_v1.parquet', f'{prefix}_water_v1.parquet',
            f'{prefix}_context_v1.json')
