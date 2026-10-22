import json
from pathlib import Path
import unittest

from app.services.masscube_config import parse_masscube_template, validate_masscube_parameters


# Walk up like SeedService: the repository root on the host, /app in the backend container.
ROOT = next(
    parent / 'workflows/nextflow4ms-dial-msdial5-clean'
    for parent in Path(__file__).resolve().parents
    if (parent / 'workflows/nextflow4ms-dial-msdial5-clean/engines/masscube/parameters.json').is_file()
)
SCHEMA = json.loads((ROOT / 'engines/masscube/parameters.json').read_text())


class MassCubeConfigTests(unittest.TestCase):
    def test_comparison_preset_is_explicit_and_editable(self):
        text = (ROOT / 'templates/masscube/orbitrap-msdial-comparison-v1.csv').read_text()
        values = parse_masscube_template(text, SCHEMA)
        self.assertEqual((values['rt_lower_limit'], values['rt_upper_limit']), (0.5, 18))
        self.assertEqual((values['mz_lower_limit'], values['mz_upper_limit']), (60, 900))
        self.assertEqual((values['mz_tol_alignment'], values['rt_tol_alignment']), (0.005, 0.15))
        self.assertEqual(values['quant_method'], 'peak_height')
        self.assertIs(values['correct_rt'], False)
        edited = text.replace('ms1_abs_int_tol,30000,', 'ms1_abs_int_tol,42000,')
        self.assertEqual(parse_masscube_template(edited, SCHEMA)['ms1_abs_int_tol'], 42000)

    def test_invalid_csv_and_science_are_rejected_instead_of_using_defaults(self):
        for text in ('', 'value,parameter\n1,mz_tol_ms1\n',
                     'parameter,value\nmz_tol_ms1,0.01\nmz_tol_ms1,0.02\n',
                     'parameter,value\nunknown,2\n', 'parameter,value\nms2_library_path,secret\n',
                     'parameter,value\nms1_abs_int_tol,\n', 'parameter,value\nfuzzy_search,maybe\n',
                     'parameter,value\nrt_lower_limit,18\nrt_upper_limit,0.5\n',
                     'parameter,value\ncorrect_rt,true\n', 'parameter,value\nscan_number_cutoff,1.5\n',
                     'parameter,value\nmz_tol_ms1,NaN\n', 'parameter,value\nmz_upper_limit,Infinity\n',
                     'parameter,value\nms1_abs_int_tol,-1\n', 'parameter,value\n' + 'x' * 65536):
            with self.subTest(text=text[:100]), self.assertRaises(ValueError):
                parse_masscube_template(text, SCHEMA)
        for values in ({'rt_lower_limit': 10001}, {'mz_lower_limit': 900, 'mz_upper_limit': 60},
                       {'rt_upper_limit': 0}, {'mz_tol_ms1': float('nan')}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_masscube_parameters(values, SCHEMA)
