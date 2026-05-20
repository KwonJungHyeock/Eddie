// preload.js — Electron 보안 브릿지
// main 프로세스가 읽은 상태를 HUD(렌더러)로 안전하게 전달

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('eddieAPI', {
  // main 으로부터 상태 업데이트 수신
  onStateUpdate: (callback) => {
    ipcRenderer.on('eddie-state', (_event, data) => callback(data));
  },
});
