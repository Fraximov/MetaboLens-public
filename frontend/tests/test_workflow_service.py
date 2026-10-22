import unittest
from unittest.mock import patch

from services import workflow_service

# The launch-form contract of the MS-DIAL engine workflow, reduced to what these tests read.
MSDIAL_ENGINE_DEFINITION = {
    'id': 3,
    'static_input_config_json': {'engine': {
        'execution_profiles': ['local_docker', 'slurm_singularity_ssh'],
        'required_inputs': {'reference_file_id': 'reference acquisition'},
        'review_transitions': {'is_qc_review': 'qc_preview', 'qc_preview': 'full'},
    }},
    'parameter_schema_json': {'type': 'object', 'properties': {
        'msdial_config_path': {'type': 'string'}, 'ms1_library_path': {'type': 'string'},
        'ms2_library_path': {'type': 'string'}, 'reference_file_id': {'type': 'integer'},
        'workflow_stage': {'enum': ['full', 'is_qc_review', 'qc_preview'], 'default': 'full'},
        'analysis_mode': {'enum': ['qc_only'], 'default': 'qc_only'},
        'enable_sirius': {'type': 'boolean'},
        'sirius_annotation_mode': {'enum': ['formulas', 'canopus', 'structures', 'full']},
    }, 'additionalProperties': False},
}


class WorkflowServiceTests(unittest.TestCase):
    def test_masscube_template_uses_shared_editor_but_its_own_api_fields(self):
        definition = {'id': 3, 'static_input_config_json': {'engine': {'execution_profiles': ['local_docker']}},
                      'parameter_schema_json': {'properties': {'masscube_config_path': {'type': 'string'}}}}
        source = 'templates/masscube/comparison v1.csv'
        edited = 'parameter,value\nrt_upper_limit,18\n'
        with patch('services.workflow_service.call_api', return_value={}) as api:
            workflow_service.get_workflow_template_content('token', 3, source, definition)
            self.assertIn('/masscube-template?path=templates%2Fmasscube%2Fcomparison+v1.csv', api.call_args.args[1])
            workflow_service.launch_workflow('token', 2, 3, '{}', workflow_definition=definition,
                                            msdial_config_path=source, msdial_template_text=edited)
        payload = api.call_args.kwargs['payload']
        self.assertEqual(payload['parameters'], {'masscube_config_path': source})
        self.assertEqual(payload['masscube_template_text'], edited)
        self.assertNotIn('msdial_template_text', payload)

    def test_optional_library_clear_removes_stale_json_in_preview_and_launch(self):
        definition = {'id': 3, 'name': 'Third engine',
                      'static_input_config_json': {'engine': {'engine_id': 'thirdengine',
                          'required_inputs': {}, 'support_files': ['ms2_library_path'],
                          'execution_profiles': ['local_docker', 'slurm_singularity_ssh']}},
                      'parameter_schema_json': {'properties': {
                          'ms2_library_path': {'type': 'string'}, 'masscube_parameters': {'type': 'object'},
                          'workflow_stage': {'enum': ['qc_preview', 'full'], 'default': 'qc_preview'}}}}
        for target, profile in [('local_docker', 'docker'), ('slurm_singularity_ssh', 'embl_slurm')]:
            with self.subTest(target=target), patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
                workflow_service.launch_workflow(
                    token='token', assay_id=2, workflow_id=3, workflow_definition=definition,
                    workflow_params='{"ms2_library_path":"/stale.msp","masscube_parameters":{"mz_tol_ms1":0.005}}',
                    ms2_library_path=None, execution_target=target)
                self.assertEqual(api.call_args.kwargs['payload']['parameters'], {
                    'masscube_parameters': {'mz_tol_ms1': 0.005}, 'workflow_stage': 'qc_preview',
                    'execution_profile': target, 'nextflow_profile': profile})
        self.assertEqual(workflow_service.engine_form_parameters(
            definition, {'ms2_library_path': '/stale.msp'}, {'ms2_library_path': '/selected.msp'}, None),
            {'ms2_library_path': '/selected.msp'})
        preview = workflow_service.build_nextflow_command_preview(
            [definition], 3, '{"ms2_library_path":"/stale.msp"}',
            None, None, None, None, None, execution_target='local_docker')
        self.assertNotIn('ms2_library_path', preview)

    def test_package_feature_id_is_url_encoded_as_an_opaque_query_value(self):
        from urllib.parse import urlsplit, parse_qs
        with patch('services.workflow_service.call_api', return_value={}) as api:
            workflow_service.get_package_feature_trace('token', 18, 142, 90, 'engine/a:2 +?')
        url = api.call_args.args[1]
        self.assertEqual(urlsplit(url).path, '/assays/18/workflow-jobs/142/features/trace')
        self.assertEqual(parse_qs(urlsplit(url).query), {'preview_file_id':['90'], 'feature_id':['engine/a:2 +?']})

    def test_engine_launch_does_not_inject_controls_from_another_engine(self):
        definition = {
            'id': 3, 'static_input_config_json': {'engine': {
                'execution_profiles': ['local_docker'], 'review_transitions': {'qc_preview': 'full'}}},
            'parameter_schema_json': {'type': 'object', 'properties': {
                'threshold': {'type': 'number'},
                'analysis_mode': {'enum': ['qc_only'], 'default': 'qc_only'},
                'workflow_stage': {'enum': ['full', 'qc_preview'], 'default': 'full'},
            }, 'additionalProperties': False},
        }
        with patch('services.workflow_service.call_api', return_value={'id': 42}) as api:
            workflow_service.launch_workflow(
                token='token', assay_id=2, workflow_id=3, workflow_params='{"threshold":2}',
                workflow_definition=definition, analysis_mode='full', review_stages=['qc_preview'],
                msdial_config_path='stale.txt', msdial_template_text='stale template',
                ms1_library_path='stale.txt', reference_file_id=12, enable_sirius=True,
                execution_target='local_docker',
            )
        payload = api.call_args.kwargs['payload']
        self.assertEqual(payload['parameters'], {
            'threshold': 2, 'analysis_mode': 'qc_only', 'workflow_stage': 'qc_preview',
            'execution_profile': 'local_docker', 'nextflow_profile': 'docker',
        })
        self.assertNotIn('msdial_template_text', payload)

    def test_engine_launch_filters_legacy_json_parameters(self):
        definition = {'static_input_config_json': {'engine': {'execution_profiles': ['local_docker']}},
                      'parameter_schema_json': {'type': 'object', 'properties': {
                          'threshold': {'type': 'number'}, 'workflow_stage': {'enum': ['full'], 'default': 'full'},
                      }, 'additionalProperties': False}}
        with patch('services.workflow_service.call_api', return_value={'id': 43}) as api:
            workflow_service.launch_workflow(
                token='token', assay_id=2, workflow_id=3,
                workflow_params='{"threshold":2,"run_lipid_module":true,"imputation_method":"missForest"}',
                workflow_definition=definition, execution_target='local_docker')
        self.assertEqual(api.call_args.kwargs['payload']['parameters'], {
            'threshold': 2, 'workflow_stage': 'full', 'execution_profile': 'local_docker', 'nextflow_profile': 'docker'})

    def test_engine_form_limits_modes_and_execution_profiles(self):
        definition = {'static_input_config_json': {'engine': {'execution_profiles': ['local_docker']}},
                      'parameter_schema_json': {'properties': {'analysis_mode': {'enum': ['qc_only'], 'default': 'qc_only'}}}}
        controls = workflow_service.workflow_form_controls(definition)
        self.assertEqual([x['value'] for x in controls['analysis_mode']], ['qc_only'])
        self.assertEqual([x['value'] for x in controls['execution_target']], ['local_docker'])
        self.assertFalse(controls['msdial_config_path'])
        self.assertFalse(controls['enable_sirius'])

    def test_readiness_uses_engine_requirements_instead_of_msdial_defaults(self):
        definition = {'static_input_config_json': {'engine': {'required_inputs': {}}}}
        args = dict(token='token', study_id=1, assay_id=2, workflow_id=3, workflow_params='{}',
                    ms1_library_path=None, ms2_library_path=None, msdial_config_path=None,
                    reference_file_id=None, selection_store=None, workflow_definition=definition)
        self.assertTrue(workflow_service.workflow_launch_readiness(**args)['ready'])
        definition['static_input_config_json']['engine']['required_inputs'] = {'custom_database': 'Engine database'}
        self.assertEqual(workflow_service.workflow_launch_readiness(**args)['missing'], ['Engine database'])
        args['workflow_params'] = '{"custom_database":"library.dat"}'
        self.assertTrue(workflow_service.workflow_launch_readiness(**args)['ready'])

    def test_delete_workflow_job_calls_assay_scoped_endpoint(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 42}) as call_api:
            result = workflow_service.delete_workflow_job("token", 18, 42)

        self.assertEqual(result["id"], 42)
        call_api.assert_called_once_with(
            "DELETE",
            "/assays/18/workflow-jobs/42",
            token="token",
        )

    def test_get_workflow_template_content_encodes_selected_path(self) -> None:
        response = {
            "path": "templates/msdial/a b.txt",
            "content": "MS-DIAL ver. 5.5\n",
            "checksum_sha256": "a" * 64,
        }
        with patch("services.workflow_service.call_api", return_value=response) as call_api:
            result = workflow_service.get_workflow_template_content(
                "token",
                7,
                "templates/msdial/a b.txt",
            )

        self.assertEqual(result, response)
        call_api.assert_called_once_with(
            "GET",
            "/workflows/7/msdial-template?path=templates%2Fmsdial%2Fa+b.txt",
            token="token",
        )

    def test_launch_workflow_includes_selected_library_paths_and_overrides_json(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 42}) as call_api:
            result = workflow_service.launch_workflow(
                token="token",
                assay_id=10,
                workflow_id=3,
                workflow_params='{"ms1_library_path": "libraries/old.txt", "profile": "test"}',
                file_ids=[5, 6],
                ms1_library_path="libraries/selected_ms1.txt",
                ms2_library_path="libraries/selected_ms2.msp",
                msdial_config_path="templates/msdial/selected_params.txt",
                reference_file_id=6,
                analysis_mode="qc_only",
                workflow_definition=MSDIAL_ENGINE_DEFINITION,
            )

        self.assertEqual(result, {"id": 42})
        _, kwargs = call_api.call_args
        self.assertEqual(kwargs["payload"]["file_ids"], [5, 6])
        self.assertNotIn("profile", kwargs["payload"]["parameters"])
        self.assertEqual(kwargs["payload"]["parameters"]["ms1_library_path"], "libraries/selected_ms1.txt")
        self.assertEqual(kwargs["payload"]["parameters"]["ms2_library_path"], "libraries/selected_ms2.msp")
        self.assertEqual(kwargs["payload"]["parameters"]["msdial_config_path"], "templates/msdial/selected_params.txt")
        self.assertEqual(kwargs["payload"]["parameters"]["reference_file_id"], 6)
        self.assertEqual(kwargs["payload"]["parameters"]["analysis_mode"], "qc_only")

    def test_launch_workflow_includes_selected_execution_target(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 44}) as call_api:
            workflow_service.launch_workflow(
                token="token",
                assay_id=10,
                workflow_id=3,
                workflow_params="{}",
                execution_target="slurm_singularity_ssh",
                workflow_definition=MSDIAL_ENGINE_DEFINITION,
            )

        _, kwargs = call_api.call_args
        self.assertEqual(kwargs["payload"]["parameters"]["execution_profile"], "slurm_singularity_ssh")
        self.assertEqual(kwargs["payload"]["parameters"]["nextflow_profile"], "embl_slurm")

    def test_launch_workflow_includes_sirius_controls(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 46}) as call_api:
            workflow_service.launch_workflow(
                token="token",
                assay_id=10,
                workflow_id=3,
                workflow_params="{}",
                enable_sirius=True,
                sirius_annotation_mode="full",
                workflow_definition=MSDIAL_ENGINE_DEFINITION,
            )

        _, kwargs = call_api.call_args
        self.assertTrue(kwargs["payload"]["parameters"]["enable_sirius"])
        self.assertEqual(kwargs["payload"]["parameters"]["sirius_annotation_mode"], "full")

    def test_launch_workflow_selected_template_overrides_json(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 43}) as call_api:
            workflow_service.launch_workflow(
                token="token",
                assay_id=10,
                workflow_id=3,
                workflow_params='{"msdial_config_path": "functional_test/sample_data/msdial_params.txt"}',
                msdial_config_path="templates/msdial/selected_params.txt",
                workflow_definition=MSDIAL_ENGINE_DEFINITION,
            )

        _, kwargs = call_api.call_args
        self.assertEqual(kwargs["payload"]["parameters"]["msdial_config_path"], "templates/msdial/selected_params.txt")

    def test_launch_workflow_sends_edited_template_outside_parameters(self) -> None:
        edited_text = "MS-DIAL ver. 5.5\nMinimum peak height: 2500\n"
        with patch("services.workflow_service.call_api", return_value={"id": 43}) as call_api:
            workflow_service.launch_workflow(
                token="token",
                assay_id=10,
                workflow_id=3,
                workflow_params="{}",
                msdial_config_path="templates/msdial/selected_params.txt",
                msdial_template_text=edited_text,
                workflow_definition=MSDIAL_ENGINE_DEFINITION,
            )

        _, kwargs = call_api.call_args
        self.assertEqual(kwargs["payload"]["msdial_template_text"], edited_text)
        self.assertNotIn("msdial_template_text", kwargs["payload"]["parameters"])

    def test_launch_and_preview_refuse_a_workflow_without_an_engine(self) -> None:
        legacy = {"id": 1, "name": "msdial5-nextflow", "nextflow_entrypoint": "main_msdial5.nf"}
        with patch("services.workflow_service.call_api") as call_api, self.assertRaises(ValueError):
            workflow_service.launch_workflow(
                token="token", assay_id=10, workflow_id=1, workflow_params="{}", workflow_definition=legacy)
        call_api.assert_not_called()
        preview = workflow_service.build_nextflow_command_preview(
            [legacy], 1, "{}", None, None, None, None, None)
        self.assertEqual(preview, "# Select a workflow to preview its launch.")

    def test_continue_workflow_job_posts_to_continue_endpoint(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 42, "status": "queued"}) as call_api:
            result = workflow_service.continue_workflow_job("token", 10, 42)

        self.assertEqual(result["status"], "queued")
        call_api.assert_called_once_with(
            "POST",
            "/assays/10/workflow-jobs/42/continue",
            token="token",
        )

    def test_continue_workflow_job_posts_operator_note_when_present(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 42, "status": "queued"}) as call_api:
            workflow_service.continue_workflow_job("token", 10, 42, operator_note="Checked IS RT manually.")

        call_api.assert_called_once_with(
            "POST",
            "/assays/10/workflow-jobs/42/continue",
            token="token",
            payload={"operator_note": "Checked IS RT manually."},
        )

    def test_continue_workflow_job_posts_rt_correction_choice(self) -> None:
        with patch("services.workflow_service.call_api", return_value={"id": 42}) as call_api:
            workflow_service.continue_workflow_job(
                "token",
                10,
                42,
                operator_note="Reviewed",
                apply_rt_correction=True,
                rt_correction_target_ids=["1", "3"],
            )

        call_api.assert_called_once_with(
            "POST",
            "/assays/10/workflow-jobs/42/continue",
            token="token",
            payload={
                "operator_note": "Reviewed",
                "apply_rt_correction": True,
                "rt_correction_target_ids": ["1", "3"],
            },
        )

    def test_is_qc_review_api_reads_summary_targets_and_trace(self) -> None:
        with patch("services.workflow_service.call_api", side_effect=[{"overall_status": "pass"}, [{"target_name": "IS: A"}], {"traces": []}]) as call_api:
            summary = workflow_service.get_is_qc_summary("token", 10, 42, ["1", "3"])
            targets = workflow_service.list_is_qc_targets("token", 10, 42, ["1", "3"])
            trace = workflow_service.get_is_qc_trace("token", 10, 42, "IS: A", ["1", "3"])

        self.assertEqual(summary["overall_status"], "pass")
        self.assertEqual(targets[0]["target_name"], "IS: A")
        self.assertEqual(trace["traces"], [])
        selection = "rt_correction_target_id=1&rt_correction_target_id=3"
        self.assertEqual(call_api.call_args_list[0].args[1], f"/assays/10/workflow-is-qc/42/summary?{selection}")
        self.assertEqual(call_api.call_args_list[1].args[1], f"/assays/10/workflow-is-qc/42/targets?{selection}")
        self.assertEqual(
            call_api.call_args_list[2].args[1],
            f"/assays/10/workflow-is-qc/42/traces?target_name=IS%3A+A&{selection}",
        )

    def test_list_workflow_output_files_filters_by_job_when_selected(self) -> None:
        with patch("services.workflow_service.call_api", return_value=[]) as call_api:
            workflow_service.list_workflow_output_files("token", 10, 42)

        call_api.assert_called_once_with(
            "GET",
            "/assays/10/workflow-output-files?job_id=42",
            token="token",
        )

    def test_list_workflow_output_files_keeps_all_jobs_without_job_filter(self) -> None:
        with patch("services.workflow_service.call_api", return_value=[]) as call_api:
            workflow_service.list_workflow_output_files("token", 10, "__all__")

        call_api.assert_called_once_with(
            "GET",
            "/assays/10/workflow-output-files",
            token="token",
        )


if __name__ == "__main__":
    unittest.main()
