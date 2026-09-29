#!/usr/bin/env python3
"""Owned, temporary PostgreSQL for approved local integration tests.

No TCP, sudo, installation, production data or automatic directory deletion.
All fixture data and logs are retained. Filesystem permissions protect against
other users, not hostile processes running as the invoking user.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

DB = 'synthbit_test'
PORT = 55439
ENV_KEY = 'SYNTHBIT_PG_FIXTURE'


class FixtureBlocked(RuntimeError):
    """Required fixture unavailable, unsafe, or not stopped successfully."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def clean_environment(root):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'home'),
            'LC_ALL': 'C', 'LANG': 'C', 'TZ': 'UTC',
            'TMPDIR': str(root), 'GIT_CONFIG_NOSYSTEM': '1'}


def checked_path(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise FixtureBlocked('Symlink in fixture path')
    return path


def persist_manifest(path, data):
    path = checked_path(path)
    fd, tmp = tempfile.mkstemp(prefix='.fixture-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def new_manifest(root, tools):
    return {'schema_version': 1, 'kind': 'synthbit-postgres-fixture',
            'fixture_id': uuid.uuid4().hex, 'created_utc': utc(),
            'root': str(root), 'data_dir': str(root / 'data'),
            'socket_dir': str(root / 'socket'), 'database': DB, 'role': DB,
            'port': PORT, 'owner_uid': os.getuid(), 'status': 'ALLOCATED',
            'tools': tools, 'pid': None, 'tcp_enabled': False}


def find_tools(bin_dir=None):
    if bin_dir is None:
        config = shutil.which('pg_config')
        if not config:
            raise FixtureBlocked('pg_config missing; no installation attempted')
        p = subprocess.run([config, '--bindir'], capture_output=True, text=True, timeout=5)
        if p.returncode:
            raise FixtureBlocked('Cannot resolve installed PostgreSQL tools')
        bin_dir = Path(p.stdout.strip())
    bin_dir = Path(bin_dir)
    tools = {}
    for name in ('initdb', 'postgres', 'psql', 'pg_ctl', 'pg_isready'):
        p = bin_dir / name
        if not p.is_file() or not os.access(p, os.X_OK):
            raise FixtureBlocked('Missing installed PostgreSQL tool: ' + name)
        tools[name] = str(p.resolve())
    return tools


class FixtureClient:
    def __init__(self, path):
        self.path = checked_path(path)
        if not self.path.is_file() or self.path.stat().st_size > 16384:
            raise FixtureBlocked('Fixture manifest missing or oversized')
        self.manifest = json.loads(self.path.read_text(encoding='utf-8'))
        self.root = checked_path(self.manifest['root'])
        self.validate_local()
        self.env = clean_environment(self.root)

    @classmethod
    def from_environment(cls):
        path = os.environ.get(ENV_KEY)
        if not path:
            raise FixtureBlocked('Required isolated PostgreSQL fixture is not configured')
        return cls(path)

    def validate_local(self):
        m = self.manifest
        if (m.get('schema_version') != 1 or m.get('kind') != 'synthbit-postgres-fixture'
                or m.get('status') != 'RUNNING' or m.get('database') != DB
                or m.get('role') != DB or m.get('port') != PORT
                or m.get('owner_uid') != os.getuid() or m.get('tcp_enabled') is not False):
            raise FixtureBlocked('Fixture identity or lifecycle status rejected')
        if self.path != self.root / 'fixture.json':
            raise FixtureBlocked('Manifest outside its fixture directory')
        for name, expected in [('data_dir', self.root / 'data'),
                               ('socket_dir', self.root / 'socket')]:
            p = checked_path(m[name])
            if p != expected or not p.is_dir():
                raise FixtureBlocked('Fixture directory mismatch')
        for p in (self.root, self.root / 'socket', self.root / 'home', self.path):
            info = p.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise FixtureBlocked('Fixture ownership or permissions rejected')
        if type(m.get('pid')) is not int or m['pid'] <= 1:
            raise FixtureBlocked('Fixture server identity missing')
        try:
            os.kill(m['pid'], 0)
        except OSError as exc:
            raise FixtureBlocked('Fixture server is not alive') from exc

    def _run_sql(self, sql, database=DB):
        if database not in (DB, 'postgres'):
            raise FixtureBlocked('Database outside fixture scope')
        argv = [self.manifest['tools']['psql'], '-X', '--no-password',
                '--host=' + self.manifest['socket_dir'], '--port=' + str(PORT),
                '--username=' + DB, '--dbname=' + database,
                '--set=ON_ERROR_STOP=1', '-t', '-A', '-f', '-']
        try:
            p = subprocess.run(argv, input=sql, env=self.env, cwd=self.root,
                               text=True, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FixtureBlocked('Fixture SQL transport unavailable') from exc
        if p.returncode:
            # Synthetic test SQL only; don't emit connection strings or SQL values.
            raise FixtureBlocked('Fixture SQL failed with exit ' + str(p.returncode))
        return p.stdout.strip()

    def verify_identity(self, database=DB):
        self.validate_local()
        query = """SELECT json_build_object(
          'data_directory', current_setting('data_directory'),
          'listen_addresses', current_setting('listen_addresses'),
          'database', current_database(), 'role', current_user,
          'socket_dir', current_setting('unix_socket_directories'),
          'port', current_setting('port'))::text;"""
        observed = json.loads(self._run_sql(query, database))
        expected = {'data_directory': self.manifest['data_dir'],
                    'listen_addresses': '', 'database': database, 'role': DB,
                    'socket_dir': self.manifest['socket_dir'], 'port': str(PORT)}
        if observed != expected:
            raise FixtureBlocked('Server identity does not match isolated fixture')
        return observed

    def sql(self, sql, check=True):
        # check=False in production callers must not hide fixture SQL failures.
        self.verify_identity()
        return self._run_sql(sql)


class OwnedPostgres:
    def __init__(self, bin_dir=None):
        if os.geteuid() == 0:
            raise FixtureBlocked('Fixture must run as a non-root user')
        tools = find_tools(bin_dir)
        self.root = Path(tempfile.mkdtemp(prefix='synthbit-pg-')).resolve()
        os.chmod(self.root, 0o700)
        for directory in ('socket', 'home'):
            (self.root / directory).mkdir(mode=0o700)
        self.manifest = new_manifest(self.root, tools)
        self.manifest_path = self.root / 'fixture.json'
        self.process = None
        self.log_file = None
        persist_manifest(self.manifest_path, self.manifest)

    def wait_ready(self):
        deadline = time.monotonic() + 15
        argv = [self.manifest['tools']['pg_isready'],
                '--host=' + self.manifest['socket_dir'], '--port=' + str(PORT),
                '--username=' + DB, '--dbname=postgres', '--timeout=1']
        while self.process.poll() is None and time.monotonic() < deadline:
            p = subprocess.run(argv, env=clean_environment(self.root), cwd=self.root,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if p.returncode == 0:
                return
            time.sleep(0.05)
        raise FixtureBlocked('Isolated server did not become ready')

    def start(self):
        if self.manifest['status'] != 'ALLOCATED':
            raise FixtureBlocked('Fixture cannot be started twice')
        env = clean_environment(self.root)
        tools = self.manifest['tools']
        fd = os.open(self.root / 'postgres.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.log_file = os.fdopen(fd, 'wb')
        self.manifest['status'] = 'INITIALIZING'
        persist_manifest(self.manifest_path, self.manifest)
        try:
            version = subprocess.run([tools['postgres'], '--version'], env=env, cwd=self.root,
                                     capture_output=True, text=True, timeout=5)
            if version.returncode:
                raise FixtureBlocked('PostgreSQL version probe failed')
            self.manifest['postgres_version'] = version.stdout.strip()
            p = subprocess.run([tools['initdb'], '-D', self.manifest['data_dir'],
                                '-U', DB, '--no-locale', '-E', 'UTF8',
                                '--auth-local=trust', '--auth-host=reject', '--no-instructions'],
                               env=env, cwd=self.root, stdout=self.log_file,
                               stderr=subprocess.STDOUT, timeout=45)
            if p.returncode:
                raise FixtureBlocked('Isolated cluster initialization failed')
            self.process = subprocess.Popen(
                [tools['postgres'], '-D', self.manifest['data_dir'],
                 '-k', self.manifest['socket_dir'], '-p', str(PORT),
                 '-c', 'listen_addresses=', '-c', 'unix_socket_permissions=0700'],
                env=env, cwd=self.root, stdout=self.log_file,
                stderr=subprocess.STDOUT, start_new_session=True)
            self.manifest.update(status='RUNNING', pid=self.process.pid)
            persist_manifest(self.manifest_path, self.manifest)
            self.wait_ready()
            client = FixtureClient(self.manifest_path)
            client.verify_identity(database='postgres')
            client._run_sql('CREATE DATABASE synthbit_test;', database='postgres')
            client.verify_identity()
            self.manifest['identity_verified_utc'] = utc()
            persist_manifest(self.manifest_path, self.manifest)
            return client
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.manifest.get('status') == 'STOPPED':
            return
        try:
            if self.process is not None and self.process.poll() is None:
                # PostgreSQL SIGINT performs a fast, clean shutdown of this child.
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=15)
            if self.process is not None and self.process.poll() is None:
                raise FixtureBlocked('Owned PostgreSQL process is still running')
            self.manifest.update(status='STOPPED', stopped_utc=utc())
        except (OSError, subprocess.SubprocessError, FixtureBlocked) as exc:
            self.manifest.update(status='STOP_FAILED', stop_error=type(exc).__name__)
            raise FixtureBlocked('Could not confirm fixture shutdown; artifacts retained') from exc
        finally:
            persist_manifest(self.manifest_path, self.manifest)
            if self.log_file is not None:
                self.log_file.close()
                self.log_file = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('An explicit test command after -- is required')
    owner = None
    writer = None
    result = 2
    try:
        owner = OwnedPostgres(args.bin_dir)
        print('Fixture artifacts: ' + str(owner.root), flush=True)
        owner.start()
        env = clean_environment(owner.root)
        env[ENV_KEY] = str(owner.manifest_path)
        writer = subprocess.Popen(command, env=env, start_new_session=True)
        result = writer.wait(timeout=240)
        owner.manifest['test_exit_status'] = result
    except (FixtureBlocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('BLOCKED: ' + (str(exc) if isinstance(exc, FixtureBlocked) else type(exc).__name__), file=sys.stderr)
        result = 2
    except KeyboardInterrupt:
        result = 130
    finally:
        try:
            if writer is not None and writer.poll() is None:
                try:
                    os.killpg(writer.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # Child exited between poll and signal; still reap it.
                try:
                    writer.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Only this explicitly owned test process group.
                    try:
                        os.killpg(writer.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    writer.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            print('BLOCKED: test process teardown not confirmed', file=sys.stderr)
            result = 2
        finally:
            if owner is not None:
                try:
                    owner.stop()
                    print('Fixture STOPPED; artifacts retained: ' + str(owner.root), flush=True)
                except (FixtureBlocked, OSError) as exc:
                    print('BLOCKED: fixture shutdown/persistence failure: ' + type(exc).__name__, file=sys.stderr)
                    result = 2
    return result


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
