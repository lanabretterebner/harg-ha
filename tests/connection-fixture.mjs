/** Synthetic lifecycle model of HA's Connection, not a socket/browser test.
 * HA frontend 20260930.3 pins home-assistant-js-websocket 9.7.1:
 * https://github.com/home-assistant/frontend/blob/20260930.3/package.json
 * https://github.com/home-assistant/home-assistant-js-websocket/blob/9.7.1/lib/connection.ts
 * Connection clears its socket before synchronously firing `disconnected`;
 * after replacing its socket it synchronously fires `ready`. Listeners receive
 * the Connection itself, not a DOM Event. These are the exercised semantics.
 * The frontend connection-mixin updates hass.connected from those callbacks.
 */
export function connectionFixture() {
  const listeners = new Map();
  return {
    connected: true,
    addEventListener(type, callback) {
      const values = listeners.get(type) || new Set();
      values.add(callback); listeners.set(type, values);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    disconnect() { this.connected = false; this.emit('disconnected'); },
    reconnect() { this.connected = true; this.emit('ready'); },
    emit(type) { for (const callback of [...(listeners.get(type) || [])]) callback(this); },
    listenerCount() { return [...listeners.values()].reduce((sum, values) => sum + values.size, 0); },
  };
}
