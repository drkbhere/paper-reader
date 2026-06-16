from backend.textclean import simplify_citations


def test_condenses_multi_author_group():
    src = "Brand trust mediates loyalty (Smith, Jones & Lee, 2020; Brown et al., 2019)."
    assert simplify_citations(src) == (
        "Brand trust mediates loyalty (Smith and colleagues; Brown and colleagues)."
    )


def test_single_author_keeps_surname_only():
    assert simplify_citations("Loyalty rose (Kumar, 2021).") == "Loyalty rose (Kumar)."


def test_two_authors_with_and_is_multi():
    assert simplify_citations("(Smith and Jones, 2018)") == "(Smith and colleagues)"


def test_drops_leadin_words():
    assert simplify_citations("as shown (see Smith, 2020)") == "as shown (Smith)"
    assert simplify_citations("e.g. (e.g., Kumar, 2021)") == "e.g. (Kumar)"


def test_removes_bare_year_parenthetical():
    assert simplify_citations("Smith (2020) found that trust matters.") == (
        "Smith found that trust matters."
    )


def test_removes_bare_year_with_page_and_year_list():
    assert simplify_citations("As argued (2020, p. 14), trust matters.") == (
        "As argued, trust matters."
    )
    assert simplify_citations("Earlier work (2019, 2021) agrees.") == "Earlier work agrees."


def test_strips_parenthetical_figure_table_asides():
    assert simplify_citations("Loyalty increased (see Table 2).") == "Loyalty increased."
    assert simplify_citations("The effect held (Figure 3).") == "The effect held."
    assert simplify_citations("Means differ (Fig. 3a).") == "Means differ."


def test_keeps_grammatical_figure_mention():
    assert simplify_citations("Figure 3 shows the interaction.") == (
        "Figure 3 shows the interaction."
    )


def test_leaves_statistics_untouched():
    for stat in ("(p < .05)", "(M = 3.42, SD = 0.81)", "(N = 200)", "(95% CI [.12, .34])"):
        assert simplify_citations(f"The result was significant {stat}.") == (
            f"The result was significant {stat}."
        )


def test_leaves_plain_parenthetical_untouched():
    assert simplify_citations("This was true (in most cases).") == (
        "This was true (in most cases)."
    )


def test_mixed_content_group_left_untouched():
    # one part is not a citation -> be conservative, change nothing
    src = "Trust matters (Smith, 2020; in this specific case)."
    assert simplify_citations(src) == src


def test_cleans_double_spaces_and_space_before_punctuation():
    assert simplify_citations("Trust mattered (see Table 2) ; loyalty rose.") == (
        "Trust mattered; loyalty rose."
    )


def test_cleanup_does_not_weld_decimals():
    # the (?!\d) guard in _cleanup must leave a spaced decimal alone
    assert simplify_citations("The value was 3 .5 (Kumar, 2021).") == (
        "The value was 3 .5 (Kumar)."
    )
