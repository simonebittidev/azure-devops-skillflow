/**
 * SkillFlow — Node.js wrapper for the Python task.
 *
 * Azure DevOps requires a Node.js entry point. This wrapper:
 *  1. Locates the Python interpreter (python3 → python → py)
 *  2. Installs Python dependencies from requirements.txt
 *  3. Executes main.py, forwarding all environment variables
 *  4. Propagates the exit code back to the AzDO agent
 */

const { execFileSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const taskDir = __dirname;

// ---------------------------------------------------------------------------
// Find Python interpreter
// ---------------------------------------------------------------------------
function findPython() {
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    const result = spawnSync(cmd, ['--version'], { encoding: 'utf8' });
    if (result.status === 0) {
      return cmd;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// AzDO logging helpers
// ---------------------------------------------------------------------------
function logError(msg) {
  console.error(`##vso[task.logissue type=error]${msg}`);
}

function logSection(msg) {
  console.log(`##[section]${msg}`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const python = findPython();

if (!python) {
  logError('Python interpreter not found. Make sure Python 3 is installed on the agent.');
  logError('Tried: python3, python, py');
  process.exit(1);
}

// Install dependencies
const requirementsFile = path.join(taskDir, 'requirements.txt');
if (fs.existsSync(requirementsFile)) {
  logSection('Installing Python dependencies');
  const install = spawnSync(
    python,
    ['-m', 'pip', 'install', '-r', requirementsFile, '--quiet', '--disable-pip-version-check'],
    { stdio: 'inherit', encoding: 'utf8' }
  );
  if (install.status !== 0) {
    logError(`pip install failed with exit code ${install.status}`);
    process.exit(install.status || 1);
  }
}

// Run main.py
const mainScript = path.join(taskDir, 'main.py');
const run = spawnSync(
  python,
  [mainScript],
  { stdio: 'inherit', env: process.env }
);

process.exit(run.status || 0);
