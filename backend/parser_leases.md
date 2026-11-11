# Trusted parser leases on the application PDF lane

ParserLeases(storage_workers, cpu_workers) uses the existing application-owned
CPUWorkers instance. It never constructs or closes a second PDFSupervisor.
Both local session.run_pdf and materialized leases dispatch through
CPUWorkers._submit_pdf. Existing document_processing continues to use the original
local path; no GridFS route, client or storage configuration is activated.

Before materialization, parse registers a DOCUMENT/CPU PDF processing session and
an owned job. The session retains that job and its lease until cleanup succeeds.
The existing admission limits apply even across multiple ParserLeases instances.
Production GridFS processing passes its already-admitted PDF session explicitly.
The boundary verifies the application owner and retains that session through
cleanup, without closing the borrowed session or acquiring another CPU slot.
The request's processing context remains responsible for publication and closure.
A shielded task owns materialization, dispatch, completion and cleanup independently
of the request. Cancellation before admission does nothing; cancellation during
materialization waits for the worker and cleans the resulting lease without parser
dispatch. After dispatch, cancellation never authorizes file deletion.

The trusted CPU adapter supplies the completion future, derived only from the
existing supervisor's native future. That signal follows child termination,
reaping and IPC cleanup, including confirmed crashes. SubmissionRejected proves
no dispatch. Unknown dispatch/completion retains ownership and capacity. Immediate
completion cannot release capacity before cleanup. Cleanup errors retain the job,
file and admission slot; no automatic deletion or retry is introduced.

ParserLeases.aclose seals and drains its registry; it does not close CPUWorkers.
The existing application shutdown drains AdmissionService before CPUWorkers and
then database resources. The retained session makes this ordering include lease
cleanup without a new lifespan hook. Optional storage lifecycle integration must
seal parser admissions and drain leases before closing storage workers/client.
That optional lifecycle and GridFS route activation remain future work.

Unresolved native completion, materialization or cleanup can deliberately prevent
drain indefinitely. Forced process termination does not prove safe cleanup.
Diagnostics expose stage names only. No restart sweep or orphan deletion exists.
Callers must transfer leases exclusively and must not close or reuse them.

Offline tests use real CPU/admission ownership and a simulated supervisor, plus
existing real PDF subprocess regressions. They do not establish Atlas integration,
restart recovery, or production GridFS readiness.
