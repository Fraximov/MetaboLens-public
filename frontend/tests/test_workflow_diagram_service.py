import unittest

from services.workflow_diagram_service import (
    engine_technical_dag_asset,
    workflow_diagram_asset,
    workflow_overview_asset,
)


class WorkflowDiagramServiceTests(unittest.TestCase):
    def test_masscube_has_its_nextflow_dag(self) -> None:
        asset = engine_technical_dag_asset({"engine_id": "masscube"})
        self.assertEqual(asset["src"], "/assets/masscube-workflow-dag.svg")

    def test_an_engine_without_a_dag_gets_none(self) -> None:
        self.assertIsNone(engine_technical_dag_asset({"engine_id": "msdial5"}))
        self.assertIsNone(engine_technical_dag_asset(None))

    def test_defaults_to_modular_v2_full_dag(self) -> None:
        asset = workflow_diagram_asset(None)

        self.assertEqual(asset["src"], "/assets/msdial5-workflow-dag-v2-full.svg")
        self.assertIn("modular StatPipeline v2 full", asset["alt"])

    def test_selects_modular_qc_only_dag(self) -> None:
        asset = workflow_diagram_asset("qc_only")

        self.assertEqual(asset["src"], "/assets/msdial5-workflow-dag-v2-qc-only.svg")
        self.assertIn("QC-only", asset["alt"])

    def test_unknown_modular_mode_uses_full_dag(self) -> None:
        asset = workflow_diagram_asset("unexpected")

        self.assertEqual(asset["src"], "/assets/msdial5-workflow-dag-v2-full.svg")

    def test_overview_describes_the_route_map(self) -> None:
        asset = workflow_overview_asset("qc_only")

        self.assertEqual(asset["src"], "/assets/metabolens-workflow-metro.svg")
        self.assertIn("route map", asset["alt"])

    def test_overview_describes_review_resume_and_separate_sirius_outputs(self) -> None:
        asset = workflow_overview_asset("full")

        self.assertIn("pause", asset["alt"].lower())
        self.assertIn("resume", asset["alt"].lower())
        self.assertIn("SIRIUS annotations registered separately", asset["alt"])


if __name__ == "__main__":
    unittest.main()
