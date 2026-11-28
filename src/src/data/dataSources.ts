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
  // Athens's municipal partition is the National Garden inventory — the only
  // tree dataset any Greek portal publishes. Milos (GRMLO) and Santorini
  // (GRSAN) have no municipal source at all; their rows are COMMUNITY_* and
  // OSM_*, labelled by the prefix rules below.
  // >>> sync_cities: data source labels
  BUENOSAIRES_OPENDATA: 'Buenos Aires Data',
  VIENNA_OPENDATA: 'Vienna Open Data',
  ADELAIDE_OPENDATA: 'Adelaide Open Data',
  UNLEY: 'City of Unley',
  BURNSIDE: 'City of Burnside',
  BALLARAT_OPENDATA: 'Ballarat Open Data',
  BASSENDEAN_OPENDATA: 'Bassendean Open Data',
  BENDIGO_OPENDATA: 'Bendigo Open Data',
  CANBERRA_OPENDATA: 'Canberra Open Data',
  COCKBURN_OPENDATA: 'Cockburn Open Data',
  COLAC_OTWAY_OPENDATA: 'Colac Otway Open Data',
  LAUNCESTON_OPENDATA: 'Launceston Open Data',
  LATROBE_OPENDATA: 'Latrobe City Open Data',
  MELBOURNE_OPENDATA: 'City of Melbourne',
  PORT_PHILLIP: 'City of Port Phillip',
  YARRA: 'City of Yarra',
  GLEN_EIRA: 'Glen Eira City Council',
  HOBSONS_BAY: 'Hobsons Bay City Council',
  BRIMBANK: 'Brimbank City Council',
  MANNINGHAM: 'Manningham City Council',
  MACEDON_OPENDATA: 'Macedon Ranges Open Data',
  PERTH_OPENDATA: 'Perth Open Data',
  SHEPPARTON_OPENDATA: 'Greater Shepparton Open Data',
  SYDNEY_OPENDATA: 'Sydney Open Data',
  WODONGA_OPENDATA: 'Wodonga Open Data',
  ALBURY: 'Albury City Council',
  ANTWERPEN_OPENDATA: 'Antwerp Open Data',
  GHENT_OPENDATA: 'Ghent Open Data',
  AJAX_OPENDATA: 'Ajax Open Data',
  BARRIE_OPENDATA: 'Barrie Open Data',
  BURLINGTON_ON_OPENDATA: 'Burlington, ON Open Data',
  CALGARY_OPENDATA: 'Calgary Open Data',
  EDMONTON_OPENDATA: 'Edmonton Open Data',
  ST_ALBERT: 'City of St. Albert Open Data',
  FREDERICTON_OPENDATA: 'Fredericton Open Data',
  FORT_ST_JOHN_OPENDATA: 'Fort St. John Open Data',
  GUELPH_OPENDATA: 'Guelph Open Data',
  HAMILTON_OPENDATA: 'Hamilton Open Data',
  HALIFAX_OPENDATA: 'Halifax Open Data',
  KELOWNA_OPENDATA: 'Kelowna Open Data',
  KINGSTON_OPENDATA: 'Kingston Open Data',
  KITCHENER_OPENDATA: 'Kitchener Open Data',
  LETHBRIDGE_OPENDATA: 'Lethbridge Open Data',
  LANGLEY_OPENDATA: 'Langley Township Open Data',
  LONGUEUIL_OPENDATA: 'Longueuil Open Data',
  MEDICINEHAT_OPENDATA: 'Medicine Hat Open Data',
  MISSISSAUGA_OPENDATA: 'Mississauga Open Data',
  MONCTON_OPENDATA: 'Moncton Open Data',
  MAPLERIDGE_OPENDATA: 'Maple Ridge Open Data',
  MONTREAL_OPENDATA: 'Montreal Open Data',
  NEWWESTMINSTER_OPENDATA: 'New Westminster Open Data',
  OAKVILLE_OPENDATA: 'Oakville Open Data',
  OTTAWA_OPENDATA: 'Ottawa Open Data',
  PENTICTON_OPENDATA: 'Penticton Open Data',
  PRINCE_GEORGE_OPENDATA: 'Prince George Open Data',
  QUEBEC_OPENDATA: 'Quebec City Open Data',
  RED_DEER_OPENDATA: 'Red Deer Open Data',
  SURREY_OPENDATA: 'Surrey Open Data',
  TORONTO_OPENDATA: 'Toronto Open Data',
  VANCOUVER_OPENDATA: 'Vancouver Open Data',
  VICTORIA_OPENDATA: 'Victoria Open Data',
  WATERLOO_OPENDATA: 'Waterloo Open Data',
  WHITBY_OPENDATA: 'Whitby Open Data',
  WELLAND_OPENDATA: 'Welland Open Data',
  WINNIPEG_OPENDATA: 'Winnipeg Open Data',
  YORK_REGION_OPENDATA: 'York Region Open Data',
  BASEL_OPENDATA: 'Basel Open Data',
  BERN_OPENDATA: 'Bern Open Data',
  GENEVA_SITG: 'Geneva Open Data',
  ZURICH_OPENDATA: 'Zürich Open Data',
  BOGOTA_OPENDATA: 'Bogotá Open Data',
  PRAGUE_OPENDATA: 'Prague Open Data',
  BERLIN_OPENDATA: 'Berlin GDI',
  COLOGNE_OPENDATA: 'Cologne Open Data',
  FRANKFURT_OPENDATA: 'Frankfurt Open Data',
  HAMBURG_OPENDATA: 'Hamburg Open Data',
  LEIPZIG_OPENDATA: 'Leipzig Open Data',
  COPENHAGEN_OPENDATA: 'Copenhagen Open Data',
  BARCELONA_OPENDATA: 'Barcelona Open Data',
  MADRID_OPENDATA: 'Madrid Open Data',
  HELSINKI_OPENDATA: 'Helsinki Open Data',
  BREST_OPENDATA: 'Brest Open Data',
  BORDEAUX_OPENDATA: 'Bordeaux Open Data',
  GRENOBLE_OPENDATA: 'Grenoble Open Data',
  LYON_OPENDATA: 'Lyon Open Data',
  MONTPELLIER_OPENDATA: 'Montpellier Open Data',
  NANTES_OPENDATA: 'Nantes Open Data',
  PARIS_OPENDATA: 'Paris Open Data',
  RENNES_OPENDATA: 'Rennes Open Data',
  STRASBOURG_OPENDATA: 'Strasbourg Open Data',
  TOULOUSE_OPENDATA: 'Toulouse Open Data',
  BELFAST_OPENDATA: 'Belfast Open Data',
  BRISTOL_OPENDATA: 'Bristol Open Data',
  BASINGSTOKE_OPENDATA: 'Basingstoke Open Data',
  EDINBURGH_OPENDATA: 'Edinburgh Open Data',
  FAREHAM_OPENDATA: 'Fareham Open Data',
  LONDON_OPENDATA: 'London Datastore',
  NOTTINGHAM_OPENDATA: 'Nottingham Open Data',
  YORK_OPENDATA: 'York Open Data',
  ATHENS_OPENDATA: 'City of Athens GIS (National Garden)',
  FINGAL_OPENDATA: 'Fingal Open Data',
  MILAN_OPENDATA: 'Milan Open Data',
  TOKYO_OPENDATA: 'Tokyo Open Data',
  SEOUL_OPENDATA: 'Seoul Open Data',
  AMSTERDAM_OPENDATA: 'City of Amsterdam',
  EINDHOVEN_OPENDATA: 'Eindhoven Open Data',
  GRONINGEN_OPENDATA: 'Groningen Open Data',
  UTRECHT_OPENDATA: 'Utrecht Open Data',
  CHRISTCHURCH_OPENDATA: 'Christchurch Open Data',
  HASTINGS_OPENDATA: 'Hastings Open Data',
  WELLINGTON_OPENDATA: 'Wellington Open Data',
  LISBON_OPENDATA: 'Lisbon Open Data',
  SINGAPORE_NPARKS: 'Singapore Open Data',
  TAIPEI_OPENDATA: 'Taipei Open Data',
  ARLINGTON_MA: 'Town of Arlington, MA',
  TREES_ATLANTA: 'Trees Atlanta',
  AUSTIN_OPENDATA: 'Austin Open Data',
  BALTIMORE_OPENDATA: 'Baltimore Open Data',
  BELMONT: 'Town of Belmont',
  BISMARCK_OPENDATA: 'Bismarck Open Data',
  BROOKLINE: 'Town of Brookline',
  BEND_OPENDATA: 'Bend Open Data',
  BOISE_OPENDATA: 'Boise Open Data',
  CITY_OF_BOSTON: 'City of Boston',
  ARNOLD_ARBORETUM: 'Arnold Arboretum',
  NORTHEASTERN: 'Northeastern University Arboretum',
  BOULDER_OPENDATA: 'City of Boulder',
  BATON_ROUGE_OPENDATA: 'Baton Rouge Open Data',
  BURLINGTON_OPENDATA: 'City of Burlington, VT',
  BEAVERTON: 'City of Beaverton',
  BOZEMAN_OPENDATA: 'Bozeman Open Data',
  CAMBRIDGE: 'City of Cambridge',
  MIT: 'MIT campus tree inventory',
  CHARLOTTE_OPENDATA: 'Charlotte Open Data',
  COLUMBUS_OPENDATA: 'Columbus Open Data',
  CORAL_SPRINGS_OPENDATA: 'Coral Springs Open Data',
  DEDHAM: 'Town of Dedham',
  DENVER_OPENDATA: 'Denver Open Data',
  DURHAM_OPENDATA: 'Durham Open Data',
  DAVENPORT_OPENDATA: 'Davenport Open Data',
  EAST_LANSING: 'City of East Lansing',
  EUGENE_OPENDATA: 'Eugene Open Data',
  EVANSTON_OPENDATA: 'Evanston Open Data',
  SIOUXFALLS_OPENDATA: 'Sioux Falls Open Data',
  SPOKANE_OPENDATA: 'Spokane Open Data',
  GRAND_JUNCTION_OPENDATA: 'Grand Junction Open Data',
  HOUSTON_OPENDATA: 'Houston Open Data',
  LANSING_OPENDATA: 'Lansing Open Data',
  LAS_VEGAS_OPENDATA: 'Las Vegas Open Data',
  LOSANGELES_OPENDATA: 'LA Bureau of Street Services',
  LOUISVILLE_OPENDATA: 'Louisville Open Data',
  LOVELAND_OPENDATA: 'Loveland Open Data',
  MOORHEAD_OPENDATA: 'Moorhead Open Data',
  MADISON_OPENDATA: 'Madison Open Data',
  MISSOULA_OPENDATA: 'Missoula Open Data',
  NAPLES_OPENDATA: 'City of Naples',
  NEW_ORLEANS_OPENDATA: 'New Orleans Open Data',
  NAPERVILLE_OPENDATA: 'City of Naperville',
  ARLINGTON: 'Arlington County Open Data',
  FALLS_CHURCH: 'City of Falls Church GIS',
  NYC_OPENDATA: 'NYC Open Data',
  OAKLAND_OPENDATA: 'Oakland Open Data',
  ORLAND_PARK_OPENDATA: 'Orland Park Open Data',
  OXNARD_OPENDATA: 'Oxnard Open Data',
  PHILADELPHIA_OPENDATA: 'Philadelphia Open Data',
  PINELLAS_COUNTY_OPENDATA: 'Pinellas County Open Data',
  PITTSBURGH_OPENDATA: 'Pittsburgh Open Data',
  PLAINFIELD_PARK_DISTRICT: 'Plainfield Park District',
  PLANO_OPENDATA: 'Plano Open Data',
  PORTLAND_OPENDATA: 'Portland Open Data',
  PORT_ST_LUCIE_OPENDATA: 'Port St. Lucie Open Data',
  PROVIDENCE_OPENDATA: 'Providence Open Data',
  PORTLAND_ME_OPENDATA: 'Portland, ME Open Data',
  RIVERSIDE_OPENDATA: 'Riverside Open Data',
  REDLANDS_OPENDATA: 'Redlands Open Data',
  RICHMOND_OPENDATA: 'Richmond Open Data',
  ROCHESTER_OPENDATA: 'Rochester, NY Open Data',
  SACRAMENTO_OPENDATA: 'Sacramento Open Data',
  SALEM_MA: 'City of Salem, MA',
  SANDIEGO_OPENDATA: 'San Diego Open Data',
  SEATTLE_OPENDATA: 'Seattle Open Data',
  SF_OPENDATA: 'SF Open Data',
  SANJOSE_OPENDATA: 'San Jose Open Data',
  SALEM_OPENDATA: 'Salem Open Data',
  ST_LOUIS_PARK_OPENDATA: 'St. Louis Park Open Data',
  SANTA_MONICA_OPENDATA: 'Santa Monica Open Data',
  SAN_MATEO_OPENDATA: 'San Mateo Open Data',
  SOMERVILLE: 'City of Somerville',
  SPRINGFIELD_MO_OPENDATA: 'Springfield Open Data',
  SARASOTA_COUNTY_OPENDATA: 'Sarasota County Open Data',
  STOUGHTON_OPENDATA: 'Stoughton, WI Open Data',
  SYRACUSE_OPENDATA: 'Syracuse Open Data',
  TEMPE_OPENDATA: 'City of Tempe',
  WASHINGTONDC_OPENDATA: 'Open Data DC',
  WATERTOWN: 'City of Watertown',
  WEST_HOLLYWOOD: 'West Hollywood Open Data',
  // <<< sync_cities: data source labels
  NOTABLE_ITMIL: 'Monumental Trees of Italy (Milan)',
  NOTABLE_AUBAL: 'City of Ballarat - Exceptional Tree Register',
  NOTABLE_USBOS: 'Tree Boston - Big Old Trees',
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
