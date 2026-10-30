// Guards the design system: no raw colors outside the token file, no hand-built alerts.
// Run with `npm run check:design`. Logo.tsx is the brand mark and is exempt.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const EXEMPT = ['app/styles/tokens.css', 'components/Logo.tsx', 'app/icon.svg'];
const SKIP_DIRS = new Set(['node_modules', '.next', 'scripts']);
const RAW_COLOR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;
const HAND_ALERT = /className="alert[ "]/;

const files = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(tsx?|css)$/.test(name)) files.push(path);
  }
};
['app', 'components', 'lib'].forEach((d) => walk(join(ROOT, d)));

const problems = [];
for (const file of files) {
  const rel = relative(ROOT, file);
  if (EXEMPT.includes(rel) || rel.startsWith('components/ui/')) continue;
  readFileSync(file, 'utf8')
    .split('\n')
    .forEach((line, i) => {
      if (RAW_COLOR.test(line)) problems.push(`${rel}:${i + 1} raw color, use a token from app/styles/tokens.css`);
      if (rel.endsWith('.tsx') && HAND_ALERT.test(line))
        problems.push(`${rel}:${i + 1} hand-built alert, use <Alert> from components/ui`);
    });
}

if (problems.length) {
  console.error(problems.join('\n') + `\n\n${problems.length} design-system violation(s).`);
  process.exit(1);
}
console.log('Design system check passed.');
