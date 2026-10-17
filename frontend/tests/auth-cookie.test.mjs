import assert from "node:assert/strict";
import { test } from "node:test";
import { authCookieOptions } from "../src/lib/auth-cookie.ts";

test("生产、开发及显式配置使用一致的认证与注销 Cookie 策略", () => {
  const previous = { node: process.env.NODE_ENV, secure: process.env.AUTH_COOKIE_SECURE };
  try {
    delete process.env.AUTH_COOKIE_SECURE;
    process.env.NODE_ENV = "production";
    assert.equal(authCookieOptions().secure, true);
    assert.deepEqual(authCookieOptions(0), { ...authCookieOptions(), maxAge: 0 });
    assert.equal(authCookieOptions().httpOnly, true);
    assert.equal(authCookieOptions().sameSite, "lax");
    process.env.NODE_ENV = "development";
    assert.equal(authCookieOptions().secure, false);
    process.env.AUTH_COOKIE_SECURE = "true";
    assert.equal(authCookieOptions().secure, true);
    process.env.AUTH_COOKIE_SECURE = "false";
    assert.equal(authCookieOptions().secure, false);
    process.env.AUTH_COOKIE_SECURE = "flase";
    assert.throws(() => authCookieOptions(), /AUTH_COOKIE_SECURE/);
  } finally {
    for (const [key, value] of [["NODE_ENV", previous.node], ["AUTH_COOKIE_SECURE", previous.secure]]) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
