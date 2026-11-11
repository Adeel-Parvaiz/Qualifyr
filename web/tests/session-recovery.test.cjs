const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const vm = require("node:vm")
const ts = require("typescript")

function load(path, imports, globals = {}) {
  const code = ts.transpileModule(fs.readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const context = { exports: {}, console: { error: () => {} }, require: (name) => {
    assert.ok(name in imports, `Unexpected import: ${name}`)
    return imports[name]
  }, ...globals }
  vm.runInNewContext(code, context)
  return context.exports
}

test("concurrent API requests restore the browser user once from the server session", async () => {
  let exchanges = 0, logins = 0
  const user = { uid: "verified-user" }
  const auth = { currentUser: null, authStateReady: async () => {} }
  const client = load("src/lib/firebase/client.ts", {
    "firebase/app": { getApps: () => [{}], getApp: () => ({}) },
    "firebase/auth": {
      getAuth: () => auth,
      signInWithCustomToken: async (_, token) => {
        assert.equal(token, "custom-token")
        logins++
        auth.currentUser = user
        return { user }
      },
    },
    "./config": { firebaseConfigured: true },
  }, { fetch: async (url, options) => {
    assert.equal(url, "/api/auth/session")
    assert.equal(options.cache, "no-store")
    exchanges++
    return { ok: true, json: async () => ({ customToken: "custom-token" }) }
  } })
  const users = await Promise.all([client.signedInUser(), client.signedInUser()])
  assert.ok(users.every(result => result === user))
  assert.equal(exchanges, 1)
  assert.equal(logins, 1)
  assert.equal(await client.signedInUser(), user)
  assert.equal(exchanges, 1)
})

function sessionRoute(auth) {
  return load("src/app/api/auth/session/route.ts", {
    "next/server": { NextResponse: { json: (body, options) => ({ body, ...options }) } },
    "@/lib/firebase/admin": { createAdminAuth: () => auth },
    "@/lib/firebase/session": { SESSION_COOKIE_NAME: "session" },
  })
}

test("service-account credential files use local signing instead of the IAM API", () => {
  let certified = false
  const auth = {}
  const account = { type: "service_account", private_key: "test-key", client_email: "test@example.com" }
  const admin = load("src/lib/firebase/admin.ts", {
    "node:fs": { readFileSync: path => {
      assert.equal(path, "/credential.json")
      return JSON.stringify(account)
    } },
    "firebase-admin/app": {
      getApps: () => [],
      cert: value => {
        assert.equal(value.private_key, account.private_key)
        certified = true
        return "local-credential"
      },
      applicationDefault: () => assert.fail("Must use the private signing key"),
      initializeApp: options => {
        assert.equal(options.credential, "local-credential")
        return {}
      },
    },
    "firebase-admin/auth": { getAuth: () => auth },
  }, { process: { env: { FIREBASE_PROJECT_ID: "test", GOOGLE_APPLICATION_CREDENTIALS: "/credential.json" } } })
  assert.equal(admin.createAdminAuth(), auth)
  assert.equal(certified, true)
})

test("session recovery rejects a missing cookie", async () => {
  const route = sessionRoute({})
  const response = await route.GET({ cookies: { get: () => undefined } })
  assert.equal(response.status, 401)
})

test("session recovery mints only for the verified UID and never caches tokens", async () => {
  const route = sessionRoute({
    verifySessionCookie: async (cookie, revoked) => {
      assert.equal(cookie, "signed-cookie")
      assert.equal(revoked, true)
      return { uid: "verified-user" }
    },
    createCustomToken: async uid => {
      assert.equal(uid, "verified-user")
      return "custom-token"
    },
  })
  const response = await route.GET({ cookies: { get: () => ({ value: "signed-cookie" }) } })
  assert.equal(response.body.customToken, "custom-token")
  assert.equal(response.headers["Cache-Control"], "no-store")
})

for (const code of ["auth/invalid-session-cookie", "auth/session-cookie-revoked"]) {
  test(`session recovery rejects ${code} without minting a token`, async () => {
    const route = sessionRoute({
      verifySessionCookie: async () => { throw { code } },
      createCustomToken: () => assert.fail("Must not mint a token"),
    })
    const response = await route.GET({ cookies: { get: () => ({ value: "bad-cookie" }) } })
    assert.equal(response.status, 401)
  })
}

test("service failures are not mistaken for expired sessions", async () => {
  const failure = new Error("service unavailable")
  const route = sessionRoute({ verifySessionCookie: async () => { throw failure } })
  const response = await route.GET({ cookies: { get: () => ({ value: "cookie" }) } })
  assert.equal(response.status, 503)
  assert.equal(response.body.error, "Session recovery failed (server-error).")
})

test("public backend health works even when browser session restoration is unavailable", async () => {
  const api = load("src/lib/api.ts", {
    "firebase/auth": {},
    "@/lib/firebase/config": { firebaseConfigured: true },
    "@/lib/firebase/client": { signedInUser: () => assert.fail("Health must not depend on login") },
  }, {
    process: { env: {} },
    fetch: async (url, options) => {
      assert.equal(url, "/api/health")
      assert.equal(options.headers.Authorization, undefined)
      return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ status: "ok" }) }
    },
  })
  assert.equal((await api.api.health()).status, "ok")
})
