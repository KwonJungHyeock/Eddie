// EDDIE Electron 메인 프로세스 (Phase 3 Step 3-1)
// DSN-001 HUD 프로토타입 HTML 을 데스크톱 창으로 로드

const { app, BrowserWindow } = require('electron');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
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
    },
  });

  // DSN-001 HUD 프로토타입 로드
  // docs 폴더의 HTML 파일 경로 (실제 위치에 맞게 조정 필요)
  const hudPath = path.join(__dirname, '..', 'docs', 'EDDIE-DSN-001_HUD_prototype_v0.1.html');
  win.loadFile(hudPath);

  // 개발 중 콘솔 (필요 시 주석 해제)
  // win.webContents.openDevTools();
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
