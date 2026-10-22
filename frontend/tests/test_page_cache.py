import unittest

from services.page_cache import build_page_cache, page_cache_data, page_cache_matches, should_reuse_page_cache


class PageCacheTests(unittest.TestCase):
    def test_cache_matches_exact_auth_study_and_assay_context(self):
        cache = build_page_cache({"rows": [1]}, "token-a", study_id=9, assay_id=18, loaded_at=123)
        self.assertTrue(page_cache_matches(cache, "token-a", study_id=9, assay_id=18))
        self.assertFalse(page_cache_matches(cache, "token-b", study_id=9, assay_id=18))
        self.assertFalse(page_cache_matches(cache, "token-a", study_id=10, assay_id=18))
        self.assertFalse(page_cache_matches(cache, "token-a", study_id=9, assay_id=19))
        self.assertEqual(page_cache_data(cache), {"rows": [1]})

    def test_empty_or_malformed_cache_never_matches(self):
        self.assertFalse(page_cache_matches({}, "token-a", study_id=9))
        self.assertFalse(page_cache_matches({"context": {}, "data": []}, "token-a", study_id=9))
        self.assertEqual(page_cache_data(None, default=[]), [])

    def test_cache_is_reused_only_for_passive_triggers(self):
        cache = build_page_cache([{"id": 1}], "token-a", study_id=9, assay_id=18)
        self.assertTrue(
            should_reuse_page_cache(
                cache,
                "token-a",
                triggered_id="url",
                passive_trigger_ids={"url"},
                study_id=9,
                assay_id=18,
            )
        )
        self.assertFalse(
            should_reuse_page_cache(
                cache,
                "token-a",
                triggered_id="refresh-btn",
                passive_trigger_ids={"url"},
                study_id=9,
                assay_id=18,
            )
        )
