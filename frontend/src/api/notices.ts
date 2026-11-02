type Listener = (message: string) => void;

const listeners = new Set<Listener>();

/** Confirms something the API accepted; failures stay beside the control as an ErrorNote. */
export function notify(message: string) {
  listeners.forEach((listener) => listener(message));
}

export function onNotice(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
