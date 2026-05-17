import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";

export const runtime = "nodejs";

const backendBaseUrl =
  process.env.BACKEND_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:32007";

async function proxy(request: NextRequest, path: string[]) {
  const upstreamUrl = new URL(`${backendBaseUrl}/api/${path.join("/")}`);
  const query = request.nextUrl.searchParams.toString();
  if (query) {
    upstreamUrl.search = query;
  }

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");

  if (contentType) {
    headers.set("content-type", contentType);
  }
  if (accept) {
    headers.set("accept", accept);
  }
  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: body && body.byteLength > 0 ? body : undefined,
    cache: "no-store",
    redirect: "manual",
  });

  const responseHeaders = new Headers();
  const upstreamContentType = upstream.headers.get("content-type");
  const upstreamCacheControl = upstream.headers.get("cache-control");
  const upstreamContentDisposition = upstream.headers.get("content-disposition");

  if (upstreamContentType) {
    responseHeaders.set("content-type", upstreamContentType);
  }
  if (upstreamCacheControl) {
    responseHeaders.set("cache-control", upstreamCacheControl);
  }
  if (upstreamContentDisposition) {
    responseHeaders.set("content-disposition", upstreamContentDisposition);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}
