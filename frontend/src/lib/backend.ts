export const BACKEND_BASE_URL =
  process.env.BACKEND_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:32007";

export async function fetchBackend(
  path: string,
  init?: RequestInit
): Promise<Response> {
  return fetch(`${BACKEND_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
  });
}
