// Cookie 策略只读取服务端环境变量，不依赖可伪造的请求来源或转发头。
export function authCookieOptions(maxAge = 60 * 60 * 24 * 7) {
  const configured = process.env.AUTH_COOKIE_SECURE;
  if (configured !== undefined && !["true", "false"].includes(configured)) {
    throw new Error("AUTH_COOKIE_SECURE 必须为 true 或 false");
  }
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: configured === undefined ? process.env.NODE_ENV === "production" : configured === "true",
    path: "/",
    maxAge,
  };
}
