# Private operator-provisioned access

Set `PRIVATE_ACCESS_ENABLED=true` on the cloud backend before exposing it. Default
false preserves local registration and legacy accounts. Invalid values fail closed.
Private mode rejects POST /api/auth/register and requires exact boolean approved=true
and enabled=true at login and every authenticated request. Revoking approval or
 disabling an account blocks existing JWTs on their next authenticated request;
already-admitted work is not cancelled. JWT expiration and ownership checks remain.
Do not turn private mode off as a recovery workaround: legacy accounts would regain
access. No public request accepts approval flags and no admin endpoint is added.

The frontend reads GET /api/auth/policy (no secrets, no-store). Until confirmed
public, it hides signup links and the registration form. Fetch failure hides signup.
The backend remains authoritative, including direct API calls and stale browser tabs.
No frontend build-time approval flag is needed.

## Operator procedure (not executed by tests or startup)

Run only from a trusted operator terminal using the intended server-side MongoDB
configuration. Existing normal startup must have initialized the unique email index.
Never put passwords in command arguments, source files or logs.

```powershell
.\backend\.venv\Scripts\python.exe backend/operator_accounts.py create
# Enter database confirmation, name/email and a hidden password twice.
# Creation is pending, never automatically approved. Verify the returned user ID.
.\backend\.venv\Scripts\python.exe backend/operator_accounts.py approve --id <verified-user-id>
.\backend\.venv\Scripts\python.exe backend/operator_accounts.py revoke --id <verified-user-id>
.\backend\.venv\Scripts\python.exe backend/operator_accounts.py disable --id <verified-user-id>
```

Each invocation requires typing the intended database name. Approval explicitly
sets both approved and enabled true; use it only for an identity verified outside
this application. Existing users can be approved by their verified immutable ID.
Creation reuses RegistrationRequest, create_user and the existing Argon2 hashing.
No duplicate-account overwrite, automatic schema creation, or remote retry occurs.
On an unconfirmed write, inspect the account before another attempt. Provisioning
credentials must remain operator-only; normal application users cannot invoke it.

Offline tests do not approve production writes. Private access does not replace
persistent provider budgets, storage monitoring or secure secret management.
