/**
 * POSTing to the hosted Trilogy resolver, with the one retry policy every
 * resolver-backed suite should share.
 *
 * trilogy-service.fly.dev runs on a shared-CPU Fly instance with a burst quota.
 * Sustained compiling drains it and every request is throttled until it
 * refills, which surfaces as a 502 or as a compile that takes tens of seconds
 * instead of ~0.5s. CI makes that routine rather than exotic: `test` and
 * `dashboard-queries` both compile against it and run concurrently, so each is
 * part of the other's load.
 *
 * The rule is the same everywhere and it is worth stating once: **retry the
 * transport, never the verdict.** A 5xx or a dropped connection is the instance
 * being unwell and says nothing about the query. A query that cannot plan comes
 * back inside a 200 carrying its own `error`, and retrying that would only turn
 * a real failure into a slow real failure.
 *
 * Retrying costs time, so a caller using this needs a per-test timeout with
 * room for the backoff below (~14s of sleeping, plus however long a throttled
 * request itself takes). 30s is not enough; the suites here use 120s.
 */

export const RESOLVER_URL = process.env.TRILOGY_RESOLVER_URL ?? 'https://trilogy-service.fly.dev'

export type ResolverResponse = { ok: true; text: string } | { ok: false; error: string }

/** Attempts after the first, and the backoff before each: 2s, 4s, 8s. */
const RETRIES = 3
const BACKOFF_MS = (attempt: number) => 2_000 * 2 ** (attempt - 1)

export async function postToResolver(
  path: string,
  body: unknown,
  { retries = RETRIES }: { retries?: number } = {},
): Promise<ResolverResponse> {
  const payload = JSON.stringify(body)
  let failure = 'the request was never sent'

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, BACKOFF_MS(attempt)))

    let response: Response
    try {
      response = await fetch(`${RESOLVER_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
      })
    } catch (error) {
      failure = `request failed: ${(error as Error).message}`
      continue
    }

    const text = await response.text()
    if (response.ok) return { ok: true, text }

    let detail = text
    try {
      const parsed = JSON.parse(text) as { detail?: unknown }
      if (parsed.detail != null) {
        detail = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail)
      }
    } catch {
      // Not JSON — keep the raw body.
    }
    failure = `HTTP ${response.status}: ${detail.slice(0, 600)}`

    // 4xx is the request being wrong, and retrying will not change that.
    if (response.status < 500) break
  }

  return { ok: false, error: failure }
}

/** `postToResolver` for callers that would only rethrow the failure anyway. */
export async function postToResolverOrThrow(path: string, body: unknown): Promise<string> {
  const result = await postToResolver(path, body)
  if (!result.ok) throw new Error(result.error)
  return result.text
}
