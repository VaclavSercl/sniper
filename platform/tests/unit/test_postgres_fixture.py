"""Fixture refusal tests: no server, model, network, or production DB calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

SYNTHBIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYNTHBIT))
import postgres_fixture as fixture


class FixtureSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthbit-pg-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        for name in ('data', 'socket', 'home'):
            (self.root / name).mkdir(mode=0o700)
        self.manifest = fixture.new_manifest(self.root, {'psql': '/usr/bin/psql'})
        self.manifest.update(status='RUNNING', pid=os.getpid())
        self.path = self.root / 'fixture.json'
        fixture.persist_manifest(self.path, self.manifest)

    def test_import_does_not_probe_database(self):
        path = SYNTHBIT / 'unit/test_perfect_market_ingest.py'
        spec = importlib.util.spec_from_file_location('fixture_import_check', path)
        module = importlib.util.module_from_spec(spec)
        with patch('subprocess.run', side_effect=RuntimeError('forbidden')) as run:
            spec.loader.exec_module(module)
        run.assert_not_called()

    def test_missing_fixture_is_blocked_not_skipped(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(fixture.FixtureBlocked):
                fixture.FixtureClient.from_environment()

    def test_wrong_database_is_rejected_before_connection(self):
        self.manifest['database'] = 'beroun'
        fixture.persist_manifest(self.path, self.manifest)
        with patch('subprocess.run') as run:
            with self.assertRaises(fixture.FixtureBlocked):
                fixture.FixtureClient(self.path)
        run.assert_not_called()

    def test_wrong_socket_is_rejected_before_connection(self):
        self.manifest['socket_dir'] = '/var/run/postgresql'
        fixture.persist_manifest(self.path, self.manifest)
        with patch('subprocess.run') as run:
            with self.assertRaises(fixture.FixtureBlocked):
                fixture.FixtureClient(self.path)
        run.assert_not_called()

    def test_symlink_socket_is_rejected(self):
        (self.root / 'socket').rmdir()
        (self.root / 'socket').symlink_to(self.root / 'data')
        with self.assertRaises(fixture.FixtureBlocked):
            fixture.FixtureClient(self.path)

    def test_inherited_pg_environment_is_removed(self):
        with patch.dict(os.environ, {'PGHOST': 'production', 'PGSERVICE': 'production',
                                    'PGPASSWORD': 'synthetic', 'PGOPTIONS': '-c unsafe=true'}):
            env = fixture.clean_environment(self.root)
        self.assertFalse(any(k.startswith('PG') for k in env))
        self.assertEqual(env['HOME'], str(self.root / 'home'))

    def test_identity_mismatch_prevents_requested_sql(self):
        client = fixture.FixtureClient(self.path)
        response = json.dumps({'data_directory': '/wrong', 'listen_addresses': '',
                               'database': 'synthbit_test', 'role': 'synthbit_test',
                               'socket_dir': str(self.root / 'socket'), 'port': '55439'})
        with patch.object(client, '_run_sql', return_value=response) as run:
            with self.assertRaises(fixture.FixtureBlocked):
                client.sql('CREATE TABLE test_only(id integer)')
        self.assertEqual(run.call_count, 1)

    def test_missing_binary_is_reported(self):
        with self.assertRaises(fixture.FixtureBlocked):
            fixture.find_tools(self.root / 'missing-binaries')

    def test_failed_stop_is_visible_and_preserves_files(self):
        owner = fixture.OwnedPostgres.__new__(fixture.OwnedPostgres)
        owner.root = self.root
        owner.manifest = self.manifest
        owner.manifest_path = self.path
        owner.process = Mock()
        owner.process.poll.return_value = None
        owner.process.wait.side_effect = subprocess.TimeoutExpired('owned postgres', 1)
        owner.log_file = None
        with self.assertRaises(fixture.FixtureBlocked):
            owner.stop()
        self.assertTrue(self.path.exists())
        owner.process.kill.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text())['status'], 'STOP_FAILED')

    def test_stopped_fixture_cannot_be_reused(self):
        self.manifest['status'] = 'STOPPED'
        fixture.persist_manifest(self.path, self.manifest)
        with self.assertRaises(fixture.FixtureBlocked):
            fixture.FixtureClient(self.path)

    def test_test_process_exit_race_still_stops_database(self):
        owner = Mock()
        owner.root = self.root
        owner.manifest = {}
        owner.manifest_path = self.path
        writer = Mock()
        writer.poll.return_value = None
        writer.wait.side_effect = [subprocess.TimeoutExpired('fixture tests', 1), 0]
        with patch.object(sys, 'argv', ['postgres_fixture.py', '--', 'true']), \
                patch.object(fixture, 'OwnedPostgres', return_value=owner), \
                patch('subprocess.Popen', return_value=writer), \
                patch('os.killpg', side_effect=ProcessLookupError):
            result = fixture.main()
        self.assertEqual(result, 2)
        owner.stop.assert_called_once()

    def test_readiness_waits_until_connections_are_accepted(self):
        owner = fixture.OwnedPostgres.__new__(fixture.OwnedPostgres)
        owner.root = self.root
        owner.manifest = self.manifest
        owner.manifest['tools']['pg_isready'] = '/fixture/pg_isready'
        owner.process = Mock()
        owner.process.poll.return_value = None
        results = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with patch('subprocess.run', side_effect=results) as run, patch('time.sleep'):
            owner.wait_ready()
        self.assertEqual(run.call_count, 2)
        self.assertIn('--host=' + str(self.root / 'socket'), run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
