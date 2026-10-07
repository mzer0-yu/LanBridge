const {spawnSync} = require('node:child_process');
const path = require('node:path');

const checks = [
  'website-management-check.js',
  'login-method-check.js',
  'navigation-layout-check.js',
  'refresh-feedback-check.js',
  'temporary-access-check.js',
  'audit-size-check.js',
  'layout-security-check.js',
  'disclosure-style-check.js',
  'auth-bootstrap-check.js',
  'public-client-check.js',
  'client-disabled-check.js',
  'content-layout-check.js',
];
for (const name of checks) {
  const result = spawnSync(process.execPath, [path.join(__dirname, name)], {stdio:'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
console.log(`PASS: ${checks.length} isolated browser checks`);
