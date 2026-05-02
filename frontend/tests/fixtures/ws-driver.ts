import type { Page } from '@playwright/test'

export class WsDriver {
  private actions: Array<Record<string, any>> = []
  private page: Page | null = null

  async install(page: Page, _bookId: string) {
    this.page = page
    this.actions = []
    await page.exposeFunction('__recordWsAction', (action: any) => {
      this.actions.push(action)
    })
    await page.addInitScript(`
      window.__wsDriverSocket = null;
      window.WebSocket = class MockWS {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          this._onmessage = null;
          this.binaryType = '';
          window.__wsDriverSocket = this;
          setTimeout(() => {
            if (this.onopen) this.onopen({ type: 'open' });
          }, 0);
        }
        set onmessage(fn) { this._onmessage = fn; }
        get onmessage() { return this._onmessage; }
        send(data) {
          try { window.__recordWsAction(JSON.parse(data)); } catch (e) { /* ignore */ }
        }
        close() {}
        addEventListener() {}
        removeEventListener() {}
      };
    `)
  }

  actionsOf(action: string): Array<Record<string, any>> {
    return this.actions.filter(a => a.action === action)
  }

  async sendSentenceStart(index: number, sessionId: number) {
    await this.page!.evaluate(({ index, sessionId }) => {
      const ws = (window as any).__wsDriverSocket
      if (ws && ws._onmessage) {
        ws._onmessage({ data: JSON.stringify({ type: 'sentence_start', index, session_id: sessionId }) })
      }
    }, { index, sessionId })
  }

  async sendAudioChunk(data: Buffer | Uint8Array) {
    const size = data.length
    await this.page!.evaluate((size: number) => {
      const ws = (window as any).__wsDriverSocket
      if (ws && ws._onmessage) {
        ws._onmessage({ data: new ArrayBuffer(size) })
      }
    }, size)
  }

  async sendComplete(sessionId: number) {
    await this.page!.evaluate((sessionId) => {
      const ws = (window as any).__wsDriverSocket
      if (ws && ws._onmessage) {
        ws._onmessage({ data: JSON.stringify({ type: 'complete', session_id: sessionId }) })
      }
    }, sessionId)
  }
}
