import importlib.util
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[2] / 'runtime/scripts/check_gds.py'
spec = importlib.util.spec_from_file_location('check_gds', path)
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)


class CPUStatusTests(unittest.TestCase):
    def test_known_safe_states(self):
        for state in cpu.MITIGATED:
            self.assertEqual(cpu.classify(state+'\n'), 'PASS')

    def test_vulnerable_is_failure(self):
        for state in ('Vulnerable', 'Vulnerable: No microcode'):
            self.assertEqual(cpu.classify(state), 'FAIL')

    def test_unknown_and_substring_injection_are_blocked(self):
        for state in ('', 'Unknown: Dependent on hypervisor status', 'Not affected\nVulnerable',
                      'Vulnerable: Mitigation disabled', 'Mitigation: unknown'):
            self.assertEqual(cpu.classify(state), 'BLOCKED')

    def test_actual_file_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'status'
            self.assertEqual(cpu.main(['--status-file', str(path)]), 2)
            path.write_text('Vulnerable: No microcode\n')
            self.assertEqual(cpu.main(['--status-file', str(path)]), 1)
            path.write_text('Not affected\n')
            self.assertEqual(cpu.main(['--status-file', str(path)]), 0)


if __name__ == '__main__': unittest.main()
