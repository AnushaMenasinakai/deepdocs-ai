// Runs once through the localhost exception, inside the disposable mongod.
// Never print exceptions, command responses, credentials or connection strings.
try {
  const fs = require('fs');
  const c = JSON.parse(fs.readFileSync('/run/bootstrap.json', 'utf8'));
  if (!/^dd_smoke_[0-9]+_[0-9]+$/.test(c.database) || c.replica !== c.database) throw Error();
  const admin = db.getSiblingDB('admin');
  let reachable = false;
  for (let i = 0; i < 60; i++) {
    try { if (admin.runCommand({ping: 1}).ok === 1) { reachable = true; break; } } catch (_) {}
    sleep(1000);
  }
  if (!reachable) throw Error();
  if (admin.runCommand({replSetInitiate: {_id: c.replica, members: [{_id: 0, host: 'localhost:27017'}]}}).ok !== 1) throw Error();
  let primary = false;
  for (let i = 0; i < 60; i++) {
    const h = admin.runCommand({hello: 1});
    if (h.isWritablePrimary && h.setName === c.replica) { primary = true; break; }
    sleep(1000);
  }
  if (!primary) throw Error();
  admin.createUser({user: 'bootstrap', pwd: c.admin_password, roles: ['root']}, {w: 'majority', wtimeout: 5000});
  const authenticated = admin.auth('bootstrap', c.admin_password);
  if (authenticated !== 1 && authenticated?.ok !== 1) throw Error();
  db.getSiblingDB(c.database).createUser({user: 'smoke', pwd: c.password,
    roles: [{role: 'readWrite', db: c.database}]}, {w: 'majority', wtimeout: 5000});
  print('Replica set and scoped user initialized.');
} catch (_) { print('Isolated bootstrap failed.'); quit(1); }
