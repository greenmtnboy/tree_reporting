"""Sentence case for common names, with proper-name components kept.

The editorial convention for a vernacular name is sentence case: `common
hackberry`, `northern hackberry`, `American hackberry`, `Mississippi
hackberry`.  Only a word that is independently a proper noun or proper
adjective keeps its capital -- a place, a people, a person, a feast day -- and
a descriptive word (`northern`, `common`, `evergreen`, `weeping`) does not,
unless it starts the name.  Scientific names follow a different rule and are
not touched by this.

The published table did not follow any convention: of 29,039 names in
September 2026, 4,942 were ALL CAPS, 11,032 were Title Case and 8,651 began
lower case, with `Evergreen Pear`, `EVERGREEN PEAR` and `evergreen pear`
all present.  So source casing is not preserved; everything is lowered and
the proper components are put back from the lists below.

The lists are the whole method, and they were built from the data: every
word the published names capitalised mid-name was read (1,082 distinct) and
the proper ones kept.  A word missing here is lowercased -- `Port Orford
cedar` would come out `port orford cedar` -- so when a new name shows up
wrong, the fix is one entry.  Two shapes need care:

- **Phrases** are matched before words, so `new` is capitalised in `New
  Zealand` and nowhere else, and `St. John's wort` keeps both capitals.
- **Cultivar and trade names** embedded in a common name (`Autumn Blaze red
  maple`) are proper names in their own right and are listed as phrases; a
  cultivar in quotes is left exactly as written, as the ingest does.
"""

from __future__ import annotations

import re

# Multi-word proper names, in the casing they should be published with.
# Matched case-insensitively on word boundaries, longest first.
PROPER_PHRASES: tuple[str, ...] = (
    # Places
    "New Zealand", "New England", "New York", "New Mexico", "New Mexican",
    "New Jersey", "New Caledonia", "New Caledonian", "New Guinea", "New World",
    "New South Wales", "Hong Kong", "Port Orford", "Port Jackson", "Port Lincoln",
    "Moreton Bay", "Canary Island", "Canary Islands", "Norfolk Island",
    "Lord Howe", "Cook Island", "Cook Islands", "Ulleung Island", "Bald Island",
    "North Island", "South Island", "Three Kings", "Kermadec", "Santa Cruz",
    "Santa Lucia", "San Pedro Mártir", "San Pedro", "San Jose", "Rio Grande",
    "Black Hills", "Blue Ridge", "Rocky Mountain", "Great Plains", "Great Basin",
    "West Indies", "West Indian", "East Indies", "East Indian", "South Australia",
    "South Australian", "Western Australia", "Western Australian",
    "South Gippsland", "British Columbia", "Cape Leeuwin", "Beaufort Inlet",
    "Flinders Range", "Flinders Ranges", "Salt River", "Valley Forge", "Palo Alto",
    "Del Mar", "East Anglian", "East Asian", "Southeast Asian", "Central American",
    "South American", "North American", "Middle Eastern", "Sri Lanka", "Costa Rican",
    "Mount Etna", "Mount Hua", "Mount Tabor", "Mount Morgan", "Mount Lewis",
    "Mount Jorullo", "Mount Atlas", "Mt. Fuji", "Mt Fuji", "Mt. Morrison",
    "Tapao Shan", "Tian Shan", "Cape Cod", "Cape Town",
    # Saints and people
    "St. John's", "St John's", "St. Johns", "St Johns", "St. James", "St James",
    "St Lucie", "St. Lucie", "St Vincent's", "St. Vincent's", "St. Thomas'",
    "St Thomas'", "Saint John's", "Saint-Valery", "Père David's", "Pere David's",
    "Père David’s", "Pere David’s", "Father David", "Lady Banks'", "Lady Banks",
    "Lady Josika's", "Miss Willmott's", "Mrs Robb's", "Madame Galen",
    "Madame Naiz", "Madame Yass", "Duke of Argyll's", "Reine Claude",
    "Granny Smith", "James Grieve", "King Billy", "King William", "King Boris",
    "Prince Rupprecht", "Prince Rupprecht's", "Joe Pye", "Joe-Pye", "Queen Anne's",
    "Solomon's seal", "Adam's needle", "Eve's necklace", "Hercules' club",
    "Jacob's ladder", "Aaron's beard", "Fourth of July", "Turk's-cap", "Turk's cap",
    "Dutchman's pipe", "Dutchman's-pipe", "Dutchman's breeches", "Judas tree",
    "Joshua tree", "Jerusalem thorn", "Jerusalem artichoke", "Nellie Stevens",
    "Ray Hartman", "Jacqueline Hillier", "John Downie", "Donald Wyman",
    "Hally Jolivette", "Eddie's White Wonder", "Bracken's Brown Beauty",
    "Marshall's Seedless", "Paul's Scarlet", "Winter King", "Autumn Blaze",
    "Autumn Brilliance", "Autumn Gold", "Autumn Glory", "Autumn Splendor",
    "Autumn Purple", "Crimson Spire", "Crimson King", "Crimson Cloud", "Sango Kaku", "Green Vase",
    "Sugar Tyme", "Red Robin", "Blue Princess", "Shawnee Brave", "Golden Globe",
    "White Shield", "Spring Grove", "Pink Cascade", "Prairie Pride",
    "Princeton Sentry", "Princeton Upright", "Princeton Gold", "Gold Rush",
    "Majestic Beauty", "Kirsten Pink", "Joseph Rock", "Casque Rouge",
    "Dawyck Purple", "Emerald Queen", "Royal Red", "October Glory", "Red Sunset",
    "Bloodgood", "Thundercloud", "Akebono", "Kwanzan", "Kanzan", "Little John",
    "Ice Cream Bean", "Morton Accolade", "Handsome Harry",
)

# Single proper words: places, peoples, languages, persons, feast days, and
# the possessive or adjectival forms the names use.  Lower-case lookup, this
# casing published.
PROPER_WORDS: tuple[str, ...] = (
    # Nationalities, regions and languages
    "African", "Afghan", "Albanian", "Algerian", "Alsatian", "Amazonian",
    "American", "Andean", "Anglian", "Appalachian", "Arabian", "Arctic",
    "Argentine", "Argentinian", "Armenian", "Asian", "Asiatic", "Atlantic",
    "Australian", "Austrian", "Balkan", "Balearic", "Baltic", "Bavarian",
    "Bengal", "Bhutan", "Bohemian", "Bolivian", "Bosnian", "Brazilian",
    "British", "Bulgarian", "Burmese", "Byzantine", "Calabrian", "Californian",
    "Cambodian", "Canadian", "Cantonese", "Caribbean", "Carolinian",
    "Caspian", "Catalan", "Caucasian", "Ceylon", "Chilean", "Chinese",
    "Colombian", "Corsican", "Cretan", "Crimean", "Cuban",
    "Cypriot", "Cyprus", "Dalmatian", "Danish", "Dutch", "Egyptian", "English",
    "Ethiopian", "Eurasian", "European", "Filipino", "Finnish", "Flemish",
    "Formosan", "French", "Galician", "German", "Greek", "Guatemalan",
    "Hawaiian", "Himalayan", "Hispanic", "Honduran", "Hungarian", "Iberian",
    "Icelandic", "Indian", "Indonesian", "Iranian", "Irish", "Italian",
    "Jamaican", "Japanese", "Javan", "Javanese", "Kashmir", "Kashmiri",
    "Korean", "Lebanese", "Levantine", "Macedonian", "Malabar", "Malayan",
    "Malaysian", "Manchurian", "Mediterranean", "Mediterranian", "Melanesian",
    "Mexican", "Micronesian", "Moluccan", "Mongolian", "Moroccan", "Nepal",
    "Nepalese", "Nordic", "Norwegian", "Norway", "Pacific", "Pakistani",
    "Palestinian", "Panamanian", "Paraguayan", "Patagonian", "Persian",
    "Peruvian", "Philippine", "Polish", "Polynesian", "Portuguese", "Prussian",
    "Pyrenean", "Queensland", "Roman", "Romanian", "Russian", "Sardinian",
    "Scandinavian", "Scotch", "Scots", "Scottish", "Serbian", "Siberian",
    "Sicilian", "Sinhalese", "Slovak", "Slovenian", "Spanish", "Sudanese",
    "Sumatran", "Swedish", "Swiss", "Syrian", "Tahitian", "Taiwan", "Taiwanese",
    "Tasmanian", "Tatar", "Thai", "Tibetan", "Turkestan", "Turkish", "Turk",
    "Tyrolean", "Ukrainian", "Uruguayan", "Venezuelan", "Victorian",
    "Vietnamese", "Welsh", "Yugoslav", "Yunnan",
    # Countries, states, provinces, cities, mountains, rivers
    "Africa", "Alabama", "Alaska", "Alaskan", "Aleppo", "Algeria", "Allegheny",
    "Amur", "Andes", "Angola", "Arizona", "Arkansas", "Asia", "Atlas",
    "Australia", "Bermuda", "Bismarck", "Bolivia", "Brazil", "Brisbane",
    "Burma", "California", "Canada", "Canadá", "Canary", "Cape", "Carolina",
    "Cascade", "Chile", "China", "Colorado", "Colombia", "Connecticut",
    "Cordillera", "Corsica", "Cuba", "Dalby", "Delaware", "Deodar", "Etna",
    "Europe", "Fiji", "Florida", "Fontainebleau", "Fuji", "Georgia",
    "Gippsland", "Goldfields", "Guadalupe", "Guatemala", "Hankow", "Hawaii",
    "Hokkaido", "Honshu", "Hubei", "Hupeh", "Illinois", "India", "Indiana",
    "Iowa", "Israel", "Italy", "Japan", "Japon", "Jujuy", "Kamchatka", "Kansas",
    "Kansu", "Kentucky", "Korea", "Lebanon", "Lombardy", "Louisiana", "Madeira",
    "Maine", "Manchuria", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Mongolia", "Montana", "Monterey",
    "Montezuma", "Natal", "Nebraska", "Nevada", "Nootka", "Norfolk",
    "Ohio", "Oklahoma", "Oregon", "Ozark", "Panama", "Pennsylvania", "Peru",
    "Provins", "Quebec", "Rome", "Saipan", "Sardinia", "Shensi", "Shanxi",
    "Sichuan", "Sierra", "Sikkim", "Sitka", "Sogamoso", "Sonora", "Sonoran",
    "Sudan", "Tennessee", "Texas", "Tibet", "Tucumán", "Uganda", "Ussuri",
    "Utah", "Vermont", "Virginia", "Washington", "Wisconsin", "Wyoming",
    "Yoshino", "Zealand", "London", "Paris", "Berlin", "Boston", "Vienna",
    "Tokyo", "Kyoto", "Nanking", "Peking", "Shanghai", "Hollywood", "Bombay",
    "Hudson", "Manitoba", "Ontario", "Alberta", "Labrador", "Newfoundland",
    "Idaho", "Dakota", "Chihuahua", "Durango", "Sinaloa", "Oaxaca", "Yucatán",
    "Yucatan", "Honduras", "Nicaragua", "Haiti", "Trinidad", "Barbados",
    "Bahama", "Bahamas", "Mallorca", "Majorca", "Crete", "Anatolia", "Anatolian",
    "Caucasus", "Ural", "Altai", "Baikal", "Sakhalin", "Himalaya", "Himalayas",
    "Punjab", "Assam", "Siam", "Java", "Sumatra", "Borneo", "Celebes", "Timor",
    "Papua", "Guinea", "Victoria", "Tasmania", "Sydney", "Melbourne", "Adelaide",
    "Perth", "Darwin", "Kimberley", "Pilbara", "Illawarra", "Murray", "Kalahari",
    "Karoo", "Transvaal", "Zululand", "Madagascar", "Mauritius", "Azores",
    "Tuscany", "Tuscan", "Provence", "Burgundy", "Normandy",
    "Brittany", "Cornwall", "Cornish", "Devon", "Kent", "Kentish", "Sussex",
    "Yorkshire", "Cumbrian", "Chusan", "Kiangsi", "Hunan", "Guangdong",
    "Fujian", "Hainan", "Henan", "Shandong", "Zhejiang", "Jiangsu", "Anhui",
    "Guizhou", "Guangxi", "Hebei", "Liaoning", "Jilin", "Gansu", "Qinghai",
    "Xinjiang", "Luzon", "Mindanao", "Okinawa", "Ryukyu", "Kyushu",
    "Shikoku", "Ussurian", "Iran", "Iraq", "Afghanistan",
    "Pakistan", "Yemen", "Oman", "Arabia",
    "Ethiopia", "Kenya", "Tanzania", "Zambia", "Zimbabwe", "Mozambique",
    "Namibia", "Botswana", "Senegal", "Ghana", "Nigeria", "Cameroon", "Congo",
    "Egypt", "Libya", "Tunisia", "Morocco", "Sahara", "Saharan", "Sahel",
    "Argentina", "Uruguay", "Paraguay", "Ecuador", "Venezuela", "Guyana",
    "Surinam", "Amazon", "Patagonia", "Chaco", "Pampas", "Yosemite", "Tahoe",
    "Shasta", "Siskiyou", "Klamath", "Modoc", "Lassen", "Mojave", "Chihuahuan",
    "Ozarks", "Adirondack", "Catskill", "Piedmont", "Everglades",
    "Guam", "Samoa", "Samoan", "Tonga", "Tongan", "Fijian", "Maori",
    # Peoples and cultural / religious proper nouns
    "Aztec", "Cherokee", "Inca", "Mayan", "Mormon", "Osage", "Shawnee",
    "Christmas", "Easter", "Lenten", "Michaelmas", "Whitsun", "Advent",
    "Christ", "Cristo", "Cristi", "Corona", "Judas", "Joshua", "Solomon",
    "Solomon's", "Adam", "Adam's", "Eve", "Eve's", "Hercules", "Hercules'",
    "Jacob", "Jacob's", "Aaron", "Aaron's", "Sodom", "Gilead", "Sharon",
    "Dutchman", "Dutchman's",
    # Persons the names honour, with their possessives
    "Douglas", "Sargent", "Sargent's", "Lawson", "Lawson's", "Leyland",
    "Jeffrey", "Coulter", "Torrey", "Engelmann", "Brewer", "Brewer's", "Gambel",
    "Gambel's", "Fremont", "Fremont's", "Callery", "Bradford", "Loebner",
    "Veitch", "Veitch's", "Vietch's", "Henry", "Henry's", "David", "David's",
    "David’s", "Wilson", "Wilson's", "Fortune", "Fortune's", "Thunberg",
    "Thunberg's", "Delavay", "Forrest", "Forrest's", "Farrer", "Farrer's",
    "Kalm", "Kalm's", "Dodge", "Dodge's", "Greene", "Greene's", "Goldie",
    "Goldie's", "Carey", "Carey's", "Marshall", "Marshall's", "Morton",
    "Hillier", "Wyman", "Downie", "Eddie", "Eddie's", "Paul", "Paul's",
    "Hartman", "Thomas", "Thomas'", "James", "John", "John's", "Johns",
    "Johnswort", "William", "Boris", "Rupprecht", "Rupprecht's", "Banks",
    "Banks'", "Josika", "Josika's", "Willmott", "Willmott's", "Robb",
    "Robb's", "Galen", "Naiz", "Yass", "Argyll", "Argyll's", "Claude",
    "Claudia", "Smith", "Grieve", "Harry", "Macgregor", "Macgregor's",
    "Parlatore", "Parlatore's", "Neubert", "Neubert's", "Beale", "Beale's",
    "Tanaka", "Tanaka's", "Diels", "Diels'", "Dombey", "Dombey's", "Menzies",
    "Menzies'", "Buckley", "Buckley's", "Dudley", "Dudley's", "Stevens",
    "Nellie", "Merrill", "Morrison", "Nagami", "Princeton", "Jack", "Jack's",
    "Nordmann", "Nordmann's", "Hooker", "Hooker's", "Lambert", "Lambert's",
    "Bailey", "Bailey's", "Parry", "Parry's", "Lindley", "Lindley's",
    "Meyer", "Meyer's", "Bosc", "Bartlett", "Nuttall", "Nuttall's", "Schwedler",
    "Drummond", "Drummond's", "Emory", "Emory's", "Fraser", "Fraser's",
    "Wollemi", "Higan", "Zumi",
    # Rank words that only ever precede a proper name.  Not "King", "Lady" or
    # "Jack": those are common nouns in "king sago", "lady-fern" and
    # "jack-in-the-pulpit", and the names that use them as titles are phrases.
    "St", "St.", "Mt", "Mt.", "San", "Santa", "Père", "Pere", "Saint",
)

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?['’]?", re.UNICODE)
# A quoted cultivar riding along in a common name.  Anchored to word edges so
# an apostrophe used as a letter is not read as a quote ("'Ohi'a lehua").
_QUOTED_RE = re.compile(r"((?:(?<=\s)|^)(?:'[^']+'|\"[^\"]+\"|‘[^’]+’)(?=[\s,;)]|$))")

_PHRASE_INDEX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![^\W\d_])" + re.escape(p.lower()) + r"(?![^\W\d_])", re.IGNORECASE), p)
    for p in sorted(PROPER_PHRASES, key=len, reverse=True)
]
_WORD_INDEX: dict[str, str] = {w.lower(): w for w in PROPER_WORDS}


def _restore_word(match: re.Match[str]) -> str:
    token = match.group(0)
    hit = _WORD_INDEX.get(token.lower())
    if hit is not None:
        return hit
    # A possessive of a listed name: "dodge's" -> "Dodge's", "diels'" -> "Diels'".
    for suffix in ("'s", "’s", "'", "’"):
        if token.lower().endswith(suffix):
            stem = token[: -len(suffix)]
            hit = _WORD_INDEX.get(stem.lower())
            if hit is not None:
                return hit + suffix
    return token


def _sentence_case_span(span: str) -> str:
    """Lower a span that carries no quoted cultivar, then restore proper names."""
    low = span.lower()
    for pattern, canonical in _PHRASE_INDEX:
        low = pattern.sub(canonical, low)
    # Hyphenated parts are words of their own ("douglas-fir", "red-cedar").
    return _WORD_RE.sub(_restore_word, low)


def _quoted(part: str) -> str:
    """A quoted cultivar is kept as written, unless the source shouted it."""
    inner = part[1:-1]
    if inner.isupper():
        inner = " ".join(w[:1] + w[1:].lower() for w in inner.split(" "))
    return part[0] + inner + part[-1]


def normalize_common_name(name: str | None) -> str | None:
    """Return *name* in sentence case with proper components capitalised.

    Whitespace is collapsed; a blank name is ``None``.  Text inside quotes -- a
    cultivar riding along in a common name -- is kept exactly as written.

    Examples:
        "Mississippi Hackberry"      -> "Mississippi hackberry"
        "EVERGREEN PEAR"             -> "Evergreen pear"
        "common hackberry"           -> "Common hackberry"
        "Rocky Mountain Douglas-fir" -> "Rocky Mountain Douglas-fir"
        "Port Orford Cedar"          -> "Port Orford cedar"
        "shrubby St. John's wort"    -> "Shrubby St. John's wort"
        "Autumn Blaze Red Maple"     -> "Autumn Blaze red maple"
        "Apple 'James Grieve'"       -> "Apple 'James Grieve'"
    """
    if name is None:
        return None
    s = " ".join(str(name).split())
    if not s:
        return None
    parts = _QUOTED_RE.split(s)
    out = [_quoted(part) if i % 2 else _sentence_case_span(part) for i, part in enumerate(parts)]
    s = "".join(out)
    # Sentence case: the first letter is always capitalised -- unless the name
    # opens with a quoted cultivar, which is kept verbatim.  A leading
    # apostrophe that is a letter ("'ohi'a lehua") is not a quote.
    if parts[0] == "" and len(parts) > 1:
        return s
    for i, ch in enumerate(s):
        if ch.isalpha():
            if ch.islower():
                s = s[:i] + ch.upper() + s[i + 1:]
            break
    return s


def normalize_common_names(names) -> list[str] | None:
    """Normalise a list, dropping blanks and case-duplicates, order kept."""
    if not names:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        cleaned = normalize_common_name(raw)
        if cleaned is None or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        out.append(cleaned)
    return out or None
