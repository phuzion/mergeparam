import pytest

from mergeparam import infobox as ib

PERSON = """{{Short description|American physicist}}
{{Infobox person
| name         = Jane Doe
| birth_date   = {{birth date|1950|1|1}}
| alma_mater   = [[Harvard University]]
| occupation   = Physicist
}}
'''Jane Doe''' is a physicist.
"""

BOTH = """{{Infobox scientist
| name       = John Roe
| education  = [[Stanford University]] ([[Bachelor of Science|BS]])
| alma_mater = [[Massachusetts Institute of Technology|MIT]]
| fields     = Chemistry
}}
"""


# ------------------------------------------------------------- find_target

def test_find_target_simple():
    t = ib.find_target(PERSON)
    assert t.template == "Infobox person"
    assert t.alma == "[[Harvard University]]"
    assert t.education is None
    assert t.others == 0


def test_find_target_ignores_empty_and_comment_only():
    text = "{{Infobox person\n| alma_mater = <!-- only if known -->\n| education =\n}}"
    assert ib.find_target(text) is None
    assert ib.find_target("{{Infobox person\n| alma_mater =\n}}") is None


def test_find_target_ignores_non_infobox_templates():
    assert ib.find_target("{{Persondata|alma_mater=[[X]]}}") is None


def test_find_target_nested_module_and_template_prefix():
    text = ("{{Infobox officeholder\n| name = A\n| module = {{Template:infobox_person|embed=yes"
            "\n | alma_mater = [[Yale University]]\n }}\n}}")
    t = ib.find_target(text)
    assert t.template == "Infobox person"
    assert t.alma == "[[Yale University]]"


def test_find_target_counts_other_infoboxes_and_repeats():
    text = ("{{Infobox person|alma_mater=[[A]]|alma_mater=[[B]]}}\n"
            "{{Infobox academic|alma_mater=[[C]]}}")
    t = ib.find_target(text)
    assert t.others == 1
    assert t.repeated_alma
    assert t.alma == "[[B]]"  # MediaWiki uses the last one


def test_in_use_tagged():
    assert ib.in_use_tagged("{{in use}}\n" + PERSON)
    assert not ib.in_use_tagged(PERSON)


# --------------------------------------------------------------- parse_list

@pytest.mark.parametrize("value,kind,items", [
    ("", "empty", []),
    ("<!-- x -->", "empty", []),
    ("[[A]]", "single", ["[[A]]"]),
    ("{{plainlist|\n* [[A]] (BA)\n* [[B]]\n}}", "plainlist", ["[[A]] (BA)", "[[B]]"]),
    ("{{Plainlist|\n*[[A]]\n** [[A2]]\n}}", "plainlist", ["[[A]]", "[[A2]]"]),
    ("{{ubl|[[A]]|[[B]] (PhD)}}", "positional", ["[[A]]", "[[B]] (PhD)"]),
    ("{{unbulleted list|class=x|[[A]]|[[B]]}}", "positional", ["[[A]]", "[[B]]"]),
    ("[[A]]<br />[[B]]<br>[[C]]", "br", ["[[A]]", "[[B]]", "[[C]]"]),
    ("\n* [[A]]\n* [[B]]", "bullets", ["[[A]]", "[[B]]"]),
    ("[[A]] ([[Bachelor of Arts|BA]], 1990)", "single", ["[[A]] ([[Bachelor of Arts|BA]], 1990)"]),
])
def test_parse_list(value, kind, items):
    parsed = ib.parse_list(value)
    assert parsed.kind == kind
    assert parsed.items == items


def test_br_inside_other_markup_is_not_a_separator():
    parsed = ib.parse_list("<small>[[A]]<br />[[B]]</small>")
    assert parsed.kind == "single"


# --------------------------------------------------------------- duplicates

@pytest.mark.parametrize("item,existing,dup", [
    ("[[Harvard University]]", ["[[Harvard University]] (AB)"], True),
    ("[[Harvard University|Harvard]]", ["[[harvard_University]] (AB)"], True),
    ("[[Harvard]]", ["[[Harvard University]] (AB)"], True),  # via redirect map
    ("[[Yale University]]", ["[[Harvard University]] (AB)"], False),
    ("Harvard University", ["Harvard University (BA)"], True),
    ("[[Harvard University]]", ["Harvard University (BA)"], True),
    # Containment without a parenthetical is not enough: different institutions.
    ("University of California", ["University of California, Berkeley"], False),
    ("UCL", ["UCL"], False),  # too short to compare as plain text
])
def test_is_duplicate(item, existing, dup):
    redirects = {"Harvard": "Harvard University"}
    assert ib.is_duplicate(item, existing, redirects) is dup


# ----------------------------------------------------------------- suggest

def test_suggest_rename_when_no_education():
    s = ib.suggest("[[Harvard University]]", None)
    assert s.action == "rename"
    assert s.value == "[[Harvard University]]"


def test_suggest_rename_when_education_comment_only():
    s = ib.suggest("[[A]]", "<!-- degrees go here -->")
    assert s.action == "rename"


def test_suggest_drop_when_all_duplicates():
    s = ib.suggest("[[Harvard University]]", "[[Harvard University]] ([[Bachelor of Arts|AB]])")
    assert s.action == "drop"
    assert s.value == "[[Harvard University]] ([[Bachelor of Arts|AB]])"
    assert s.duplicates == ["[[Harvard University]]"]


def test_suggest_single_plus_single_uses_default_plainlist():
    s = ib.suggest("[[MIT]]", "[[Stanford University]] (BS)")
    assert s.action == "merge"
    assert s.value == "{{Plainlist|\n* [[Stanford University]] (BS)\n* [[MIT]]\n}}"
    assert s.added == ["[[MIT]]"]


def test_suggest_appends_to_plainlist_preserving_format():
    edu = "{{plainlist|\n* [[A]] (BA)\n* [[B]] (MA)\n}}"
    s = ib.suggest("{{ubl|[[B]]|[[C]]}}", edu)
    assert s.value == "{{plainlist|\n* [[A]] (BA)\n* [[B]] (MA)\n* [[C]]\n}}"
    assert s.duplicates == ["[[B]]"]


def test_suggest_appends_to_ubl():
    s = ib.suggest("[[C]]", "{{ubl|[[A]]|[[B]]}}")
    assert s.value == "{{ubl|[[A]]|[[B]]|[[C]]}}"


def test_suggest_appends_to_multiline_ubl():
    s = ib.suggest("[[C]]", "{{Unbulleted list\n|[[A]]\n|[[B]]\n}}")
    assert s.value == "{{Unbulleted list\n|[[A]]\n|[[B]]\n|[[C]]\n}}"


def test_suggest_appends_with_same_br():
    s = ib.suggest("[[C]]", "[[A]]<br>[[B]]")
    assert s.value == "[[A]]<br>[[B]]<br>[[C]]"


def test_suggest_appends_to_bullets():
    s = ib.suggest("[[C]]", "\n* [[A]]\n* [[B]]")
    assert s.value == "* [[A]]\n* [[B]]\n* [[C]]"


def test_suggest_single_education_takes_alma_list_style():
    s = ib.suggest("[[B]]<br />[[C]]", "[[A]] (BA)")
    assert s.value == "[[A]] (BA)<br />[[B]]<br />[[C]]"


def test_suggest_keeps_comment_in_single_education():
    s = ib.suggest("[[B]]", "[[A]] <!-- verified -->")
    assert "* [[A]] <!-- verified -->" in s.value


# ------------------------------------------------------------- check_value

def test_check_value_normalizes_newlines():
    assert ib.check_value("{{Plainlist|\r\n* [[A]]\r\n}}\r\n") == "{{Plainlist|\n* [[A]]\n}}"


@pytest.mark.parametrize("bad", ["", "  ", "[[A]] | foo = bar", "[[A]]}}", "{{ubl|[[A]]", "[[A]]}} {{X"])
def test_check_value_rejects_breaking_input(bad):
    with pytest.raises(ib.InvalidValue):
        ib.check_value(bad)


@pytest.mark.parametrize("good", ["{{ubl|a|b}}", "[[A|B]]", "a=b", "{{Plainlist|\n* [[A]]\n}}",
                                  "[[A]]<ref>{{cite web|url=http://x|title=Y}}</ref>"])
def test_check_value_accepts_valid_input(good):
    assert ib.check_value(good) == good


# ------------------------------------------------------------------- apply

def test_apply_rename_keeps_alignment():
    out = ib.apply(PERSON, 0, "[[Harvard University]]")
    assert "| education    = [[Harvard University]]\n" in out
    assert "alma_mater" not in out
    # nothing else on the page changed
    assert out == PERSON.replace("| alma_mater   = ", "| education    = ")


def test_apply_merge_removes_alma_line_cleanly():
    out = ib.apply(BOTH, 0, "{{Plainlist|\n* A\n* B\n}}")
    assert out == """{{Infobox scientist
| name       = John Roe
| education  = {{Plainlist|\n* A\n* B\n}}
| fields     = Chemistry
}}
"""


def test_apply_into_empty_education_uses_alma_spacing():
    text = "{{Infobox person\n| education =\n| alma_mater = [[A]]\n| x = y\n}}"
    out = ib.apply(text, 0, "[[A]]")
    assert out == "{{Infobox person\n| education = [[A]]\n| x = y\n}}"


def test_apply_keeps_comment_in_empty_education():
    text = "{{Infobox person\n| education = <!-- degrees -->\n| alma_mater = [[A]]\n}}"
    out = ib.apply(text, 0, "[[A]]")
    assert out == "{{Infobox person\n| education = [[A]] <!-- degrees -->\n}}"


def test_apply_removes_repeated_alma_params():
    text = "{{Infobox person\n| alma_mater = [[A]]\n| name = N\n| alma_mater = [[B]]\n}}"
    out = ib.apply(text, 0, "[[B]]")
    assert out == "{{Infobox person\n| name = N\n| education = [[B]]\n}}"


def test_apply_single_line_infobox():
    out = ib.apply("{{Infobox person|name=N|alma_mater=[[A]]}}", 0, "[[A]]")
    assert out == "{{Infobox person|name=N|education=[[A]]}}"


def test_apply_nested_module():
    text = "{{Infobox officeholder\n| module = {{Infobox person|embed=yes\n | alma_mater = [[Y]]\n }}\n}}"
    out = ib.apply(text, 0, "[[Y]]")
    assert out == "{{Infobox officeholder\n| module = {{Infobox person|embed=yes\n | education = [[Y]]\n }}\n}}"


def test_apply_rejects_bad_value_without_touching_text():
    with pytest.raises(ib.InvalidValue):
        ib.apply(PERSON, 0, "[[A]] | birth_date = 2000")


def test_round_trip_suggest_then_apply():
    t = ib.find_target(BOTH)
    s = ib.suggest(t.alma, t.education)
    out = ib.apply(BOTH, t.index, s.value)
    assert ib.find_target(out) is None
    assert "{{Plainlist|\n* [[Stanford University]] ([[Bachelor of Science|BS]])\n" \
           "* [[Massachusetts Institute of Technology|MIT]]\n}}" in out
