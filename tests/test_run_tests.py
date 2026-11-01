import unittest

from scripts.run_tests import partition_modules


class TestRunnerSchedulingTests(unittest.TestCase):
    def test_full_application_smoke_runs_outside_parallel_module_pool(self):
        modules = ["test_archives", "test_combined_backup", "test_file_tracking"]

        serial, parallel = partition_modules(modules)

        self.assertEqual(serial, ["test_combined_backup"])
        self.assertEqual(parallel, ["test_archives", "test_file_tracking"])
        self.assertCountEqual(serial + parallel, modules)


if __name__ == "__main__":
    unittest.main()
