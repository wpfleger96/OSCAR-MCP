"""Unit tests for MCP clinical profiles."""

from __future__ import annotations

import pytest

from snore.mcp.profiles import (
    VALID_PROFILES,
    ClinicalProfile,
    get_profile,
    list_profiles,
)


class TestGetProfile:
    def test_neutral_profile_returned_by_name(self) -> None:
        p = get_profile("neutral")
        assert isinstance(p, ClinicalProfile)
        assert p.name == "neutral"

    def test_uars_profile_returned_by_name(self) -> None:
        p = get_profile("uars")
        assert p.name == "uars"

    def test_osa_profile_returned_by_name(self) -> None:
        p = get_profile("osa")
        assert p.name == "osa"

    def test_csa_profile_returned_by_name(self) -> None:
        p = get_profile("csa")
        assert p.name == "csa"

    def test_unknown_profile_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unknown_profile"):
            get_profile("unknown_profile")

    def test_all_valid_profiles_are_retrievable(self) -> None:
        for name in VALID_PROFILES:
            p = get_profile(name)
            assert p.name == name

    def test_profile_has_non_empty_priority_hint(self) -> None:
        for name in VALID_PROFILES:
            p = get_profile(name)
            assert p.priority_hint.strip()

    def test_profile_has_non_empty_clinical_context(self) -> None:
        for name in VALID_PROFILES:
            p = get_profile(name)
            assert p.clinical_context.strip()


class TestListProfiles:
    def test_list_profiles_returns_all_four(self) -> None:
        profiles = list_profiles()
        assert len(profiles) == 4

    def test_list_profiles_starts_with_neutral(self) -> None:
        profiles = list_profiles()
        assert profiles[0].name == "neutral"

    def test_list_profiles_names_match_valid_profiles(self) -> None:
        profiles = list_profiles()
        names = {p.name for p in profiles}
        assert names == VALID_PROFILES
