// Run the installed VSIX, not an extension development override or fake Shape CLI.
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = process.env.SHAPE_DOCS_REPO;
const {runTests} = require(path.join(root, 'editors/vscode/node_modules/@vscode/test-electron'));
const workspace = path.join(process.cwd(), 'editor-workspace');
fs.mkdirSync(workspace, {recursive: true});
const git = (...args) => execFileSync('git', args, {cwd: workspace, stdio: 'pipe'});
git('init', '-q');
git('config', 'user.name', 'Documentation fixture');
git('config', 'user.email', 'fixture@example.test');
const python = process.env.SHAPE_DOCS_PYTHON || 'python';
const make = rows => execFileSync(python, ['-c', `import shape, pyarrow as pa; shape.save(shape.profile(pa.table({'id': list(range(${rows}))}), name='editor-demo'), 'demo.shape')`], {cwd: workspace, stdio: 'pipe'});
make(3);
git('add', 'demo.shape');
git('commit', '-qm', 'Synthetic editor fixture');
make(4);
runTests({
  vscodeExecutablePath: process.env.SHAPE_DOCS_VSCODE_EXECUTABLE,
  extensionTestsPath: path.join(root, 'scripts/docs_vscode_suite.cjs'),
  launchArgs: [workspace, '--user-data-dir', process.env.SHAPE_DOCS_VSCODE_USER,
    '--extensions-dir', process.env.SHAPE_DOCS_VSCODE_EXTENSIONS, '--disable-workspace-trust',
    '--skip-welcome', '--disable-updates', '--no-sandbox'],
  extensionTestsEnv: {SHAPE_DOCS_EDITOR_FILE: path.join(workspace, 'demo.shape')},
}).catch(error => { console.error(error); process.exitCode = 1; });
