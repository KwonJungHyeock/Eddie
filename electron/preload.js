// preload.js — Electron 보안 브릿지
// 상태 수신(Python→HUD) + 명령 발행(HUD→Python)

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('eddieAPI', {
  // Python 상태 수신
  onStateUpdate: (callback) => {
    ipcRenderer.on('eddie-state', (_event, data) => callback(data));
  },
  // Python 데이터 수신 (센서 수치 패널 / 시리얼 플로터)
  onDataUpdate: (callback) => {
    ipcRenderer.on('eddie-data', (_event, data) => callback(data));
  },
  // HUD → Python 명령 발행 (스페이스바 녹음 등)
  sendCommand: (command) => {
    ipcRenderer.send('eddie-command', command);
  },
});
