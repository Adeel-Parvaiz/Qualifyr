import type { MetadataRoute } from "next"

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://qualifyr.vercel.app"

/** Only the public marketing page belongs here. /sign-in is noindex and everything under
 *  (app) requires auth, so a crawler should never be pointed at them. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: siteUrl,
      lastModified: new Date(),
      changeFrequency: "monthly",
      priority: 1,
    },
  ]
}
