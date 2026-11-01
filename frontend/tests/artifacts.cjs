const { mkdtempSync, mkdirSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');

// Each standalone run defaults to an isolated directory outside the repository.
const directory = process.env.FRONTEND_TEST_ARTIFACT_DIR
  ? resolve(process.env.FRONTEND_TEST_ARTIFACT_DIR)
  : mkdtempSync(join(tmpdir(), 'deepdocs-browser-'));
mkdirSync(directory, { recursive: true });
exports.artifactPath = name => join(directory, name);
