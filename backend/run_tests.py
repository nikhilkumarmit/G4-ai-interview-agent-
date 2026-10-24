"""Run scripted interview tests and print a compact pass/fail table."""
import unittest

suite = unittest.defaultTestLoader.discover("backend/tests", pattern="test_*.py")
flattened = []
def walk(s):
    """Collect each test case from a possibly nested test suite."""
    for test in s:
        if isinstance(test, unittest.TestSuite):
            walk(test)
        else:
            flattened.append(test)
walk(suite)
result = unittest.TextTestRunner(verbosity=0).run(suite)
print("\nTEST RESULTS")
print("| Test | Result |")
print("|---|---|")
for test, error in result.failures:
    print(f"| {test.id().split('.')[-1]} | FAIL |")
for test, error in result.errors:
    print(f"| {test.id().split('.')[-1]} | ERROR |")
failed = {test for test, _ in result.failures + result.errors}
for test in flattened:
    if test not in failed:
        print(f"| {test.id().split('.')[-1]} | PASS |")
print(f"\n{result.testsRun - len(failed)}/{result.testsRun} passed")
raise SystemExit(0 if result.wasSuccessful() else 1)
