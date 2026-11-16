"""Real adapter/transaction lifecycle; only called after smoke isolation guards."""
import hashlib
import io
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from bson import ObjectId
from gridfs_storage import GridFSPDFStorage
from storage_contract import DeletionOutcome
from storage_intents import StorageIntents, IntentIdentity, QuotaPolicy, QuotaExceeded, StorageTransactionError
from storage_reconciliation import ReconciliationInspector
from pdf_processing import extract_chunks
import pymupdf


def fixtures():
    result=[]
    for text in ('Synthetic Aurora is coordinated by Alex Morgan.', 'Synthetic Finch seeds are stored at six degrees Celsius.'):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40,40),text)
            result.append((pdf.tobytes(),text))
    return result


def lifecycle(database, reconnect):
    owner, base=ObjectId(), ObjectId()
    database.knowledge_bases.insert_one({'_id':base,'owner_id':owner,'_document_ids':[], '_document_revision':0})
    policy=QuotaPolicy(global_documents=2,user_documents=2)  # isolated test policy only
    ledger=StorageIntents(database,policy)
    records=[]
    with tempfile.TemporaryDirectory() as root:
        storage=GridFSPDFStorage(database,owner_id=owner,knowledge_base_id=base,temp_root=root)
        for data,text in fixtures():
            identity=IntentIdentity.new(ObjectId(),owner,base)
            digest=hashlib.sha256(data).hexdigest()
            ledger.create(identity,len(data),digest,'smoke_worker')
            ledger.reserve(identity)
            ledger.transition(identity,'prepared','write_dispatched','smoke_worker')
            stored=storage.save(SimpleNamespace(filename='synthetic.pdf',content_type='application/pdf',file=io.BytesIO(data)),identity.document_id,5*1024**2)
            doc={**stored,'_id':identity.document_id,'owner_id':owner,'knowledge_base_id':base,
                 'storage_operation_id':identity.operation_id,'storage_checksum':digest}
            lease=storage.materialize(doc)
            try:
                assert hashlib.sha256(lease.path.read_bytes()).hexdigest()==digest
                pages,chunks=extract_chunks(lease.path,SimpleNamespace(max_source_bytes=5*1024**2,max_pages=2,
                    max_text_characters=10000,chunk_target=1000,chunk_overlap=150,max_chunks=10))
                assert pages==1 and text in chunks[0]['text']
            finally: lease.close()
            assert lease.cleanup_complete
            ledger.transition(identity,'write_dispatched','file_confirmed','smoke_worker')
            database.documents.insert_one(doc)
            database.knowledge_bases.update_one({'_id':base},{'$push':{'_document_ids':identity.document_id}})
            ledger.transition(identity,'file_confirmed','published','smoke_worker')
            ledger.quota_transition(identity,'commit')
            records.append((identity,doc))
        print('PASS: two GridFS uploads, checksum readback and original PDF extraction/chunking.',flush=True)
        database.client.close()
        client,database=reconnect()  # repeats TLS, replica and scoped-role verification
        try:
            storage=GridFSPDFStorage(database,owner_id=owner,knowledge_base_id=base,temp_root=root)
            ledger=StorageIntents(database,policy)
            for identity,doc in records:
                assert database.documents.find_one({'_id':identity.document_id}) is not None
                lease=storage.materialize(doc)
                try: assert hashlib.sha256(lease.path.read_bytes()).hexdigest()==doc['storage_checksum']
                finally: lease.close()
                assert ReconciliationInspector(database).inspect(identity).classification=='confirmed_consistent'
            print('PASS: client recreation persistence and consistent snapshot inspection.',flush=True)
            # Remove one exact original and finalize metadata/quota atomically.
            def delete(identity,doc):
                deletion=IntentIdentity.new(identity.document_id,owner,base,'delete')
                ledger.create(deletion,doc['file_size'],doc['storage_checksum'],'smoke_worker',identity.operation_id)
                database.documents.update_one({'_id':identity.document_id},{'$set':{'_operation':deletion.operation_id}})
                ledger.transition(deletion,'prepared','delete_dispatched','smoke_worker')
                # No vectors were ever created: this boundary is intentionally vacuous.
                ledger.transition(deletion,'delete_dispatched','vectors_deleted','smoke_worker')
                assert storage.delete(doc)==DeletionOutcome.DELETED
                ledger.transition(deletion,'vectors_deleted','file_deleted','smoke_worker')
                ledger.finalize_deletion(deletion,'smoke_worker')
                assert database.documents.count_documents({'_id':identity.document_id})==0
                assert database['pdf_blobs.files'].count_documents({'_id':identity.document_id})==0
                assert database['pdf_blobs.chunks'].count_documents({'files_id':identity.document_id})==0
            delete(*records[0])
            # Two concurrent reservations compete for exactly one remaining slot.
            contenders=[IntentIdentity.new(ObjectId(),owner,base) for _ in range(2)]
            for i in contenders: ledger.create(i,1,'a'*64,'smoke_worker')
            barrier=threading.Barrier(2)
            def reserve(i):
                barrier.wait(timeout=5)
                try: ledger.reserve(i); return 'reserved'
                except QuotaExceeded: return 'quota_exhausted'
                except StorageTransactionError as e:
                    if e.category not in ('transaction_write_conflict','duplicate_identity_conflict'): raise
                    return e.category
            with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(reserve,contenders))
            assert outcomes.count('reserved')==1
            row=database.storage_quota_ledger.find_one({'_id':'pdf_payload_v1'})
            assert len(row['entries'])==2 and sum(e['bytes'] for e in row['entries'])==records[1][1]['file_size']+1
            winner=contenders[outcomes.index('reserved')]
            ledger.transition(winner,'prepared','write_dispatched','smoke_worker')
            ledger.mark_uncertain(winner,'write_dispatched')  # staged interruption, no remote file mutation
            inspection=ReconciliationInspector(database).inspect(winner)
            assert inspection.classification=='ambiguous_manual_review' and not inspection.mutation_authorized
            delete(*records[1])
            row=database.storage_quota_ledger.find_one({'_id':'pdf_payload_v1'})
            assert len(row['entries'])==1 and row['entries'][0]['operation_id']==winner.operation_id and row['entries'][0]['state']=='reserved'
            assert database['pdf_blobs.files'].count_documents({})==0 and database['pdf_blobs.chunks'].count_documents({})==0
            print('PASS: concurrent admission='+','.join(outcomes)+'; exact deletion; only uncertain one-byte reservation retained.',flush=True)
            print('MANUAL REVIEW: staged interrupted intent retained; no recovery mutation authorized. Disposable database teardown removes it.',flush=True)
        finally: client.close()
