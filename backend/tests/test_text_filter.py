"""
TDD tests for text_filter.py.
Patterns derived from real books: Clean Code, Hard Thing About Hard Things, Logic text.
"""
import pytest
from services.text_filter import TextFilter, FilterReason


@pytest.fixture
def f():
    return TextFilter()


# ── Prose that must PASS (not filtered) ────────────────────────────────────────

class TestProsePassThrough:
    def test_normal_prose_sentence(self, f):
        assert not f.should_filter("Writing clean code is what you must do in order to call yourself a professional.")

    def test_narrative_prose(self, f):
        assert not f.should_filter("Until now, I've never told that story to anyone, but it shaped my life.")

    def test_academic_prose(self, f):
        assert not f.should_filter("The best way to identify whether an argument is present is to ask whether there is a statement that someone is trying to establish as true.")

    def test_short_but_valid_dialogue(self, f):
        assert not f.should_filter('"Flowers are really cheap. But do you know what\'s expensive?" he asked.')

    def test_sentence_with_numbers_inline(self, f):
        assert not f.should_filter("The company was founded in 1986 and grew to over 500 employees by 1995.")


# ── Copyright / Legal (must be filtered) ───────────────────────────────────────

class TestCopyrightFilter:
    def test_copyright_symbol(self, f):
        assert f.should_filter("Copyright © 2009 Pearson Education, Inc.")

    def test_all_rights_reserved(self, f):
        assert f.should_filter("All rights reserved. Printed in the United States of America.")

    def test_isbn_line(self, f):
        assert f.should_filter("ISBN-13: 978-0-13-235088-4 ISBN-10: 0-13-235088-2")

    def test_isbn_short(self, f):
        assert f.should_filter("ISBN 0-13-235088-2 (pbk. : alk. paper)")

    def test_creative_commons(self, f):
        assert f.should_filter("This work is licensed under a Creative Commons Attribution 4.0 International License.")

    def test_publisher_rights(self, f):
        assert f.should_filter("Pearson Education, Inc Rights and Contracts Department 501 Boylston Street, Suite 900")

    def test_printed_in(self, f):
        assert f.should_filter("Text printed in the United States on recycled paper at Courier Westford.")


# ── TOC entries (must be filtered) ─────────────────────────────────────────────

class TestTOCFilter:
    def test_dotted_toc_entry(self, f):
        assert f.should_filter("Chapter 5: Formatting ......................................................................75")

    def test_dotted_toc_entry_with_number(self, f):
        assert f.should_filter("Commented-Out Code......................................................................68")

    def test_toc_header(self, f):
        assert f.should_filter("x Contents")

    def test_toc_header_roman(self, f):
        assert f.should_filter("xi Contents")

    def test_toc_header_bare(self, f):
        assert f.should_filter("CONTENTS")

    def test_table_of_contents(self, f):
        assert f.should_filter("Table of contents")


# ── Chapter / Section headers (must be filtered) ───────────────────────────────

class TestHeadingFilter:
    def test_chapter_heading(self, f):
        assert f.should_filter("Chapter 1: Reconstructing and analyzing arguments")

    def test_chapter_heading_numeric(self, f):
        assert f.should_filter("Chapter 5: Formatting")

    def test_section_heading_numbered(self, f):
        assert f.should_filter("1.1  What is an argument?")

    def test_section_heading_numbered_2(self, f):
        assert f.should_filter("1.2  Identifying arguments")

    def test_all_caps_section_marker(self, f):
        assert f.should_filter("TURN YOUR SHIT IN")

    def test_all_caps_section_marker_2(self, f):
        assert f.should_filter("BLIND DATE")

    def test_all_caps_single_word(self, f):
        assert f.should_filter("DEDICATION")

    def test_preface_single_word(self, f):
        assert f.should_filter("Preface")

    def test_appendix_heading(self, f):
        assert f.should_filter("Appendix A: Reference Tables")


# ── Bare page numbers (must be filtered) ───────────────────────────────────────

class TestPageNumberFilter:
    def test_bare_integer(self, f):
        assert f.should_filter("4")

    def test_bare_roman_numeral(self, f):
        assert f.should_filter("i")

    def test_bare_roman_numeral_upper(self, f):
        assert f.should_filter("XIV")

    def test_page_number_with_label(self, f):
        assert f.should_filter("Page 42")


# ── Publisher / contact info (must be filtered) ────────────────────────────────

class TestPublisherInfoFilter:
    def test_publisher_location_bullets(self, f):
        assert f.should_filter("Upper Saddle River, NJ • Boston • Indianapolis • San Francisco")

    def test_email_address_line(self, f):
        assert f.should_filter("International Sales international@pearsoned.com")

    def test_url_only_line(self, f):
        assert f.should_filter("http://creativecommons.org/licenses/by/4.0/")

    def test_phone_number_line(self, f):
        assert f.should_filter("U.S. Corporate and Government Sales (800) 382-3419 corpsales@pearsontechgroup.com")

    def test_for_more_information(self, f):
        assert f.should_filter("For more information, visit informit.com/martinseries")


# ── Exercise / numbered list markers (must be filtered) ────────────────────────

class TestExerciseFilter:
    def test_exercise_marker(self, f):
        assert f.should_filter("Exercise 2: Which of the following are arguments?")

    def test_numbered_list_item(self, f):
        assert f.should_filter("1. The woman in the hat is not a witch since witches have long noses")

    def test_numbered_list_short(self, f):
        assert f.should_filter("2. I have been wrangling cattle since before you were old enough")


# ── FilterReason reporting ──────────────────────────────────────────────────────

class TestFilterReason:
    def test_returns_reason_for_filtered(self, f):
        reason = f.filter_reason("Copyright © 2009 Pearson Education, Inc.")
        assert reason is not None
        assert isinstance(reason, FilterReason)

    def test_returns_none_for_prose(self, f):
        reason = f.filter_reason("Writing clean code is what you must do.")
        assert reason is None

    def test_reason_has_name(self, f):
        reason = f.filter_reason("ISBN-13: 978-0-13-235088-4")
        assert reason.name  # non-empty string describing why
