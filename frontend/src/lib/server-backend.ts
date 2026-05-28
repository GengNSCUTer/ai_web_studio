import { fetchBackend } from "@/lib/backend";

export async function fetchBackendJson<T>(path: string, token?: string): Promise<T> {
  const response = await fetchBackend(path, {
    headers: token
      ? {
          authorization: `Bearer ${token}`,
        }
      : undefined,
  });

  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function fetchBackendJsonOrNull<T>(path: string, token?: string): Promise<T | null> {
  try {
    return await fetchBackendJson<T>(path, token);
  } catch {
    return null;
  }
}
