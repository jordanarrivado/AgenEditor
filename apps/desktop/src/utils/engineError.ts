/**
 * Engine and shell errors reach the page as text, often the engine's JSON error
 * (`{"code":…,"message":…}`). Show the person just the message.
 */
export function engineErrorText(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && typeof (parsed as { message?: unknown }).message === 'string') {
      return (parsed as { message: string }).message;
    }
  } catch { /* plain text */ }
  return text;
}
