import csv
import importlib.util
import json
import os
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", REPO_ROOT / "workflows" / "nextflow4ms-dial-msdial5-clean"))
if "WORKFLOW_PIPELINE_ROOT" in os.environ:
    REPO_ROOT = PIPELINE_ROOT.parents[1]
EXPORTER_PATH = PIPELINE_ROOT / "adapters" / "canonicalize_outputs.py"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "canonical_output_example"


def load_exporter():
    spec = importlib.util.spec_from_file_location("canonicalize_outputs", EXPORTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def cell_name(column_index: int, row_index: int) -> str:
    name = ""
    column = column_index
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return f"{name}{row_index}"


def write_xlsx(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    def cell_xml(column_index: int, row_index: int, value: object) -> str:
        ref = cell_name(column_index, row_index)
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = []
    for row_index, values in enumerate([headers, *rows], start=1):
        cells = "".join(cell_xml(column_index, row_index, value) for column_index, value in enumerate(values, start=1))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/></Types>')
        workbook.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        workbook.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        workbook.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def write_fixture(root: Path) -> Path:
    (root / "contract").mkdir(parents=True)
    (root / "results" / "stats").mkdir(parents=True)
    (root / "results" / "statpipeline" / "parse").mkdir(parents=True)
    (root / "results" / "statpipeline" / "normalize" / "normalized_matrices").mkdir(parents=True)
    (root / "results" / "statpipeline" / "statistics").mkdir(parents=True)
    (root / "contract" / "params.json").write_text(
        json.dumps(
            {
                "job_id": 7,
                "workflow_definition_id": 3,
                "run_name": "std1_assay2_job7",
                "nextflow_params": {"analysis_mode": "full", "workflow_stage": "full"},
                "selected_files": [{"file_asset_id": 11, "original_filename": "sample_a.raw"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "results" / "stats" / "AlignResult-test_adapted.txt").write_text(
        "Alignment ID\tAverage Rt(min)\tAverage Mz\tAdduct type\tMS/MS assigned\tSpectrum reference file name\tMetabolite name\tsample_a\tsample_b\n"
        "1\t1.00\t100.1\t[M+H]+\tFalse\t\tFeature A\t10\t30\n"
        "2\t1.50\t101.1\t[M+H]+\tTrue\tspec-2\tFeature B\t20\t40\n",
        encoding="utf-8",
    )
    (root / "results" / "statpipeline" / "parse" / "feature_metadata.tsv").write_text(
        "Alignment ID\tAverage Rt(min)\tAverage Mz\tAdduct type\tMS/MS assigned\tSpectrum reference file name\tMetabolite name\n"
        "2\t1.50\t101.1\t[M+H]+\tTrue\tspec-2\tFeature B\n"
        "1\t1.00\t100.1\t[M+H]+\tFalse\t\tFeature A\n",
        encoding="utf-8",
    )
    (root / "results" / "statpipeline" / "parse" / "raw_feature_matrix.tsv").write_text(
        "Sample\tClass\tFiletype\tInjOrder\tName\t1\t2\n"
        "sample_a\tcase\tSample\t1\tsample_a\t10\t20\n"
        "sample_b\tcontrol\tSample\t2\tsample_b\t30\t40\n",
        encoding="utf-8",
    )
    (root / "results" / "statpipeline" / "normalize" / "selected_normalization.json").write_text(
        json.dumps({"selected_normalization_method": "Raw"}),
        encoding="utf-8",
    )
    (root / "results" / "statpipeline" / "normalize" / "normalized_matrices" / "normalized_Raw_matrix.tsv").write_text(
        "Sample\tClass\tFiletype\tInjOrder\tName\t1\t2\n"
        "sample_a\tcase\tSample\t1\tsample_a\t11\t21\n"
        "sample_b\tcontrol\tSample\t2\tsample_b\t31\t41\n",
        encoding="utf-8",
    )
    (root / "results" / "statpipeline" / "statistics" / "statistical_results_case_vs_control.txt").write_text(
        "Alignment ID\tstatistic\tp_value\tq_value\tlog2_fc\n"
        "1\t2.5\t0.01\t0.02\t1.2\n",
        encoding="utf-8",
    )
    return root


class CanonicalizeOutputsTests(unittest.TestCase):
    def test_exporter_writes_canonical_files_and_referential_ids(self) -> None:
        exporter = load_exporter()

        with TemporaryDirectory() as tmpdir:
            fixture_root = (FIXTURE_ROOT if (FIXTURE_ROOT / "results/stats/AlignResult-test_adapted.txt").is_file()
                            else write_fixture(Path(tmpdir) / "fixture"))
            params_json = fixture_root / "contract" / "params.json"
            stats_table = fixture_root / "results" / "stats" / "AlignResult-test_adapted.txt"
            outdir = Path(tmpdir) / "canonical"

            exporter.export_canonical_outputs(
                run_id="std1_assay2_job7",
                outdir=outdir,
                params_json=params_json,
                stats_table=stats_table,
                search_roots=[fixture_root / "results" / "statpipeline"],
            )

            expected_files = {
                "run_manifest.json",
                "features.tsv",
                "feature_quant.tsv",
                "r_results.tsv",
                "feature_annotations.tsv",
                "network_edges.tsv",
                "artifacts.json",
            }
            self.assertEqual({path.name for path in outdir.iterdir()}, expected_files)

            features = read_tsv(outdir / "features.tsv")
            feature_ids = [row["canonical_feature_id"] for row in features]
            self.assertEqual(feature_ids, ["msdial5_1", "msdial5_2"])
            self.assertEqual(len(feature_ids), len(set(feature_ids)))
            self.assertEqual(features[0]["source_feature_id"], "1")
            self.assertEqual(features[0]["mz"], "100.1")
            self.assertIn("Metabolite name", json.loads(features[0]["source_row_json"]))

            quant = read_tsv(outdir / "feature_quant.tsv")
            self.assertEqual(len(quant), 4)
            self.assertTrue({row["canonical_feature_id"] for row in quant}.issubset(set(feature_ids)))
            self.assertEqual(quant[0]["raw_area"], "10")
            self.assertEqual(quant[0]["normalized_area"], "11")

            r_results = read_tsv(outdir / "r_results.tsv")
            self.assertEqual(r_results[0]["canonical_feature_id"], "msdial5_1")
            self.assertEqual(r_results[0]["result_name"], "statistical_results_case_vs_control")

            manifest = json.loads((outdir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], "std1_assay2_job7")
            self.assertTrue(manifest["params_hash"])
            self.assertIn("features.tsv", manifest["output_assets"])
            self.assertIn("artifacts.json", manifest["output_assets"])

            artifacts = json.loads((outdir / "artifacts.json").read_text(encoding="utf-8"))
            artifact_names = {Path(item["uri"]).name for item in artifacts["assets"]}
            self.assertTrue(expected_files - {"artifacts.json"} <= artifact_names)

    def test_exporter_falls_back_to_adapted_stats_table(self) -> None:
        exporter = load_exporter()

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stats_table = root / "AlignResult-test_adapted.txt"
            outdir = root / "canonical"
            stats_table.write_text(
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tAdduct type\tMS/MS assigned\n"
                "5\t3.20\t255.2\t[M-H]-\tTrue\n",
                encoding="utf-8",
            )

            exporter.export_canonical_outputs(
                run_id="review_run",
                outdir=outdir,
                stats_table=stats_table,
                search_roots=[],
            )

            features = read_tsv(outdir / "features.tsv")
            self.assertEqual(features[0]["canonical_feature_id"], "msdial5_5")
            self.assertEqual(features[0]["polarity"], "negative")
            self.assertEqual(read_tsv(outdir / "feature_quant.tsv"), [])
            self.assertEqual(read_tsv(outdir / "r_results.tsv"), [])

    def test_exporter_reads_statpipeline_xlsx_results_for_each_normalization(self) -> None:
        exporter = load_exporter()

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            parse_dir = root / "statpipeline" / "parse"
            stats_dir = root / "statpipeline" / "statistics"
            outdir = root / "canonical"
            parse_dir.mkdir(parents=True)
            (parse_dir / "feature_metadata.tsv").write_text(
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tAdduct type\tMS/MS assigned\n"
                "1\t1.00\t100.1\t[M+H]+\tFalse\n"
                "2\t1.50\t101.1\t[M+H]+\tTrue\n",
                encoding="utf-8",
            )
            headers = ["ID", "Stat_value", "p_value", "p_adj_fdr", "FC_case_vs_control", "log2FC", "Hedges_g"]
            write_xlsx(
                stats_dir / "01_Raw" / "case vs control" / "statistical_results_case_vs_control.xlsx",
                headers,
                [[1, 2.5, 0.01, 0.02, 1.7, 0.77, 0.4], [2, -1.0, 0.20, 0.25, 0.6, -0.73, -0.2]],
            )
            write_xlsx(
                stats_dir / "02_TIC" / "case vs control" / "statistical_results_case_vs_control.xlsx",
                headers,
                [[1, 3.5, 0.03, 0.04, 1.2, 0.26, 0.5], [2, -2.0, 0.30, 0.35, 0.5, -1.00, -0.3]],
            )

            exporter.export_canonical_outputs(
                run_id="xlsx_run",
                outdir=outdir,
                search_roots=[root / "statpipeline"],
            )

            r_results = read_tsv(outdir / "r_results.tsv")
            self.assertEqual(len(r_results), 4)
            self.assertEqual({row["normalization_method"] for row in r_results}, {"Raw", "TIC"})
            self.assertEqual({row["canonical_feature_id"] for row in r_results}, {"msdial5_1", "msdial5_2"})
            first = r_results[0]
            self.assertEqual(first["contrast"], "case vs control")
            self.assertEqual(first["statistic"], "2.5")
            self.assertEqual(first["p_value"], "0.01")
            self.assertEqual(first["q_value"], "0.02")
            self.assertEqual(first["log2_fc"], "0.77")
            self.assertEqual(json.loads(first["extra_json"])["FC_case_vs_control"], "1.7")

    def test_exporter_reads_nextflow_staged_symlinked_statpipeline_dirs(self) -> None:
        exporter = load_exporter()

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            upstream = root / "upstream" / "statpipeline"
            staged = root / "task"
            outdir = root / "canonical"
            (upstream / "parse").mkdir(parents=True)
            (upstream / "normalize" / "normalized_matrices").mkdir(parents=True)
            staged.mkdir()
            (upstream / "parse" / "feature_metadata.tsv").write_text(
                "Alignment ID\tAverage Rt(min)\tAverage Mz\tAdduct type\tMS/MS assigned\n"
                "1\t1.00\t100.1\t[M+H]+\tFalse\n",
                encoding="utf-8",
            )
            (upstream / "parse" / "raw_feature_matrix.tsv").write_text(
                "Sample\tClass\tFiletype\tInjOrder\tName\t1\n"
                "sample_a\tcase\tSample\t1\tsample_a\t10\n",
                encoding="utf-8",
            )
            (upstream / "normalize" / "selected_normalization.json").write_text(
                json.dumps({"selected_normalization_method": "Raw"}),
                encoding="utf-8",
            )
            (upstream / "normalize" / "normalized_matrices" / "normalized_Raw_matrix.tsv").write_text(
                "Sample\tClass\tFiletype\tInjOrder\tName\t1\n"
                "sample_a\tcase\tSample\t1\tsample_a\t11\n",
                encoding="utf-8",
            )
            write_xlsx(
                upstream / "statistics" / "01_Raw" / "case vs control" / "statistical_results_case_vs_control.xlsx",
                ["ID", "Stat_value", "p_value", "p_adj_fdr", "log2FC"],
                [[1, 2.5, 0.01, 0.02, 0.77]],
            )
            for name in ("parse", "normalize", "statistics"):
                os.symlink(upstream / name, staged / name)

            exporter.export_canonical_outputs(
                run_id="symlink_run",
                outdir=outdir,
                search_roots=[staged],
            )

            features = read_tsv(outdir / "features.tsv")
            self.assertEqual(len(features), 1)
            self.assertEqual(features[0]["canonical_feature_id"], "msdial5_1")
            quant = read_tsv(outdir / "feature_quant.tsv")
            self.assertEqual(len(quant), 1)
            self.assertEqual(quant[0]["normalized_area"], "11")
            r_results = read_tsv(outdir / "r_results.tsv")
            self.assertEqual(len(r_results), 1)
            self.assertEqual(r_results[0]["canonical_feature_id"], "msdial5_1")
            self.assertEqual(r_results[0]["normalization_method"], "Raw")


if __name__ == "__main__":
    unittest.main(verbosity=2)
