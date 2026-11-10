/** Supabase connection details, read once and validated in one place.
 *
 * Both values are publishable: the URL is a hostname and the anon key is designed to sit
 * in a browser bundle. What guards the data is the bearer check on the FastAPI side
 * (gtm_engine/api/auth.py), not the secrecy of these.
 */

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? ""
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? ""

/** False when the project has not been configured yet.
 *
 * Checked rather than assumed because these are inlined at *build* time: a deploy built
 * before the variables were set carries empty strings forever, and creating a client with
 * them throws somewhere unhelpful deep in the SDK. Callers surface a real message instead.
 */
export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY)
