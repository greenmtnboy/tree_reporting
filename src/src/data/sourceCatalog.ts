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
  { city: 'Quebec City', label: 'Donnees Quebec - Ville de Quebec, Arbres repertories (CC-BY 4.0)', url: 'https://www.donneesquebec.ca/recherche/dataset/vque_arbrerepertorie' },
  { city: 'Montreal', label: 'Donnees Montreal - Arbres publics sur le territoire de la Ville (CC-BY 4.0)', url: 'https://donnees.montreal.ca/dataset/arbres' },
  { city: 'Toronto', label: 'City of Toronto Open Data - Street Tree Data', url: 'https://open.toronto.ca/dataset/street-tree-data/' },
  { city: 'Winnipeg', label: 'City of Winnipeg Open Data - Tree Inventory', url: 'https://data.winnipeg.ca/Parks/Tree-Inventory/hfwk-jp4h' },
  { city: 'Edmonton', label: 'City of Edmonton Open Data - Trees', url: 'https://data.edmonton.ca/Environmental-Services/Trees/eecg-fc54' },
  { city: 'Calgary', label: 'City of Calgary Open Data - Public Trees', url: 'https://data.calgary.ca/Environment/Public-Trees/tfs4-3wwa' },
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
  { city: 'Athens', label: 'City of Athens Open Data (Trees of the National Garden)', url: 'https://opendata.cityofathens.gr/dataset/gis-athens-8303d4c8-371b-11ec-b388-0242ac120009' },
  { city: 'Denver', label: 'Denver Open Data (Parks, Medians, and Parkway Trees)', url: 'https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::parks-medians-and-parkway-trees' },
  // Milos and Santorini have no published tree inventory anywhere in Greece's
  // open data portals; their trees are approved community submissions plus
  // supplemental OpenStreetMap nodes.
  { city: 'Milos', label: 'Community submissions (no municipal inventory)' },
  { city: 'Santorini', label: 'Community submissions (no municipal inventory)' },
  // Community submissions are reviewed and approved before they reach the map;
  // each published tree carries a COMMUNITY_<CITY> value in its data_source column.
  { city: 'All cities', label: 'Community submissions, reviewed before publication' },
  // Supplemental OSM trees (OSM_<CITY> in data_source) are ODbL-licensed and
  // this attribution is required, not optional.
  //
  // One 'All cities' line rather than one per city, because every city carries
  // an OSM partition -- a city is wired to OSM at the same time as its
  // municipal source, and `OSM_DATA_SOURCES` in data/raw/_ingest_shared.py is
  // the list. The per-city form was the original and it silently fell behind:
  // OSM rolled out to every city while this list still named the first five,
  // so twelve cities were rendering ODbL data with no attribution at all.
  // `test_osm_city_carries_odbl_attribution` accepts either form and checks
  // every city is covered by one of them.
  { city: 'All cities', label: '© OpenStreetMap contributors (supplemental trees, ODbL)', url: 'https://www.openstreetmap.org/copyright' },
]

export const LANDMARK_SOURCES: CitySourceLink[] = [
  { city: 'Quebec City', label: 'Repertoire du patrimoine culturel du Quebec - immeubles classes et cites (CC-BY 4.0)', url: 'https://www.donneesquebec.ca/recherche/dataset/immeubles-patrimoniaux-classes-par-le-ministre-de-la-culture-et-des-communications' },
  { city: 'Montreal', label: 'Repertoire du patrimoine culturel du Quebec - immeubles classes et cites (CC-BY 4.0)', url: 'https://www.donneesquebec.ca/recherche/dataset/immeubles-patrimoniaux-classes-par-le-ministre-de-la-culture-et-des-communications' },
  { city: 'Toronto', label: 'City of Toronto Open Data - Places of Interest and Toronto Attractions', url: 'https://open.toronto.ca/dataset/places-of-interest-and-toronto-attractions/' },
  { city: 'Winnipeg', label: 'City of Winnipeg Open Data - Historical Resources', url: 'https://data.winnipeg.ca/Heritage/Historical-Resources/ptpx-kgiu' },
  { city: 'Edmonton', label: 'City of Edmonton Open Data - Register and Inventory of Historic Resources', url: 'https://data.edmonton.ca/City-Administration/The-Register-and-Inventory-of-Historic-Resources-in/jgsn-dhai' },
  { city: 'Calgary', label: 'City of Calgary Open Data - Historic Resource', url: 'https://data.calgary.ca/Government/Historic-Resource/99yf-6c5u' },
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
  { city: 'Athens', label: 'Curated landmark list geocoded via Nominatim' },
  { city: 'Milos', label: 'Curated landmark list geocoded via Nominatim' },
  { city: 'Santorini', label: 'Curated landmark list geocoded via Nominatim' },
  { city: 'Denver', label: 'Denver Open Data (Historic Landmark Structures)', url: 'https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::historic-landmarks' },
]

export const SPECIES_ENRICHMENT_SOURCES: AttributionSourceLink[] = [
  { label: 'Wikipedia', description: 'REST & MediaWiki APIs', url: 'https://en.wikipedia.org/' },
  { label: 'Plants of the World Online', description: 'POWO / Royal Botanic Gardens, Kew', url: 'https://powo.science.kew.org/' },
  { label: 'GBIF', description: 'Global Biodiversity Information Facility', url: 'https://www.gbif.org/' },
  { label: 'SelecTree', description: 'Cal Poly Urban Forest Ecosystems Institute', url: 'https://selectree.calpoly.edu/' },
]
