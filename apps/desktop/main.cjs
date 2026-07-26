const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const net = require('net');
const path = require('path');

const repositoryRoot = path.resolve(__dirname, '../..');
let serverUrl;
let engineProcess;

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

function waitForEngine(attempts = 50) {
  return new Promise((resolve, reject) => {
    const probe = () => {
      const request = http.get(`${serverUrl}/api/health`, (response) => {
        response.resume();
        response.statusCode === 200 ? resolve() : retry();
      });
      request.on('error', retry);
    };
    const retry = () => {
      if (attempts-- <= 0) reject(new Error('本地 ISO 引擎未能启动。'));
      else setTimeout(probe, 120);
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
    stdio: 'ignore',
  });
  engineProcess.on('error', async (error) => {
    await dialog.showMessageBox({ type: 'error', message: `无法启动 Python 引擎：${error.message}` });
    app.quit();
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
