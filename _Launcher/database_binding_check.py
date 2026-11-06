"""Unified selection regression: real store, injection and HTTP guards, no database connection."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from launcher_security import LauncherSecurityStore, DATABASE_SERVICES
from launcher_web import create_management_app
from sd_sma_launcher import service_with_launcher_credential


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        credentials = {name: dict(engine='mysql', host='127.0.0.1', port=3307, database='test_only',
                                  username=name, password='test-only-' + name, managed_storage=True)
                       for name in DATABASE_SERVICES}
        store = LauncherSecurityStore(root / 'security.json', managed_credentials=credentials)
        store.disable_pin()
        for user in ('root', 'operator'):
            saved = store.upsert_credential(dict(id=user, username=user, password='test-only-' + user,
                                                host='example.invalid', database='test_only', port=3306))
            assert 'password' not in saved
        store.assign('query_web', 'operator')
        old = store.database_snapshot()
        store.apply_database_binding(dict(mode='external', external_credential_id='root'))
        assert not store.public_credentials()['assignments']
        for name in DATABASE_SERVICES:
            env = service_with_launcher_credential({'name': name}, store)['env']
            assert env['SD_SMA_DB_USERNAME'] == 'root' and env['SD_SMA_DB_DATABASE'] == 'test_only'
            assert env['SD_SMA_MANAGED_DATABASE'] == ''
        assert service_with_launcher_credential({'name': 'report_copy'}, store) == {'name': 'report_copy'}
        client = TestClient(create_management_app(None, store, None, Path(__file__).parent / 'static'))
        for method, url, body in (
            ('post', '/api/launcher/credentials', dict(id='root', password='replacement')),
            ('delete', '/api/launcher/credentials/root', None),
            ('put', '/api/launcher/credentials/assignments/query_web', dict(credential_id='operator')),
        ):
            response = client.request(method, url, **({'json': body} if body else {}))
            assert response.status_code == 409, response.text
        assert 'test-only-' not in client.get('/api/launcher/credentials').text
        missing_loader_calls = []
        def missing_managed():
            missing_loader_calls.append(True)
            raise RuntimeError('missing managed credentials')
        reloaded = LauncherSecurityStore(store.path, managed_loader=missing_managed)
        for name in DATABASE_SERVICES:
            assert reloaded.credential_for_service(name)['username'] == 'root'
        assert not missing_loader_calls
        original = store.path.read_bytes()
        temporary_file = store.path.with_suffix('.json.tmp')
        temporary_file.mkdir()
        try:
            store.apply_database_binding({'mode': 'managed'})
        except OSError:
            pass
        else:
            raise AssertionError('Failed atomic replace was accepted')
        temporary_file.rmdir()
        assert store.path.read_bytes() == original
        assert store.credential_for_service('query_web')['username'] == 'root'
        store.apply_database_binding({'mode': 'managed'})
        for name in DATABASE_SERVICES:
            assert store.credential_for_service(name) == credentials[name]
        store.restore_database(old)
        assert store.credential_for_service('query_web')['username'] == 'operator'
        assert store.public_credentials()['database_binding'] is None
        broken = root / 'broken.json'
        for binding in (dict(mode='external', external_credential_id='missing'), None, dict(mode='wrong')):
            broken.write_text(json.dumps(dict(database_binding=binding)), encoding='utf-8')
            invalid = LauncherSecurityStore(broken, managed_credentials=credentials)
            try:
                invalid.credential_for_service('collector_web')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Invalid binding silently fell back')
        broken.write_text('broken json', encoding='utf-8')
        invalid = LauncherSecurityStore(broken)
        assert invalid.load_error
        assert TestClient(create_management_app(None, invalid, None, Path(__file__).parent / 'static')).get('/').status_code == 200
        try:
            invalid.credential_for_service('collector_web')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Corrupt selection silently ignored')
    print('database_binding_check: all assertions passed')


if __name__ == '__main__':
    main()
