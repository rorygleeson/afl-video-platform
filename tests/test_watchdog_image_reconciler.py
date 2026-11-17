"""Read-only/offline tests for the existing image-reconcile watchdog fallback.

The scheduled scan uses mocked DynamoDB and Lambda; NO AWS calls, model calls,
live job creation or paid submissions are possible from these tests.
"""
import ast
import io
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from boto3.dynamodb.conditions import Attr

SOURCE = Path(__file__).resolve().parents[1] / "src/image_worker/handler.py"
ID = "a" * 32
NOW = datetime.now(timezone.utc)


def subject(scan_pages, outcome='MONITORING_STARTED_BY_RECONCILER'):
    tree = ast.parse(SOURCE.read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == 'reconcile_stalled')
    table = MagicMock()
    table.scan.side_effect = scan_pages
    watcher = MagicMock()
    watcher.invoke.return_value = {'Payload': io.BytesIO(json.dumps({'status': outcome}).encode())}
    boto = MagicMock()
    boto.client.return_value = watcher
    recover = MagicMock()
    scope = {
        'Attr': Attr, 'TABLE': table, 'boto3': boto, 'REGION': 'ap-southeast-2',
        'datetime': datetime, 'timezone': timezone, 'timedelta': timedelta,
        'time': __import__('time'), 'json': json, 'recover': recover,
        'LOG': logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['reconcile_stalled'], table, watcher, recover


class ImageReconcilerWatchdogTests(unittest.TestCase):
    def test_recent_consent_job_uses_existing_watchdog_only(self):
        item = {'jobId': ID, 'status': 'GENERATING_IMAGES', 'autoVideoConsent': True,
                'createdAt': NOW.isoformat(), 'generationStartedAt': int(NOW.timestamp())}
        reconcile, table, watcher, recover = subject([{'Items': [item]}])
        result = reconcile()
        self.assertEqual(result['monitorChecked'], 1)
        self.assertEqual(result['monitorStarted'], 1)
        self.assertEqual(result['recovered'], 0)
        self.assertFalse(result['scanIncomplete'])
        call = watcher.invoke.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'afl-app-dev-handoff-watchdog')
        self.assertEqual(call['InvocationType'], 'RequestResponse')
        self.assertEqual(json.loads(call['Payload']), {'op': 'ensure_monitor', 'jobId': ID})
        recover.assert_not_called()
        table.update_item.assert_not_called()

    def test_nonconsenting_job_never_invokes_watchdog(self):
        item = {'jobId': ID, 'status': 'COMPLETED', 'autoVideoConsent': False,
                'createdAt': NOW.isoformat()}
        reconcile, _, watcher, recover = subject([{'Items': [item]}])
        self.assertEqual(reconcile()['monitorChecked'], 0)
        watcher.invoke.assert_not_called()
        recover.assert_not_called()

    def test_existing_stale_recovery_still_runs(self):
        item = {'jobId': ID, 'status': 'GENERATING_IMAGES',
                'generationStartedAt': int(NOW.timestamp())-50*60}
        reconcile, _, watcher, recover = subject([{'Items': [item]}])
        result = reconcile()
        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['recovered'], 1)
        watcher.invoke.assert_not_called()
        recover.assert_called_once_with(ID)

    def test_monitor_failure_does_not_cancel_stale_image_recovery(self):
        item = {'jobId': ID, 'status': 'GENERATING_IMAGES',
                'generationStartedAt': int(NOW.timestamp())-50*60,
                'autoVideoConsent': True, 'createdAt': NOW.isoformat()}
        reconcile, _, watcher, recover = subject([{'Items': [item]}])
        watcher.invoke.side_effect = RuntimeError('temporary throttle')
        result = reconcile()
        self.assertEqual(result['recovered'], 1)
        self.assertEqual(result['monitorStarted'], 0)
        recover.assert_called_once_with(ID)

    def test_scan_limit_is_reported_not_claimed_as_full_coverage(self):
        item={'jobId':ID, 'status':'COMPLETED',
              'autoVideoConsent':True, 'createdAt':NOW.isoformat()}
        pages=[{'Items':[item], 'LastEvaluatedKey':{'jobId':ID}} for _ in range(10)]
        reconcile, table, watcher, _ = subject(pages, outcome='ALREADY_MONITORING')
        result=reconcile()
        self.assertTrue(result['scanIncomplete'])
        self.assertEqual(table.scan.call_count, 10)
        self.assertEqual(result['monitorStarted'], 0)

if __name__ == '__main__':
    unittest.main()
