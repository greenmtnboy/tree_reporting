"""`_chinese_species`, the Taiwanese common name -> binomial table.

The table is curated by hand, so what a test can add is the mechanical half:
that every value is a name the ingest would keep as written, that every key is
in normalised form (a key that is not can never be looked up), that the
normaliser folds the things Taipei's two files actually publish, and that
every one of the 471 names those files publish is accounted for -- resolved,
or listed as deliberately unresolved -- so a name that is neither is a red
test rather than a quiet `Unknown`.

The taxonomy itself is not testable here and is not meant to be -- it is
reviewed by reading the file, the same way `JAPANESE_SPECIES`,
`SPECIES_SYNONYMS` and `_NON_TAXON_REWRITES` are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _chinese_species import (  # noqa: E402
    CHINESE_SPECIES,
    UNRESOLVED,
    chinese_name_key,
    species_from_chinese_name,
)
from _ingest_shared import (  # noqa: E402
    SPECIES_SENTINELS,
    form_sentinel_for,
    is_not_a_tree,
    sanitize_species,
)

# Every distinct `TreeType` value in Taipei's street-tree and park-tree files
# (September 2026), as published: 471 values, including the blank one and the
# one the export could not encode.  A committed copy rather than a live read,
# because the point is to pin what this table was curated against; a new
# value the portal starts publishing is added here and then resolved.
TAIPEI_TREE_TYPES: tuple[str, ...] = (
    '榕樹',
    '茄苳',
    '樟樹',
    '楓香',
    '臺灣欒樹',
    '黑板樹',
    '白千層',
    '小葉欖仁',
    '水黃皮',
    '山櫻花',
    '大花紫薇',
    '大王椰子',
    '光蠟樹',
    '阿勃勒',
    '苦楝',
    '木棉',
    '垂榕',
    '福木',
    '蒲葵',
    '烏桕',
    '菩提樹',
    '芒果',
    '黃椰子',
    '紫薇',
    '榔榆',
    '九芎',
    '盾柱木',
    '龍柏',
    '落羽杉',
    '櫻花',
    '櫸',
    '羅比親王海棗',
    '亞力山大椰子',
    '蘭嶼羅漢松',
    '流蘇',
    '印度紫檀',
    '第倫桃',
    '鳳凰木',
    '血桐',
    '鵝掌柴',
    '羅漢松',
    '黃連木',
    '梅',
    '欖仁樹',
    '美人樹',
    '黃脈刺桐',
    '青楓',
    '印度橡膠樹',
    '厚皮香',
    '猢猻木',
    '洋紅風鈴木',
    '火焰木',
    '水柳',
    '肯氏蒲桃',
    '構樹',
    '馬拉巴栗',
    '豔紫荊',
    '大葉山欖',
    '杜英',
    '龍眼',
    '雀榕',
    '風鈴木',
    '海檬果',
    '小葉南洋杉',
    '蓮霧',
    '香楠',
    '無患子',
    '富士櫻',
    '緬梔花',
    '蘭嶼烏心石',
    '黃金風鈴木',
    '竹柏',
    '青剛櫟',
    '肯氏南洋杉',
    '白玉蘭',
    '桂花',
    '刺桐',
    '陰香',
    '桑樹',
    '鐵刀木',
    '穗花棋盤腳',
    '洋紫荊',
    '柚子',
    '大葉桉',
    '溼地松',
    '楊桃',
    '鐵冬青',
    '側柏',
    '錫蘭橄欖',
    '紅楠',
    '桃花心木',
    '掌葉蘋婆',
    '烏心石',
    '大葉桃花心木',
    '山黃麻',
    '相思樹',
    '華盛頓椰子',
    '朴樹',
    '番石榴',
    '稜果榕',
    '酒瓶椰子',
    '菲律賓紫檀',
    '臺灣海棗',
    '槭葉翅子木',
    '羊蹄甲',
    '檳榔',
    '麵包樹',
    '海棗',
    '島榕',
    '緬梔',
    '森氏紅淡比',
    '酪梨',
    '洋玉蘭',
    '月橘',
    '大葉楠',
    '蒲桃',
    '木麻黃',
    '亞里垂榕',
    '黑松',
    '石朴',
    '臺灣海桐',
    '日本山茶',
    '白匏子',
    '楊梅',
    '棍棒椰子',
    '錫蘭饅頭果',
    '肖楠',
    '蠟腸樹',
    '苦茶',
    '風箱樹',
    '樹杞',
    '土肉桂',
    '海桐',
    '凍子椰子',
    '千年桐',
    '銀樺',
    '墨西哥落羽松',
    '軟毛柿',
    '小葉桑',
    '象牙柿',
    '臺灣赤楠',
    '荔枝',
    '春不老',
    '旅人蕉',
    '象牙樹',
    '枇杷',
    '澳洲鴨腳木',
    '紅花鐵刀木',
    '石栗',
    '叢立孔雀椰子',
    '含笑花',
    '大葉雀榕',
    '欖仁',
    '蛋黃果',
    '吉野櫻',
    '串錢柳',
    '蟲屎',
    '油椰子',
    '孟加拉榕',
    '黃玉蘭',
    '藍花楹',
    '菲律賓榕',
    '大風子',
    '臺灣櫸',
    '波羅蜜',
    '魚木',
    '水同木',
    '壯幹棕櫚',
    '海南菜豆樹',
    '琴葉榕',
    '苦橙',
    '珊瑚樹',
    '木瓜',
    '樹蘭',
    '披針葉饅頭果',
    '金桔',
    '大頭茶',
    '水石榕',
    '香椿',
    '臺灣三角楓',
    '大葉合歡',
    '黃金榕',
    '破布子',
    '銀合歡',
    '扁柏',
    '咖啡樹',
    '柳橙',
    '九節木',
    '刺杜密',
    '桃',
    '厚葉榕',
    '九重葛',
    '印度黃檀',
    '落羽松',
    '魯花樹',
    '河津櫻',
    '黃褥花',
    '檸檬',
    '黃皮',
    '烏?',
    '煙火樹',
    '香龍血樹',
    '山菜豆',
    '柑橘',
    '臺灣肖楠',
    '加拿列海棗',
    '孔雀椰子',
    '可可椰子',
    '人心果',
    '雞冠刺桐',
    '瓊崖海棠',
    '馬尼拉椰子',
    '銀葉鈕扣樹',
    '尖尾長葉榕',
    '',
    '山陀兒',
    '垂柳',
    '毛風鈴木',
    '南洋含笑',
    '臺灣五葉松',
    '爪哇旃那',
    '黃槿',
    '大漁櫻',
    '山紅柿',
    '金龜樹',
    '九丁榕',
    '厚葉石斑木',
    '薄葉虎皮楠',
    '夾竹桃',
    '日日櫻',
    '柿子',
    '龍眼樹',
    '筆筒樹',
    '珊瑚刺桐',
    '木芙蓉',
    '李',
    '紅瓶刷子樹',
    '澳洲茶樹',
    '池杉',
    '山刈葉',
    '山芙蓉',
    '樹葡萄',
    '扁桃葉斑鳩菊',
    '臺灣二葉松',
    '野梨',
    '山埔姜',
    '毛柿',
    '黃金蒲桃',
    '黃鐘花',
    '加羅林魚木',
    '南美假櫻桃',
    '食茱萸',
    '牛樟',
    '黃槐',
    '檸檬桉',
    '山黃梔',
    '錫蘭肉桂',
    '小實女貞',
    '沉香',
    '朱槿',
    '臺灣石楠',
    '枯木',
    '草莓番石榴',
    '山油麻',
    '玉蘭',
    '杜虹花',
    '羅氏鹽膚木',
    '水杉',
    '菲律賓饅頭果',
    '釋迦',
    '蘭嶼肉桂',
    '紅淡比',
    '臭娘子',
    '澀葉榕',
    '土楠',
    '寒櫻',
    '辣木',
    '八重櫻',
    '金露花',
    '槭葉酒瓶樹',
    '檬果',
    '雞爪楓',
    '小梗木薑子',
    '臺灣油杉',
    '山刺番荔枝',
    '大果藤榕',
    '斑葉垂榕',
    '細葉饅頭果',
    '火筒樹',
    '尾葉灰木',
    '大花曼陀羅',
    '糙葉樹',
    '火刺木',
    '龍鱗櫚',
    '番仔林投',
    '吉貝木棉',
    '紅花繼木',
    '紫玉蘭',
    '小花鼠刺',
    '蘋婆',
    '豆梨',
    '花旗木',
    '金剛纂',
    '錫蘭海棗',
    '馬尼拉欖仁',
    '重瓣彎子木',
    '臺東漆',
    '紅邊竹蕉',
    '圓柏',
    '栗豆樹',
    '象耳榕',
    '香水樹',
    '三角椰子',
    '長梗紫麻',
    '倒卵葉冬青',
    '酒瓶蘭',
    '昭和櫻',
    '南洋櫻',
    '霸王櫚',
    '馬尾松',
    '油茶',
    '蘇鐵',
    '土樟',
    '木槿',
    '千頭木麻黃',
    '野桐',
    '牛油果',
    '印度棗',
    '臺灣香檬',
    '三年桐',
    '白樹仔',
    '變葉木',
    '白鳥蕉',
    '三腳虌',
    '山豬肝',
    '細葉桉',
    '奧氏虎皮楠',
    '小實孔雀豆',
    '南洋馬蹄花',
    '杏',
    '棋盤腳',
    '橄欖',
    '火漆木',
    '黃蝴蝶',
    '賽赤楠',
    '黃花風鈴木',
    '蘇利南合歡',
    '羅望子',
    '稜果蒲桃',
    '臺東石楠',
    '石斑木',
    '墨水樹',
    '沙盒樹',
    '台灣梭羅木',
    '刺葉桂櫻',
    '水金京',
    '臺灣雅楠',
    '賊仔樹',
    '紅棕櫚',
    '烏來冬青',
    '金新木薑子',
    '橘子',
    '密花白飯樹',
    '垂花琴木',
    '番龍眼',
    '銀葉樹',
    '白桕',
    '橡皮樹',
    '樹商陸',
    '巴西乳香',
    '綠珊瑚',
    '大香葉樹',
    '粗糠柴',
    '檄樹',
    '椬梧',
    '圓葉福祿桐',
    '黃金果',
    '梧桐',
    '雨豆樹',
    '合歡',
    '山馬茶',
    '金棗',
    '羅庚果',
    '辛夷',
    '頷垂豆',
    '長尾栲',
    '黃杞',
    '龍血樹',
    '藍棕櫚',
    '神秘果',
    '水冬瓜',
    '山桂花',
    '棕櫚',
    '林投',
    '楠樹',
    '昆欄樹',
    '白水木',
    '安石榴',
    '孔雀木',
    '百合竹',
    '蒲瓜樹',
    '銀杏',
    '彎子木',
    '臺灣泡桐',
    '麻楝',
    '使君子',
    '裡白楤木',
    '白木香',
    '夜合花',
    '綠葉竹蕉',
    '黃果垂榕',
    '亞歷山大椰子',
    '南洋含笑花',
    '油桐',
    '芭樂',
    '冇骨消',
    '印度塔樹',
    '紅刺露兜',
    '山枇杷',
    '馬拉巴柿',
    '澳洲胡桃',
    '蘭嶼山桂花',
    '楓港柿',
    '番茉莉',
    '夜香木',
    '日本香柏',
    '叢花百日青',
    '白桐',
    '長紅木',
    '黃荊',
    '油葉石櫟',
    '槲櫟',
    '銀葉桉',
    '劍葉緬梔',
    '白仙丹花',
    '美國紅梣',
    '石苓舅',
    '柳丁',
    '牛奶榕',
    '山柿',
    '紅仔珠',
    '冬青',
    '巴西胡椒木',
    '七里香',
    '槭樹',
    '米飯花',
    '早田氏柃木',
    '臺灣梣',
    '大明橘',
    '紅葉樹',
    '象腳王蘭',
    '化香樹',
    '盤龍木',
    '霸王椰子',
    '桂葉黃梅',
    '太平洋榲桲',
    '過山香',
    '鷹爪花',
    '灰木',
    '潺槁木薑子',
    '糊樗',
    '紅皮',
    '赤皮',
    '茜草樹',
    '小果油茶',
    '馬氏射葉椰子',
    '狐尾椰子',
    '樟葉槭',
    '金雞納樹',
    '香拔',
    '朝鮮紫珠',
)


@pytest.mark.parametrize("key,value", sorted(CHINESE_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    """`sanitize_species` must return the value unchanged.

    `species` is the join key into the enrichment table, and
    `enforce_tree_schema` puts every value through `sanitize_species` before
    publishing it.  A value that gets rewritten there (a rank below species, a
    U+00D7 hybrid mark, a lowercase genus, a name in `SPECIES_SYNONYMS`) would
    publish as something other than what this table says, and a value that
    gets *rejected* would publish as `Unknown` -- so the table would look
    right and do nothing.
    """
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(CHINESE_SPECIES))
def test_every_key_is_in_normalised_form(key: str):
    """A key not in `chinese_name_key` form is dead: nothing can match it."""
    assert chinese_name_key(key) == key


def test_no_value_is_also_a_key():
    """One lookup is enough -- the table is not chained.

    The same rule `SPECIES_SYNONYMS` and `JAPANESE_SPECIES` follow.  A key
    and a value cannot collide in practice here, since the keys are Chinese
    and the values are Latin, and that is exactly why it is worth asserting
    rather than assuming: a future key in pinyin would.
    """
    assert not set(CHINESE_SPECIES) & set(CHINESE_SPECIES.values())


def test_no_value_is_a_sentinel():
    """A sentinel is not a taxon and must not be reachable from this table.

    `species_from_chinese_name` returns a sentinel by asking
    `form_sentinel_for`, which is the one place that decision lives.  A
    sentinel hardcoded here would be a second, silent copy of it.
    """
    assert not set(CHINESE_SPECIES.values()) & SPECIES_SENTINELS


def test_unresolved_is_disjoint_from_the_table():
    """A name is resolved or it is not; listing it as both hides a decision."""
    assert not {chinese_name_key(v) for v in UNRESOLVED} & set(CHINESE_SPECIES)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The form almost every value takes passes through.
        ("榕樹", "榕樹"),
        # `台` folds to `臺`: one character, two ways of writing it, and both
        # are published.
        ("台灣梭羅木", "臺灣梭羅木"),
        ("臺灣梭羅木", "臺灣梭羅木"),
        # Every kind of space goes, including the ideographic one.
        ("小葉 欖仁", "小葉欖仁"),
        ("大王　椰子", "大王椰子"),
        # NFKC folds full-width ASCII, so a full-width question mark and a
        # half-width one are one key.
        ("烏？", "烏?"),
        (None, ""),
        ("   ", ""),
    ],
)
def test_chinese_name_key(raw, expected):
    assert chinese_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("榕樹", "Ficus microcarpa"),
        ("茄苳", "Bischofia javanica"),
        ("樟樹", "Cinnamomum camphora"),
        ("楓香", "Liquidambar formosana"),
        ("臺灣欒樹", "Koelreuteria elegans"),
        # A genus is an answer: the survey recorded "cherry", not a species.
        ("櫻花", "Prunus"),
        ("風鈴木", "Handroanthus"),
        # The everyday spelling reaches the formal key.
        ("台灣梭羅木", "Reevesia thyrsoidea"),
        # A standing dead tree keeps its condition rather than falling to
        # Unknown; the sentinel comes from form_sentinel_for, so this returns
        # the raw value for enforce_tree_schema to map.
        ("枯木", "枯木"),
        # Unresolved is None -- never the raw value.
        ("烏?", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_species_from_chinese_name(raw, expected):
    assert species_from_chinese_name(raw) == expected


def test_the_regional_readings_are_curated():
    """The reason this table is curated rather than translated.

    A Chinese tree name does not mean the same tree in Taiwan, on the
    mainland and in Hong Kong, and these are the ones a dictionary gets
    backwards.  Each is pinned because the fix is a single table row that a
    regeneration would silently undo.
    """
    # Taiwan's `洋紫荊` is the purple orchid tree and its `羊蹄甲` the
    # variegated one -- the reverse of the Hong Kong usage.  The hybrid on
    # Hong Kong's flag is `豔紫荊` here.
    assert species_from_chinese_name("洋紫荊") == "Bauhinia purpurea"
    assert species_from_chinese_name("羊蹄甲") == "Bauhinia variegata"
    assert species_from_chinese_name("豔紫荊") == "Bauhinia x blakeana"
    # `紅花鐵刀木` reads as a red-flowered *Senna siamea* and is the pink
    # shower, *Cassia grandis*.
    assert species_from_chinese_name("鐵刀木") == "Senna siamea"
    assert species_from_chinese_name("紅花鐵刀木") == "Cassia grandis"
    # `山桂花` reads as "mountain osmanthus" and is a *Maesa*.
    assert species_from_chinese_name("桂花") == "Osmanthus fragrans"
    assert species_from_chinese_name("山桂花") == "Maesa japonica"
    # Taipei's own plant page gives *S. macrophylla* for `桃花心木`, where
    # the national flora gives *S. mahagoni*; the publisher's usage wins.
    assert species_from_chinese_name("桃花心木") == "Swietenia macrophylla"


def test_an_unresolved_name_is_not_published_as_a_genus():
    """The failure this module exists to avoid, in its Chinese form.

    A name this table does not have must come back `None` rather than being
    guessed at from a prefix it shares with one that it does: `榕` is the
    fig character, and a fig this table has never seen is not `Ficus`.
    """
    assert species_from_chinese_name("榕樹") == "Ficus microcarpa"
    # Same leading character, not a name in the table.
    assert species_from_chinese_name("榕樹仔") is None


def test_the_committed_name_list_is_the_one_the_module_was_curated_against():
    assert len(TAIPEI_TREE_TYPES) == 471
    assert len(set(TAIPEI_TREE_TYPES)) == 471


@pytest.mark.parametrize("name", sorted(TAIPEI_TREE_TYPES))
def test_every_published_name_is_resolved_or_deliberately_not(name: str):
    """No published value may fall through by accident.

    Each of the 471 `TreeType` values is one of: a key in the table, a value
    the shared hygiene already understands (a growth-form or condition
    sentinel, or an empty site), blank, or listed in `UNRESOLVED` with a
    reason.  A value that is none of those publishes as `Unknown` without
    anyone having decided that it should, which is what this catches.
    """
    key = chinese_name_key(name)
    if key == "" or key in UNRESOLVED:
        assert species_from_chinese_name(name) is None
        return
    if key in CHINESE_SPECIES:
        assert species_from_chinese_name(name) == CHINESE_SPECIES[key]
        return
    assert is_not_a_tree(name) or form_sentinel_for(name) is not None, (
        f"{name!r} is not in CHINESE_SPECIES, not in UNRESOLVED, and not a "
        f"value the shared hygiene understands"
    )
    assert species_from_chinese_name(name) == name
