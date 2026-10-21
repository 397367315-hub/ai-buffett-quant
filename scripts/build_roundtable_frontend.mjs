import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(process.cwd(), '..', 'integrations', 'trading-roundtable', 'frontend');
if (!existsSync(resolve(root, 'package-lock.json'))) {
  throw new Error(`交易圆桌前端缺少锁文件：${root}`);
}
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
execFileSync(npm, ['ci', '--ignore-scripts'], { cwd: root, stdio: 'inherit' });
execFileSync(npm, ['run', 'build'], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, VITE_ROUNDTABLE_API_BASE: process.env.VITE_ROUNDTABLE_API_BASE || 'https://ai-buffett-backend.onrender.com/api/v1/roundtable' },
});
