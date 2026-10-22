import unittest

from services.status_reader import StatusReader


def trace(*pairs):
    return [{"name": name, "status": status} for name, status in pairs]


class ProcessNameTests(unittest.TestCase):
    def test_the_sample_tag_is_dropped(self):
        self.assertEqual(
            StatusReader.process_name("RUN_SIRIUS:run_sirius (sirius_input)"),
            "RUN_SIRIUS:run_sirius",
        )

    def test_the_subworkflow_prefix_is_kept(self):
        # It is what distinguishes one branch of the pipeline from another.
        self.assertEqual(
            StatusReader.process_name("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)"),
            "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5",
        )

    def test_a_process_without_a_tag_is_unchanged(self):
        self.assertEqual(StatusReader.process_name("prepare_msdial5_input"), "prepare_msdial5_input")

    def test_missing_names_do_not_raise(self):
        for value in ["", None]:
            with self.subTest(value=value):
                self.assertEqual(StatusReader.process_name(value), "")


class ProcessStateRollupTests(unittest.TestCase):
    def test_a_process_is_done_when_every_task_finished(self):
        states = StatusReader.process_states(
            trace(("msdial (1)", "COMPLETED"), ("msdial (2)", "COMPLETED"))
        )
        self.assertEqual(states, {"msdial": "done"})

    def test_one_running_task_makes_the_process_running(self):
        states = StatusReader.process_states(
            trace(("msdial (1)", "COMPLETED"), ("msdial (2)", "RUNNING"))
        )
        self.assertEqual(states["msdial"], "running")

    def test_one_failed_task_makes_the_process_failed(self):
        states = StatusReader.process_states(
            trace(("sirius (1)", "COMPLETED"), ("sirius (2)", "FAILED"))
        )
        self.assertEqual(states["sirius"], "failed")

    def test_failure_outranks_a_task_still_running(self):
        states = StatusReader.process_states(
            trace(("sirius (1)", "FAILED"), ("sirius (2)", "RUNNING"))
        )
        self.assertEqual(states["sirius"], "failed")

    def test_cached_counts_as_done(self):
        self.assertEqual(StatusReader.process_states(trace(("msdial (1)", "CACHED"))),
                         {"msdial": "done"})

    def test_processes_are_reported_separately(self):
        states = StatusReader.process_states(trace(
            ("prepare_msdial5_input (1)", "COMPLETED"),
            ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)", "RUNNING"),
            ("RUN_SIRIUS:run_sirius (sirius_input)", "FAILED"),
        ))
        self.assertEqual(states, {
            "prepare_msdial5_input": "done",
            "RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5": "running",
            "RUN_SIRIUS:run_sirius": "failed",
        })

    def test_an_empty_trace_reports_nothing(self):
        self.assertEqual(StatusReader.process_states([]), {})

    def test_rows_without_a_name_or_status_are_skipped(self):
        states = StatusReader.process_states(
            [{"name": "", "status": "COMPLETED"}, {"name": "msdial (1)", "status": ""}]
        )
        self.assertEqual(states, {})


class RealTraceShapeTests(unittest.TestCase):
    """The exact rows a MetaboLens run produced, from job 107's trace."""

    def test_a_real_run_rolls_up_as_expected(self):
        states = StatusReader.process_states(trace(
            ("prepare_msdial5_input (1)", "COMPLETED"),
            ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5 (1)", "COMPLETED"),
            ("RUN_SIRIUS:prepare_sirius_msp (AlignResult-2026771355)", "COMPLETED"),
            ("RUN_MSDIAL5_PREPARED_TO_STATS_TXT:adapt_mdalign_to_stats_txt (1)", "COMPLETED"),
            ("RUN_SIRIUS:run_sirius (sirius_input)", "FAILED"),
        ))
        self.assertEqual(states["prepare_msdial5_input"], "done")
        self.assertEqual(states["RUN_SIRIUS:run_sirius"], "failed")
        self.assertEqual(states["RUN_SIRIUS:prepare_sirius_msp"], "done")


if __name__ == "__main__":
    unittest.main()


class RunningFromTheLogTests(unittest.TestCase):
    """The trace only gains a row once a task terminates -- across every run in
    this repository it has only ever written COMPLETED, CACHED, FAILED and
    ABORTED -- so a task in flight is invisible to it."""

    def test_a_submitted_process_with_no_trace_row_is_running(self):
        states = StatusReader.process_states(
            trace(("prepare_msdial5_input (1)", "COMPLETED")),
            submitted={"prepare_msdial5_input", "RUN_SIRIUS:run_sirius"},
        )
        self.assertEqual(states["prepare_msdial5_input"], "done")
        self.assertEqual(states["RUN_SIRIUS:run_sirius"], "running")

    def test_the_trace_still_wins_for_a_finished_process(self):
        states = StatusReader.process_states(
            trace(("sirius (1)", "FAILED")), submitted={"sirius"}
        )
        self.assertEqual(states["sirius"], "failed")

    def test_no_log_means_no_invented_running_states(self):
        self.assertEqual(
            StatusReader.process_states(trace(("msdial (1)", "COMPLETED")), submitted=None),
            {"msdial": "done"},
        )

    def test_submitted_lines_are_parsed_from_a_real_log_shape(self):
        import tempfile
        from pathlib import Path
        text = (
            "Oct-02 09:12:01.123 [Task submitter] INFO  nextflow.Session - "
            "[a1/2b3c4d] Submitted process > RUN_SIRIUS:run_sirius (sirius_input)\n"
            "Oct-02 09:12:02.456 [Task submitter] INFO  nextflow.Session - "
            "[e5/6f7g8h] Submitted process > prepare_msdial5_input (1)\n"
            "Oct-02 09:12:03.000 [main] DEBUG something else entirely\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "execution.log"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(
                StatusReader.submitted_processes(path),
                {"RUN_SIRIUS:run_sirius", "prepare_msdial5_input"},
            )

    def test_a_missing_log_is_not_an_error(self):
        from pathlib import Path
        self.assertEqual(StatusReader.submitted_processes(Path("/nope/execution.log")), set())
