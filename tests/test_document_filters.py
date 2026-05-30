import unittest

from document_filters import (
    matches_blacklist,
    should_consider_scrape_input,
    should_keep_output_file,
)


class TestDocumentFilters(unittest.TestCase):
    def test_blacklisted_keywords_are_case_and_accent_insensitive(self):
        self.assertTrue(matches_blacklist("2026_Ordre du jour.pdf"))
        self.assertTrue(matches_blacklist("2026_RAPPORT.pdf"))
        self.assertTrue(matches_blacklist("2026_beheersovereenkomst.pdf"))

    def test_abbreviations_match_as_uppercase_tokens(self):
        self.assertTrue(matches_blacklist("2026_CBS_verslag.pdf"))
        self.assertTrue(matches_blacklist("MJP 2026.pdf"))
        self.assertFalse(matches_blacklist("abcbs-notulen.pdf"))

    def test_prefixes_and_substrings_match(self):
        self.assertTrue(matches_blacklist("SP_2026_document.pdf"))
        self.assertTrue(matches_blacklist("WW document.pdf"))
        self.assertTrue(matches_blacklist("GRC2026.pdf"))
        self.assertTrue(matches_blacklist("2026_AR_document.pdf"))

    def test_bekendmaking_without_notulen_or_zittingsverslag_is_blacklisted(self):
        self.assertTrue(matches_blacklist("bekendmaking-gemeenteraad.pdf"))
        self.assertFalse(matches_blacklist("bekendmaking-notulen-gemeenteraad.pdf"))
        self.assertFalse(matches_blacklist("bekendmaking-zittingsverslag.pdf"))

    def test_extension_filter_supports_cleanup_and_pdf_only_input(self):
        self.assertTrue(should_keep_output_file("metadata.json"))
        self.assertTrue(should_keep_output_file("notulen.html"))
        self.assertFalse(should_keep_output_file("thumbnail.png"))

        self.assertTrue(should_consider_scrape_input("notulen.pdf"))
        self.assertFalse(should_consider_scrape_input("notulen.docx"))
        self.assertFalse(should_consider_scrape_input("agenda.pdf"))


if __name__ == "__main__":
    unittest.main()
