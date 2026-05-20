// EDDIE Electron 메인 프로세스 (Phase 3 Step 3-3: GUI 음성 연동)
// HUD HTML 로드 + eddie_state.json 폴링 → HUD로 상태 전달

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

// 상태 파일 경로 = 프로젝트 루트 (state_bus.py 와 동일 위치)
// main.js 는 electron/ 안에 있으므로 한 단계 상위가 프로젝트 루트
const STATE_FILE = path.join(__dirname, '..', 'eddie_state.json');

let win = null;
let pollTimer = null;
let lastState = null;

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

  const hudPath = path.join(__dirname, '..', 'docs', 'EDDIE-DSN-001_HUD_prototype_v0.1.html');
  win.loadFile(hudPath);

  win.webContents.on('did-finish-load', () => {
    startPolling();
  });

  win.on('closed', () => {
    stopPolling();
    win = null;
  });
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    fs.readFile(STATE_FILE, 'utf-8', (err, data) => {
      if (err) return;
      try {
        const parsed = JSON.parse(data);
        const state = parsed.state;
        if (state && state !== lastState) {
          lastState = state;
          if (win && !win.isDestroyed()) {
            win.webContents.send('eddie-state', parsed);
          }
        }
      } catch (e) {
        // JSON 파싱 실패 (쓰기 도중) 무시
      }
    });
  }, 200);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopPolling();
  if (process.platform !== 'darwin') app.quit();
});
