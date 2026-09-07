"""Resolve a Japanese inventory's vernacular name to an accepted binomial.

NOT a uv inline script -- a regular importable module, like `_ingest_shared`.

Tokyo publishes its street trees under the Japanese vernacular name and nothing
else: `イチョウ`, `ケヤキ`, `トキワマンサク`, 446 distinct values over 228,058
trees on metropolitan roads, with no binomial column anywhere in either file.
`species` is the join key into the enrichment table, so without this the whole
city arrives labelled `Unknown`.

This is `_common_name_species` applied to a second language, and the shape is
deliberately the same -- a curated dict, a key normaliser, and a lookup that
returns `None` rather than guessing.  It is a separate module rather than more
rows in that one because the *keys* live in a different script and normalise by
different rules; the English table's `common_name_key` reduces a value to
`[a-z ]`, which erases a katakana name entirely.

Usage:

    from _japanese_species import species_from_japanese_name

    species_from_japanese_name("イチョウ")      # 'Ginkgo biloba'
    species_from_japanese_name("サクラ")        # 'Prunus'  -- a genus is an answer
    species_from_japanese_name("枯木")          # '枯木'    -- a Dead sentinel
    species_from_japanese_name("カナメモチ/ハマヒサカキ")  # None

**GBIF drafted this table and POWO adjudicated it; neither was trusted alone.**
GBIF's backbone carries Japanese vernacular names, and an exact
`qField=VERNACULAR` match resolved 290 of the 446 values, which is what made a
446-row hand curation affordable.  It is a drafting aid and not the authority,
for the reason the Canadian table records about inverting the enrichment index:
it is right most of the time and wrong without saying so.  Two of its answers
would have mislabelled thousands of trees --

* `ツバキ` came back *Camellia hiemalis*.  `ツバキ` is the common camellia,
  *Camellia japonica*; *C. hiemalis* is `カンツバキ`, which this data publishes
  as its own value 966 times.
* `アメリカヒイラギ` came back *Cartrema americana* (devilwood).  The name is
  "American holly" and it means *Ilex opaca* -- and this data publishes
  `セイヨウヒイラギ`, `ヒイラギモチ` and `シナヒイラギ` alongside it, all hollies.

-- and one is simply not a tree: `ミモザ` came back *Mimosa pudica*, the
sensitive plant, where Japanese horticulture means *Acacia dealbata*.

Every value was then put to POWO the way `species_audit.py` does it, reading
**every** exact match rather than the first.  285 of the 336 came back
`accepted` outright; the rest split three ways, and each way changed something:

* **A homonym is not a problem.**  35 came back `ambiguous` with one reading
  `accepted` -- `Juniperus chinensis`, `Quercus glauca`, `Osmanthus fragrans`.
  A name Kew has published twice is still a name, which is the same reading
  `species_audit.py` takes.
* **A genuine synonym was replaced.**  `Acer amoenum` (オオモミジ) is a synonym
  under both of its readings, so that value is `Acer palmatum`; `Euonymus
  sieboldianus` became `Euonymus hamiltonianus`; `Linnaea x grandiflora` became
  `Abelia x grandiflora`, POWO having moved it back.  `Rhododendron obtusum`
  has no accepted reading at all, so the Kurume and Kirishima azaleas -- which
  are complex hybrids -- resolve to the genus.
* **Four names POWO does not publish** were re-asked in another spelling:
  `Camellia x hiemalis` is `Camellia hiemalis` there, `Citrus junos` is
  `Citrus x junos`, and `Citrus natsudaidai` resolves only as far as
  `Citrus x aurantium`.  `Machilus japonica` has no record, so ホソバタブ is
  the genus.

**Where POWO and the published table disagreed, the published table won.**
This is the one rule that is about *this repo* rather than about taxonomy, and
it is the same argument `SPECIES_SYNONYMS` makes: the same taxon under two
names is two enrichment rows, two LLM calls and two entries in every species
rollup.  POWO calls `Cinnamomum camphora` a synonym of `Camphora officinarum`,
and the enrichment table has carried `Cinnamomum camphora` since San Francisco
was wired -- so 6,882 Tokyo camphor trees join the row that already exists.
The same call was taken for `Acca sellowiana`, `Mahonia japonica`,
`Morus australis`, `Euscaphis japonica`, `Juglans ailantifolia` and
`Cupressus macrocarpa`, the last of which the repo already names as a synonym
*target*.

The two places where that rule pointed the other way became `SPECIES_SYNONYMS`
entries instead, because POWO adjudicated them with a single unambiguous
reading: `Sapium sebiferum` -> `Triadica sebifera` (ナンキンハゼ, 463 trees) and
`Callistemon citrinus` -> `Melaleuca citrina` (ブラシノキ).  Both reclaim an
enrichment row that was being paid for twice.

**A genus is an answer, and 4.2% of these trees get one.**  `サクラ` is 7,128
trees recorded as "cherry" with no species, `シャクナゲ` is "rhododendron", and
a value ending `属` (genus) or `類` (-kind) says so outright -- `モクレン属` is
*Magnolia*, `サクラ類` is *Prunus*.  Those resolve to the genus rather than to
a guessed species, exactly as `ASH SPP.` -> `Fraxinus` does in the English
table.

**What is deliberately left unresolved** is 57 trees, 0.02%:

* `不明` (32), "unknown" -- there is nothing to resolve;
* a family name, which is not a species-rank name.  Five values end in `科`
  (`ツバキ科` Theaceae, `バラ科` Rosaceae); `sanitize_species` drops the
  `-aceae` spelling for the same reason, so this is that rule in Japanese;
* a cell naming several taxa (10 trees).  `カナメモチ/ハマヒサカキ` and
  `シャリンバイ/カナメモチ/ヒイラギ/キンモクセイ` are mixed hedges, and the row
  is one tree in a planting of several species;
* `モクレンモドキ` (2), which names no taxon this curation could place.

They fall through to `None` and publish as `Unknown`, which is honest.  There
is no shape rule for any of them: a value not in the table is `None` already,
and adding rules would only make the module claim to know something.

**One value is a judgement call worth naming.**  `スズカケノキ` is 6,268 trees
and the name means *Platanus orientalis*, so that is what it publishes -- but
Tokyo's plane trees are in practice mostly the London plane, which Japanese
calls `モミジバスズカケノキ`, a value this data never uses.  Translating the
name the publisher wrote is the rule here; asserting a taxon the publisher did
not write would be a different and worse claim.  `プラタナス` and
`スズカケノキ属`, which *are* category names, resolve to `Platanus`.

`tests/test_japanese_species.py` pins the mechanical half -- every value is a
name `sanitize_species` keeps as written, every key is in normalised form, no
value is also a key, no value is a sentinel.  The taxonomy is reviewed by
reading the file, the same way `SPECIES_SYNONYMS` and `COMMON_NAME_SPECIES`
are.
"""

from __future__ import annotations

import unicodedata

from _ingest_shared import form_sentinel_for, is_not_a_tree

# Hiragana to katakana, one codepoint block apart.  Japanese plant names are
# conventionally written in katakana and both files do so almost everywhere,
# but `さくら` and `しらかし` appear in hiragana -- the same name, typed by
# somebody who did not switch modes.  Folding the two scripts is one rule where
# two more table rows would be a coincidence waiting to be repeated.
_HIRAGANA_TO_KATAKANA = {chr(c): chr(c + 0x60) for c in range(0x3041, 0x3097)}

# The katakana middle dot, which separates the parts of a transliterated
# binomial: `ユッカ・エレファンティペス`.  Removed with the spaces so that
# `タイサンボク リトルジェム` and `タイサンボクリトルジェム` -- both published,
# eleven trees and one -- are one key.
_MIDDLE_DOT = "・"


def japanese_name_key(value: str | None) -> str:
    """A published Japanese name reduced to the form the table uses.

    NFKC-normalised (which folds half-width katakana and full-width ASCII),
    hiragana folded to katakana, and stripped of every space -- including the
    ideographic space U+3000, which `ロドレイア　ヘンリー` is written with.

    Returns `""` for a value with nothing left in it.

    Examples:
        "イチョウ"                    -> "イチョウ"
        "さくら"                      -> "サクラ"
        "ロドレイア　ヘンリー"        -> "ロドレイアヘンリー"
        "ユッカ・エレファンティペス"  -> "ユッカエレファンティペス"
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(_HIRAGANA_TO_KATAKANA.get(ch, ch) for ch in text)
    return "".join(text.split()).replace(_MIDDLE_DOT, "")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
#
# Keys are `japanese_name_key` output; values are names `sanitize_species`
# keeps as written -- accepted at POWO (or accepted under one of its readings),
# ASCII hybrid mark, genus capitalised, no rank below species.  Grouped by
# genus, because that is how it was checked against POWO.
#
# A trailing `# comment` is the value as the portal publishes it, shown where
# keying changed it.

JAPANESE_SPECIES: dict[str, str] = {
    "アベリア": "Abelia x grandiflora",
    "ハナゾノツクバネウツギ": "Abelia x grandiflora",
    "モミ": "Abies firma",
    "モミノキ": "Abies firma",
    "ミモザ": "Acacia dealbata",
    "モリシマアカシア": "Acacia mearnsii",
    "フェイジョア": "Acca sellowiana",
    "アメリカハナノキ": "Acer rubrum",
    "イタヤカエデ": "Acer pictum",
    "イロハモミジ": "Acer palmatum",
    "オオモミジ": "Acer palmatum",
    "カエデ": "Acer",
    "サトウカエデ": "Acer saccharum",
    "シダレモミジ": "Acer palmatum",
    "トウカエデ": "Acer buergerianum",
    "ネグンドカエデ": "Acer negundo",
    "ノルウェーカエデ": "Acer platanoides",
    "ハウチワカエデ": "Acer japonicum",
    "ハナノキ": "Acer pycnanthum",
    "モミジ": "Acer palmatum",
    "モミジ司シルエット": "Acer palmatum",
    "セイヨウトチノキ": "Aesculus hippocastanum",
    "トチノキ": "Aesculus turbinata",
    "ベニバナトチノキ": "Aesculus x carnea",
    "ニワウルシ": "Ailanthus altissima",
    "ネムノキ": "Albizia julibrissin",
    "ケヤマハンノキ": "Alnus hirsuta",
    "ヤマハンノキ": "Alnus hirsuta",
    "アメリカザイフリボク": "Amelanchier canadensis",
    "ザイフリボク": "Amelanchier asiatica",
    "ムクノキ": "Aphananthe aspera",
    "シマナンヨウスギ": "Araucaria heterophylla",
    "イチゴノキ": "Arbutus unedo",
    "ストロベリーツリー": "Arbutus unedo",
    "ヒメイチゴノキ": "Arbutus unedo",
    "アオキ": "Aucuba japonica",
    "シラカバ": "Betula platyphylla",
    "キダチチョウセンアサガオ": "Brugmansia",
    "ツゲ": "Buxus microphylla",
    "ボックスウッド": "Buxus",
    "コムラサキ": "Callicarpa dichotoma",
    "ムラサキシキブ": "Callicarpa japonica",
    "オトメツバキ": "Camellia japonica",
    "カンツバキ": "Camellia hiemalis",
    "コウオトメツバキ": "Camellia japonica",
    "サザンカ": "Camellia sasanqua",
    "サザンカ類": "Camellia",
    "タチカンツバキ": "Camellia hiemalis",
    "チャノキ": "Camellia sinensis",
    "ツバキ": "Camellia japonica",
    "ツバキ類": "Camellia",
    "ヒメサザンカ": "Camellia lutchuensis",
    "ヤブツバキ": "Camellia japonica",
    "ノウゼンカズラ": "Campsis grandiflora",
    "ノウゼンカツラ": "Campsis grandiflora",
    "アカシデ": "Carpinus laxiflora",
    "イヌシデ": "Carpinus tschonoskii",
    "クマシデ": "Carpinus japonica",
    "クリ": "Castanea crenata",
    "シイノキ": "Castanopsis",
    "スダジイ": "Castanopsis sieboldii",
    "ヒマラヤスギ": "Cedrus deodara",
    "エノキ": "Celtis sinensis",
    "カツラ": "Cercidiphyllum japonicum",
    "アメリカハナズオウ": "Cercis canadensis",
    "ハナズオウ": "Cercis chinensis",
    "ボケ": "Chaenomeles speciosa",
    "サワラ": "Chamaecyparis pisifera",
    "ヒノキ": "Chamaecyparis obtusa",
    "ソシンロウバイ": "Chimonanthus praecox",
    "ロウバイ": "Chimonanthus praecox",
    "アメリカヒトツバタゴ": "Chionanthus virginicus",
    "ヒトツバタゴ": "Chionanthus retusus",
    "クスノキ": "Cinnamomum camphora",
    "シバニッケイ": "Cinnamomum doederleinii",
    "ニッケイ": "Cinnamomum sieboldii",
    "カラタチ": "Citrus trifoliata",
    "キンカン": "Citrus japonica",
    "ナツミカン": "Citrus x aurantium",
    "ユズ": "Citrus x junos",
    "リョウブ": "Clethra barbinervis",
    "サカキ": "Cleyera japonica",
    "フイリサカキ": "Cleyera japonica",
    "ニオイシュロラン": "Cordyline australis",
    "クマノミズキ": "Cornus macrophylla",
    "サンシュユ": "Cornus officinalis",
    "ジョウリョクヤマボウシ": "Cornus hongkongensis",
    "ハナミズキ": "Cornus florida",
    "ミズキ": "Cornus controversa",
    "ヤマボウシ": "Cornus kousa",
    "常緑ヤマボウシ": "Cornus hongkongensis",
    "コウヤミズキ": "Corylopsis gotoana",
    "トサミズキ": "Corylopsis spicata",
    "トサミズキ属": "Corylopsis",
    "ヒュウガミズキ": "Corylopsis pauciflora",
    "スギ": "Cryptomeria japonica",
    "アリゾナイトスギ": "Cupressus arizonica",
    "イトスギ": "Cupressus",
    "ゴールドクレスト": "Cupressus macrocarpa",
    "モントレーイトスギ": "Cupressus macrocarpa",
    "レイランドヒノキ": "Cupressus x leylandii",
    "マルメロ": "Cydonia oblonga",
    "エニシダ": "Cytisus scoparius",
    "ジンチョウゲ": "Daphne odora",
    "チンチョウゲ": "Daphne odora",
    "ヒメユズリハ": "Daphniphyllum teijsmannii",
    "ユズリハ": "Daphniphyllum macropodum",
    "ハンカチノキ": "Davidia involucrata",
    "カクレミノ": "Dendropanax trifidus",
    "ウツギ": "Deutzia crenata",
    "ウツギ属": "Deutzia",
    "ツクバネウツギ": "Diabelia spathulata",
    "カキノキ": "Diospyros kaki",
    "カキノキ属": "Diospyros",
    "カキ属": "Diospyros",
    "マメガキ": "Diospyros lotus",
    "イスノキ": "Distylium racemosum",
    "タイワンレンギョウ": "Duranta erecta",
    "ミツマタ": "Edgeworthia chrysantha",
    "アキグミ": "Elaeagnus umbellata",
    "オオバグミ": "Elaeagnus macrophylla",
    "グミ": "Elaeagnus",
    "ツルグミ": "Elaeagnus glabra",
    "ナツグミ": "Elaeagnus multiflora",
    "ナワシログミ": "Elaeagnus pungens",
    "ニワグミ": "Elaeagnus multiflora",
    "ホルトノキ": "Elaeocarpus decipiens",
    "サラサドウダン": "Enkianthus campanulatus",
    "ドウダンツツジ": "Enkianthus perulatus",
    "ビワ": "Eriobotrya japonica",
    "コマユミ": "Euonymus alatus",
    "ニシキギ": "Euonymus alatus",
    "ニシキギ属": "Euonymus",
    "フイリマサキ": "Euonymus japonicus",
    "マサキ": "Euonymus japonicus",
    "マユミ": "Euonymus hamiltonianus",
    "ハマヒサカキ": "Eurya emarginata",
    "ヒサカキ": "Eurya japonica",
    "ゴンズイ": "Euscaphis japonica",
    "リキュウバイ": "Exochorda racemosa",
    "ヤツデ": "Fatsia japonica",
    "イチジク": "Ficus carica",
    "イヌビワ": "Ficus erecta",
    "インドゴムノキ": "Ficus elastica",
    "フィカス": "Ficus",
    "アオギリ": "Firmiana simplex",
    "レンギョウ": "Forsythia suspensa",
    "アオダモ": "Fraxinus lanuginosa",
    "シマトネリコ": "Fraxinus griffithii",
    "マルバアオダモ": "Fraxinus sieboldiana",
    "クチナシ": "Gardenia jasminoides",
    "コクチナシ": "Gardenia jasminoides",
    "イチョウ": "Ginkgo biloba",
    "サイカチ": "Gleditsia japonica",
    "シナマンサク": "Hamamelis mollis",
    "マンサ": "Hamamelis japonica",
    "マンサク": "Hamamelis japonica",
    "カボック": "Heptapleurum arboricola",
    "カポック": "Heptapleurum arboricola",
    "ヤドリフカノキ": "Heptapleurum arboricola",
    "フヨウ": "Hibiscus mutabilis",
    "ムクゲ": "Hibiscus syriacus",
    "アジサイ": "Hydrangea macrophylla",
    "アジサイ類": "Hydrangea",
    "カシワバアジサイ": "Hydrangea quercifolia",
    "ガクアジサイ": "Hydrangea macrophylla",
    "キンシバイ": "Hypericum patulum",
    "ビョウヤナギ": "Hypericum monogynum",
    "イイギリ": "Idesia polycarpa",
    "アメリカヒイラギ": "Ilex opaca",
    "イヌツゲ": "Ilex crenata",
    "ウメモドキ": "Ilex serrata",
    "キンメツゲ": "Ilex crenata",
    "クロガネモチ": "Ilex rotunda",
    "シナヒイラギ": "Ilex cornuta",
    "セイヨウヒイラギ": "Ilex aquifolium",
    "ソヨゴ": "Ilex pedunculosa",
    "ナナミノキ": "Ilex chinensis",
    "ヒイラギモチ": "Ilex cornuta",
    "ヒメヒイラギ": "Ilex dimorphophylla",
    "モチノキ": "Ilex integra",
    "シキミ": "Illicium anisatum",
    "ジャカランダ": "Jacaranda mimosifolia",
    "オニグルミ": "Juglans ailantifolia",
    "イブキ": "Juniperus chinensis",
    "カイズカイブキ": "Juniperus chinensis",
    "カイヅカイブキ": "Juniperus chinensis",
    "コロラドビャクシン": "Juniperus scopulorum",
    "ネズミサシ": "Juniperus rigida",
    "ビャクシン": "Juniperus chinensis",
    "カルミア": "Kalmia latifolia",
    "ヤマブキ": "Kerria japonica",
    "モクゲンジ": "Koelreuteria paniculata",
    "サルスベリ": "Lagerstroemia indica",
    "ランタナ": "Lantana camara",
    "ゲッケイジュ": "Laurus nobilis",
    "ハギ属": "Lespedeza",
    "イボタノキ": "Ligustrum obtusifolium",
    "イボタノキ属": "Ligustrum",
    "セイヨウイボタ": "Ligustrum vulgare",
    "トウネズミモチ": "Ligustrum lucidum",
    "ネズミモチ": "Ligustrum japonicum",
    "クロモジ": "Lindera umbellata",
    "ダンコウバイ": "Lindera obtusiloba",
    "ヤマコウバシ": "Lindera glauca",
    "アメリカフウ": "Liquidambar styraciflua",
    "フウ": "Liquidambar formosana",
    "モミジバフウ": "Liquidambar styraciflua",
    "モミジバフウウ": "Liquidambar styraciflua",
    "ユリノキ": "Liriodendron tulipifera",
    "マテバシイ": "Lithocarpus edulis",
    "ビロウ": "Livistona chinensis",
    "トキワマンサク": "Loropetalum chinense",
    "トキワマンサク属": "Loropetalum",
    "ベニバナトキワマンサク": "Loropetalum chinense",
    "ネジキ": "Lyonia ovalifolia",
    "イヌエンジュ": "Maackia amurensis",
    "タブノキ": "Machilus thunbergii",
    "ホソバタブ": "Machilus",
    "オガタマ": "Magnolia compressa",
    "オガタマノキ": "Magnolia compressa",
    "オガタマノキ類": "Magnolia",
    "カラタネオガタマ": "Magnolia figo",
    "コブシ": "Magnolia kobus",
    "シデコブシ": "Magnolia stellata",
    "シモクレン": "Magnolia liliiflora",
    "タイサンボク": "Magnolia grandiflora",
    "タイサンボクリトルジェム": "Magnolia grandiflora",  # タイサンボク リトルジェム
    "タムシバ": "Magnolia salicifolia",
    "トウモクレン": "Magnolia",
    "ハクモクレン": "Magnolia denudata",
    "ヒメタイサンボク": "Magnolia virginiana",
    "ホオノキ": "Magnolia obovata",
    "マグノリア": "Magnolia",
    "ミヤマガンショウ": "Magnolia maudiae",
    "モクレン": "Magnolia liliiflora",
    "モクレン属": "Magnolia",
    "ワダスメモリー": "Magnolia",
    "ヒイラギナンテン": "Mahonia japonica",
    "アカメガシワ": "Mallotus japonicus",
    "イヌリンゴ": "Malus prunifolia",
    "カイドウ": "Malus halliana",
    "ハナカイドウ": "Malus halliana",
    "ハナリンゴ": "Malus",
    "ヒメリンゴ": "Malus prunifolia",
    "リンゴ": "Malus domestica",
    "ブラシノキ": "Melaleuca citrina",
    "センダン": "Melia azedarach",
    "メタセコイア": "Metasequoia glyptostroboides",
    "クワ": "Morus",
    "マグワ": "Morus alba",
    "ヤマグワ": "Morus australis",
    "ヤマモモ": "Myrica rubra",
    "キンバイカ": "Myrtus communis",
    "ギンバイカ": "Myrtus communis",
    "ナギ": "Nageia nagi",
    "オタフクナンテン": "Nandina domestica",
    "ナンテン": "Nandina domestica",
    "シロダモ": "Neolitsea sericea",
    "キョウチクトウ": "Nerium oleander",
    "キンモク": "Osmanthus fragrans",
    "キンモクセイ": "Osmanthus fragrans",
    "ギンモクセイ": "Osmanthus fragrans",
    "シマモクセイ": "Osmanthus insularis",
    "ヒイラギ": "Osmanthus heterophyllus",
    "ヒイラギモクセイ": "Osmanthus x fortunei",
    "モクセイ属": "Osmanthus",
    "キリ": "Paulownia tomentosa",
    "カナリーヤシ": "Phoenix canariensis",
    "アカメ": "Photinia x fraseri",
    "オオカナメモチ": "Photinia serratifolia",
    "カナメモチ": "Photinia glabra",
    "セイヨウベニカナメモチ": "Photinia x fraseri",
    "ベニカナメモチ": "Photinia x fraseri",
    "モウソウチク": "Phyllostachys edulis",
    "コロラドトウヒ": "Picea pungens",
    "ドイツトウヒ": "Picea abies",
    "アセビ": "Pieris japonica",
    "アイグロマツ": "Pinus",
    "アカマツ": "Pinus densiflora",
    "クロマツ": "Pinus thunbergii",
    "マツ": "Pinus",
    "トベラ": "Pittosporum tobira",
    "スズカケノキ": "Platanus orientalis",
    "スズカケノキ属": "Platanus",
    "プラタナス": "Platanus",
    "コノテガシワ": "Platycladus orientalis",
    "コノテヒバ": "Platycladus orientalis",
    "イヌマキ": "Podocarpus macrophyllus",
    "アプリコット": "Prunus armeniaca",
    "アンズ": "Prunus armeniaca",
    "イヌザクラ": "Prunus buergeriana",
    "ウメ": "Prunus mume",
    "ウメ類": "Prunus",
    "ウワミズザクラ": "Prunus grayana",
    "ギンコウバイ": "Prunus mume",
    "コウバイ": "Prunus mume",
    "コバザクラ": "Prunus",
    "サクラ": "Prunus",  # さくら
    "サクラ類": "Prunus",
    "サトザクラ類": "Prunus serrulata",
    "ザクラ": "Prunus",
    "シダレザクラ": "Prunus",
    "シダレモモ": "Prunus persica",
    "スモモ": "Prunus salicina",
    "セイヨウバクチノキ": "Prunus laurocerasus",
    "ソメイヨシノ": "Prunus x yedoensis",
    "テルテモモ": "Prunus persica",
    "ニワウメ": "Prunus japonica",
    "ハナモモ": "Prunus persica",
    "ホウキモモ": "Prunus persica",
    "ミロバランスモモ": "Prunus cerasifera",
    "モモ": "Prunus persica",
    "モモ属": "Prunus",
    "ヤマザクラ": "Prunus jamasakura",
    "ユスラウメ": "Prunus tomentosa",
    "ヨウコウ": "Prunus",
    "カリン": "Pseudocydonia sinensis",
    "ザクロ": "Punica granatum",
    "タチバナモドキ": "Pyracantha angustifolia",
    "トキワサンザシ": "Pyracantha coccinea",
    "トキワサンザシ属": "Pyracantha",
    "ピラカンサ": "Pyracantha",
    "ピラカンサス": "Pyracantha",
    "ピラカンサ類": "Pyracantha",
    "ナシ": "Pyrus pyrifolia",
    "アカガシ": "Quercus acuta",
    "アメリカガシワ": "Quercus",
    "アラカシ": "Quercus glauca",
    "イギリスナラ": "Quercus robur",
    "ウバメガシ": "Quercus phillyreoides",
    "クヌギ": "Quercus acutissima",
    "コナラ": "Quercus serrata",
    "シラカシ": "Quercus myrsinifolia",  # しらかし
    "ツクバネガシ": "Quercus sessilifolia",
    "レッドオーク": "Quercus rubra",
    "シャリンバイ": "Rhaphiolepis umbellata",
    "ヒメシャリンバイ": "Rhaphiolepis umbellata",
    "マルバシャリンバイ": "Rhaphiolepis umbellata",
    "オオムラサキ": "Rhododendron x pulchrum",
    "オオムラサキツツジ": "Rhododendron x pulchrum",
    "キリシマツツジ": "Rhododendron",
    "クルメツツジ": "Rhododendron",
    "サツキ": "Rhododendron indicum",
    "シャクナゲ": "Rhododendron",
    "シャクナゲ類": "Rhododendron",
    "セイヨウシャクナゲ": "Rhododendron",
    "ツツジ": "Rhododendron",
    "ニホンシャクナゲ": "Rhododendron degronianum",
    "ハクサンシャクナゲ": "Rhododendron brachycarpum",
    "ミツバツツジ": "Rhododendron dilatatum",
    "ミツバツツジ類": "Rhododendron",
    "ヤマツツジ": "Rhododendron kaempferi",
    "シャクナゲモドキ": "Rhodoleia championii",
    "ロドレイア": "Rhodoleia championii",
    "ロドレイアヘンリー": "Rhodoleia championii",  # ロドレイア　ヘンリー
    "ロドレイヤ": "Rhodoleia championii",
    "ヌルデ": "Rhus chinensis",
    "ニセアカシア": "Robinia pseudoacacia",
    "イノバラ": "Rosa multiflora",
    "ノイバラ": "Rosa multiflora",
    "バラ": "Rosa",
    "バラ類": "Rosa",
    "アカメヤナギ": "Salix chaenomeloides",
    "カワヤナギ": "Salix gilgiana",
    "シダレヤナギ": "Salix babylonica",
    "タチヤナギ": "Salix triandra",
    "ニワトコ": "Sambucus racemosa",
    "ムクロジ": "Sapindus mukorossi",
    "コウヤマキ": "Sciadopitys verticillata",
    "ハクチョウゲ": "Serissa japonica",
    "イヌホオズキ": "Solanum nigrum",
    "ニワナナカマド": "Sorbaria kirilowii",
    "ホザキナナカマド": "Sorbaria sorbifolia",
    "ナナカマド": "Sorbus commixta",
    "コデマリ": "Spiraea cantoniensis",
    "シモツケ": "Spiraea japonica",
    "ユキヤナギ": "Spiraea thunbergii",
    "キブシ": "Stachyurus praecox",
    "ナツツバキ": "Stewartia pseudocamellia",
    "ヒメシャラ": "Stewartia monadelpha",
    "エンジュ": "Styphnolobium japonicum",
    "エゴノキ": "Styrax japonicus",
    "ハクウンボク": "Styrax obassia",
    "ハイノキ": "Symplocos myrtacea",
    "ライラック": "Syringa vulgaris",
    "イチイ": "Taxus cuspidata",
    "キャラボク": "Taxus cuspidata",
    "モッコク": "Ternstroemia gymnanthera",
    "ニオイヒバ": "Thuja occidentalis",
    "ヒバ": "Thujopsis dolabrata",
    "オオバボダイジュ": "Tilia maximowicziana",
    "シナノキ": "Tilia japonica",
    "シナノキ属": "Tilia",
    "セイヨウシナノキ": "Tilia x europaea",
    "ナツボダイジュ": "Tilia platyphyllos",
    "ボダイジュ": "Tilia miqueliana",
    "カヤ": "Torreya nucifera",
    "ハゼノキ": "Toxicodendron succedaneum",
    "シュロ": "Trachycarpus fortunei",
    "トウジュロ": "Trachycarpus fortunei",
    "ナンキンハゼ": "Triadica sebifera",
    "アキニレ": "Ulmus parvifolia",
    "ブルーベリー": "Vaccinium",
    "オオデマリ": "Viburnum plicatum",
    "ガマズミ": "Viburnum dilatatum",
    "ガマズミ属": "Viburnum",
    "サンゴジュ": "Viburnum odoratissimum",
    "セイヨウテマリカンボク": "Viburnum opulus",
    "ハクサンボク": "Viburnum japonicum",
    "ビブルヌムティヌス": "Viburnum tinus",
    "ミヤマガマズミ": "Viburnum wrightii",
    "ヤブデマリ": "Viburnum plicatum",
    "セイヨウニンジンボク": "Vitex agnus-castus",
    "ニンジンボク": "Vitex negundo",
    "ワシントンヤシ": "Washingtonia filifera",
    "タニウツギ": "Weigela hortensis",
    "ニシキウツギ": "Weigela decora",
    "ハコネウツギ": "Weigela coraeensis",
    "フジ": "Wisteria floribunda",
    "アツバキミガヨラン": "Yucca gloriosa",
    "キミガヨラン": "Yucca gloriosa",
    "ユッカ": "Yucca",
    "ユッカエレファンティペス": "Yucca gigantea",  # ユッカ・エレファンティペス
    "ユッカ類": "Yucca",
    "サンショウ": "Zanthoxylum piperitum",
    "ケヤキ": "Zelkova serrata",
}


def species_from_japanese_name(value: str | None) -> str | None:
    """A published Japanese tree name as a species value, or ``None``.

    The return value is what belongs in the ``species`` column, ready for
    `enforce_tree_schema`:

    * a name from `JAPANESE_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands it --
      a growth form (`form_sentinel_for`, which is what keeps `枯木` as `Dead`
      and `ヤシ科sp.` as `Palm` rather than losing both to `Unknown`) or an
      empty planting site (`is_not_a_tree`, which makes `enforce_tree_schema`
      drop the row; no Japanese value triggers that one today, and the branch
      is here so that one would not need this module changed);
    * ``None`` otherwise, which publishes as ``Unknown``.

    Passing an *unresolved* value through is refused, as it is in
    `_common_name_species`, and here the reason is stronger rather than weaker:
    `sanitize_species` rejects a katakana string outright, so a passed-through
    name would publish as `Unknown` anyway -- but by a longer route, and
    without this module ever admitting that it did not know.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = JAPANESE_SPECIES.get(japanese_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
