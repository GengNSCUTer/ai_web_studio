import { NextRequest, NextResponse } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { authCookieOptions } from "@/lib/auth-cookie";
import { fetchBackend } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const body = await request.text();
  const upstream = await fetchBackend("/api/auth/register", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body,
  });

  const contentType = upstream.headers.get("content-type") ?? "application/json";
  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": contentType,
      },
    });
  }

  const payload = (await upstream.json()) as {
    access_token: string;
    user: unknown;
  };

  const response = NextResponse.json({ user: payload.user });
  response.cookies.set(AUTH_COOKIE_NAME, payload.access_token, authCookieOptions());
  return response;
}
