/**
 * Display labels for the `data_source` column carried by every tree row.
 *
 * The stored values are the per-city `{code}_source` enums declared in each
 * city's Trilogy model (see data/raw/core.preql for why the enums are per-city
 * rather than global); the value list itself lives in DATA_SOURCES in
 * data/raw/shared/ingest.py. This map is only the picklist's display side —
 * an unmapped value falls back to a readable form of the raw value rather than
 * disappearing, so a newly added city shows something sensible before anyone
 * gets round to naming it here.
 */
export const DATA_SOURCE_LABELS: Record<string, string> = {
  SYDNEY_OPENDATA: 'Sydney Open Data',
  GRENOBLE_OPENDATA: 'Grenoble Open Data',
  MONTPELLIER_OPENDATA: 'Montpellier Open Data',
  LOUISVILLE_OPENDATA: 'Louisville Open Data',
  KITCHENER_OPENDATA: 'Kitchener Open Data',
  SANDIEGO_OPENDATA: 'San Diego Open Data',
  ANTWERPEN_OPENDATA: 'Antwerp Open Data',
  RENNES_OPENDATA: 'Rennes Open Data',
  BERN_OPENDATA: 'Bern Open Data',
  EINDHOVEN_OPENDATA: 'Eindhoven Open Data',
  STRASBOURG_OPENDATA: 'Strasbourg Open Data',
  BORDEAUX_OPENDATA: 'Bordeaux Open Data',
  WELLINGTON_OPENDATA: 'Wellington Open Data',
  LEIPZIG_OPENDATA: 'Leipzig Open Data',
  GENEVA_SITG: 'Geneva Open Data',
  TOULOUSE_OPENDATA: 'Toulouse Open Data',
  UTRECHT_OPENDATA: 'Utrecht Open Data',
  HAMILTON_OPENDATA: 'Hamilton Open Data',
  BRISTOL_OPENDATA: 'Bristol Open Data',
  GHENT_OPENDATA: 'Ghent Open Data',
  LISBON_OPENDATA: 'Lisbon Open Data',
  CANBERRA_OPENDATA: 'Canberra Open Data',
  FINGAL_OPENDATA: 'Fingal Open Data',
  CHRISTCHURCH_OPENDATA: 'Christchurch Open Data',
  FRANKFURT_OPENDATA: 'Frankfurt Open Data',
  COLOGNE_OPENDATA: 'Cologne Open Data',
  BALTIMORE_OPENDATA: 'Baltimore Open Data',
  MADRID_OPENDATA: 'Madrid Open Data',
  LYON_OPENDATA: 'Lyon Open Data',
  HAMBURG_OPENDATA: 'Hamburg Open Data',
  SACRAMENTO_OPENDATA: 'Sacramento Open Data',
  SANJOSE_OPENDATA: 'San Jose Open Data',
  PITTSBURGH_OPENDATA: 'Pittsburgh Open Data',
  PHILADELPHIA_OPENDATA: 'Philadelphia Open Data',
  PORTLAND_OPENDATA: 'Portland Open Data',
  SEATTLE_OPENDATA: 'Seattle Open Data',
  BASEL_OPENDATA: 'Basel Open Data',
  NOTTINGHAM_OPENDATA: 'Nottingham Open Data',
  ZURICH_OPENDATA: 'Zürich Open Data',
  EUGENE_OPENDATA: 'Eugene Open Data',
  VIENNA_OPENDATA: 'Vienna Open Data',
  COLUMBUS_OPENDATA: 'Columbus Open Data',
  WODONGA_OPENDATA: 'Wodonga Open Data',
  BALLARAT_OPENDATA: 'Ballarat Open Data',
  PERTH_OPENDATA: 'Perth Open Data',
  ADELAIDE_OPENDATA: 'Adelaide Open Data',
  NAPERVILLE_OPENDATA: 'City of Naperville',
  BOULDER_OPENDATA: 'City of Boulder',
  MILAN_OPENDATA: 'Milan Open Data',
  NOTABLE_ITMIL: 'Monumental Trees of Italy (Milan)',
  NOTABLE_AUBAL: 'City of Ballarat - Exceptional Tree Register',
  CHARLOTTE_OPENDATA: 'Charlotte Open Data',
  NAPLES_OPENDATA: 'City of Naples',
  TREES_ATLANTA: 'Trees Atlanta',
  NEW_ORLEANS_OPENDATA: 'New Orleans Open Data',
  DURHAM_OPENDATA: 'Durham Open Data',
  HOUSTON_OPENDATA: 'Houston Open Data',
  AUSTIN_OPENDATA: 'Austin Open Data',
  ARLINGTON: 'Arlington County Open Data',
  FALLS_CHURCH: 'City of Falls Church GIS',
  PRAGUE_OPENDATA: 'Prague Open Data',
  STOUGHTON_OPENDATA: 'Stoughton, WI Open Data',
  HELSINKI_OPENDATA: 'Helsinki Open Data',
  COPENHAGEN_OPENDATA: 'Copenhagen Open Data',
  TAIPEI_OPENDATA: 'Taipei Open Data',
  BOGOTA_OPENDATA: 'Bogotá Open Data',
  TOKYO_OPENDATA: 'Tokyo Open Data',
  MONCTON_OPENDATA: 'Moncton Open Data',
  AJAX_OPENDATA: 'Ajax Open Data',
  BURLINGTON_ON_OPENDATA: 'Burlington, ON Open Data',
  OTTAWA_OPENDATA: 'Ottawa Open Data',
  MISSISSAUGA_OPENDATA: 'Mississauga Open Data',
  NEWWESTMINSTER_OPENDATA: 'New Westminster Open Data',
  KELOWNA_OPENDATA: 'Kelowna Open Data',
  VICTORIA_OPENDATA: 'Victoria Open Data',
  LETHBRIDGE_OPENDATA: 'Lethbridge Open Data',
  KINGSTON_OPENDATA: 'Kingston Open Data',
  HALIFAX_OPENDATA: 'Halifax Open Data',
  LONGUEUIL_OPENDATA: 'Longueuil Open Data',
  QUEBEC_OPENDATA: 'Quebec City Open Data',
  MONTREAL_OPENDATA: 'Montreal Open Data',
  TORONTO_OPENDATA: 'Toronto Open Data',
  WINNIPEG_OPENDATA: 'Winnipeg Open Data',
  EDMONTON_OPENDATA: 'Edmonton Open Data',
  CALGARY_OPENDATA: 'Calgary Open Data',
  SF_OPENDATA: 'SF Open Data',
  NYC_OPENDATA: 'NYC Open Data',
  CITY_OF_BOSTON: 'City of Boston',
  ARNOLD_ARBORETUM: 'Arnold Arboretum',
  CAMBRIDGE: 'City of Cambridge',
  BROOKLINE: 'Town of Brookline',
  SOMERVILLE: 'City of Somerville',
  BELMONT: 'Town of Belmont',
  WATERTOWN: 'City of Watertown',
  NORTHEASTERN: 'Northeastern University Arboretum',
  NOTABLE_USBOS: 'Tree Boston - Big Old Trees',
  PARIS_OPENDATA: 'Paris Open Data',
  BURLINGTON_OPENDATA: 'City of Burlington, VT',
  VANCOUVER_OPENDATA: 'Vancouver Open Data',
  BERLIN_OPENDATA: 'Berlin GDI',
  AMSTERDAM_OPENDATA: 'City of Amsterdam',
  LONDON_OPENDATA: 'London Datastore',
  MELBOURNE_OPENDATA: 'City of Melbourne',
  PORT_PHILLIP: 'City of Port Phillip',
  YARRA: 'City of Yarra',
  GLEN_EIRA: 'Glen Eira City Council',
  HOBSONS_BAY: 'Hobsons Bay City Council',
  BUENOSAIRES_OPENDATA: 'Buenos Aires Data',
  LOSANGELES_OPENDATA: 'LA Bureau of Street Services',
  WEST_HOLLYWOOD: 'West Hollywood Open Data',
  WASHINGTONDC_OPENDATA: 'Open Data DC',
  TEMPE_OPENDATA: 'City of Tempe',
  // Athens's municipal partition is the National Garden inventory — the only
  // tree dataset any Greek portal publishes. Milos (GRMLO) and Santorini
  // (GRSAN) have no municipal source at all; their rows are COMMUNITY_* and
  // OSM_*, labelled by the prefix rules below.
  ATHENS_OPENDATA: 'City of Athens GIS (National Garden)',
  DENVER_OPENDATA: 'Denver Open Data',
}

/** True for the approved-community-submission sources (`COMMUNITY_<CITY>`). */
export function isCommunitySource(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('COMMUNITY_')
}

/** True for the OpenStreetMap supplemental sources (`OSM_<CITY>`). */
export function isOsmSource(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('OSM_')
}

/**
 * True for the reviewed aerial-imagery sources (`SATELLITE_<CITY>`): model
 * detections on NAIP tiles that a person accepted in the reviewer and
 * published. See data/raw/satellite_tree_info.py.
 */
export function isSatelliteSource(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('SATELLITE_')
}

/**
 * True for the bulk aerial-imagery sources (`PREDICTED_<CITY>`): model
 * detections published without a person reviewing each one. See
 * data/raw/predicted_tree_info.py.
 */
export function isPredictedSource(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('PREDICTED_')
}

/** Human-readable label for a `data_source` value; null when there is none. */
export function formatDataSource(value: string | null | undefined): string | null {
  if (!value) return null
  if (isCommunitySource(value)) return 'Community submission'
  if (isOsmSource(value)) return 'OpenStreetMap'
  if (isSatelliteSource(value)) return 'Aerial imagery (reviewed)'
  if (isPredictedSource(value)) return 'Aerial imagery (model prediction, unreviewed)'
  return (
    DATA_SOURCE_LABELS[value] ??
    value
      .toLowerCase()
      .split('_')
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ')
  )
}

/** Only a successful zero-count read can establish an unconfirmed prediction. */
export function isUnconfirmedPrediction(source: string | null | undefined, checkinCount: number | null | undefined): boolean {
  return isPredictedSource(source) && checkinCount === 0
}
