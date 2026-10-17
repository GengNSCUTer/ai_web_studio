import { fetchBackend } from "@/lib/backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    // 探测 BFF 实际使用的地址，避免首页可打开但请求仍指向旧后端。
    const upstream = await fetchBackend("/health/ready", {
      signal: AbortSignal.timeout(5_000),
    });
    return Response.json(
      { status: upstream.ok ? "ready" : "not_ready" },
      { status: upstream.ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return Response.json(
      { status: "not_ready" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
