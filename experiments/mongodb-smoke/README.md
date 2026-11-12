# Isolated MongoDB replica-set smoke harness

Run **Actions → Isolated MongoDB replica-set smoke tests → Run workflow** only
after reviewing the selected branch. There are no push/PR triggers, inputs,
repository secrets, production environment files, image publishing or deployment.
This phase has not been executed against MongoDB; offline checks are not a live pass.

The workflow uses Ubuntu 24.04 AMD64, a 20-minute job limit and a resource preflight
of 4 GiB available memory / 10 GiB disk. MongoDB 8.0.38-noble is pinned to its
Linux AMD64 manifest `sha256:9b73c38e39783bd21335f603a9f08be60fe385f5c28933ea32bb4a9fa1fd6d23`.
Docker Hub registry manifest bytes were independently SHA-256 checked during
preflight review (2026-11-12). The parent image-index digest was
`sha256:76a93cc399d05709baeea4e55385749657aa0085ad5b497c851380778eccea2c`.
The tester reuses the repository's digest-pinned Python 3.13.15 base and pins
PyMongo 4.18.1 and dnspython 2.8.0. Only a small allowlisted build context is sent.
Python package versions are pinned, but wheel hashes are not locked in this
isolated harness. Public registry availability remains a build prerequisite.

Each run/attempt gets its own database, replica-set identity, labeled containers
and image tag. MongoDB data lives only in disposable tmpfs. MongoDB has networking
disabled and listens on loopback; the tester shares that exact network namespace.
Neither runtime container has an external route or host-published port. Public
image/package downloads occur only before runtime. There are no external service
tests or provider credentials.

A temporary CA verifies the server's localhost SAN. Authentication uses a random
keyfile and generated passwords. The localhost exception initializes the empty
replica set and creates a bootstrap administrator; it then creates a readWrite user
restricted to the uniquely named test database. The test container receives only
that scoped credential and CA, not the bootstrap password or server private key.
Secret files remain within a private runner-temporary directory and are not
uploaded as artifacts. The disposable hosted runner discards them after the job.
Both mongosh invocations use writable /tmp as HOME. The generated certificate is
checked for server purpose and both localhost/IP identities before startup.
TLS client certificates are not required: SCRAM credentials authenticate clients;
server certificate and hostname validation remain mandatory.

Before schema/data writes, the tester rejects unexpected database names, replica
identities, advertised members and application roles. The connection host is
hardcoded localhost, direct discovery is enabled and TLS verification is never
disabled. Schema helpers are invoked explicitly; GRIDFS_UPLOAD_ENABLED is not
changed and no application lifespan is started.

Smoke coverage: TLS-authenticated ping, writable primary/replica identity,
acknowledged majority write, snapshot transaction commit, explicit rollback,
required schema setup/verification and repeat-setup index equality. The actual
production schema module is tested, not a duplicate manifest.

Only one voting member is used: majority acknowledgement here means one member.
This does **not** test replicated durability, failover, concurrent admission,
GridFS streaming, interrupted writes, lost acknowledgements, recovery/fencing,
Atlas permissions or deployment suitability. No fault injection is performed.

On failure, fixed-stage output and a sanitized exception class identify the
boundary. No raw database exceptions, container logs, environment dumps or secret
artifacts are printed. Do not weaken isolation or index checks to obtain a pass.
The always-run cleanup validates resource ownership labels before removing only
these containers and the unique tester image; there is no global prune. For the
shared network namespace, cleanup removes the tester before MongoDB. Base images
are preserved. Temporary credentials are not uploaded; runner disposal is their
final cleanup boundary, including interruption cases. As with
any hosted job, runner loss can prevent cleanup; resources are runner-local.

Offline validation from repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q experiments/mongodb-smoke/test_contract.py backend/tests/test_storage_schema.py backend/tests/test_storage_transaction_errors.py backend/tests/test_storage_intents.py backend/tests/test_storage_reconciliation.py
```

Bootstrap follows MongoDB's documented localhost-exception procedure:
https://www.mongodb.com/docs/manual/core/localhost-exception/
Official image version metadata:
https://github.com/docker-library/official-images/blob/master/library/mongo
