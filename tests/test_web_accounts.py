"""Account authorization and data isolation at the real HTTP boundary."""
from http.client import HTTPConnection
import json
import re
import threading

import pytest

from sparktutor.config.settings import Settings, ExecutionMode
from sparktutor.web.accounts import AccountStore, COOKIE
from sparktutor.web.server import WebServer


@pytest.fixture
def server(tmp_path):
    instance = WebServer(('127.0.0.1', 0), Settings(data_dir=tmp_path,execution_mode=ExecutionMode.DRY_RUN), accounts=True)
    thread = threading.Thread(target=instance.serve_forever,daemon=True)
    thread.start()
    yield instance
    instance.shutdown();instance.server_close();thread.join(timeout=5)


def request(server, path, values=None, *, cookie='', token='', method=None, session='a'*32):
    connection = HTTPConnection('127.0.0.1',server.server_port,timeout=15)
    headers = {'Content-Type':'application/json','X-SparkTutor-Auth':'1',
               'X-SparkTutor-Token':token,'X-SparkTutor-Session':session,'Cookie':cookie}
    connection.request(method or ('POST' if values is not None else 'GET'),path,
                       json.dumps(values) if values is not None else None,headers)
    response = connection.getresponse()
    body = response.read().decode()
    data = json.loads(body) if response.getheader('Content-Type','').startswith('application/json') else body
    result = response.status,data,response.getheader('Set-Cookie')
    connection.close()
    return result


def login(server, username, password='test-password-123'):
    status,data,cookie = request(server,'/api/auth/login',{'username':username,'password':password})
    assert status==200,data
    cookie=cookie.split(';',1)[0]
    status,page,_=request(server,'/',cookie=cookie)
    token=re.search('name="sparktutor-token" content="([^"]+)"',page)[1]
    return {'cookie':cookie,'token':token,'user':data['user']}


def register(server, username):
    status,data,_=request(server,'/api/auth/register',{'username':username,'displayName':username,'password':'test-password-123','role':'admin'})
    assert status==201,data
    return login(server,username)


def rpc(server, client, method, params=None):
    return request(server,'/api/rpc',{'method':method,'params':params or {}},cookie=client['cookie'],token=client['token'])[:2]


def administrator(server):
    initial=json.loads((server.settings.data_dir/'accounts/initial-admin.json').read_text())
    client=login(server,**initial)
    status,_,_=request(server,'/api/auth/password',{'oldPassword':initial['password'],'newPassword':'new-admin-password-123'},cookie=client['cookie'],token=client['token'])
    assert status==200
    return login(server,'admin','new-admin-password-123')


def test_login_required_and_registration_cannot_select_admin_role(server):
    status,page,_=request(server,'/')
    assert status==200 and 'login-form' in page and '__API_TOKEN__' not in page
    assert request(server,'/api/rpc',{'method':'getLearningDashboard'})[0]==401
    client=register(server,'learner')
    assert client['user']['role']=='student'
    assert request(server,'/api/admin/users',cookie=client['cookie'])[0]==403
    status,_,cookie=request(server,'/api/auth/login',{'username':'learner','password':'test-password-123'})
    assert status==200 and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert request(server,'/api/auth/login',{'username':'learner','password':'wrong-password'})[0]==401


def test_checkins_are_account_private_idempotent_and_do_not_create_grades(server):
    first = register(server, 'checkin_first')
    second = register(server, 'checkin_second')
    assert rpc(server, first, 'getCheckInStatus')[1]['result']['totalDays'] == 0
    before = rpc(server, first, 'getDiagnosis')[1]['result']
    assert rpc(server, first, 'checkIn')[1]['result']['recorded'] is True
    assert rpc(server, first, 'checkIn')[1]['result']['recorded'] is False
    assert rpc(server, first, 'getCheckInStatus')[1]['result']['totalDays'] == 1
    assert rpc(server, second, 'getCheckInStatus')[1]['result']['totalDays'] == 0
    after = rpc(server, first, 'getDiagnosis')[1]['result']
    assert before['dimensions'] == after['dimensions']
    assert before['eligibleEventCount'] == after['eligibleEventCount']
    assert rpc(server, first, 'checkIn', {'day':'2026-01-01'})[0] == 400
    assert rpc(server, first, 'getCheckInStatus', {'month':'2026-13'})[0] == 400


@pytest.mark.parametrize('method', [
    'getLearningDataOverview', 'exportLearningSummary', 'backupLearningDatabase', 'clearLearningRecords',
])
def test_removed_data_management_rpc_is_unavailable_even_with_a_valid_login(server, method):
    client = register(server, 'no_data_export')
    status, body = rpc(server, client, method, {'confirmation':'clear-learning-records'})
    assert status == 404
    assert 'result' not in body
    assert 'databasePath' not in str(body) and 'base64' not in str(body)
    assert rpc(server, client, 'getLearningDashboard')[0] == 200


def test_accounts_with_the_same_tab_id_cannot_read_or_clear_each_others_records(server):
    first=register(server,'first_user');second=register(server,'second_user')
    params={'courseId':'learning_spark','lessonIdx':0,'depth':'beginner'}
    _,loaded=rpc(server,first,'loadLesson',params)
    step=loaded['result']['step']['id']
    assert rpc(server,first,'saveDraft',{'stepId':step,'code':'# private to first'})[1]['result']['saved']
    rpc(server,first,'advance',{'code':'# private to first'})
    _,other=rpc(server,second,'loadLesson',params)
    assert other['result']['currentIndex']==0
    assert not other['result']['draftFound']
    assert '# private to first' not in json.dumps(other)
    assert request(server,'/api/rpc',{'method':'getLearningDashboard'},cookie=second['cookie'],token=first['token'])[0]==403
    assert rpc(server,second,'clearLearningRecords',{'confirmation':'clear-learning-records'})[0] == 404
    rpc(server,first,'goBack')
    _,restored=rpc(server,first,'loadLesson',params)
    assert restored['result']['restoredCode']=='# private to first'
    assert (server.settings.data_dir/'users'/first['user']['id']).is_dir()
    assert (server.settings.data_dir/'users'/second['user']['id']).is_dir()


def test_admin_must_change_initial_password_before_management_and_passwords_revoke_sessions(server):
    initial=json.loads((server.settings.data_dir/'accounts/initial-admin.json').read_text())
    client=login(server,**initial)
    assert client['user']['mustChangePassword']
    assert request(server,'/api/admin/users',cookie=client['cookie'])[0]==403
    assert rpc(server,client,'getLearningDashboard')[0]==403
    assert request(server,'/api/auth/password',{'oldPassword':'bad','newPassword':'new-admin-password-123'},cookie=client['cookie'],token=client['token'])[0]==403
    assert request(server,'/api/auth/password',{'oldPassword':initial['password'],'newPassword':'new-admin-password-123'},cookie=client['cookie'],token=client['token'])[0]==200
    assert rpc(server,client,'getLearningDashboard')[0]==401


def test_admin_can_create_disable_and_reset_without_promoting_existing_students(server):
    admin=administrator(server);student=register(server,'managed_student')
    auth={'cookie':admin['cookie'],'token':admin['token']}
    status,data,_=request(server,'/api/admin/users',cookie=admin['cookie'])
    assert status==200 and len(data['users'])==2
    assert all('password_hash' not in row and 'salt' not in row for row in data['users'])
    assert request(server,'/api/admin/status',{'userId':student['user']['id'],'enabled':False},**auth)[0]==200
    assert rpc(server,student,'getLearningDashboard')[0]==401
    assert request(server,'/api/auth/login',{'username':'managed_student','password':'test-password-123'})[0]==401
    request(server,'/api/admin/status',{'userId':student['user']['id'],'enabled':True},**auth)
    student=login(server,'managed_student')
    assert request(server,'/api/admin/password',{'userId':student['user']['id'],'newPassword':'reset-password-123'},**auth)[0]==200
    assert rpc(server,student,'getLearningDashboard')[0]==401
    replacement=login(server,'managed_student','reset-password-123')
    assert replacement['user']['mustChangePassword']
    assert request(server,'/api/admin/status',{'userId':admin['user']['id'],'enabled':False},**auth)[0]==400


def test_multi_user_execution_cannot_be_enabled_and_owner_model_key_is_not_inherited(server,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-owner-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY','private-owner-anthropic')
    client=register(server,'restricted_user')
    status,data=rpc(server,client,'webSettings')
    assert status==200 and data['result']['mode']=='dry_run'
    assert not data['result']['hasApiKey'] and not data['result']['hasAnthropicApiKey']
    for method in ('configureWebSettings','setExecutionMode'):
        assert rpc(server,client,method,{'mode':'local'})[0]==400


def test_accounts_and_sessions_survive_store_reopen_and_logout_revokes(server):
    client=register(server,'persistent_user')
    reopened=AccountStore(server.settings.data_dir/'accounts')
    assert reopened.authenticate(client['cookie'])['id']==client['user']['id']
    assert request(server,'/api/auth/logout',{},cookie=client['cookie'],token=client['token'])[0]==200
    assert reopened.authenticate(client['cookie']) is None
