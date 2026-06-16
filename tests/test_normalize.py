from src.normalize import build_job_key, country_from_location, infer_remote, slug
from src.models import RemoteType


class TestSlug:
    def test_lowercases(self):
        assert slug("Stripe") == "stripe"

    def test_spaces_to_dashes(self):
        assert slug("Grafana Labs") == "grafana-labs"

    def test_special_chars_stripped(self):
        assert slug("Node.js") == "node-js"

    def test_consecutive_separators_collapsed(self):
        assert slug("Full--Stack  Engineer") == "full-stack-engineer"

    def test_leading_trailing_dashes_stripped(self):
        assert slug("  hello  ") == "hello"


class TestBuildJobKey:
    def test_format(self):
        key = build_job_key("Stripe", "Software Engineer", "IE")
        assert key == "stripe__software-engineer__ie"

    def test_deterministic(self):
        assert build_job_key("X", "Y", "Z") == build_job_key("X", "Y", "Z")

    def test_slugs_components(self):
        key = build_job_key("Grafana Labs", "Backend Developer", "DE")
        assert key == "grafana-labs__backend-developer__de"


class TestCountryFromLocation:
    def test_city_hint(self):
        assert country_from_location("Berlin, Germany") == "DE"
        assert country_from_location("Amsterdam, Netherlands") == "NL"
        assert country_from_location("London, UK") == "GB"
        assert country_from_location("Dublin, Ireland") == "IE"
        assert country_from_location("Istanbul, Turkey") == "TR"
        assert country_from_location("San Francisco, CA") == "US"

    def test_case_insensitive(self):
        assert country_from_location("BERLIN") == "DE"
        assert country_from_location("london") == "GB"

    def test_unknown_location_returns_remote(self):
        assert country_from_location("Somewhere Unknown") == "REMOTE"

    def test_empty_returns_remote(self):
        assert country_from_location("") == "REMOTE"


class TestInferRemote:
    def test_remote_signal(self):
        assert infer_remote("Remote") == RemoteType.remote
        assert infer_remote("Work from home") == RemoteType.remote
        assert infer_remote("WFH") == RemoteType.remote
        assert infer_remote("work from anywhere") == RemoteType.remote

    def test_hybrid_signal(self):
        assert infer_remote("Hybrid") == RemoteType.hybrid
        assert infer_remote("Berlin – hybrid role") == RemoteType.hybrid

    def test_remote_beats_hybrid(self):
        assert infer_remote("Remote or hybrid") == RemoteType.remote

    def test_no_signal(self):
        assert infer_remote("Berlin, Germany") == RemoteType.unknown

    def test_multiple_text_args(self):
        assert infer_remote("Berlin", "fully remote team") == RemoteType.remote
