"""Operator-only account provisioning. Never imported by application routes."""
import argparse
import asyncio
import getpass
from bson import ObjectId
from pymongo import AsyncMongoClient
from config import load_settings
from users import create_user
from user_schemas import RegistrationRequest

async def run(action, identifier):
    settings=load_settings()
    if input('Type the intended database name to authorize this operation: ') != settings.mongodb_db_name:
        raise ValueError('Confirmation failed')
    registration=None
    if action=='create':
        name=input('Name: ')
        email=input('Email: ')
        password=getpass.getpass('Password: ')
        if password != getpass.getpass('Confirm password: '): raise ValueError('Passwords differ')
        registration=RegistrationRequest(name=name,email=email,password=password)
    else:
        identifier=ObjectId(identifier)
    client=AsyncMongoClient(settings.mongodb_uri,tls=True,tlsAllowInvalidCertificates=False,
        tlsAllowInvalidHostnames=False,serverSelectionTimeoutMS=5000,connectTimeoutMS=5000,
        socketTimeoutMS=5000,timeoutMS=5000,retryWrites=False)
    try:
        db=client.get_database(settings.mongodb_db_name)
        # Require the existing unique-email index; never alter schema here.
        indexes=await db.users.index_information()
        idx=indexes.get('users_email_unique',{})
        if idx.get('key') != [('email',1)] or idx.get('unique') is not True:
            raise ValueError('Required index unavailable')
        if registration:
            user=await create_user(db,registration)
            identifier=ObjectId(user.id)
            print('Created pending account ID:',user.id)
            print('Approve separately after verifying this identity. No approval was granted.')
            return
        flags={'approve':{'approved':True,'enabled':True},'revoke':{'approved':False},'disable':{'enabled':False}}[action]
        result=await db.users.update_one({'_id':identifier},{'$set':flags})
        if result.matched_count != 1: raise ValueError('Account not found')
        print('Account policy updated.')
    finally:
        await client.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('create','approve','revoke','disable'))
    parser.add_argument('--id')
    args=parser.parse_args()
    if args.action!='create' and not args.id: parser.error('--id is required')
    try: asyncio.run(run(args.action,args.id))
    except Exception:
        print('Operation unconfirmed. Inspect the intended account before retrying; no automatic retry was made.')
        raise SystemExit(1) from None
