import unittest

from services.nextflow_override import slurm_singularity_override


class NextflowOverrideTests(unittest.TestCase):
    def test_slurm_override_names_the_committed_profile_without_redefining_it(self) -> None:
        config = slurm_singularity_override("embl_slurm")

        self.assertIn("Profile 'embl_slurm' is defined in the workflow's nextflow.config", config)
        self.assertNotIn("process.executor = 'slurm'", config)
        self.assertNotIn("singularity.enabled = true", config)


if __name__ == "__main__":
    unittest.main()
