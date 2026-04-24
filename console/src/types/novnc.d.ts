// noVNC type declarations (#903)
declare module "@novnc/novnc/lib/rfb" {
  export class RFB {
    constructor(container: HTMLElement, url: string, options?: Record<string, unknown>);
    disconnect(): void;
    sendCtrlAltDel(): void;
    sendKey(key: number, down: boolean): void;
    addEventListener(type: string, handler: (e: Event) => void): void;
    removeEventListener(type: string, handler: (e: Event) => void): void;
    clipViewport: boolean;
    viewport: boolean;
    viewOnly: boolean;
  }
}
