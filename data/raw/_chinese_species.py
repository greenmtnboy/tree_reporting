"""Resolve a Taiwanese inventory's Chinese common name to an accepted binomial.

NOT a uv inline script -- a regular importable module, like `_ingest_shared`.

Taipei's Parks and Street Lights Office publishes its street trees (90,976
rows) and park trees (72,011) under a Traditional-Chinese common name and
nothing else: `榕樹`, `茄苳`, `樟樹`, `楓香`, 471 distinct values over 162,987
trees, with no binomial column in either file.  `species` is the join key
into the enrichment table, so without this the whole city arrives labelled
`Unknown`.

This is `_japanese_species` for a third language, and the shape is
deliberately the same -- a curated dict, a key normaliser, and a lookup that
returns `None` rather than guessing.  It is its own module for the reason
that one is: the keys normalise by different rules.  `common_name_key`
reduces a value to `[a-z ]`, which erases a Chinese name entirely, and
`japanese_name_key` folds hiragana onto katakana, which says nothing about
the one variant Taipei's files actually contain (`台`/`臺`, below).

Usage:

    from _chinese_species import species_from_chinese_name

    species_from_chinese_name("榕樹")      # 'Ficus microcarpa'
    species_from_chinese_name("櫻花")      # 'Prunus'  -- a genus is an answer
    species_from_chinese_name("枯木")      # '枯木'    -- a Dead sentinel
    species_from_chinese_name("烏?")       # None

**Taiwan's own references drafted this table and POWO adjudicated it.**  The
draft came from what the name means in Taiwanese horticultural usage --
TaiBNET, the TAI plant database, and the Taipei city government's own plant
pages -- because a Chinese name does not mean the same tree on both sides of
the Strait or in Hong Kong: Taiwan's `洋紫荊` is *Bauhinia purpurea* and its
`羊蹄甲` is *B. variegata*, the reverse of the Hong Kong usage in which
`洋紫荊` is the *B. x blakeana* on the flag (that one is `豔紫荊` here).
Three of the draft's own first guesses were wrong in the same way and are
worth recording, because a regeneration from a dictionary would repeat
them:

* `紅花鐵刀木` reads as "red-flowered *Senna siamea*" and is *Cassia grandis*,
  the pink shower;
* `山桂花` reads as "mountain osmanthus" and is *Maesa japonica*, a
  primulaceous shrub, with `大明橘` (*Myrsine seguinii*) beside it;
* `桃花心木` is *Swietenia mahagoni* in the national flora, and Taipei's
  own plant page gives *S. macrophylla* for it -- the publisher's usage
  wins over the dictionary's, with `大葉桃花心木` mapping to the same name.

Every value was then put to POWO the way `species_audit.py` does it, reading
**every** exact match rather than the first.  The 424 distinct values this
table publishes stand at **363 accepted, 35 ambiguous with an accepted
reading, 16 genera (all accepted), and 10 kept against POWO** because the
published enrichment table already carries that spelling.  Twenty-five draft
names that POWO called a synonym, or could not match, were replaced before
anything was written down, and that list is the useful one if this is ever
re-audited:

* **Synonyms replaced by their accepted name** (none of these was in the
  published table, so nothing was lost by following Kew): `Ardisia
  squamulosa` -> `Ardisia elliptica` (春不老), `Callicarpa formosana` ->
  `Callicarpa pedunculata` (杜虹花), `Celtis formosana` -> `Celtis tetrandra`
  (石朴), `Daphniphyllum oldhamii` -> `Daphniphyllum pentandrum` (奧氏虎皮楠),
  `Diospyros discolor` -> `Diospyros blancoi` (毛柿), `Ficus aurantiaca` ->
  `Ficus glandulifera` (大果藤榕), `Ficus irisana` -> `Ficus ampelos` (澀葉榕),
  `Glochidion rubrum` -> `Phyllanthus subscandens` (細葉饅頭果), `Glochidion
  zeylanicum` -> `Phyllanthus obliquus` (錫蘭饅頭果), `Glycosmis citrifolia`
  -> `Glycosmis parviflora` (石苓舅), `Hydnocarpus anthelminthicus` ->
  `Hydnocarpus castaneus` (大風子), `Machilus kusanoi` -> `Machilus japonicus`
  (大葉楠), `Pouteria campechiana` -> `Lucuma campechiana` (蛋黃果),
  `Ptychosperma macarthurii` -> `Ptychosperma propinquum` (馬氏射葉椰子),
  `Reevesia formosana` -> `Reevesia thyrsoidea` (臺灣梭羅木), `Salix
  warburgii` -> `Salix tetrasperma` (水柳, 505 trees: the national flora
  keeps the endemic and Kew sinks it), `Semecarpus gigantifolius` ->
  `Semecarpus longifolius` (臺東漆), `Trophis scandens` -> `Malaisia
  scandens` (盤龍木, Kew having moved it back), `Michelia pilifera` ->
  `Magnolia champaca` (南洋含笑, a synonym of its var. *pubinervia*).
* **Names POWO does not publish** were re-asked in another spelling: `Ficus
  ampelas` is `Ficus ampelos` there (菲律賓榕), `Trema orientalis` is `Trema
  orientale` (山黃麻), `Trema tomentosa` is `Trema tomentosum` (山油麻),
  `Citrus depressa` is `Citrus x depressa` (臺灣香檬), and `Phoenix pusilla`
  has two synonym readings and no accepted one while its other name
  `Phoenix zeylanica` resolves to `Phoenix sylvestris` (錫蘭海棗).
  `Cinnamomum kanehirae` (牛樟) has no record at all, and its usual
  synonym *C. micranthum* is itself sunk into *Camphora micrantha*; that is
  two claims Kew cannot confirm, so 牛樟 is the genus.

**The 10 kept against POWO are all one deliberate rule**, and nothing else:
`Cinnamomum camphora`, `Citrus x sinensis`, `Dypsis decaryi`, `Dypsis
lutescens`, `Morus australis`, `Schefflera actinophylla`, `Taxodium
mucronatum`, `Washingtonia robusta` (each a synonym at Kew), `Dracaena
marginata` (two synonym readings, neither accepted) and `Hibiscus
rosa-sinensis` (which Kew spells as the nothospecies `Hibiscus x
rosa-sinensis`).

**Where POWO and the published table disagreed, the published table won.**
This is the one rule that is about *this repo* rather than about taxonomy,
and it is the argument `SPECIES_SYNONYMS` and `_japanese_species` make: the
same taxon under two names is two enrichment rows, two LLM calls and two
entries in every species rollup.  POWO calls `Cinnamomum camphora` a synonym
of `Camphora officinarum`; the table has carried `Cinnamomum camphora` since
San Francisco was wired and Tokyo's 6,882 camphors joined it, so Taipei's
11,911 do the same.  `Morus australis` is the same call for a worse reason:
Kew's only exact match is Poiret's homonym, a synonym of the paper mulberry,
and relabelling `小葉桑` as *Broussonetia papyrifera* would be wrong rather
than merely old.  Where the published table carries a *synonym* of an
accepted name this module publishes -- `Michelia champaca`, `Callistemon
viminalis`, `Tabebuia impetiginosa`, `Tabebuia chrysotricha`, the two
`Fortunella` -- the accepted name is also already published, so the table
joins the accepted row and the pair is a `SPECIES_SYNONYMS` candidate rather
than a decision made here.

**A genus is an answer, and 1.3% of these trees get one.**  `櫻花` is 1,060
trees recorded as "cherry", and the named cultivars Taiwan plants --
`富士櫻`, `河津櫻`, `寒櫻`, `大漁櫻`, `昭和櫻` -- are *P. campanulata*
hybrids with no binomial (Taiwan's `富士櫻` is not Japan's *P. incisa*; it is
the cross with it).  `八重櫻` is different: in Taiwan it names the double
form of the native *Prunus campanulata* and publishes as that species.
`風鈴木` resolves to *Handroanthus*, since every named trumpet tree in this
data (`黃金`/`黃花`/`洋紅`/`毛風鈴木`) belongs there and no *Tabebuia* is
published; `海棗` is *Phoenix* rather than the date palm, which Taipei's
climate does not grow; `柑橘` is *Citrus*, `九重葛` *Bougainvillea*, `沉香`
*Aquilaria*, `龍血樹` *Dracaena*, `楠樹` *Machilus*, `油桐` *Vernicia* (both
tung trees are published under their own names), `冬青` *Ilex*, `槭樹`
*Acer*, `灰木` *Symplocos*, `金雞納樹` *Cinchona*, `澳洲胡桃` *Macadamia*,
and `香拔` -- a guava cultivar with no binomial of its own -- *Psidium*.

**What is deliberately left unresolved** is 42 trees, 0.03%: a blank value
(18) and `烏?` (24), a name whose second character the export could not
encode and which could be any of several trees.  Both fall through to
`None` and publish as `Unknown`, which is honest.  `枯木` (10) is a standing
dead tree, which `_FORM_SENTINEL_ALIASES` already reads as `Dead`; it
passes through unchanged for `enforce_tree_schema` to map.

**One key rule, and a decision not to add another.**  `chinese_name_key`
folds `台` onto `臺` because both are published (`台灣梭羅木` once, `臺灣`
in twenty-odd other names) and they are one character written two ways.
It does *not* strip a parenthetical: no value in either file carries one,
so there is nothing to show that a parenthesis here would be a qualifier
(as Halifax's `Acer (genus)` is) rather than a second name, and a rule
with no observed instance is a claim the data has not made.  The other
variant pairs the language has (`溼`/`濕`, `裡`/`裏`) are likewise not
folded, because only one spelling of each is published.

`tests/test_chinese_species.py` pins the mechanical half -- every value is
a name `sanitize_species` keeps as written, every key is in normalised form,
no value is also a key, no value is a sentinel, and every one of the 471
published names is either a key here or in `UNRESOLVED`.  The taxonomy is
reviewed by reading the file, the same way `SPECIES_SYNONYMS` and
`JAPANESE_SPECIES` are.
"""

from __future__ import annotations

import unicodedata

from _ingest_shared import form_sentinel_for, is_not_a_tree

# `台` is the everyday form and `臺` the formal one of the same character,
# and both files use the formal one almost everywhere (`臺灣欒樹`, `臺灣海棗`)
# with a single `台灣梭羅木`.  Keys are written the formal way.
_VARIANT_FOLDS = str.maketrans({"台": "臺"})


def chinese_name_key(value: str | None) -> str:
    """A published Chinese name reduced to the form the table uses.

    NFKC-normalised (which folds full-width ASCII and compatibility
    ideographs), stripped of every space -- including the ideographic space
    U+3000 -- and with `台` folded onto `臺`.

    Returns `""` for a value with nothing left in it.

    Examples:
        "榕樹"            -> "榕樹"
        "台灣梭羅木"      -> "臺灣梭羅木"
        "小葉 欖仁"       -> "小葉欖仁"
        "大王　椰子"      -> "大王椰子"
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return "".join(text.split()).translate(_VARIANT_FOLDS)


# Published values this module knows it cannot resolve, so that a coverage
# test can tell "deliberately unresolved" from "forgotten".  A blank value is
# handled by the lookup itself and is not listed.
UNRESOLVED: frozenset[str] = frozenset(
    {
        # The second character failed to encode in the portal's export.  The
        # trees Taipei plants whose names begin 烏 -- 烏桕, 烏心石, 烏來冬青 --
        # are all published under their full names, so this is not one of
        # them mistyped, and there is no honest way to pick.
        "烏?",
    }
)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
#
# Keys are `chinese_name_key` output; values are names `sanitize_species`
# keeps as written -- accepted at POWO (or accepted under one of its
# readings), ASCII hybrid mark, genus capitalised, no rank below species.
# Sorted by value, because that is how it was checked against POWO.
#
# A trailing `# comment` is the value as the portal publishes it, shown where
# keying changed it.

CHINESE_SPECIES: dict[str, str] = {
    "相思樹": "Acacia confusa",
    "槭樹": "Acer",
    "樟葉槭": "Acer albopurpurascens",
    "臺灣三角楓": "Acer buergerianum",
    "雞爪楓": "Acer palmatum",
    "青楓": "Acer serrulatum",
    "猢猻木": "Adansonia digitata",
    "小實孔雀豆": "Adenanthera microsperma",
    "馬尼拉椰子": "Adonidia merrillii",
    "樹蘭": "Aglaia odorata",
    "茜草樹": "Aidia cochinchinensis",
    "合歡": "Albizia julibrissin",
    "大葉合歡": "Albizia lebbeck",
    "石栗": "Aleurites moluccanus",
    "千頭木麻黃": "Allocasuarina nana",
    "黑板樹": "Alstonia scholaris",
    "山刺番荔枝": "Annona montana",
    "釋迦": "Annona squamosa",
    "糙葉樹": "Aphananthe aspera",
    "沉香": "Aquilaria",
    "白木香": "Aquilaria sinensis",
    "裡白楤木": "Aralia bipinnata",
    "肯氏南洋杉": "Araucaria cunninghamii",
    "小葉南洋杉": "Araucaria heterophylla",
    "頷垂豆": "Archidendron lucidum",
    "亞力山大椰子": "Archontophoenix alexandrae",
    "亞歷山大椰子": "Archontophoenix alexandrae",
    "春不老": "Ardisia elliptica",
    "樹杞": "Ardisia sieboldii",
    "檳榔": "Areca catechu",
    "鷹爪花": "Artabotrys hexapetalus",
    "麵包樹": "Artocarpus altilis",
    "波羅蜜": "Artocarpus heterophyllus",
    "楊桃": "Averrhoa carambola",
    "棋盤腳": "Barringtonia asiatica",
    "穗花棋盤腳": "Barringtonia racemosa",
    "洋紫荊": "Bauhinia purpurea",
    "羊蹄甲": "Bauhinia variegata",
    "豔紫荊": "Bauhinia x blakeana",
    "酒瓶蘭": "Beaucarnea recurvata",
    "茄苳": "Bischofia javanica",
    "霸王椰子": "Bismarckia nobilis",
    "霸王櫚": "Bismarckia nobilis",
    "木棉": "Bombax ceiba",
    "九重葛": "Bougainvillea",
    "槭葉酒瓶樹": "Brachychiton acerifolius",
    "紅仔珠": "Breynia officinalis",
    "刺杜密": "Bridelia balansae",
    "構樹": "Broussonetia papyrifera",
    "大花曼陀羅": "Brugmansia suaveolens",
    "番茉莉": "Brunfelsia uniflora",
    "凍子椰子": "Butia capitata",
    "黃蝴蝶": "Caesalpinia pulcherrima",
    "蘇利南合歡": "Calliandra surinamensis",
    "朝鮮紫珠": "Callicarpa japonica",
    "杜虹花": "Callicarpa pedunculata",
    "肖楠": "Calocedrus formosana",
    "臺灣肖楠": "Calocedrus formosana",
    "瓊崖海棠": "Calophyllum inophyllum",
    "小果油茶": "Camellia brevistyla",
    "日本山茶": "Camellia japonica",
    "油茶": "Camellia oleifera",
    "苦茶": "Camellia oleifera",
    "香水樹": "Cananga odorata",
    "橄欖": "Canarium album",
    "木瓜": "Carica papaya",
    "叢立孔雀椰子": "Caryota mitis",
    "孔雀椰子": "Caryota urens",
    "花旗木": "Cassia bakeriana",
    "阿勃勒": "Cassia fistula",
    "紅花鐵刀木": "Cassia grandis",
    "爪哇旃那": "Cassia javanica",
    "長尾栲": "Castanopsis carlesii",
    "栗豆樹": "Castanospermum australe",
    "木麻黃": "Casuarina equisetifolia",
    "吉貝木棉": "Ceiba pentandra",
    "美人樹": "Ceiba speciosa",
    "朴樹": "Celtis sinensis",
    "石朴": "Celtis tetrandra",
    "風箱樹": "Cephalanthus tetrandrus",
    "海檬果": "Cerbera manghas",
    "夜香木": "Cestrum nocturnum",
    "扁柏": "Chamaecyparis obtusa",
    "流蘇": "Chionanthus retusus",
    "麻楝": "Chukrasia tabularis",
    "金雞納樹": "Cinchona",
    "牛樟": "Cinnamomum",
    "陰香": "Cinnamomum burmanni",
    "樟樹": "Cinnamomum camphora",
    "蘭嶼肉桂": "Cinnamomum kotoense",
    "土肉桂": "Cinnamomum osmophloeum",
    "土樟": "Cinnamomum reticulatum",
    "錫蘭肉桂": "Cinnamomum verum",
    "垂花琴木": "Citharexylum spinosum",
    "柑橘": "Citrus",
    "金桔": "Citrus japonica",
    "金棗": "Citrus japonica",
    "柚子": "Citrus maxima",
    "橘子": "Citrus reticulata",
    "苦橙": "Citrus x aurantium",
    "臺灣香檬": "Citrus x depressa",
    "檸檬": "Citrus x limon",
    "柳丁": "Citrus x sinensis",
    "柳橙": "Citrus x sinensis",
    "過山香": "Clausena excavata",
    "黃皮": "Clausena lansium",
    "煙火樹": "Clerodendrum quadriloculare",
    "森氏紅淡比": "Cleyera japonica",
    "紅淡比": "Cleyera japonica",
    "彎子木": "Cochlospermum vitifolium",
    "重瓣彎子木": "Cochlospermum vitifolium",
    "可可椰子": "Cocos nucifera",
    "變葉木": "Codiaeum variegatum",
    "咖啡樹": "Coffea arabica",
    "使君子": "Combretum indicum",
    "銀葉鈕扣樹": "Conocarpus erectus",
    "破布子": "Cordia dichotoma",
    "檸檬桉": "Corymbia citriodora",
    "魚木": "Crateva adansonii",
    "加羅林魚木": "Crateva religiosa",
    "蒲瓜樹": "Crescentia cujete",
    "蘇鐵": "Cycas revoluta",
    "印度黃檀": "Dalbergia sissoo",
    "薄葉虎皮楠": "Daphniphyllum macropodum",
    "奧氏虎皮楠": "Daphniphyllum pentandrum",
    "鳳凰木": "Delonix regia",
    "第倫桃": "Dillenia indica",
    "龍眼": "Dimocarpus longan",
    "龍眼樹": "Dimocarpus longan",
    "毛柿": "Diospyros blancoi",
    "軟毛柿": "Diospyros eriantha",
    "象牙柿": "Diospyros ferrea",
    "象牙樹": "Diospyros ferrea",
    "山柿": "Diospyros japonica",
    "柿子": "Diospyros kaki",
    "馬拉巴柿": "Diospyros malabarica",
    "山紅柿": "Diospyros morrisiana",
    "楓港柿": "Diospyros vaccinioides",
    "龍血樹": "Dracaena",
    "番仔林投": "Dracaena angustifolia",
    "綠葉竹蕉": "Dracaena fragrans",
    "香龍血樹": "Dracaena fragrans",
    "紅邊竹蕉": "Dracaena marginata",
    "百合竹": "Dracaena reflexa",
    "金露花": "Duranta erecta",
    "三角椰子": "Dypsis decaryi",
    "黃椰子": "Dypsis lutescens",
    "椬梧": "Elaeagnus oldhamii",
    "油椰子": "Elaeis guineensis",
    "水石榕": "Elaeocarpus hainanensis",
    "錫蘭橄欖": "Elaeocarpus serratus",
    "杜英": "Elaeocarpus sylvestris",
    "土楠": "Endiandra coriacea",
    "黃杞": "Engelhardia roxburghiana",
    "山枇杷": "Eriobotrya deflexa",
    "枇杷": "Eriobotrya japonica",
    "珊瑚刺桐": "Erythrina corallodendron",
    "雞冠刺桐": "Erythrina crista-galli",
    "刺桐": "Erythrina variegata",
    "黃脈刺桐": "Erythrina variegata",
    "銀葉桉": "Eucalyptus cinerea",
    "大葉桉": "Eucalyptus robusta",
    "細葉桉": "Eucalyptus tereticornis",
    "稜果蒲桃": "Eugenia uniflora",
    "金剛纂": "Euphorbia neriifolia",
    "綠珊瑚": "Euphorbia tirucalli",
    "早田氏柃木": "Eurya hayatae",
    "澀葉榕": "Ficus ampelos",
    "菲律賓榕": "Ficus ampelos",
    "象耳榕": "Ficus auriculata",
    "孟加拉榕": "Ficus benghalensis",
    "垂榕": "Ficus benjamina",
    "斑葉垂榕": "Ficus benjamina",
    "黃果垂榕": "Ficus benjamina",
    "大葉雀榕": "Ficus caulocarpa",
    "印度橡膠樹": "Ficus elastica",
    "橡皮樹": "Ficus elastica",
    "牛奶榕": "Ficus erecta",
    "水同木": "Ficus fistulosa",
    "大果藤榕": "Ficus glandulifera",
    "尖尾長葉榕": "Ficus heteropleura",
    "琴葉榕": "Ficus lyrata",
    "亞里垂榕": "Ficus maclellandii",
    "厚葉榕": "Ficus microcarpa",
    "榕樹": "Ficus microcarpa",
    "黃金榕": "Ficus microcarpa",
    "九丁榕": "Ficus nervosa",
    "菩提樹": "Ficus religiosa",
    "稜果榕": "Ficus septica",
    "雀榕": "Ficus subpisocarpa",
    "島榕": "Ficus virgata",
    "梧桐": "Firmiana simplex",
    "羅庚果": "Flacourtia rukam",
    "密花白飯樹": "Flueggea virosa",
    "光蠟樹": "Fraxinus griffithii",
    "臺灣梣": "Fraxinus insularis",
    "美國紅梣": "Fraxinus pennsylvanica",
    "黃褥花": "Galphimia glauca",
    "福木": "Garcinia subelliptica",
    "山黃梔": "Gardenia jasminoides",
    "銀杏": "Ginkgo biloba",
    "南洋櫻": "Gliricidia sepium",
    "披針葉饅頭果": "Glochidion lanceolatum",
    "菲律賓饅頭果": "Glochidion philippicum",
    "石苓舅": "Glycosmis parviflora",
    "銀樺": "Grevillea robusta",
    "扁桃葉斑鳩菊": "Gymnanthemum amygdalinum",
    "墨水樹": "Haematoxylum campechianum",
    "風鈴木": "Handroanthus",
    "黃花風鈴木": "Handroanthus chrysanthus",
    "黃金風鈴木": "Handroanthus chrysanthus",
    "毛風鈴木": "Handroanthus chrysotrichus",
    "洋紅風鈴木": "Handroanthus impetiginosus",
    "紅葉樹": "Helicia cochinchinensis",
    "白水木": "Heliotropium arboreum",
    "鵝掌柴": "Heptapleurum heptaphyllum",
    "銀葉樹": "Heritiera littoralis",
    "木芙蓉": "Hibiscus mutabilis",
    "朱槿": "Hibiscus rosa-sinensis",
    "木槿": "Hibiscus syriacus",
    "山芙蓉": "Hibiscus taiwanensis",
    "黃槿": "Hibiscus tiliaceus",
    "沙盒樹": "Hura crepitans",
    "大風子": "Hydnocarpus castaneus",
    "酒瓶椰子": "Hyophorbe lagenicaulis",
    "棍棒椰子": "Hyophorbe verschaffeltii",
    "冬青": "Ilex",
    "糊樗": "Ilex formosana",
    "倒卵葉冬青": "Ilex maximowicziana",
    "鐵冬青": "Ilex rotunda",
    "烏來冬青": "Ilex uraiensis",
    "小花鼠刺": "Itea parviflora",
    "白仙丹花": "Ixora parviflora",
    "藍花楹": "Jacaranda mimosifolia",
    "日日櫻": "Jatropha integerrima",
    "火漆木": "Jatropha integerrima",
    "圓柏": "Juniperus chinensis",
    "龍柏": "Juniperus chinensis",
    "臺灣油杉": "Keteleeria davidiana",
    "蠟腸樹": "Kigelia africana",
    "臺灣欒樹": "Koelreuteria elegans",
    "紫薇": "Lagerstroemia indica",
    "大花紫薇": "Lagerstroemia speciosa",
    "九芎": "Lagerstroemia subcostata",
    "藍棕櫚": "Latania loddigesii",
    "紅棕櫚": "Latania lontaroides",
    "火筒樹": "Leea guineensis",
    "銀合歡": "Leucaena leucocephala",
    "小實女貞": "Ligustrum sinense",
    "大香葉樹": "Lindera megaphylla",
    "楓香": "Liquidambar formosana",
    "荔枝": "Litchi chinensis",
    "油葉石櫟": "Lithocarpus konishii",
    "潺槁木薑子": "Litsea glutinosa",
    "小梗木薑子": "Litsea hypophaea",
    "蒲葵": "Livistona chinensis",
    "紅花繼木": "Loropetalum chinense",
    "蛋黃果": "Lucuma campechiana",
    "澳洲胡桃": "Macadamia",
    "血桐": "Macaranga tanarius",
    "楠樹": "Machilus",
    "大葉楠": "Machilus japonicus",
    "紅楠": "Machilus thunbergii",
    "香楠": "Machilus zuihoensis",
    "山桂花": "Maesa japonica",
    "蘭嶼山桂花": "Maesa lanyuensis",
    "南洋含笑": "Magnolia champaca",
    "南洋含笑花": "Magnolia champaca",
    "黃玉蘭": "Magnolia champaca",
    "夜合花": "Magnolia coco",
    "烏心石": "Magnolia compressa",
    "蘭嶼烏心石": "Magnolia compressa",
    "含笑花": "Magnolia figo",
    "洋玉蘭": "Magnolia grandiflora",
    "紫玉蘭": "Magnolia liliiflora",
    "辛夷": "Magnolia liliiflora",
    "玉蘭": "Magnolia x alba",
    "白玉蘭": "Magnolia x alba",
    "盤龍木": "Malaisia scandens",
    "野桐": "Mallotus japonicus",
    "白匏子": "Mallotus paniculatus",
    "粗糠柴": "Mallotus philippensis",
    "檬果": "Mangifera indica",
    "芒果": "Mangifera indica",
    "人心果": "Manilkara zapota",
    "澳洲茶樹": "Melaleuca alternifolia",
    "紅瓶刷子樹": "Melaleuca citrina",
    "白千層": "Melaleuca leucadendra",
    "串錢柳": "Melaleuca viminalis",
    "蟲屎": "Melanolepis multiglandulosa",
    "苦楝": "Melia azedarach",
    "三腳虌": "Melicope pteleifolia",
    "山刈葉": "Melicope semecarpifolia",
    "水杉": "Metasequoia glyptostroboides",
    "印度塔樹": "Monoon longifolium",
    "檄樹": "Morinda citrifolia",
    "辣木": "Moringa oleifera",
    "桑樹": "Morus alba",
    "小葉桑": "Morus australis",
    "南美假櫻桃": "Muntingia calabura",
    "七里香": "Murraya paniculata",
    "月橘": "Murraya paniculata",
    "楊梅": "Myrica rubra",
    "大明橘": "Myrsine seguinii",
    "竹柏": "Nageia nagi",
    "金新木薑子": "Neolitsea sericea",
    "夾竹桃": "Nerium oleander",
    "桂葉黃梅": "Ochna kirkii",
    "長梗紫麻": "Oreocnide pedunculata",
    "桂花": "Osmanthus fragrans",
    "馬拉巴栗": "Pachira aquatica",
    "大葉山欖": "Palaquium formosanum",
    "林投": "Pandanus odorifer",
    "紅刺露兜": "Pandanus utilis",
    "白桐": "Paulownia kawakamii",
    "臺灣泡桐": "Paulownia x taiwaniana",
    "盾柱木": "Peltophorum pterocarpum",
    "牛油果": "Persea americana",
    "酪梨": "Persea americana",
    "臺灣雅楠": "Phoebe formosana",
    "海棗": "Phoenix",
    "加拿列海棗": "Phoenix canariensis",
    "臺灣海棗": "Phoenix loureiroi",
    "羅比親王海棗": "Phoenix roebelenii",
    "錫蘭海棗": "Phoenix sylvestris",
    "臺東石楠": "Photinia serratifolia",
    "錫蘭饅頭果": "Phyllanthus obliquus",
    "細葉饅頭果": "Phyllanthus subscandens",
    "樹商陸": "Phytolacca dioica",
    "溼地松": "Pinus elliottii",
    "馬尾松": "Pinus massoniana",
    "臺灣五葉松": "Pinus morrisonicola",
    "臺灣二葉松": "Pinus taiwanensis",
    "黑松": "Pinus thunbergii",
    "黃連木": "Pistacia chinensis",
    "金龜樹": "Pithecellobium dulce",
    "臺灣海桐": "Pittosporum pentandrum",
    "海桐": "Pittosporum tobira",
    "化香樹": "Platycarya strobilacea",
    "側柏": "Platycladus orientalis",
    "孔雀木": "Plerandra elegantissima",
    "樹葡萄": "Plinia cauliflora",
    "劍葉緬梔": "Plumeria pudica",
    "緬梔": "Plumeria rubra",
    "緬梔花": "Plumeria rubra",
    "蘭嶼羅漢松": "Podocarpus costalis",
    "叢花百日青": "Podocarpus fasciculus",
    "羅漢松": "Podocarpus macrophyllus",
    "圓葉福祿桐": "Polyscias scutellaria",
    "大頭茶": "Polyspora axillaris",
    "番龍眼": "Pometia pinnata",
    "水黃皮": "Pongamia pinnata",
    "臺灣石楠": "Pourthiaea lucida",
    "黃金果": "Pouteria caimito",
    "臭娘子": "Premna serratifolia",
    "大漁櫻": "Prunus",
    "富士櫻": "Prunus",
    "寒櫻": "Prunus",
    "昭和櫻": "Prunus",
    "櫻花": "Prunus",
    "河津櫻": "Prunus",
    "杏": "Prunus armeniaca",
    "八重櫻": "Prunus campanulata",
    "山櫻花": "Prunus campanulata",
    "梅": "Prunus mume",
    "桃": "Prunus persica",
    "李": "Prunus salicina",
    "刺葉桂櫻": "Prunus spinulosa",
    "吉野櫻": "Prunus x yedoensis",
    "香拔": "Psidium",
    "草莓番石榴": "Psidium cattleyanum",
    "番石榴": "Psidium guajava",
    "芭樂": "Psidium guajava",
    "九節木": "Psychotria asiatica",
    "印度紫檀": "Pterocarpus indicus",
    "菲律賓紫檀": "Pterocarpus vidalianus",
    "槭葉翅子木": "Pterospermum acerifolium",
    "馬氏射葉椰子": "Ptychosperma propinquum",
    "安石榴": "Punica granatum",
    "火刺木": "Pyracantha koidzumii",
    "豆梨": "Pyrus calleryana",
    "野梨": "Pyrus calleryana",
    "槲櫟": "Quercus aliena",
    "赤皮": "Quercus gilva",
    "青剛櫟": "Quercus glauca",
    "海南菜豆樹": "Radermachera hainanensis",
    "山菜豆": "Radermachera sinica",
    "旅人蕉": "Ravenala madagascariensis",
    "臺灣梭羅木": "Reevesia thyrsoidea",  # 台灣梭羅木
    "石斑木": "Rhaphiolepis indica",
    "厚葉石斑木": "Rhaphiolepis umbellata",
    "羅氏鹽膚木": "Rhus chinensis",
    "大王椰子": "Roystonea regia",
    "龍鱗櫚": "Sabal palmetto",
    "垂柳": "Salix babylonica",
    "水柳": "Salix tetrasperma",
    "雨豆樹": "Samanea saman",
    "冇骨消": "Sambucus javanica",
    "山陀兒": "Sandoricum koetjape",
    "無患子": "Sapindus mukorossi",
    "水冬瓜": "Saurauia tristyla",
    "澳洲鴨腳木": "Schefflera actinophylla",
    "巴西乳香": "Schinus terebinthifolia",
    "巴西胡椒木": "Schinus terebinthifolia",
    "魯花樹": "Scolopia oldhamii",
    "臺東漆": "Semecarpus longifolius",
    "鐵刀木": "Senna siamea",
    "黃槐": "Senna surattensis",
    "火焰木": "Spathodea campanulata",
    "筆筒樹": "Sphaeropteris lepifera",
    "太平洋榲桲": "Spondias dulcis",
    "掌葉蘋婆": "Sterculia foetida",
    "蘋婆": "Sterculia monosperma",
    "白鳥蕉": "Strelitzia nicolai",
    "紅皮": "Styrax suberifolius",
    "白樹仔": "Suregada aequorea",
    "大葉桃花心木": "Swietenia macrophylla",
    "桃花心木": "Swietenia macrophylla",
    "灰木": "Symplocos",
    "尾葉灰木": "Symplocos caudata",
    "山豬肝": "Symplocos theophrastifolia",
    "神秘果": "Synsepalum dulcificum",
    "賽赤楠": "Syzygium buxifolium",
    "肯氏蒲桃": "Syzygium cumini",
    "臺灣赤楠": "Syzygium formosanum",
    "蒲桃": "Syzygium jambos",
    "長紅木": "Syzygium myrtifolium",
    "蓮霧": "Syzygium samarangense",
    "山馬茶": "Tabernaemontana divaricata",
    "南洋馬蹄花": "Tabernaemontana pandacaqui",
    "羅望子": "Tamarindus indica",
    "池杉": "Taxodium distichum",
    "落羽杉": "Taxodium distichum",
    "落羽松": "Taxodium distichum",
    "墨西哥落羽松": "Taxodium mucronatum",
    "黃鐘花": "Tecoma stans",
    "馬尼拉欖仁": "Terminalia calamansanai",
    "欖仁": "Terminalia catappa",
    "欖仁樹": "Terminalia catappa",
    "小葉欖仁": "Terminalia mantaly",
    "厚皮香": "Ternstroemia gymnanthera",
    "賊仔樹": "Tetradium glabrifolium",
    "日本香柏": "Thuja standishii",
    "香椿": "Toona sinensis",
    "棕櫚": "Trachycarpus fortunei",
    "山黃麻": "Trema orientale",
    "山油麻": "Trema tomentosum",
    "白桕": "Triadica cochinchinensis",
    "烏桕": "Triadica sebifera",
    "昆欄樹": "Trochodendron aralioides",
    "榔榆": "Ulmus parvifolia",
    "米飯花": "Vaccinium bracteatum",
    "油桐": "Vernicia",
    "三年桐": "Vernicia fordii",
    "千年桐": "Vernicia montana",
    "珊瑚樹": "Viburnum odoratissimum",
    "黃荊": "Vitex negundo",
    "山埔姜": "Vitex quinata",
    "壯幹棕櫚": "Washingtonia filifera",
    "華盛頓椰子": "Washingtonia robusta",
    "水金京": "Wendlandia formosana",
    "狐尾椰子": "Wodyetia bifurcata",
    "黃金蒲桃": "Xanthostemon chrysanthus",
    "象腳王蘭": "Yucca gigantea",
    "食茱萸": "Zanthoxylum ailanthoides",
    "櫸": "Zelkova serrata",
    "臺灣櫸": "Zelkova serrata",
    "印度棗": "Ziziphus mauritiana",
}


def species_from_chinese_name(value: str | None) -> str | None:
    """A published Chinese tree name as a species value, or ``None``.

    The return value is what belongs in the ``species`` column, ready for
    `enforce_tree_schema`:

    * a name from `CHINESE_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands it
      -- a growth form or condition (`form_sentinel_for`, which is what keeps
      `枯木` as `Dead` rather than losing it to `Unknown`) or an empty
      planting site (`is_not_a_tree`, which makes `enforce_tree_schema` drop
      the row; no Taipei value triggers that one today, and the branch is
      here so that one would not need this module changed);
    * ``None`` otherwise, which publishes as ``Unknown``.

    Passing an *unresolved* value through is refused, as it is in
    `_common_name_species` and `_japanese_species`: `sanitize_species`
    rejects a Chinese string outright, so a passed-through name would publish
    as `Unknown` anyway -- but by a longer route, and without this module
    ever admitting that it did not know.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = CHINESE_SPECIES.get(chinese_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
