"""Exercise real switching/store/supervisor and SQLite draining; substitute only process and remote I/O."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / '_Prj/SD_SMA_COMMON'), str(ROOT / '_Prj/SD_SMA_DATA_COLLECTOR')]
from database.db_manager import DatabaseManager
from database.data_storage import DataStorageProcessor
from database.persistent_queue import PersistentQueueStore
from sd_sma_common.database_control import pending_outbox
from launcher_database import DatabaseController
from launcher_security import LauncherSecurityStore, DATABASE_SERVICES
from launcher_supervisor import ServiceSupervisor
from sd_sma_launcher import service_with_launcher_credential


async def check_drain(directory):
    for persistent in (False, True):
        db = DatabaseManager(dict(type='sqlite', name=str(directory / f'data-{persistent}.db')))
        assert db.connect()
        processor = DataStorageProcessor(db, persistent_queue_config=dict(enabled=persistent, path=str(directory / 'outbox.db')))
        processor.group_data_points['Data_Test'] = ['Value']
        processor.group_partition_interval_years['Data_Test'] = 0
        assert processor.initialize_tables_for_runtime()
        record = dict(group_name='Data_Test', collection_time=datetime.now(), data={'Value': {'value': 'old-target'}})
        processor.add_data(record)
        await processor.start_processing()
        state = await processor.drain_for_database_switch()
        assert not state['queue_size'] and not state['retry_queue_size'] and not state['outbox_pending_size']
        assert db.execute_query('SELECT Value FROM Data_Test') == [('old-target',)]
        db.disconnect()
        db.db_config['name'] = str(directory)
        db.engine = db._create_engine(f'sqlite:///{directory}')
        processor.add_data(record)
        state = await processor.drain_for_database_switch()
        assert state['retry_queue_size'] + state['dead_letter_queue_size'] + state['outbox_retry_size'] > 0
        if persistent:
            assert any(pending_outbox(directory / 'outbox.db').values())
            processor.persistent_store.close()
        db.disconnect()
    print('PASS real storage: memory and persistent queues drain to old SQLite; failures retain rows')


class Process:
    def __init__(self):
        self.pid = 123456789
        self.code = None

    def poll(self):
        return self.code


class BoundaryController(DatabaseController):
    def rpc(self, name, action='', body=None):
        if name == 'db_admin' and action == 'pause' and self.busy:
            raise RuntimeError('busy database task')
        if action:
            return dict(storage={'queue_size': self.pending}, outboxes={}, held=action != 'resume')
        return dict(connected=not self.unavailable, target=self.runtime[name],
                    running=False, filename='', phase='idle', task_active=False, storage={}, tools_available=True)


def check_switch(directory):
    for fault in ('none', 'preflight', 'busy', 'memory', 'start', 'rollback', 'stopped',
                  'pending', 'processing', 'retry', 'dead_letter'):
        folder = directory / fault
        folder.mkdir()
        store = LauncherSecurityStore(folder / 'security.json')
        old = dict(id='old', host='example.invalid', port=3306, database='old_db', username='root', password='test-only')
        store.upsert_credential(old)
        store.apply_database_binding(dict(mode='external', external_credential_id='old'))
        runtime = {}
        fail_start = []
        def start(service):
            name = service['name']
            credential = store.credential_for_service(name)
            if fail_start and name == 'query_web' and (credential['database'] == 'new_db' or fault == 'rollback'):
                raise RuntimeError('injected process start failure')
            runtime[name] = DatabaseController._target(credential) if credential else None
            assert 'SD_SMA_DB_PASSWORD' in service_with_launcher_credential(service, store).get('env', {}) or name == 'report_copy'
            return SimpleNamespace(process=Process())
        supervisor = ServiceSupervisor([dict(name=name, port=0) for name in (*DATABASE_SERVICES, 'report_copy')],
                                       folder / 'services.json', start_process=start,
                                       stop_process=lambda process: setattr(process.process, 'code', 0),
                                       health_check=lambda service: True, poll_interval_seconds=0.005, max_restarts=0)
        supervisor.start()
        import time
        for _ in range(100):
            if all(row['state'] == 'running' for row in supervisor.status()['services']):
                break
            time.sleep(0.01)
        if fault == 'stopped':
            supervisor.stop_for_database(['query_web'])
        controller = BoundaryController(store, supervisor, folder, folder)
        controller.runtime = runtime
        controller.busy = fault == 'busy'
        controller.pending = int(fault == 'memory')
        controller.unavailable = False
        outbox = None
        if fault in ('pending', 'processing', 'retry', 'dead_letter'):
            outbox = PersistentQueueStore(str(folder / 'outbox.db'))
            record_id = outbox.enqueue({'group_name': 'retained-old-task'})
            if fault == 'processing':
                outbox.mark_processing([record_id])
            elif fault == 'retry':
                outbox.mark_retry([record_id], 'test-only', 3600)
            elif fault == 'dead_letter':
                outbox.mark_dead_letter([record_id], 'test-only')
            (folder / 'collector.json').write_text(json.dumps({'persistent_queue': {'enabled': True, 'path': outbox.path}}), encoding='utf-8')
        initial = store.path.read_bytes()
        if fault in ('start', 'rollback'):
            fail_start.append(True)
        payload = dict(mode='external', credential=dict(host='example.invalid', port=3308, database='new_db',
                                                       username='operator', password='new-test-only'))
        try:
            with patch('launcher_database.connection_preflight', return_value={'ok': fault != 'preflight'}):
                result = controller.apply(payload)
            if fault in ('none', 'stopped'):
                assert result['ok'], result
                assert store.credential_for_service('collector_web')['database'] == 'new_db'
            else:
                assert not result['ok'], result
                assert store.path.read_bytes() == initial, result
                if fault in ('start', 'rollback'):
                    assert result['rollback']['ok'] == (fault == 'start'), result
            states = {row['name']: row for row in supervisor.status()['services']}
            assert states['report_copy']['state'] == 'running'
            if fault == 'stopped':
                assert not states['query_web']['desired_running'] and states['query_web']['pid'] is None
            if outbox:
                assert pending_outbox(Path(outbox.path)) == {fault: 1}
        finally:
            supervisor.shutdown()
            if outbox:
                outbox.close()
    print('PASS real apply/supervisor/store: candidate failure, busy, memory backlog, start failure, rollback failure, stopped service')


def main():
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        asyncio.run(check_drain(directory))
        check_switch(directory)
    print('database_switch_check: all assertions passed')


if __name__ == '__main__':
    main()
