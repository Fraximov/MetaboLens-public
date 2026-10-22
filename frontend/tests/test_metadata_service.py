import unittest

from services.metadata_service import build_metadata_payload
from ui.metadata import metadata_defaults_for_profile


class MetadataServiceTests(unittest.TestCase):
    def test_build_metadata_payload_includes_gcms_and_nmr_sections(self) -> None:
        payload = build_metadata_payload(
            "qexactive_trace1310_gcms",
            {
                "platform_name": "GC platform",
                "ms_vendor": "Thermo",
                "column_type": "5% phenyl",
                "derivatization": "MSTFA",
                "nmr_tube_type": "5 mm",
                "pulse_sequence_name": "noesypr1d",
            },
        )

        self.assertEqual(payload["instrument"]["platform_name"], "GC platform")
        self.assertEqual(payload["chromatography"]["column_type"], "5% phenyl")
        self.assertEqual(payload["chromatography"]["derivatization"], "MSTFA")
        self.assertEqual(payload["nmr"]["nmr_tube_type"], "5 mm")
        self.assertEqual(payload["nmr"]["pulse_sequence_name"], "noesypr1d")

    def test_metadata_defaults_for_profile_reads_extended_defaults(self) -> None:
        values = metadata_defaults_for_profile(
            {
                "default_payload_json": {
                    "instrument": {"platform_name": "Bruker"},
                    "chromatography": {"column_type": "Amide", "derivatization": "MSTFA"},
                    "acquisition": {},
                    "run_context": {},
                    "nmr": {"nmr_tube_type": "5 mm", "magnetic_field_strength": "600 MHz"},
                }
            }
        )

        self.assertIn("Bruker", values)
        self.assertIn("Amide", values)
        self.assertIn("MSTFA", values)
        self.assertIn("5 mm", values)
        self.assertIn("600 MHz", values)


if __name__ == "__main__":
    unittest.main()
