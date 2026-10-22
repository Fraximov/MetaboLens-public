import os
import re
import unittest
from pathlib import Path


class WorkflowSourceContractTests(unittest.TestCase):
    def test_msdial5_entrypoint_uses_active_stats_scripts_only(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        main_script = pipeline_root / "main_msdial5.nf"

        source = main_script.read_text(encoding="utf-8")
        engine = (pipeline_root / "modules" / "msdial" / "main.nf").read_text(encoding="utf-8")
        # MS-DIAL's own processes live in modules/msdial so a second engine can
        # supply the same emits from its own module. Retired scripts must not
        # reappear in either file.
        combined = source + engine

        self.assertNotIn("Scripts /", combined)
        self.assertNotIn("R/legacy/StatPipeline_mvp.R", combined)
        self.assertNotIn("run_statpipeline_mvp", combined)
        self.assertNotIn("stats_pipeline_mode", combined)
        self.assertNotIn("stats_pipeline_container_image", combined)

        self.assertIn("adapters/mdalign_to_stats_txt.py", source)
        self.assertIn("${adapter_script}", engine)
        self.assertIn('export TMPDIR="\\$PWD/tmp"', engine)
        self.assertIn('mkdir -p "\\$TMPDIR"', engine)
        self.assertIn("process prepare_msdial5_input", engine)
        self.assertIn("process peak_detection_msdial5", engine)
        self.assertIn("process adapt_mdalign_to_stats_txt", engine)
        self.assertIn("workflow RUN_MSDIAL5_PREPARED_TO_STATS_TXT", engine)
        self.assertIn("from './modules/msdial/main'", source)

        self.assertIn("workflow_stage", source)
        self.assertIn("is_qc_review", source)
        self.assertIn("RUN_IS_QC", source)
        self.assertIn("adapters/extract_is_qc.py", source)
        self.assertIn("msdial_review", source)
        self.assertIn("CANONICALIZE_WITH_STATPIPELINE", source)
        self.assertIn("CANONICALIZE_FROM_STATS_ONLY", source)
        self.assertIn("adapters/canonicalize_outputs.py", source)
        self.assertIn("params.run_id", source)
        self.assertIn("params.params_json", source)
        self.assertTrue((pipeline_root / "adapters" / "mdalign_to_stats_txt.py").is_file())
        self.assertTrue((pipeline_root / "adapters" / "extract_is_qc.py").is_file())
        self.assertTrue((pipeline_root / "adapters" / "canonicalize_outputs.py").is_file())
        self.assertTrue((pipeline_root / "assets" / "empty_params.json").is_file())
        self.assertTrue((pipeline_root / "R" / "cli" / "parse_msdial_cli.R").is_file())
        self.assertFalse((pipeline_root / "R" / "legacy" / "StatPipeline_mvp.R").exists())

    def test_workflow_process_images_do_not_reuse_app_worker_image(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        nextflow_config = (pipeline_root / "nextflow.config").read_text(encoding="utf-8")
        main_script = (pipeline_root / "main_msdial5.nf").read_text(encoding="utf-8")
        adapter_dockerfile = pipeline_root / "Dockerfile.stats_adapter"
        is_qc_dockerfile = pipeline_root / "Dockerfile.is_qc"
        sirius_dockerfile = pipeline_root / "Dockerfile.sirius"
        v2_dockerfile = pipeline_root / "Dockerfile.statpipeline_v2"
        repo_root = pipeline_root.parents[1] if pipeline_root.is_absolute() else Path(".")
        compose_file = repo_root / "infra" / "compose" / "docker-compose.yml"
        hpc_compose_file = repo_root / "infra" / "compose" / "docker-compose.hpc-ssh.yml"

        self.assertIn('stats_adapter_container_image = "metabolens-public-stats-adapter:unconfigured"', nextflow_config)
        self.assertIn('is_qc_container_image = "metabolens-public-is-qc:unconfigured"', nextflow_config)
        self.assertIn('enable_sirius = false', nextflow_config)
        self.assertIn('sirius_container_image = "metabolens-public-sirius:unconfigured"', nextflow_config)
        self.assertNotIn("stats_pipeline_mode", nextflow_config)
        self.assertNotIn("stats_pipeline_container_image", nextflow_config)
        self.assertNotIn("run_statpipeline_mvp", nextflow_config)
        for source in (nextflow_config, main_script):
            self.assertNotRegex(source, r"['\"](?:metabolens-(?!public-)|nextflow4ms-dial-msdial5:)")
        self.assertIn("'metabolens-public-stats-adapter:unconfigured'", main_script)
        self.assertIn("'metabolens-public-is-qc:unconfigured'", main_script)
        self.assertIn("CANONICALIZE_", nextflow_config)
        self.assertTrue(adapter_dockerfile.is_file())
        self.assertTrue(is_qc_dockerfile.is_file())
        self.assertTrue(sirius_dockerfile.is_file())
        self.assertFalse((pipeline_root / "Dockerfile.statpipeline_mvp").exists())
        self.assertNotIn("FROM compose-worker", adapter_dockerfile.read_text(encoding="utf-8"))
        self.assertNotIn("FROM compose-worker", is_qc_dockerfile.read_text(encoding="utf-8"))
        self.assertNotIn("FROM compose-worker", sirius_dockerfile.read_text(encoding="utf-8"))
        self.assertNotIn("FROM compose-worker", v2_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("procps", adapter_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("procps", is_qc_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("procps", sirius_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("ARG SIRIUS_VERSION=6.3.4", sirius_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("github.com/sirius-ms/sirius/releases/download", sirius_dockerfile.read_text(encoding="utf-8"))
        self.assertIn("procps", v2_dockerfile.read_text(encoding="utf-8"))
        if compose_file.exists():
            compose_text = compose_file.read_text(encoding="utf-8")
            self.assertNotIn("workflow-is-qc-image", compose_text)
            self.assertNotIn("Dockerfile.is_qc", compose_text)
            self.assertIn("WORKFLOW_SIRIUS_CONTAINER_OPTIONS", compose_text)
            self.assertIn("runtime/sirius-home", compose_text)
        if hpc_compose_file.exists():
            hpc_compose_text = hpc_compose_file.read_text(encoding="utf-8")
            self.assertIn("WORKFLOW_IS_QC_CONTAINER_IMAGE", hpc_compose_text)
            self.assertIn("is-qc.sif", hpc_compose_text)
            self.assertIn("WORKFLOW_SIRIUS_CONTAINER_IMAGE", hpc_compose_text)
            self.assertIn("WORKFLOW_SIRIUS_CONTAINER_OPTIONS", hpc_compose_text)
            self.assertIn("singularity/images/sirius.sif", hpc_compose_text)

        local_login = repo_root / "scripts" / "sirius-login-local.sh"
        if local_login.parent.exists():
            self.assertTrue(local_login.is_file())
            self.assertIn("runtime/sirius-home", local_login.read_text(encoding="utf-8"))
            self.assertIn("org.metabolens.public-build", local_login.read_text(encoding="utf-8"))

    def test_nextflow_configs_do_not_require_custom_config_functions(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        nextflow_config = (pipeline_root / "nextflow.config").read_text(encoding="utf-8")
        self.assertNotIn("def check_max(", nextflow_config)
        self.assertNotIn("check_max", nextflow_config)
        for relative in re.findall(r"includeConfig\s+['\"]([^'\"]+)['\"]", nextflow_config):
            self.assertTrue((pipeline_root / relative).is_file(), relative)

    def test_rt_correction_is_optional_cached_and_not_published_as_mzml(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        main = (pipeline_root / "main_msdial5.nf").read_text(encoding="utf-8")
        module = (pipeline_root / "modules" / "rt_correction" / "main.nf").read_text(encoding="utf-8")
        config = (pipeline_root / "nextflow.config").read_text(encoding="utf-8")

        self.assertIn("BUILD_RT_CORRECTION_REVIEW", main)
        self.assertIn("APPLY_RT_CORRECTION", main)
        self.assertIn("params.apply_rt_correction", main)
        self.assertIn("params.rt_correction_target_ids", main)
        self.assertIn("workflowStage == 'is_qc_review' || applyRtCorrection", main)
        self.assertIn("RUN_IS_QC.out.artifacts", main)
        self.assertIn("rt_correction_artifacts", main)
        self.assertIn("rt_correction_artifacts {\n        path '.'", main)
        self.assertNotIn("corrected_input = corrected_input", main.split("output {")[1])
        self.assertIn("process build_rt_correction_plan", module)
        self.assertIn("process rewrite_mzml_retention_times", module)
        self.assertIn("rt_correction_plan.json", module)
        self.assertIn("corrected_input", module)
        self.assertIn("val selected_target_ids", module)
        self.assertIn("--target-ids-json '${selected_target_ids}'", module)
        review_workflow = module.split("workflow BUILD_RT_CORRECTION_REVIEW", 1)[1].split(
            "workflow APPLY_RT_CORRECTION", 1
        )[0]
        self.assertNotIn("selected_target_ids", review_workflow)
        self.assertIn("withName: build_rt_correction_plan", config)
        self.assertIn("withName: rewrite_mzml_retention_times", config)

    def test_canonical_boundary_documents_future_annotation_seam(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        main_source = (pipeline_root / "main_msdial5.nf").read_text(encoding="utf-8")
        canonical_module = (pipeline_root / "modules" / "canonicalize" / "main.nf").read_text(encoding="utf-8")

        self.assertIn("Future extension seam", main_source)
        self.assertIn("SIRIUS, GNPS/GNPS2, and MZmine", main_source)
        self.assertIn("consume canonical outputs", main_source)
        self.assertIn("does not recompute scientific results", canonical_module)

    def test_readability_cleanup_pins_entrypoint_and_sections(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        main_source = (pipeline_root / "main_msdial5.nf").read_text(encoding="utf-8")
        r_only_source = (pipeline_root / "workflows" / "metabolens_r_analysis.nf").read_text(encoding="utf-8")

        self.assertIn("Legacy worker entrypoint", main_source)
        self.assertIn("active worker-used Nextflow entrypoint", main_source)
        self.assertIn('backend seed currently references `main_msdial5.nf`', main_source)
        self.assertIn("input prep -> MS-DIAL -> adapter -> StatPipeline v2 -> canonical outputs", main_source)
        self.assertIn("Future annotation modules should consume canonical outputs", main_source)
        engine_source = (pipeline_root / "modules" / "msdial" / "main.nf").read_text(encoding="utf-8")
        for section in (
            "Section: params/config",
            "Section: MS-DIAL engine",
            "Section: StatPipeline v2",
            "Section: canonical export",
            "Section: main workflow",
            "Section: future annotation seam",
        ):
            self.assertIn(section, main_source)

        # The engine's own steps are sectioned inside its module now.
        for section in (
            "Section: input preparation",
            "Section: MS-DIAL execution",
            "Section: MS-DIAL-to-stats adapter",
        ):
            self.assertIn(section, engine_source)

        self.assertIn("Secondary/manual R-only entrypoint", r_only_source)
        self.assertIn("not launched by the MetaboLens worker", r_only_source)
        self.assertIn("worker entrypoint remains `main_msdial5.nf`", r_only_source)

    def test_canonical_contract_markdown_files_remain_present(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        repo_root = pipeline_root.parents[1] if pipeline_root.is_absolute() else Path(".")
        contracts_dir = repo_root / "contracts"
        if not contracts_dir.exists():
            self.skipTest("repository-level contracts directory is not mounted in this test environment")

        for filename in (
            "run_manifest.md",
            "features.md",
            "feature_quant.md",
            "r_results.md",
            "feature_annotations.md",
            "network_edges.md",
            "artifacts.md",
        ):
            self.assertTrue((contracts_dir / filename).is_file(), filename)


    def test_sirius_module_declares_prepare_and_run_processes(self):
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        sirius_module = pipeline_root / "modules" / "sirius"
        if not sirius_module.exists():
            self.skipTest("SIRIUS module is not present in this workflow")

        module_source = (sirius_module / "main.nf").read_text(encoding="utf-8")
        self.assertIn("process prepare_sirius_msp", module_source)
        self.assertIn("process run_sirius", module_source)
        self.assertIn("sirius_output/logs/sirius.stdout.log", module_source)
        self.assertIn("sirius_output/logs/sirius.stderr.log", module_source)
        self.assertIn("Login ERROR", module_source)
        self.assertIn("find sirius_output/summaries -type f", module_source)
        self.assertIn("sirius_workspace", module_source)
        self.assertIn("--workspace", module_source)
        self.assertIn('mode=${shellQuote(sirius_annotation_mode)}', module_source)
        self.assertIn('workspace_args=()', module_source)
        self.assertIn('recompute_args=()', module_source)
        self.assertIn('local step_name="\\$1"', module_source)
        self.assertIn('--input sirius_input.msp', module_source)
        self.assertIn("SIRIUS step '\\${step_name}' failed", module_source)
        self.assertIn("fingerprints", module_source)
        self.assertIn("classes", module_source)
        self.assertIn("structures", module_source)
        self.assertIn("summaries", module_source)
        self.assertNotIn("sirius_output/summaries sirius_output/project sirius_output/logs", module_source)
        self.assertIn("--project sirius_output/project.sirius", module_source)


    def test_sirius_module_is_opt_in_after_msdial_before_stats(self) -> None:
        pipeline_root = Path(os.environ.get("WORKFLOW_PIPELINE_ROOT", "workflows/nextflow4ms-dial-msdial5-clean"))
        sirius_module = pipeline_root / "modules" / "sirius"
        module_source = (sirius_module / "main.nf").read_text(encoding="utf-8")
        main_source = (pipeline_root / "main_msdial5.nf").read_text(encoding="utf-8")
        nextflow_config = (pipeline_root / "nextflow.config").read_text(encoding="utf-8")

        self.assertIn("RUN_SIRIUS", main_source)
        self.assertIn("enableSirius", main_source)
        self.assertIn("params.enable_sirius", main_source)
        self.assertIn("RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_spectra", main_source)
        self.assertIn("adapters/prepare_sirius_msp.py", main_source)
        self.assertIn("sirius_outputs", main_source)

        self.assertIn("enable_sirius = false", nextflow_config)
        self.assertIn("sirius_container_image", nextflow_config)
        self.assertIn("sirius_container_options", nextflow_config)
        self.assertIn("sirius_workspace", nextflow_config)
        self.assertIn("withName: run_sirius", nextflow_config)

if __name__ == "__main__":
    unittest.main()
