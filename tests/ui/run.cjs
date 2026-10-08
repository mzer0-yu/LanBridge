const {spawnSync} = require('node:child_process');
const path = require('node:path');

const checks = [
  'autofill-check.js',
  'website-management-check.js',
  'multi-zone-site-check.js',
  'default-zone-removal-check.js',
  'background-save-check.js',
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
  'gateway-recovery-check.js',
  'visitor-verification-check.js',
  'admin-restart-check.js',
  'domain-onboarding-check.js',
  'aliyun-connected-domains-check.js',
  'domain-task-layout-check.js',
  'oauth-renewal-check.js',
  'permission-recovery-check.js',
  'browser-switch-check.js',
  'auto-tunnel-setup-check.js',
  'auth-failure-time-check.js',
  'update-status-check.js',
];
for (const name of checks) {
  const result = spawnSync(process.execPath, [path.join(__dirname, name)], {stdio:'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
console.log(`PASS: ${checks.length} isolated browser checks`);
