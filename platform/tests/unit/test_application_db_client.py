from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'platform/scripts'))
import db_client


class DbClientTests(unittest.TestCase):
    def test_peer_role_no_sudo_no_password_no_sql_in_argv(self):
        with patch.object(db_client.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout=' beroun\n'
            self.assertEqual(db_client.psql('SELECT current_user;'),'beroun')
        args=run.call_args.args[0]
        self.assertEqual(args[0],'psql');self.assertIn('-w',args);self.assertNotIn('sudo',args)
        self.assertNotIn('SELECT current_user;',args)
        self.assertEqual(run.call_args.kwargs['input'],'SELECT current_user;')
        self.assertEqual(run.call_args.kwargs['timeout'],30)

    def test_errors_never_expose_sql_credentials_or_become_empty_success(self):
        for check in (True,False):
            with patch.object(db_client.subprocess,'run') as run:
                run.return_value.returncode=2;run.return_value.stderr='PRIVATE_CONNECTION_STRING'
                with self.assertRaises(RuntimeError) as exc: db_client.psql('PRIVATE_SQL',check=check)
                self.assertNotIn('PRIVATE',str(exc.exception));self.assertEqual(run.call_count,1)
        with patch.object(db_client.subprocess,'run',side_effect=subprocess.TimeoutExpired('psql',30)):
            with self.assertRaises(subprocess.TimeoutExpired):db_client.psql('SELECT 1;')


if __name__=='__main__':unittest.main()
