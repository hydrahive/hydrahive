from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.llm_config_validation import (
    LlmConfigValueError,
    clean_provider_base_url,
    clean_provider_secret,
)


class TestCleanProviderSecret:
    def test_strips_ascii_secret(self):
        assert clean_provider_secret("  nvapi-secret  ", label="NVIDIA API-Key") == "nvapi-secret"

    def test_empty_secret_allowed(self):
        assert clean_provider_secret("   ", label="NVIDIA API-Key") == ""

    def test_rejects_non_ascii_secret(self):
        with pytest.raises(LlmConfigValueError):
            clean_provider_secret("nvapi—bad", label="NVIDIA API-Key")

    def test_rejects_multiline_secret(self):
        with pytest.raises(LlmConfigValueError):
            clean_provider_secret("nvapi-secret\nextra", label="NVIDIA API-Key")


class TestCleanProviderBaseUrl:
    def test_strips_ascii_url(self):
        assert clean_provider_base_url(" https://integrate.api.nvidia.com/v1 ") == (
            "https://integrate.api.nvidia.com/v1"
        )

    def test_rejects_url_with_whitespace(self):
        with pytest.raises(LlmConfigValueError):
            clean_provider_base_url("https://example.test/v1 bad")

    def test_rejects_non_ascii_url(self):
        with pytest.raises(LlmConfigValueError):
            clean_provider_base_url("https://example.test/ä")
