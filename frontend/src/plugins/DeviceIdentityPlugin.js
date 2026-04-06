import { registerPlugin } from '@capacitor/core';

/**
 * DeviceIdentity — Capacitor JS bridge to the native Android DeviceIdentityPlugin.
 *
 * On a real H10 device (Capacitor native):
 *   → Java DeviceIdentityPlugin.getDeviceId() returns Settings.Secure.ANDROID_ID
 *
 * In browser / dev mode (web fallback):
 *   → Returns a stable random ID persisted in localStorage under `_dev_device_id`.
 *     This ID will never match a real ANDROID_ID, so the backend's device-binding
 *     check only enforces matching on sessions that were paired on a real device.
 */
const DeviceIdentity = registerPlugin('DeviceIdentity', {
  web: {
    async getDeviceId() {
      let devId = localStorage.getItem('_dev_device_id');
      if (!devId) {
        devId = 'browser-' + Math.random().toString(36).substring(2, 18);
        localStorage.setItem('_dev_device_id', devId);
      }
      return { deviceId: devId };
    },
  },
});

export { DeviceIdentity };
