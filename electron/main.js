// EDDIE Electron 메인 (Phase 3-4 풀 통합)
// HUD 로드 + voice_chat 자식 프로세스 실행 + 상태 폴링 + 명령 전달

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const PROJECT_ROOT = path.join(__dirname, '..');
const STATE_FILE = path.join(PROJECT_ROOT, 'eddie_state.json');
const COMMAND_FILE = path.join(PROJECT_ROOT, 'eddie_command.json');

let win = null;
let pollTimer = null;
let lastState = null;
let voiceProc = null;
let cmdSeq = 0;

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#030610',
    title: 'EDDIE',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  const hudPath = path.join(PROJECT_ROOT, 'docs', 'EDDIE-DSN-001_HUD_prototype_v0.1.html');
  win.loadFile(hudPath);

  win.webContents.on('did-finish-load', () => {
    startPolling();
    startVoiceBackend();
  });

  win.on('closed', () => {
    stopPolling();
    stopVoiceBackend();
    win = null;
  });
}

// voice_chat.py 를 --gui 모드로 백그라운드 실행
function startVoiceBackend() {
  if (voiceProc) return;
  // venv 의 python 사용 (Windows)
  const pyExe = process.platform === 'win32'
    ? path.join(PROJECT_ROOT, 'venv', 'Scripts', 'python.exe')
    : path.join(PROJECT_ROOT, 'venv', 'bin', 'python');

  voiceProc = spawn(pyExe, ['voice_chat.py', '--gui'], {
    cwd: PROJECT_ROOT,
    stdio: 'ignore',  // 로그는 별도 콘솔 불필요 (HUD가 상태 표시)
  });

  voiceProc.on('error', (err) => {
    console.error('voice backend 실행 실패:', err);
  });
  voiceProc.on('exit', () => {
    voiceProc = null;
  });
}

function stopVoiceBackend() {
  // stop 명령 발행 후 프로세스 종료
  try {
    cmdSeq++;
    fs.writeFileSync(COMMAND_FILE, JSON.stringify({
      command: 'stop', seq: cmdSeq, detail: {}, ts: Date.now() / 1000,
    }), 'utf-8');
  } catch (e) {}
  if (voiceProc) {
    try { voiceProc.kill(); } catch (e) {}
    voiceProc = null;
  }
}

// HUD → Python 명령 (스페이스바 녹음)
ipcMain.on('eddie-command', (_event, command) => {
  try {
    // 기존 seq 읽어서 +1
    let prev = { seq: 0 };
    try { prev = JSON.parse(fs.readFileSync(COMMAND_FILE, 'utf-8')); } catch (e) {}
    cmdSeq = (prev.seq || 0) + 1;
    fs.writeFileSync(COMMAND_FILE, JSON.stringify({
      command: command, seq: cmdSeq, detail: { source: 'hud' }, ts: Date.now() / 1000,
    }), 'utf-8');
  } catch (e) {
    console.error('명령 발행 실패:', e);
  }
});

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    fs.readFile(STATE_FILE, 'utf-8', (err, data) => {
      if (err) return;
      try {
        const parsed = JSON.parse(data);
        if (parsed.state && parsed.state !== lastState) {
          lastState = parsed.state;
          if (win && !win.isDestroyed()) {
            win.webContents.send('eddie-state', parsed);
          }
        }
      } catch (e) {}
    });
  }, 200);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopPolling();
  stopVoiceBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  stopVoiceBackend();
});
