const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const repositoryRoot = path.resolve(__dirname, '../..');
let serverUrl;
let engineProcess;
let engineExitMessage = '';

function writeEngineLog(message) {
  const timestamp = new Date().toISOString();
  fs.appendFileSync(path.join(app.getPath('userData'), 'engine-startup.log'), `[${timestamp}] ${message}\n`);
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

function waitForEngine(timeoutMs = 36000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const probe = () => {
      let retried = false;
      const retry = () => {
        if (retried) return;
        retried = true;
        if (Date.now() >= deadline) {
          reject(new Error(engineExitMessage || `本地 ISO 引擎在 ${timeoutMs / 1000} 秒内未能启动。`));
        } else {
          setTimeout(probe, 200);
        }
      };
      const request = http.get(`${serverUrl}/api/health`, (response) => {
        response.resume();
        response.statusCode === 200 ? resolve() : retry();
      });
      request.setTimeout(1000, () => request.destroy());
      request.on('error', retry);
    };
    probe();
  });
}

async function openWorkspace() {
  const port = await findFreePort();
  serverUrl = `http://127.0.0.1:${port}`;
  const enginePath = app.isPackaged
    ? path.join(process.resourcesPath, 'engine', 'piping-iso-engine')
    : 'python3';
  const engineArgs = app.isPackaged
    ? ['--data-dir', app.getPath('userData'), '--port', String(port)]
    : ['app_server.py', '--data-dir', app.getPath('userData'), '--port', String(port)];
  engineProcess = spawn(enginePath, engineArgs, {
    cwd: app.isPackaged ? path.dirname(enginePath) : repositoryRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  writeEngineLog(`Starting ${enginePath} ${engineArgs.join(' ')}`);
  engineProcess.stdout.on('data', (chunk) => writeEngineLog(`stdout: ${chunk.toString().trim()}`));
  engineProcess.stderr.on('data', (chunk) => writeEngineLog(`stderr: ${chunk.toString().trim()}`));
  engineProcess.on('error', async (error) => {
    engineExitMessage = `无法启动本地 ISO 引擎：${error.message}`;
    writeEngineLog(engineExitMessage);
    await dialog.showMessageBox({ type: 'error', message: `无法启动 Python 引擎：${error.message}` });
    app.quit();
  });
  engineProcess.on('exit', (code, signal) => {
    engineExitMessage = `本地 ISO 引擎已退出（code=${code}, signal=${signal || 'none'}）。请查看 engine-startup.log。`;
    writeEngineLog(engineExitMessage);
  });
  try {
    await waitForEngine();
    const window = new BrowserWindow({
      width: 1500,
      height: 980,
      minWidth: 1050,
      minHeight: 720,
      backgroundColor: '#f4f0e5',
      webPreferences: { contextIsolation: true, nodeIntegration: false },
    });
    await window.loadURL(serverUrl);
  } catch (error) {
    await dialog.showMessageBox({ type: 'error', message: error.message });
    app.quit();
  }
}

app.whenReady().then(openWorkspace);
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => {
  if (engineProcess && !engineProcess.killed) engineProcess.kill();
});
