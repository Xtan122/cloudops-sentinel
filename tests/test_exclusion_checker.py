import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/lambda")))

from shared.exclusion_checker import has_exclusion_tag


def test_has_exclusion_tag_lowercase_true():
    tags = [{"Key": "skip-enforcement", "Value": "true"}]
    assert has_exclusion_tag(tags) is True


def test_has_exclusion_tag_mixed_case_true():
    tags = [{"Key": "Skip-Enforcement", "Value": "True"}]
    assert has_exclusion_tag(tags) is True


def test_has_exclusion_tag_uppercase_true():
    tags = [{"Key": "SKIP-ENFORCEMENT", "Value": "TRUE"}]
    assert has_exclusion_tag(tags) is True


def test_has_exclusion_tag_false_value():
    tags = [{"Key": "skip-enforcement", "Value": "false"}]
    assert has_exclusion_tag(tags) is False


def test_has_exclusion_tag_empty_tags():
    assert has_exclusion_tag([]) is False


def test_has_exclusion_tag_whitespace_true():
    tags = [{"Key": " SKIP-ENFORCEMENT ", "Value": " TRUE "}]
    assert has_exclusion_tag(tags) is True


def test_has_exclusion_tag_middle_of_list():
    tags = [
        {"Key": "Owner", "Value": "Admin"},
        {"Key": "skip-enforcement", "Value": "true"},
        {"Key": "Project", "Value": "Alpha"}
    ]
    assert has_exclusion_tag(tags) is True


def test_has_exclusion_tag_missing_key_or_value():
    tags = [
        {"Key": "Owner"},  # Missing Value
        {"Value": "true"}, # Missing Key
        {},                # Missing both
        {"Key": "skip-enforcement", "Value": "false"}
    ]
    assert has_exclusion_tag(tags) is False

def test_has_exclusion_tag_any_valid_tag_wins():
    tags = [
        {"Key": "Skip-Enforcement", "Value": "false"},
        {"Key": "SKIP-ENFORCEMENT", "Value": "TRUE"},
    ]

    assert has_exclusion_tag(tags) is True