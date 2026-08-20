export interface CitySourceLink {
  city: string
  label: string
  url?: string
}

export interface AttributionSourceLink {
  label: string
  description: string
  url: string
}

export const TREE_INVENTORY_SOURCES: CitySourceLink[] = [
  { city: 'San Francisco', label: 'SF Open Data Portal', url: 'https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq' },
  { city: 'New York City', label: 'NYC Open Data Street Tree Census', url: 'https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh' },
  { city: 'Boston', label: 'City of Boston Open Data', url: 'https://data.boston.gov/dataset/bprd-trees' },
  { city: 'Paris', label: 'Paris Open Data (les-arbres)', url: 'https://opendata.paris.fr/explore/dataset/les-arbres/information/' },
  { city: 'Burlington', label: 'City of Burlington VT ArcGIS FeatureServer', url: 'https://maps.burlingtonvt.gov/arcgis/rest/services/Tree_Sites_Public_View/FeatureServer/0' },
  { city: 'Vancouver', label: 'Vancouver Open Data (public-trees)', url: 'https://opendata.vancouver.ca/explore/dataset/public-trees/information/' },
  { city: 'Berlin', label: 'Berlin GDI WFS (Strassenbaume / baumbestand)', url: 'https://gdi.berlin.de/services/wfs/baumbestand' },
  { city: 'Amsterdam', label: 'City of Amsterdam REST API (bomen/stamgegevens)', url: 'https://api.data.amsterdam.nl/v1/bomen/stamgegevens/' },
  { city: 'London', label: 'London Datastore (Public Realm Trees)', url: 'https://data.london.gov.uk/dataset/2r45m' },
  { city: 'Melbourne', label: 'City of Melbourne Open Data (Urban Forest)', url: 'https://data.melbourne.vic.gov.au/explore/dataset/trees-with-species-and-dimensions-urban-forest/' },
  { city: 'Buenos Aires', label: 'Buenos Aires Data (Arbolado publico lineal)', url: 'https://data.buenosaires.gob.ar/dataset/arbolado-publico-lineal' },
  { city: 'Los Angeles', label: 'Los Angeles Open Data (Street Tree Inventory - 1990s)', url: 'https://data.lacity.org/api/views/vt5t-mscf' },
  { city: 'Washington, DC', label: 'Open Data DC (Urban Forestry Street Trees)', url: 'https://opendata.dc.gov/datasets/DCGIS::urban-forestry-street-trees' },
  { city: 'Tempe', label: 'City of Tempe Tree Inventory', url: 'https://data.tempe.gov/datasets/tempegov::tree-inventory' },
  // Community submissions are reviewed and approved before they reach the map;
  // each published tree carries a COMMUNITY_<CITY> value in its data_source column.
  { city: 'All cities', label: 'Community submissions, reviewed before publication' },
  // Supplemental OSM trees (OSM_<CITY> in data_source) are ODbL-licensed;
  // this attribution line is required, keep it whenever any city has OSM wired.
  { city: 'Tempe', label: '© OpenStreetMap contributors (supplemental trees, ODbL)', url: 'https://www.openstreetmap.org/copyright' },
  { city: 'Boston', label: '© OpenStreetMap contributors (supplemental trees, ODbL)', url: 'https://www.openstreetmap.org/copyright' },
]

export const LANDMARK_SOURCES: CitySourceLink[] = [
  { city: 'San Francisco', label: 'SF Open Data Portal (Landmarks)', url: 'https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi/about_data' },
  { city: 'New York City', label: 'NYC LPC Individual Landmark Sites', url: 'https://data.cityofnewyork.us/Housing-Development/Individual-Landmark-Sites/buis-pvji' },
  { city: 'Boston', label: 'City of Boston Open Data (Landmarks)', url: 'https://data.boston.gov/dataset/92137315-e846-4c75-8c3d-2b7e93e38d03' },
  { city: 'Paris', label: 'Ile-de-France Open Data (Monuments Historiques)', url: 'https://data.iledefrance.fr/explore/dataset/immeubles-proteges-au-titre-des-monuments-historiques/' },
  { city: 'Burlington', label: 'Geocoded from city landmark directory via Nominatim' },
  { city: 'Vancouver', label: 'Vancouver Open Data (Heritage Sites)', url: 'https://opendata.vancouver.ca/explore/dataset/heritage-sites/information/' },
  { city: 'Berlin', label: 'OpenStreetMap via Overpass API (historic=* tags)' },
  { city: 'Amsterdam', label: 'City of Amsterdam REST API (monumenten)', url: 'https://api.data.amsterdam.nl/v1/monumenten/monumenten/' },
  { city: 'London', label: 'OpenStreetMap via Overpass API (historic=* tags)' },
  { city: 'Melbourne', label: 'City of Melbourne Open Data (Landmarks and Places of Interest)', url: 'https://data.melbourne.vic.gov.au/explore/dataset/landmarks-and-places-of-interest-including-schools-theatres-health-services-spor/' },
  { city: 'Buenos Aires', label: 'No landmark dataset in production yet (empty placeholder parquet)' },
  { city: 'Los Angeles', label: 'No landmark dataset in production yet (empty placeholder parquet)' },
  { city: 'Washington, DC', label: 'No landmark dataset in production yet (empty placeholder parquet)' },
  { city: 'Tempe', label: 'No landmark dataset in production yet (empty placeholder parquet)' },
]

export const SPECIES_ENRICHMENT_SOURCES: AttributionSourceLink[] = [
  { label: 'Wikipedia', description: 'REST & MediaWiki APIs', url: 'https://en.wikipedia.org/' },
  { label: 'Plants of the World Online', description: 'POWO / Royal Botanic Gardens, Kew', url: 'https://powo.science.kew.org/' },
  { label: 'GBIF', description: 'Global Biodiversity Information Facility', url: 'https://www.gbif.org/' },
  { label: 'SelecTree', description: 'Cal Poly Urban Forest Ecosystems Institute', url: 'https://selectree.calpoly.edu/' },
]
