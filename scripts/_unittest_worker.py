"""Private subprocess entry for run_tests: report failures before a later hang."""
import faulthandler
import sys
import unittest


class ImmediateResult(unittest.TextTestResult):
    def addError(self, test, err):
        super().addError(test, err)
        self.stream.writeln(self._exc_info_to_string(err, test))
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.writeln(self._exc_info_to_string(err, test))
        self.stream.flush()


class DiagnosticRunner(unittest.TextTestRunner):
    resultclass = ImmediateResult


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def main() -> int:
    diagnostic = int(sys.argv[1])
    module_name = sys.argv[2]
    fast = "--fast" in sys.argv[3:]
    faulthandler.dump_traceback_later(diagnostic, repeat=True)
    suite = unittest.defaultTestLoader.loadTestsFromName(module_name)
    tests = list(flatten(suite))
    if fast:
        tests = [test for test in tests
                 if not getattr(getattr(test, test._testMethodName, None), "__visual__", False)]
        print(f"FAST_SELECTION {module_name} selected={len(tests)} excluded_visuals="
              f"{sum(getattr(getattr(test, test._testMethodName, None), '__visual__', False) for test in flatten(suite))}",
              flush=True)
    result = DiagnosticRunner(verbosity=2).run(unittest.TestSuite(tests))
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
