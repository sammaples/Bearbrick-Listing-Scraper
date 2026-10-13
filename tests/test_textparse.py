import pytest

from bearbrick_scout.textparse import analyze, box_status, is_relevant

EXCLUDE = ["junk", "ジャンク", "empty box", "空箱", "custom"]


def a(title, desc=None):
    return analyze(title, desc, EXCLUDE)


@pytest.mark.parametrize(
    "title,sets,loose,k1000,confident",
    [
        ("BE@RBRICK KAWS Companion 400% with box", 1, 0, 0, True),
        ("BE@RBRICK RAT FINK 100％ & 400％", 1, 0, 0, True),
        ("Bearbrick 100% + 400% set Basquiat", 1, 0, 0, True),
        ("ベアブリック 400% 2体セット", 2, 0, 0, True),
        ("2x Bearbrick 400% Sorayama", 2, 0, 0, True),
        ("Bearbrick Series 45 blind box", 0, 1, 0, True),
        ("BE@RBRICK 100% 12pcs lot", 0, 12, 0, True),
        ("Bearbrick 100% lot", 0, 1, 0, False),
        ("ベアブリック 100% まとめ売り", 0, 1, 0, False),
        ("Medicom Bearbrick 1000% Andy Warhol", 0, 0, 1, True),
        ("BE@RBRICK HAJIME SORAYAMA 30th 100％ 3PCS", 0, 3, 0, True),
    ],
)
def test_counts(title, sets, loose, k1000, confident):
    c = a(title)
    assert (c.sets_400, c.loose_100, c.figures_1000, c.confident) == (sets, loose, k1000, confident)


def test_set_plus_extra_loose_100s():
    # Owner's example: a 100%+400% set plus three extra 100% figures.
    c = a("Bearbrick 100% & 400% set + 100% x4")
    assert c.sets_400 == 1 and c.loose_100 == 3 and not c.confident


def test_mixed_without_set_phrase_treats_100_as_companion():
    c = a("Bearbrick 400% Daft Punk, 100% included")
    assert c.sets_400 == 1 and c.loose_100 == 0 and not c.confident


def test_2000_percent_is_not_a_tracked_size():
    assert a("Bearbrick 2000% statue").is_empty


@pytest.mark.parametrize(
    "text,expected",
    [
        ("400% with original box", "yes"),
        ("箱あり 美品", "yes"),
        ("新品未開封", "yes"),
        ("外箱付き", "yes"),
        ("400% no box", "no"),
        ("箱なし 本体のみ", "no"),
        ("外箱なし", "no"),  # contains 外箱 but says "none": no wins
        ("figure only, great shape", "no"),
        ("great condition", "unknown"),
    ],
)
def test_box(text, expected):
    assert box_status(text)[0] == expected


def test_box_found_in_description():
    assert a("Bearbrick 400% Snoopy", "Comes with the original box.").box == "yes"


def test_excluded_keywords():
    assert a("ベアブリック 400% ジャンク").excluded_reason
    assert a("Bearbrick 400% empty box only").excluded_reason


def test_relevance():
    assert is_relevant("BE＠RBRICK 400％")
    assert is_relevant("ベアブリック")
    assert not is_relevant("KAWS companion 20cm")
