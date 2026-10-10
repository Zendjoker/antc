"use client";

import { Dialog } from "radix-ui";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface Options { title: string; description?: string; confirmLabel?: string; destructive?: boolean }
type Ask = (o: Options) => Promise<boolean>;

const Ctx = createContext<Ask | null>(null);

/** Replaces the browser's native confirm() for protected and destructive actions. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<Options | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const ask = useCallback<Ask>((o) => {
    resolver.current?.(false);
    setOpts(o);
    return new Promise<boolean>((res) => { resolver.current = res; });
  }, []);

  const settle = (v: boolean) => {
    resolver.current?.(v);
    resolver.current = null;
    setOpts(null);
  };

  return (
    <Ctx.Provider value={ask}>
      {children}
      <Dialog.Root open={!!opts} onOpenChange={(o) => { if (!o) settle(false); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-popover p-5 text-popover-foreground shadow-xl">
            <Dialog.Title className="text-base font-semibold">{opts?.title}</Dialog.Title>
            <Dialog.Description className="mt-2 whitespace-pre-line text-sm text-muted-foreground">{opts?.description}</Dialog.Description>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" onClick={() => settle(false)}>Cancel</Button>
              <Button variant={opts?.destructive ? "destructive" : "default"} onClick={() => settle(true)}>{opts?.confirmLabel ?? "Confirm"}</Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Ctx.Provider>
  );
}

export function useConfirm() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useConfirm must be used inside ConfirmProvider");
  return v;
}
