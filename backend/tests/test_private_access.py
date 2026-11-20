import os
from unittest.mock import patch
import test_auth as helpers
from config import load_private_access_enabled, ConfigurationError
from auth import require_account_access
from fastapi import HTTPException

class PrivateAccessTests(helpers.AuthTests):
    async def test_private_registration_rejected_before_write(self):
        with patch('auth.load_private_access_enabled',return_value=True):
            status,body,_=await helpers.request('POST','/api/auth/register',{'name':'Test','email':'test@example.com','password':'test-password'})
        self.assertEqual(status,403)
        self.collection.find_one.assert_not_awaited()

    async def test_private_login_and_old_token_revocation(self):
        token=self.signed()
        with patch('auth.load_private_access_enabled',return_value=True):
            for flags,expected in (({},403),({'approved':True,'enabled':False},403),
                                   ({'approved':True,'enabled':True},200),({'approved':False,'enabled':True},403)):
                self.user.pop('approved',None); self.user.pop('enabled',None)
                self.user.update(flags)
                self.assertEqual((await self.login())[0],expected)
                self.assertEqual((await helpers.request('GET','/api/auth/me',authorization='Bearer '+token))[0],expected)

    async def test_policy_default_and_fail_closed(self):
        with patch('config.dotenv_values',return_value={}), patch.dict(os.environ,{},clear=True):
            self.assertFalse(load_private_access_enabled())
        with patch('auth.load_private_access_enabled',return_value=True):
            status,body,_=await helpers.request('GET','/api/auth/policy')
            self.assertEqual(status,200)
            self.assertFalse(body['public_registration'])
            for value in (1,'true',None):
                with self.assertRaises(HTTPException): require_account_access({'approved':value,'enabled':True})
        with patch('auth.load_private_access_enabled',side_effect=ConfigurationError('private')):
            self.assertEqual((await helpers.request('GET','/api/auth/policy'))[0],503)

class OperatorTests(helpers.unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_failure_never_creates_client(self):
        from operator_accounts import run
        from types import SimpleNamespace
        with patch('operator_accounts.load_settings',return_value=SimpleNamespace(mongodb_db_name='isolated')), \
             patch('builtins.input',return_value='wrong'), patch('operator_accounts.AsyncMongoClient') as client:
            with self.assertRaises(ValueError): await run('approve','0'*24)
            client.assert_not_called()

    async def test_approval_scoped_to_immutable_id_and_client_closed(self):
        from operator_accounts import run
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from bson import ObjectId
        users=SimpleNamespace(index_information=AsyncMock(return_value={'users_email_unique':{'key':[('email',1)],'unique':True}}),
                              update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
        client=SimpleNamespace(get_database=Mock(return_value=SimpleNamespace(users=users)),close=AsyncMock())
        identifier=ObjectId()
        with patch('operator_accounts.load_settings',return_value=SimpleNamespace(mongodb_db_name='isolated',mongodb_uri='mock')), \
             patch('builtins.input',return_value='isolated'), patch('operator_accounts.AsyncMongoClient',return_value=client):
            await run('approve',str(identifier))
        users.update_one.assert_awaited_once_with({'_id':identifier},{'$set':{'approved':True,'enabled':True}})
        client.close.assert_awaited_once()
